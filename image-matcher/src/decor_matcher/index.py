import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
from PIL import Image

from .artifacts import SSCD_ARTIFACT
from .types import Candidate, ReferenceRecord


VECTOR_FILENAME = "sscd_vectors.npy"
MANIFEST_FILENAME = "manifest.json"


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
    if require_exact_count and len(ordered_records) != 100:
        raise IndexValidationError(f"production index requires exactly 100 records, got {len(ordered_records)}")
    if not ordered_records:
        raise IndexValidationError("index requires at least one record")
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
    if require_exact_count and vectors.shape != (100, 512):
        raise IndexValidationError(
            f"production SSCD vectors must have shape (100, 512), got {vectors.shape}"
        )

    model_sha256 = str(getattr(encoder, "model_sha256", SSCD_ARTIFACT.sha256))
    index = DescriptorIndex(vectors, ordered_records, model_sha256)
    index_dir.mkdir(parents=True, exist_ok=True)
    _atomic_save_vectors(index_dir / VECTOR_FILENAME, vectors)
    _atomic_write_json(index_dir / MANIFEST_FILENAME, _manifest(index))
    return index


def load_index(index_dir: Path) -> DescriptorIndex:
    """Load an index only when vectors and ordered reference metadata agree."""
    try:
        vectors = np.load(index_dir / VECTOR_FILENAME, allow_pickle=False)
        manifest = json.loads((index_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        references = manifest["references"]
        model_sha256 = manifest["model_sha256"]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IndexValidationError(f"cannot load index: {exc}") from exc

    if vectors.dtype != np.float32 or vectors.ndim != 2:
        raise IndexValidationError("vectors must be a two-dimensional float32 array")
    if not isinstance(references, list) or vectors.shape[0] != len(references):
        raise IndexValidationError(
            f"manifest/vector row mismatch: {len(references) if isinstance(references, list) else 'invalid'} references, "
            f"{vectors.shape[0]} vectors"
        )
    if not isinstance(model_sha256, str) or not model_sha256:
        raise IndexValidationError("manifest model_sha256 must be a non-empty string")
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
        )
        for position in ranked
    ]


def _manifest(index: DescriptorIndex) -> dict[str, object]:
    return {
        "model_sha256": index.model_sha256,
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
