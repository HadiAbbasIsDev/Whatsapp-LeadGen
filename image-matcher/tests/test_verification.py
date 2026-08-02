from hashlib import sha256
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.artifacts import ArtifactSpec, ArtifactValidationError
from decor_matcher.types import Candidate, GeometryMetrics
from decor_matcher.verification import (
    Acceptance,
    Thresholds,
    _seed_verified_artifact,
    _fetch_artifact,
    _scale_to_verification_space,
    accept_candidate,
    estimate_geometry,
    make_query_views,
)


def candidate(product_id="a", score=0.92):
    return Candidate(product_id, f"Chair {product_id.upper()}", score, Path(f"{product_id}.jpg"))


def strong_geometry():
    return GeometryMetrics(
        matches=60,
        inliers=35,
        inlier_ratio=0.58,
        query_coverage=0.4,
        reference_coverage=0.5,
    )


def test_accepts_only_when_global_and_geometric_gates_pass():
    result = accept_candidate(candidate(score=0.92), candidate("b", 0.70), strong_geometry(), Thresholds())

    assert result == Acceptance(True, "accepted")


@pytest.mark.parametrize(
    ("best", "runner_up", "metrics", "reason"),
    [
        (candidate(score=0.79), None, strong_geometry(), "sscd_below_threshold"),
        (candidate(score=0.92), candidate("b", 0.90), strong_geometry(), "margin_below_threshold"),
        (
            candidate(score=0.94),
            None,
            GeometryMetrics(40, 8, 0.20, 0.05, 0.07),
            "insufficient_inliers",
        ),
        (
            candidate(score=0.94),
            None,
            GeometryMetrics(60, 35, float("nan"), 0.4, 0.5),
            "non_finite_geometry",
        ),
    ],
)
def test_rejects_candidates_when_an_acceptance_gate_fails(best, runner_up, metrics, reason):
    assert accept_candidate(best, runner_up, metrics, Thresholds()) == Acceptance(False, reason)


def test_query_views_are_named_and_deterministic():
    views = make_query_views(Image.new("RGB", (1000, 800), "white"))

    assert [(name, image.size) for name, image in views] == [
        ("full", (1000, 800)),
        ("center_90", (900, 720)),
        ("center_80", (800, 640)),
    ]
    assert all(image.mode == "RGB" for _, image in views)


def test_estimate_geometry_reports_inliers_and_normalized_hull_coverage():
    query_points = np.array([[10, 10], [90, 10], [90, 90], [10, 90]], dtype=np.float32)
    reference_points = query_points + np.array([5, 7], dtype=np.float32)

    metrics = estimate_geometry(query_points, reference_points, (100, 100), (100, 100), 5.0)

    assert metrics.matches == 4
    assert metrics.inliers == 4
    assert metrics.inlier_ratio == pytest.approx(1.0)
    assert metrics.query_coverage == pytest.approx(0.64)
    assert metrics.reference_coverage == pytest.approx(0.64)


def test_small_image_coordinates_scale_to_actual_extractor_dimensions():
    points = np.array([[0.0, 0.0], [101.0, 50.0]], dtype=np.float32)

    scaled_points, scaled_size = _scale_to_verification_space(points, (101, 50))

    assert scaled_size == (1024, 506)
    np.testing.assert_allclose(
        scaled_points,
        np.array([[0.0, 0.0], [1024.0, 506.0]], dtype=np.float32),
        rtol=0.0,
        atol=1e-5,
    )


@pytest.mark.parametrize(
    ("query_points", "reference_points"),
    [
        (np.zeros((3, 2), dtype=np.float32), np.zeros((3, 2), dtype=np.float32)),
        (
            np.array([[0, 0], [1, 0], [1, 1], [np.nan, 1]], dtype=np.float32),
            np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32),
        ),
    ],
)
def test_estimate_geometry_fails_closed_for_unusable_correspondences(query_points, reference_points):
    metrics = estimate_geometry(query_points, reference_points, (100, 100), (100, 100), 5.0)

    assert metrics.inliers == 0
    assert metrics.inlier_ratio == 0.0
    assert metrics.query_coverage == 0.0
    assert metrics.reference_coverage == 0.0


def test_seed_verified_artifact_refuses_corrupted_source(tmp_path):
    good = b"verified-model"
    source = tmp_path / "source.pth"
    source.write_bytes(b"corrupt-model!")
    spec = ArtifactSpec("source.pth", "https://models/source.pth", len(good), sha256(good).hexdigest())

    with pytest.raises(ArtifactValidationError):
        _seed_verified_artifact(source, tmp_path / "hub" / "model.pth", spec)

    assert not (tmp_path / "hub" / "model.pth").exists()


def test_seed_verified_artifact_atomically_replaces_unverified_destination(tmp_path):
    payload = b"verified-model"
    source = tmp_path / "source.pth"
    destination = tmp_path / "hub" / "model.pth"
    source.write_bytes(payload)
    destination.parent.mkdir()
    destination.write_bytes(b"bad")
    spec = ArtifactSpec("source.pth", "https://models/source.pth", len(payload), sha256(payload).hexdigest())

    _seed_verified_artifact(source, destination, spec)

    assert destination.read_bytes() == payload


def test_verifier_download_reads_size_plus_one_and_rejects_overrun(monkeypatch):
    reads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            reads.append(limit)
            return b"x" * limit

    monkeypatch.setattr("decor_matcher.verification.urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(ValueError, match="larger than its pinned size"):
        _fetch_artifact("https://raw.githubusercontent.com/cvlab-epfl/disk/master/depth-save.pth")

    assert reads == [4_375_832 + 1]
