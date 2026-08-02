from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.catalog import cache_references, load_catalog, select_references
from decor_matcher.types import CatalogItem, ReferenceRecord


def product(product_id: str, image_url: str) -> CatalogItem:
    return CatalogItem(product_id, f"Product {product_id}", image_url)


def make_jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_select_references_is_deterministic_and_exactly_100(tmp_path):
    products = [product(str(i), f"https://img/{i}.jpg") for i in range(125)]
    selected = select_references(products, limit=100)
    expected = sorted(products, key=lambda p: sha256(p.product_id.encode()).hexdigest())[:100]
    assert [r.product_id for r in selected] == [p.product_id for p in expected]


def test_select_references_skips_missing_and_duplicate_images():
    products = [product("1", ""), product("2", "https://img/a.jpg"), product("2", "https://img/a.jpg")]
    assert [r.product_id for r in select_references(products, limit=100)] == ["2"]


def test_cache_references_validates_decodable_images(tmp_path):
    records = [ReferenceRecord("2", "Chair", "https://img/a.jpg", None, None)]
    cached, failures = cache_references(records, tmp_path, lambda _: make_jpeg_bytes())
    assert len(cached) == 1
    assert cached[0].cache_path.exists()
    assert cached[0].sha256 == sha256(make_jpeg_bytes()).hexdigest()
    assert failures == []


def test_load_catalog_reads_the_catalog_schema(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        '{"catalog": [{"id": "42", "name": "Lamp", "image": "https://cdn.example/lamp.jpg"}]}',
        encoding="utf-8",
    )

    assert load_catalog(path) == [CatalogItem("42", "Lamp", "https://cdn.example/lamp.jpg")]


@pytest.mark.parametrize(
    "entry",
    [
        {"id": 42, "name": "Lamp", "image": "https://cdn.example/lamp.jpg"},
        {"id": "42", "name": 7, "image": "https://cdn.example/lamp.jpg"},
        {"id": "42", "name": "Lamp", "image": "file:///tmp/lamp.jpg"},
        {"id": "42", "name": "Lamp", "image": "https://localhost/lamp.jpg"},
    ],
)
def test_load_catalog_rejects_invalid_catalog_items(tmp_path, entry):
    path = tmp_path / "catalog.json"
    import json

    path.write_text(json.dumps({"catalog": [entry]}), encoding="utf-8")

    with pytest.raises(ValueError):
        load_catalog(path)


def test_cache_references_continues_after_invalid_image(tmp_path):
    records = [
        ReferenceRecord("bad", "Broken", "https://img/bad.jpg", None, None),
        ReferenceRecord("good", "Chair", "https://img/good.jpg", None, None),
    ]

    cached, failures = cache_references(
        records,
        tmp_path,
        lambda url: b"not an image" if "bad" in url else make_jpeg_bytes(),
    )

    assert [record.product_id for record in cached] == ["good"]
    assert failures == ["bad: cannot identify image file"]


def test_cache_references_continues_after_fetcher_failure(tmp_path):
    records = [
        ReferenceRecord("unavailable", "Unavailable", "https://img/unavailable.jpg", None, None),
        ReferenceRecord("good", "Chair", "https://img/good.jpg", None, None),
    ]

    def fetch(url: str) -> bytes:
        if "unavailable" in url:
            raise RuntimeError("network unavailable")
        return make_jpeg_bytes()

    cached, failures = cache_references(records, tmp_path, fetch)

    assert [record.product_id for record in cached] == ["good"]
    assert failures == ["unavailable: network unavailable"]
