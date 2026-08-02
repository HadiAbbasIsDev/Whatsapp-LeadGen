import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.artifacts import SSCD_ARTIFACT
from decor_matcher import index as index_module
from decor_matcher.index import (
    MAX_MANIFEST_BYTES,
    MAX_PRODUCTION_REFERENCES,
    IndexValidationError,
    build_index,
    load_index,
    retrieve,
)
from decor_matcher.types import ReferenceRecord


class FakeEncoder:
    def __init__(self, descriptors):
        self.descriptors = descriptors
        self.model_sha256 = SSCD_ARTIFACT.sha256

    def encode(self, images):
        return np.asarray(
            [self.descriptors[Path(image.filename).stem] for image in images],
            dtype=np.float32,
        )


def cached_records(tmp_path, product_ids):
    records = []
    for index, product_id in enumerate(product_ids):
        cache_path = tmp_path / f"{product_id}.png"
        Image.new("RGB", (2, 2), color=(index, index, index)).save(cache_path)
        image_sha256 = sha256(cache_path.read_bytes()).hexdigest()
        records.append(
            ReferenceRecord(
                product_id,
                f"Product {product_id}",
                f"https://img/{product_id}.png",
                cache_path,
                image_sha256,
            )
        )
    return records


def test_build_and_retrieve_returns_cosine_ranked_products(tmp_path):
    records = cached_records(tmp_path, ["a", "b", "c"])
    encoder = FakeEncoder({"a": [1.0, 0.0], "b": [0.8, 0.2], "c": [0.0, 1.0]})

    index = build_index(records, encoder, tmp_path / "index", require_exact_count=False)
    candidates = retrieve(np.array([1.0, 0.0], dtype=np.float32), index, top_k=2)

    assert [candidate.product_id for candidate in candidates] == ["a", "b"]
    assert candidates[0].score == pytest.approx(1.0)

    loaded = load_index(tmp_path / "index", allow_nonproduction=True)
    assert loaded.vectors.dtype == np.float32
    assert loaded.vectors.shape == (3, 2)
    assert [record.product_id for record in loaded.records] == ["a", "b", "c"]
    manifest = json.loads((tmp_path / "index" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_sha256"] == SSCD_ARTIFACT.sha256
    assert manifest["vectors_sha256"] == sha256(
        (tmp_path / "index" / "sscd_vectors.npy").read_bytes()
    ).hexdigest()
    assert [reference["sha256"] for reference in manifest["references"]] == [
        sha256(record.cache_path.read_bytes()).hexdigest() for record in records
    ]


def test_index_loader_rejects_manifest_vector_mismatch(tmp_path):
    vector_path = tmp_path / "sscd_vectors.npy"
    np.save(vector_path, np.zeros((2, 2), dtype=np.float32))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "model_sha256": SSCD_ARTIFACT.sha256,
                "vectors_sha256": sha256(vector_path.read_bytes()).hexdigest(),
                "references": [
                    {
                        "product_id": "a",
                        "product_name": "Product a",
                        "image_url": "https://img/a.png",
                        "cache_path": str(tmp_path / "a.png"),
                        "sha256": "hash-a",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(IndexValidationError):
        load_index(tmp_path, allow_nonproduction=True)


def test_production_index_accepts_bounded_variable_count_and_records_manifest_count(tmp_path):
    records = cached_records(tmp_path, ["a", "b", "c"])
    descriptors = {record.product_id: np.ones(512, dtype=np.float32) for record in records}

    built = build_index(records, FakeEncoder(descriptors), tmp_path / "index")
    loaded = load_index(tmp_path / "index")
    manifest = json.loads((tmp_path / "index" / "manifest.json").read_text(encoding="utf-8"))

    assert built.vectors.shape == (3, 512)
    assert loaded.vectors.shape == (3, 512)
    assert manifest["reference_count"] == 3


def test_production_index_rejects_manifest_count_mismatch(tmp_path):
    records = cached_records(tmp_path, ["a"])
    build_index(
        records,
        FakeEncoder({"a": np.ones(512, dtype=np.float32)}),
        tmp_path / "index",
    )
    manifest_path = tmp_path / "index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reference_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IndexValidationError, match="reference count"):
        load_index(tmp_path / "index")


@pytest.mark.parametrize("shape", [(0, 512), (100, 2), (MAX_PRODUCTION_REFERENCES + 1, 512)])
def test_load_index_rejects_nonproduction_shape_by_default(tmp_path, shape):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    vector_path = index_dir / "sscd_vectors.npy"
    np.save(vector_path, np.ones(shape, dtype=np.float32))
    (index_dir / "manifest.json").write_text(
        json.dumps(
            {
                "model_sha256": SSCD_ARTIFACT.sha256,
                "vectors_sha256": sha256(vector_path.read_bytes()).hexdigest(),
                "reference_count": shape[0],
                "references": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(IndexValidationError, match="production SSCD vectors"):
        load_index(index_dir)


def test_load_index_rejects_tampered_model_checksum(tmp_path):
    records = cached_records(tmp_path, ["a"])
    build_index(
        records,
        FakeEncoder({"a": [1.0, 0.0]}),
        tmp_path / "index",
        require_exact_count=False,
    )
    manifest_path = tmp_path / "index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["model_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IndexValidationError, match="model checksum"):
        load_index(tmp_path / "index", allow_nonproduction=True)


def test_build_index_rejects_source_image_hash_mismatch(tmp_path):
    records = cached_records(tmp_path, ["a"])
    Image.new("RGB", (2, 2), color="red").save(records[0].cache_path)

    with pytest.raises(IndexValidationError, match="source image checksum mismatch"):
        build_index(
            records,
            FakeEncoder({"a": [1.0, 0.0]}),
            tmp_path / "index",
            require_exact_count=False,
        )


def test_load_index_rejects_vectors_from_a_different_generation(tmp_path):
    records = cached_records(tmp_path, ["a"])
    index_dir = tmp_path / "index"
    build_index(
        records,
        FakeEncoder({"a": [1.0, 0.0]}),
        index_dir,
        require_exact_count=False,
    )
    old_manifest = (index_dir / "manifest.json").read_bytes()

    build_index(
        records,
        FakeEncoder({"a": [0.0, 1.0]}),
        index_dir,
        require_exact_count=False,
    )
    (index_dir / "manifest.json").write_bytes(old_manifest)

    with pytest.raises(IndexValidationError, match="vector file checksum"):
        load_index(index_dir, allow_nonproduction=True)


@pytest.mark.parametrize("mutation", ["missing", "changed", "directory"])
def test_load_index_rejects_missing_mutated_or_non_regular_reference(tmp_path, mutation):
    records = cached_records(tmp_path, ["a"])
    index_dir = tmp_path / "index"
    build_index(records, FakeEncoder({"a": [1.0, 0.0]}), index_dir, require_exact_count=False)
    reference_path = records[0].cache_path
    if mutation == "missing":
        reference_path.unlink()
    elif mutation == "changed":
        reference_path.write_bytes(b"changed")
    else:
        reference_path.unlink()
        reference_path.mkdir()

    with pytest.raises(IndexValidationError, match="reference"):
        load_index(index_dir, allow_nonproduction=True)


def test_load_index_rejects_reference_with_unsafe_image_metadata(tmp_path):
    records = cached_records(tmp_path, ["a"])
    index_dir = tmp_path / "index"
    build_index(records, FakeEncoder({"a": [1.0, 0.0]}), index_dir, require_exact_count=False)
    Image.new("RGB", (8192, 1), "white").save(records[0].cache_path, format="PNG")
    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["references"][0]["sha256"] = sha256(records[0].cache_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IndexValidationError, match="unsafe image"):
        load_index(index_dir, allow_nonproduction=True)


def test_load_index_rejects_oversized_vector_before_reading_it(tmp_path, monkeypatch):
    records = cached_records(tmp_path, ["a"])
    index_dir = tmp_path / "index"
    build_index(records, FakeEncoder({"a": [1.0, 0.0]}), index_dir, require_exact_count=False)
    vector_path = index_dir / "sscd_vectors.npy"
    with vector_path.open("wb") as destination:
        destination.truncate(2 * 1024 * 1024)
    real_read_bytes = Path.read_bytes

    def reject_unbounded_read(path):
        if path == vector_path:
            raise AssertionError("oversized vector must be rejected from stat metadata")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)

    with pytest.raises(IndexValidationError, match="vector"):
        load_index(index_dir, allow_nonproduction=True)


def test_load_index_rejects_oversized_manifest_before_unbounded_text_read(tmp_path, monkeypatch):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    manifest_path = index_dir / "manifest.json"
    with manifest_path.open("wb") as destination:
        destination.truncate(MAX_MANIFEST_BYTES + 1)

    def reject_read_text(path, *args, **kwargs):
        if path == manifest_path:
            raise AssertionError("oversized manifest must be rejected from stat metadata")
        return Path.read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_read_text)

    with pytest.raises(IndexValidationError, match="manifest"):
        load_index(index_dir, allow_nonproduction=True)


def test_load_index_decodes_the_same_verified_snapshot_during_vector_replace(tmp_path, monkeypatch):
    records = cached_records(tmp_path, ["a"])
    index_dir = tmp_path / "index"
    build_index(
        records,
        FakeEncoder({"a": [1.0, 0.0]}),
        index_dir,
        require_exact_count=False,
    )
    vector_path = index_dir / "sscd_vectors.npy"
    original_bytes = vector_path.read_bytes()
    replacement_buffer = BytesIO()
    np.save(replacement_buffer, np.array([[0.0, 1.0]], dtype=np.float32), allow_pickle=False)
    replacement_bytes = replacement_buffer.getvalue()

    real_read_bounded_bytes = index_module._read_bounded_bytes
    snapshot_reads = []

    def read_then_replace(path, max_bytes, label):
        snapshot = real_read_bounded_bytes(path, max_bytes, label)
        if path == vector_path:
            snapshot_reads.append(snapshot)
            vector_path.write_bytes(replacement_bytes)
        return snapshot

    monkeypatch.setattr(index_module, "_read_bounded_bytes", read_then_replace)

    loaded = load_index(index_dir, allow_nonproduction=True)

    np.testing.assert_array_equal(loaded.vectors, np.array([[1.0, 0.0]], dtype=np.float32))
    assert snapshot_reads == [original_bytes]
    assert vector_path.read_bytes() == replacement_bytes
