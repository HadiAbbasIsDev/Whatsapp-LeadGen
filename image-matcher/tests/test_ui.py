from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from decor_matcher.index import DescriptorIndex
from decor_matcher.matcher import CatalogMatcher
from decor_matcher.types import MatchResult, ReferenceRecord
from decor_matcher.ui import create_app


def _jpeg_bytes(color=(80, 140, 200)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (80, 60), color).save(output, format="JPEG")
    return output.getvalue()


def _index(reference_path: Path, product_id: str = "42") -> DescriptorIndex:
    payload = reference_path.read_bytes()
    record = ReferenceRecord(
        product_id,
        "Aura Chair",
        "https://example.invalid/aura.jpg",
        reference_path,
        sha256(payload).hexdigest(),
    )
    return DescriptorIndex(np.ones((1, 2), dtype=np.float32), (record,), "test-model")


class MatcherStub:
    def __init__(self, result: MatchResult):
        self.result = result
        self.paths = []

    def match(self, path: Path) -> MatchResult:
        self.paths.append(path)
        assert path.exists()
        return self.result


def _app(tmp_path: Path, matcher, index: DescriptorIndex):
    upload_dir = tmp_path / "uploads"
    app = create_app(tmp_path / "runtime", matcher=matcher, index=index, upload_dir=upload_dir)
    app.config.update(TESTING=True)
    return app, upload_dir


def test_ui_handoff_and_temp_cleanup(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(MatchResult("handoff", None, None, None, "no_candidate_passed", (), True))
    app, upload_dir = _app(tmp_path, matcher, _index(reference))

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(_jpeg_bytes()), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"Human handoff" in response.data
    assert b"no_candidate_passed" in response.data
    assert len(matcher.paths) == 1
    assert not matcher.paths[0].exists()
    assert list(upload_dir.iterdir()) == []


def test_ui_match_shows_input_and_safe_catalog_reference_side_by_side(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes((200, 80, 60)))
    matcher = MatcherStub(
        MatchResult("catalog_match", "42", "Aura Chair", 0.94, "accepted", ("sscd", "lightglue"), True)
    )
    app, upload_dir = _app(tmp_path, matcher, _index(reference))

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(_jpeg_bytes()), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.data.count(b"<img") == 2
    assert b"Uploaded image" in response.data
    assert b"Catalog reference" in response.data
    assert b"Aura Chair" in response.data
    assert b"Product ID:</strong> 42" in response.data
    assert b"94.00%" in response.data
    assert str(reference).encode() not in response.data
    assert list(upload_dir.iterdir()) == []


def test_ui_never_uses_unrecognized_result_id_as_a_path(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(
        MatchResult("catalog_match", "../../secret", "Unsafe", 0.99, "accepted", (), True)
    )
    app, _ = _app(tmp_path, matcher, _index(reference))

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(_jpeg_bytes()), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"Human handoff" in response.data
    assert b"catalog_reference_unavailable" in response.data
    assert response.data.count(b"<img") == 1


def test_ui_rejects_unsupported_payload_through_production_matcher(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    index = _index(reference)

    class NeverEncoder:
        def encode(self, images):
            raise AssertionError("invalid image must fail before encoding")

    class NeverVerifier:
        def verify(self, *args, **kwargs):
            raise AssertionError("invalid image must fail before verification")

    matcher = CatalogMatcher(NeverEncoder(), index, NeverVerifier())
    app, upload_dir = _app(tmp_path, matcher, index)

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(b"not an image"), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert b"Human handoff" in response.data
    assert b"invalid_query_image" in response.data
    assert list(upload_dir.iterdir()) == []


def test_ui_rejects_decodable_but_unsupported_format_before_expensive_matching(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(MatchResult("handoff", None, None, None, "unused", (), True))
    app, upload_dir = _app(tmp_path, matcher, _index(reference))
    tiff = BytesIO()
    Image.new("RGB", (40, 30), "purple").save(tiff, format="TIFF")
    tiff.seek(0)

    response = app.test_client().post(
        "/",
        data={"image": (tiff, "query.tiff")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert b"unsupported_image_format" in response.data
    assert matcher.paths == []
    assert list(upload_dir.iterdir()) == []


def test_ui_rejects_upload_at_twenty_mib_without_saving(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(MatchResult("handoff", None, None, None, "unused", (), True))
    app, upload_dir = _app(tmp_path, matcher, _index(reference))

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(b"x" * (20 * 1024 * 1024)), "huge.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert b"20 MiB" in response.data
    assert matcher.paths == []
    assert list(upload_dir.iterdir()) == []


def test_ui_deletes_temp_upload_when_matcher_raises(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())

    class RaisingMatcher:
        def match(self, path):
            assert path.exists()
            raise RuntimeError("boom")

    app, upload_dir = _app(tmp_path, RaisingMatcher(), _index(reference))

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(_jpeg_bytes()), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    assert b"Human handoff" in response.data
    assert b"matching_failed" in response.data
    assert list(upload_dir.iterdir()) == []


def test_ui_deletes_partial_temp_upload_when_write_fails(tmp_path, monkeypatch):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(MatchResult("handoff", None, None, None, "unused", (), True))
    app, upload_dir = _app(tmp_path, matcher, _index(reference))
    partial_path = upload_dir / "partial.upload"

    class WriteFailingTemporary:
        name = str(partial_path)

        def __enter__(self):
            return self

        def write(self, payload):
            partial_path.write_bytes(payload[:8])
            raise OSError("disk full")

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "decor_matcher.ui.tempfile.NamedTemporaryFile",
        lambda **kwargs: WriteFailingTemporary(),
    )

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(_jpeg_bytes()), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    assert b"Human handoff" in response.data
    assert b"upload_storage_failed" in response.data
    assert not partial_path.exists()
    assert matcher.paths == []


def test_ui_decompression_bomb_is_explicit_safe_handoff(tmp_path, monkeypatch):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(MatchResult("handoff", None, None, None, "unused", (), True))
    app, upload_dir = _app(tmp_path, matcher, _index(reference))
    monkeypatch.setattr(
        "decor_matcher.image_safety.Image.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(Image.DecompressionBombError("unsafe dimensions")),
    )

    response = app.test_client().post(
        "/",
        data={"image": (BytesIO(_jpeg_bytes()), "query.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert b"Human handoff" in response.data
    assert b"decompression_bomb" in response.data
    assert matcher.paths == []
    assert list(upload_dir.iterdir()) == []


def test_ui_get_explains_local_exact_copy_test(tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(_jpeg_bytes())
    matcher = MatcherStub(MatchResult("handoff", None, None, None, "unused", (), True))
    app, _ = _app(tmp_path, matcher, _index(reference))

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Decor Moments Image Matcher" in response.data
    assert b"screenshots" in response.data
    assert b"not connected to WhatsApp" in response.data
