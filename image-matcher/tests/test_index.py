import json
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.index import IndexValidationError, build_index, load_index, retrieve
from decor_matcher.types import ReferenceRecord


class FakeEncoder:
    def __init__(self, descriptors):
        self.descriptors = descriptors
        self.model_sha256 = "model-hash"

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
        records.append(
            ReferenceRecord(
                product_id,
                f"Product {product_id}",
                f"https://img/{product_id}.png",
                cache_path,
                f"hash-{product_id}",
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

    loaded = load_index(tmp_path / "index")
    assert loaded.vectors.dtype == np.float32
    assert loaded.vectors.shape == (3, 2)
    assert [record.product_id for record in loaded.records] == ["a", "b", "c"]
    manifest = json.loads((tmp_path / "index" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_sha256"] == "model-hash"
    assert [reference["sha256"] for reference in manifest["references"]] == [
        "hash-a",
        "hash-b",
        "hash-c",
    ]


def test_index_loader_rejects_manifest_vector_mismatch(tmp_path):
    np.save(tmp_path / "sscd_vectors.npy", np.zeros((2, 2), dtype=np.float32))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "model_sha256": "model-hash",
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
        load_index(tmp_path)


def test_build_index_requires_exactly_100_records_by_default(tmp_path):
    records = cached_records(tmp_path, ["a"])
    encoder = FakeEncoder({"a": [1.0, 0.0]})

    with pytest.raises(IndexValidationError, match="exactly 100"):
        build_index(records, encoder, tmp_path / "index")
