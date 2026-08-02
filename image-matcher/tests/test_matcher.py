from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher import matcher as matcher_module
from decor_matcher.matcher import CatalogMatcher, create_catalog_matcher
from decor_matcher.types import Candidate, GeometryMetrics
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


def make_candidate(product_id, score, tmp_path):
    return Candidate(product_id, f"Product {product_id}", score, tmp_path / f"{product_id}.jpg")


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
    assert retrieve.calls == [(0.0, 5), (1.0, 5), (2.0, 5)]
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


@pytest.mark.parametrize("dimensions", [(8193, 1), (6500, 6500)])
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
