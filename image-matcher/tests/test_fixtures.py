import json
from hashlib import sha256
from pathlib import Path

from PIL import Image

from decor_matcher.fixtures import generate_fixture_set, generate_variants
from decor_matcher.types import ReferenceRecord


def _source_image(path: Path) -> Path:
    image = Image.new("RGB", (120, 80), (40, 100, 180))
    for x in range(20, 100):
        for y in range(15, 65):
            image.putpixel((x, y), (220, 150, (x + y) % 255))
    image.save(path, format="PNG")
    return path


def test_fixture_generation_is_deterministic(tmp_path):
    source = _source_image(tmp_path / "source.png")

    first = generate_variants(source, tmp_path / "a", seed=20260802)
    second = generate_variants(source, tmp_path / "b", seed=20260802)

    assert [path.name for path in first] == [
        "query_jpeg55.jpg",
        "query_resize50.jpg",
        "query_crop12.jpg",
        "query_screenshot.jpg",
        "query_text_overlay.jpg",
    ]
    assert [sha256(path.read_bytes()).hexdigest() for path in first] == [
        sha256(path.read_bytes()).hexdigest() for path in second
    ]


def test_fixture_set_records_product_truth_and_respects_limit(tmp_path):
    first = _source_image(tmp_path / "first.png")
    second = _source_image(tmp_path / "second.png")
    records = (
        ReferenceRecord("42", "Aura Chair", "https://example/42.jpg", first, sha256(first.read_bytes()).hexdigest()),
        ReferenceRecord("99", "Cloud Sofa", "https://example/99.jpg", second, sha256(second.read_bytes()).hexdigest()),
    )

    manifest = generate_fixture_set(records, tmp_path / "fixtures", max_products=1, seed=7)

    assert manifest["products"] == 1
    assert len(manifest["fixtures"]) == 5
    assert {item["product_id"] for item in manifest["fixtures"]} == {"42"}
    assert {item["transformation"] for item in manifest["fixtures"]} == {
        "jpeg55",
        "resize50",
        "crop12",
        "screenshot",
        "text_overlay",
    }
    saved = json.loads((tmp_path / "fixtures" / "truth.json").read_text(encoding="utf-8"))
    assert saved == manifest


def test_fixture_set_rejects_missing_cached_reference(tmp_path):
    record = ReferenceRecord("42", "Aura Chair", "https://example/42.jpg", None, None)

    try:
        generate_fixture_set((record,), tmp_path / "fixtures", max_products=1)
    except ValueError as exc:
        assert "not cached" in str(exc)
    else:
        raise AssertionError("missing cached references must be rejected")
