from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher import matcher as matcher_module
from decor_matcher.matcher import CatalogMatcher, create_catalog_matcher
from decor_matcher.types import Candidate, GeometryMetrics, ReferenceRecord
from decor_matcher.index import DescriptorIndex


STRONG = GeometryMetrics(60, 35, 0.58, 0.40, 0.50)
WEAK = GeometryMetrics(40, 8, 0.20, 0.05, 0.07)


class RecordingEncoder:
    model_sha256 = "unused"

    def __init__(self):
        self.sizes = []

    def encode(self, images):
        self.sizes = [image.size for image in images]
        return np.arange(len(images), dtype=np.float32)[:, None]


class RecordingVerifier:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def verify(self, query, reference, reprojection_px=5.0):
        self.calls.append((query.size, reference))
        outcome = self.outcomes[Path(reference).stem]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def image_file(tmp_path, *, size=(100, 80), orientation=None):
    path = tmp_path / "query.jpg"
    image = Image.new("RGB", size, "white")
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    image.save(path, exif=exif)
    return path


def empty_index(tmp_path):
    return DescriptorIndex(
        np.empty((0, 1), dtype=np.float32),
        tuple(),
        "unused",
    )


def scripted_retriever(responses):
    calls = []

    def retrieve(query_vector, index, top_k=5):
        calls.append((float(query_vector[0]), top_k))
        return responses[len(calls) - 1]

    retrieve.calls = calls
    return retrieve


def make_candidate(product_id, score, tmp_path, reference_sha256=None):
    return Candidate(
        product_id,
        f"Product {product_id}",
        score,
        tmp_path / f"{product_id}.jpg",
        reference_sha256=reference_sha256,
    )


def test_matcher_deduplicates_by_max_score_and_verifies_originating_query_view(tmp_path):
    encoder = RecordingEncoder()
    verifier = RecordingVerifier({"a": STRONG})
    retrieve = scripted_retriever(
        [
            [make_candidate("a", 0.85, tmp_path), make_candidate("b", 0.80, tmp_path)],
            [make_candidate("a", 0.93, tmp_path), make_candidate("c", 0.70, tmp_path)],
            [make_candidate("d", 0.65, tmp_path)],
        ]
    )
    matcher = CatalogMatcher(encoder, empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path, size=(100, 80)))

    assert result.decision == "catalog_match"
    assert result.product_id == "a"
    assert result.confidence == 0.93
    assert encoder.sizes == [(100, 80), (90, 72), (80, 64)]
    assert retrieve.calls == [(0.0, 6), (1.0, 6), (2.0, 6)]
    assert verifier.calls == [((90, 72), tmp_path / "a.jpg")]
    assert result.experimental is True


def test_matcher_tries_next_score_ordered_candidate_after_weak_geometry(tmp_path):
    verifier = RecordingVerifier({"a": WEAK, "b": STRONG})
    retrieve = scripted_retriever(
        [
            [
                make_candidate("a", 0.95, tmp_path),
                make_candidate("b", 0.90, tmp_path),
                make_candidate("c", 0.70, tmp_path),
            ],
            [],
            [],
        ]
    )
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "catalog_match"
    assert result.product_id == "b"
    assert [reference.stem for _, reference in verifier.calls] == ["a", "b"]


def test_matcher_rejects_globally_ambiguous_top_pair_without_verification(tmp_path):
    verifier = RecordingVerifier({"a": STRONG, "b": STRONG})
    retrieve = scripted_retriever(
        [
            [
                make_candidate("a", 0.92, tmp_path),
                make_candidate("b", 0.90, tmp_path),
                make_candidate("c", 0.70, tmp_path),
            ],
            [],
            [],
        ]
    )
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "handoff"
    assert result.reason == "ambiguous_top_candidates"
    assert verifier.calls == []


def test_matcher_stops_fallback_when_next_candidate_has_ambiguous_adjacent_margin(tmp_path):
    verifier = RecordingVerifier({"a": WEAK, "b": STRONG, "c": STRONG})
    retrieve = scripted_retriever(
        [
            [
                make_candidate("a", 0.95, tmp_path),
                make_candidate("b", 0.90, tmp_path),
                make_candidate("c", 0.89, tmp_path),
                make_candidate("d", 0.70, tmp_path),
            ],
            [],
            [],
        ]
    )
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "handoff"
    assert result.reason == "ambiguous_adjacent_candidates"
    assert [reference.stem for _, reference in verifier.calls] == ["a", "b"]


def test_matcher_uses_rank_six_as_rank_five_runner_up(tmp_path):
    verifier = RecordingVerifier({"a": WEAK, "b": WEAK, "c": WEAK, "d": WEAK, "e": STRONG})
    retrieve = scripted_retriever(
        [
            [
                make_candidate("a", 0.99, tmp_path),
                make_candidate("b", 0.95, tmp_path),
                make_candidate("c", 0.91, tmp_path),
                make_candidate("d", 0.87, tmp_path),
                make_candidate("e", 0.83, tmp_path),
                make_candidate("f", 0.83, tmp_path),
            ],
            [],
            [],
        ]
    )
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "handoff"
    assert result.reason == "ambiguous_adjacent_candidates"
    assert [reference.stem for _, reference in verifier.calls] == ["a", "b", "c", "d", "e"]
    assert retrieve.calls == [(0.0, 6), (1.0, 6), (2.0, 6)]


def test_matcher_never_accepts_reference_checksum_shared_by_multiple_products(tmp_path):
    shared_sha = "1" * 64
    records = (
        ReferenceRecord(
            "a", "Product a", "https://cdn.shopify.com/a.jpg", tmp_path / "a.jpg", shared_sha
        ),
        ReferenceRecord(
            "b", "Product b", "https://cdn.shopify.com/b.jpg", tmp_path / "b.jpg", shared_sha
        ),
    )
    index = DescriptorIndex(np.ones((2, 1), dtype=np.float32), records, "unused")
    verifier = RecordingVerifier({"a": STRONG})
    retrieve = scripted_retriever(
        [[make_candidate("a", 0.95, tmp_path, reference_sha256=shared_sha)], [], []]
    )
    matcher = CatalogMatcher(RecordingEncoder(), index, verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "handoff"
    assert result.reason == "ambiguous_reference_image"
    assert verifier.calls == []


def test_matcher_propagates_exact_accepted_reference_digest_for_multi_image_product(tmp_path):
    unique_sha = "2" * 64
    shared_sha = "1" * 64
    records = (
        ReferenceRecord("a", "Product a", "https://cdn.shopify.com/a-unique.jpg", tmp_path / "a.jpg", unique_sha),
        ReferenceRecord("a", "Product a", "https://cdn.shopify.com/a-shared.jpg", tmp_path / "a2.jpg", shared_sha),
        ReferenceRecord("b", "Product b", "https://cdn.shopify.com/b-shared.jpg", tmp_path / "b.jpg", shared_sha),
    )
    index = DescriptorIndex(np.ones((3, 1), dtype=np.float32), records, "unused")
    verifier = RecordingVerifier({"a": STRONG})
    retrieve = scripted_retriever(
        [[make_candidate("a", 0.95, tmp_path, reference_sha256=unique_sha)], [], []]
    )

    result = CatalogMatcher(RecordingEncoder(), index, verifier, retriever=retrieve).match(image_file(tmp_path))

    assert result.decision == "catalog_match"
    assert result.product_id == "a"
    assert result.reference_sha256 == unique_sha


def test_matcher_converts_verifier_exception_to_sanitized_error(tmp_path):
    verifier = RecordingVerifier({"a": RuntimeError("secret boom details")})
    retrieve = scripted_retriever([[make_candidate("a", 0.95, tmp_path)], [], []])
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "error"
    assert result.reason == "matching_failed"
    assert "boom" not in repr(result)


def test_matcher_returns_handoff_when_no_candidate_passes(tmp_path):
    verifier = RecordingVerifier({"a": WEAK})
    retrieve = scripted_retriever([[make_candidate("a", 0.95, tmp_path)], [], []])
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), verifier, retriever=retrieve)

    result = matcher.match(image_file(tmp_path))

    assert result.decision == "handoff"
    assert result.product_id is None
    assert result.reason == "no_candidate_passed"
    assert result.evidence


def test_matcher_fixes_exif_orientation_before_creating_views(tmp_path):
    encoder = RecordingEncoder()
    matcher = CatalogMatcher(encoder, empty_index(tmp_path), RecordingVerifier({}), retriever=scripted_retriever([[], [], []]))

    result = matcher.match(image_file(tmp_path, size=(40, 20), orientation=6))

    assert result.decision == "handoff"
    assert encoder.sizes == [(20, 40), (18, 36), (16, 32)]


def test_matcher_rejects_input_over_twenty_mib_before_decoding(tmp_path):
    path = tmp_path / "oversized.jpg"
    with path.open("wb") as destination:
        destination.truncate(20 * 1024 * 1024 + 1)
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), RecordingVerifier({}), retriever=scripted_retriever([]))

    result = matcher.match(path)

    assert result.decision == "error"
    assert result.reason == "invalid_query_image"


@pytest.mark.parametrize("dimensions", [(8193, 1), (6500, 6500), (8192, 1)])
def test_matcher_rejects_extreme_decoded_dimensions_before_pixel_load(
    tmp_path,
    monkeypatch,
    dimensions,
):
    path = tmp_path / f"extreme-{dimensions[0]}x{dimensions[1]}.png"
    Image.new("1", dimensions).save(path)
    assert path.stat().st_size < 20 * 1024 * 1024
    load_attempted = False

    def reject_load(*args, **kwargs):
        nonlocal load_attempted
        load_attempted = True
        raise ValueError("pixel data must not be decoded")

    monkeypatch.setattr(Image.Image, "load", reject_load)
    encoder = RecordingEncoder()
    matcher = CatalogMatcher(encoder, empty_index(tmp_path), RecordingVerifier({}), retriever=scripted_retriever([]))

    result = matcher.match(path)

    assert result.decision == "error"
    assert result.reason == "invalid_query_image"
    assert load_attempted is False
    assert encoder.sizes == []


def test_matcher_rejects_decodable_unsupported_format_before_pixel_load(tmp_path, monkeypatch):
    path = tmp_path / "query.tiff"
    Image.new("RGB", (40, 30), "white").save(path, format="TIFF")
    load_attempted = False

    def reject_load(*args, **kwargs):
        nonlocal load_attempted
        load_attempted = True
        raise AssertionError("unsupported input must not be decoded")

    monkeypatch.setattr(Image.Image, "load", reject_load)
    encoder = RecordingEncoder()
    matcher = CatalogMatcher(
        encoder,
        empty_index(tmp_path),
        RecordingVerifier({}),
        retriever=scripted_retriever([]),
    )

    result = matcher.match(path)

    assert result.decision == "error"
    assert result.reason == "invalid_query_image"
    assert load_attempted is False
    assert encoder.sizes == []


def test_matcher_rejects_undecodable_input_without_leaking_decoder_details(tmp_path):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not an image")
    matcher = CatalogMatcher(RecordingEncoder(), empty_index(tmp_path), RecordingVerifier({}), retriever=scripted_retriever([]))

    result = matcher.match(path)

    assert result.decision == "error"
    assert result.reason == "invalid_query_image"
    assert "cannot identify" not in repr(result)


@pytest.mark.parametrize("failing_dependency", ["model", "index"])
def test_factory_converts_model_or_index_initialization_failure_to_error_matcher(
    tmp_path,
    monkeypatch,
    failing_dependency,
):
    if failing_dependency == "model":
        monkeypatch.setattr(
            matcher_module,
            "SscdEncoder",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret model failure")),
        )
    else:
        monkeypatch.setattr(matcher_module, "SscdEncoder", lambda *args, **kwargs: object())
        monkeypatch.setattr(
            matcher_module,
            "load_index",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret index failure")),
        )

    matcher = create_catalog_matcher(tmp_path / "sscd.pt", tmp_path / "index")
    result = matcher.match(tmp_path / "query.jpg")

    assert result.decision == "error"
    assert result.reason == "initialization_failed"
    assert "secret" not in repr(result)


def test_factory_converts_verifier_initialization_failure_to_error_matcher(tmp_path, monkeypatch):
    monkeypatch.setattr(matcher_module, "SscdEncoder", lambda *args, **kwargs: object())
    monkeypatch.setattr(matcher_module, "load_index", lambda *args, **kwargs: empty_index(tmp_path))
    monkeypatch.setattr(
        matcher_module,
        "LightGlueVerifier",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret verifier failure")),
    )

    matcher = create_catalog_matcher(tmp_path / "sscd.pt", tmp_path / "index")
    result = matcher.match(tmp_path / "query.jpg")

    assert result.decision == "error"
    assert result.reason == "initialization_failed"
    assert "secret" not in repr(result)
