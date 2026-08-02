import json
import os
import stat
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
from PIL import Image

from .artifacts import SSCD_ARTIFACT
from .image_safety import MAX_IMAGE_BYTES, ImageSafetyError, inspect_safe_image
from .types import Candidate, ReferenceRecord


VECTOR_FILENAME = "sscd_vectors.npy"
MANIFEST_FILENAME = "manifest.json"
MAX_PRODUCTION_REFERENCES = 5000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_VECTOR_BYTES = MAX_PRODUCTION_REFERENCES * 512 * np.dtype(np.float32).itemsize + 1024 * 1024


class IndexValidationError(ValueError):
    """Raised when descriptor index data violates its persisted contract."""


class Encoder(Protocol):
    def encode(self, images: Sequence[Image.Image]) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class DescriptorIndex:
    vectors: np.ndarray
    records: tuple[ReferenceRecord, ...]
    model_sha256: str


def build_index(
    records: Sequence[ReferenceRecord],
    encoder: Encoder,
    index_dir: Path,
    *,
    require_exact_count: bool = True,
    batch_size: int = 16,
) -> DescriptorIndex:
    """Encode ordered cached references and atomically persist their descriptor index."""
    ordered_records = tuple(records)
    if not ordered_records:
        raise IndexValidationError("index requires at least one record")
    if require_exact_count and len(ordered_records) > MAX_PRODUCTION_REFERENCES:
        raise IndexValidationError(
            f"production index permits at most {MAX_PRODUCTION_REFERENCES} records"
        )
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    batches: list[np.ndarray] = []
    for offset in range(0, len(ordered_records), batch_size):
        batch_records = ordered_records[offset : offset + batch_size]
        images: list[Image.Image] = []
        try:
            for record in batch_records:
                if record.cache_path is None or record.sha256 is None:
                    raise IndexValidationError(f"reference {record.product_id} is not cached")
                try:
                    source_sha256 = _sha256_file(record.cache_path)
                except OSError as exc:
                    raise IndexValidationError(
                        f"cannot hash source image for reference {record.product_id}: {exc}"
                    ) from exc
                if source_sha256 != record.sha256:
                    raise IndexValidationError(
                        f"source image checksum mismatch for reference {record.product_id}"
                    )
                try:
                    inspect_safe_image(record.cache_path, verify=True)
                except ImageSafetyError as exc:
                    raise IndexValidationError(
                        f"unsafe image for reference {record.product_id}: {exc}"
                    ) from exc
                with Image.open(record.cache_path) as source:
                    image = source.convert("RGB")
                image.filename = str(record.cache_path)
                images.append(image)
            encoded = np.asarray(encoder.encode(images), dtype=np.float32)
        finally:
            for image in images:
                image.close()

        if encoded.ndim != 2 or encoded.shape[0] != len(batch_records):
            raise IndexValidationError(
                f"encoder returned shape {encoded.shape} for {len(batch_records)} images"
            )
        batches.append(encoded)

    vectors = np.concatenate(batches, axis=0).astype(np.float32, copy=False)
    if not np.all(np.isfinite(vectors)):
        raise IndexValidationError("encoder returned non-finite descriptors")
    if require_exact_count and vectors.shape != (len(ordered_records), 512):
        raise IndexValidationError(
            f"production SSCD vectors must have shape (N, 512), got {vectors.shape}"
        )

    model_sha256 = str(getattr(encoder, "model_sha256", SSCD_ARTIFACT.sha256))
    index = DescriptorIndex(vectors, ordered_records, model_sha256)
    index_dir.mkdir(parents=True, exist_ok=True)
    vector_path = index_dir / VECTOR_FILENAME
    _atomic_save_vectors(vector_path, vectors)
    vectors_sha256 = _sha256_file(vector_path)
    _atomic_write_json(index_dir / MANIFEST_FILENAME, _manifest(index, vectors_sha256))
    return index


def load_index(index_dir: Path, *, allow_nonproduction: bool = False) -> DescriptorIndex:
    """Load an index only when vectors and ordered reference metadata agree."""
    vector_path = index_dir / VECTOR_FILENAME
    try:
        manifest_bytes = _read_bounded_bytes(
            index_dir / MANIFEST_FILENAME,
            MAX_MANIFEST_BYTES,
            "manifest",
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        references = manifest["references"]
        reference_count = manifest["reference_count"]
        model_sha256 = manifest["model_sha256"]
        vectors_sha256 = manifest["vectors_sha256"]
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IndexValidationError(f"cannot load index: {exc}") from exc

    try:
        vector_bytes = _read_bounded_bytes(vector_path, MAX_VECTOR_BYTES, "vector file")
    except (OSError, ValueError) as exc:
        raise IndexValidationError(f"cannot read index vectors: {exc}") from exc
    if not isinstance(vectors_sha256, str) or vectors_sha256 != sha256(vector_bytes).hexdigest():
        raise IndexValidationError("vector file checksum does not match manifest")
    try:
        vectors = np.load(BytesIO(vector_bytes), allow_pickle=False)
    except (OSError, ValueError, TypeError) as exc:
        raise IndexValidationError(f"cannot load index vectors: {exc}") from exc

    if vectors.dtype != np.float32 or vectors.ndim != 2:
        raise IndexValidationError("vectors must be a two-dimensional float32 array")
    if (
        not allow_nonproduction
        and (
            vectors.shape[1] != 512
            or vectors.shape[0] <= 0
            or vectors.shape[0] > MAX_PRODUCTION_REFERENCES
        )
    ):
        raise IndexValidationError(
            f"production SSCD vectors must have shape (N, 512) for 1 <= N <= {MAX_PRODUCTION_REFERENCES}, "
            f"got {vectors.shape}"
        )
    if isinstance(reference_count, bool) or not isinstance(reference_count, int):
        raise IndexValidationError("manifest reference count must be an integer")
    if reference_count <= 0 or reference_count > MAX_PRODUCTION_REFERENCES:
        raise IndexValidationError("manifest reference count is outside the production bound")
    if (
        not isinstance(references, list)
        or reference_count != len(references)
        or reference_count != vectors.shape[0]
    ):
        raise IndexValidationError(
            "manifest reference count does not match reference metadata and vectors"
        )
    if not isinstance(references, list) or vectors.shape[0] != len(references):
        raise IndexValidationError(
            f"manifest/vector row mismatch: {len(references) if isinstance(references, list) else 'invalid'} references, "
            f"{vectors.shape[0]} vectors"
        )
    if model_sha256 != SSCD_ARTIFACT.sha256:
        raise IndexValidationError("manifest model checksum does not match pinned SSCD artifact")
    if not np.all(np.isfinite(vectors)):
        raise IndexValidationError("vectors contain non-finite values")

    records: list[ReferenceRecord] = []
    for position, reference in enumerate(references):
        try:
            record = ReferenceRecord(
                product_id=reference["product_id"],
                product_name=reference["product_name"],
                image_url=reference["image_url"],
                cache_path=Path(reference["cache_path"]),
                sha256=reference["sha256"],
            )
        except (KeyError, TypeError) as exc:
            raise IndexValidationError(f"invalid reference metadata at position {position}") from exc
        if not all(
            isinstance(value, str) and value
            for value in (record.product_id, record.product_name, record.image_url, record.sha256)
        ):
            raise IndexValidationError(f"invalid reference metadata at position {position}")
        if len(record.sha256) != 64 or any(character not in "0123456789abcdef" for character in record.sha256):
            raise IndexValidationError(f"invalid reference checksum at position {position}")
        _validate_reference_file(record, position)
        records.append(record)

    return DescriptorIndex(vectors, tuple(records), model_sha256)


def retrieve(
    query_vector: np.ndarray,
    index: DescriptorIndex,
    top_k: int = 5,
) -> list[Candidate]:
    """Return the highest cosine-similarity catalog candidates."""
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    query = np.asarray(query_vector, dtype=np.float32)
    if query.ndim != 1 or query.shape[0] != index.vectors.shape[1]:
        raise IndexValidationError(
            f"query shape {query.shape} does not match descriptor width {index.vectors.shape[1]}"
        )
    query_norm = float(np.linalg.norm(query))
    vector_norms = np.linalg.norm(index.vectors, axis=1)
    if not np.isfinite(query_norm) or query_norm == 0.0:
        raise IndexValidationError("query descriptor must have a finite non-zero norm")
    if np.any(~np.isfinite(vector_norms)) or np.any(vector_norms == 0.0):
        raise IndexValidationError("index descriptors must have finite non-zero norms")

    scores = (index.vectors @ query) / (vector_norms * query_norm)
    ranked = np.argsort(-scores, kind="stable")[: min(top_k, len(index.records))]
    return [
        Candidate(
            product_id=index.records[position].product_id,
            product_name=index.records[position].product_name,
            score=float(scores[position]),
            reference_path=index.records[position].cache_path,
            reference_sha256=index.records[position].sha256,
        )
        for position in ranked
    ]


def _manifest(index: DescriptorIndex, vectors_sha256: str) -> dict[str, object]:
    return {
        "model_sha256": index.model_sha256,
        "vectors_sha256": vectors_sha256,
        "reference_count": len(index.records),
        "references": [
            {
                "product_id": record.product_id,
                "product_name": record.product_name,
                "image_url": record.image_url,
                "cache_path": str(record.cache_path),
                "sha256": record.sha256,
            }
            for record in index.records
        ],
    }


def _atomic_save_vectors(target: Path, vectors: np.ndarray) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        np.save(temporary, vectors, allow_pickle=False)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_json(target: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary.write(encoded)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded_bytes(path: Path, max_bytes: int, label: str) -> bytes:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise IndexValidationError(f"cannot stat {label}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise IndexValidationError(f"{label} is not a regular file")
    if metadata.st_size <= 0 or metadata.st_size > max_bytes:
        raise IndexValidationError(
            f"{label} size {metadata.st_size} is outside the permitted bound {max_bytes}"
        )
    try:
        with path.open("rb") as source:
            payload = source.read(max_bytes + 1)
    except OSError as exc:
        raise IndexValidationError(f"cannot read {label}: {exc}") from exc
    if len(payload) != metadata.st_size or len(payload) > max_bytes:
        raise IndexValidationError(f"{label} changed or exceeded its bound while being read")
    return payload


def _validate_reference_file(record: ReferenceRecord, position: int) -> None:
    path = record.cache_path
    if path is None:
        raise IndexValidationError(f"reference file is missing at position {position}")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise IndexValidationError(f"cannot stat reference file at position {position}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise IndexValidationError(f"reference path at position {position} is not a regular file")
    if metadata.st_size <= 0 or metadata.st_size > MAX_IMAGE_BYTES:
        raise IndexValidationError(f"reference file at position {position} has an unsafe size")

    digest = sha256()
    total = 0
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise IndexValidationError(
                        f"reference file at position {position} exceeded its size bound"
                    )
                digest.update(chunk)
    except OSError as exc:
        raise IndexValidationError(f"cannot read reference file at position {position}: {exc}") from exc
    if total != metadata.st_size:
        raise IndexValidationError(f"reference file at position {position} changed while hashing")
    if digest.hexdigest() != record.sha256:
        raise IndexValidationError(f"reference checksum mismatch at position {position}")
    try:
        inspect_safe_image(path, verify=True)
    except ImageSafetyError as exc:
        raise IndexValidationError(f"unsafe image for reference at position {position}: {exc}") from exc
