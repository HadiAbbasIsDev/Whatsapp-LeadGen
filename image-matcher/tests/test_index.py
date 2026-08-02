import json
from hashlib import sha256
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.artifacts import SSCD_ARTIFACT
from decor_matcher.index import IndexValidationError, build_index, load_index, retrieve
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


def test_build_index_requires_exactly_100_records_by_default(tmp_path):
    records = cached_records(tmp_path, ["a"])
    encoder = FakeEncoder({"a": [1.0, 0.0]})

    with pytest.raises(IndexValidationError, match="exactly 100"):
        build_index(records, encoder, tmp_path / "index")


@pytest.mark.parametrize("shape", [(99, 512), (100, 2)])
def test_load_index_rejects_nonproduction_shape_by_default(tmp_path, shape):
    records = cached_records(tmp_path, [str(index) for index in range(shape[0])])
    descriptors = {record.product_id: np.ones(shape[1], dtype=np.float32) for record in records}
    build_index(
        records,
        FakeEncoder(descriptors),
        tmp_path / "index",
        require_exact_count=False,
    )

    with pytest.raises(IndexValidationError, match=r"\(100, 512\)"):
        load_index(tmp_path / "index")


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
