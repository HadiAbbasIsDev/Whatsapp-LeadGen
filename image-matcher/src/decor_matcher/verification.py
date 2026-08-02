import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

import cv2
import numpy as np
import torch
from lightglue import DISK, LightGlue
from lightglue.utils import rbd
from PIL import Image, ImageOps

from .artifacts import ArtifactSpec, ensure_artifact, validate_artifact
from .image_safety import inspect_safe_image
from .types import Candidate, GeometryMetrics


LIGHTGLUE_DISK_ARTIFACT = ArtifactSpec(
    "disk_lightglue.pth",
    "https://github.com/cvg/LightGlue/releases/download/v0.1_arxiv/disk_lightglue.pth",
    47_631_405,
    "b5b21d47ea24f2c5e501aec9c91b9716e4c8c3429a4dc1e615c133c4c9378335",
)
DISK_DEPTH_ARTIFACT = ArtifactSpec(
    "depth-save.pth",
    "https://raw.githubusercontent.com/cvlab-epfl/disk/master/depth-save.pth",
    4_375_832,
    "9c2ee4ded238892dfa51569941372601e35e4a74aa6f84ea80053d2ab1c07abe",
)
LIGHTGLUE_HUB_FILENAME = "disk_lightglue_v0-1_arxiv.pth"
DISK_HUB_FILENAME = "depth-save.pth"


@dataclass(frozen=True, slots=True)
class Thresholds:
    sscd_min: float = 0.80
    margin_min: float = 0.03
    min_inliers: int = 20
    min_inlier_ratio: float = 0.30
    min_query_coverage: float = 0.10
    min_reference_coverage: float = 0.10
    reprojection_px: float = 5.0


@dataclass(frozen=True, slots=True)
class Acceptance:
    accepted: bool
    reason: str


def make_query_views(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """Return deterministic RGB full and centered crop views."""
    rgb = image.convert("RGB")
    views = [("full", rgb.copy())]
    for name, ratio in (("center_90", 0.90), ("center_80", 0.80)):
        width = max(1, round(rgb.width * ratio))
        height = max(1, round(rgb.height * ratio))
        left = (rgb.width - width) // 2
        top = (rgb.height - height) // 2
        views.append((name, rgb.crop((left, top, left + width, top + height))))
    return views


def accept_candidate(
    candidate: Candidate,
    runner_up: Candidate | None,
    metrics: GeometryMetrics,
    thresholds: Thresholds,
) -> Acceptance:
    """Apply the experimental global and geometric acceptance gates."""
    if not math.isfinite(candidate.score) or candidate.score < thresholds.sscd_min:
        return Acceptance(False, "sscd_below_threshold")
    if runner_up is not None:
        if not math.isfinite(runner_up.score) or candidate.score - runner_up.score < thresholds.margin_min:
            return Acceptance(False, "margin_below_threshold")

    metric_values = (
        metrics.inlier_ratio,
        metrics.query_coverage,
        metrics.reference_coverage,
    )
    if (
        metrics.matches < 0
        or metrics.inliers < 0
        or metrics.inliers > metrics.matches
        or not all(math.isfinite(value) for value in metric_values)
    ):
        return Acceptance(False, "non_finite_geometry")
    if metrics.inliers < thresholds.min_inliers:
        return Acceptance(False, "insufficient_inliers")
    if metrics.inlier_ratio < thresholds.min_inlier_ratio:
        return Acceptance(False, "inlier_ratio_below_threshold")
    if metrics.query_coverage < thresholds.min_query_coverage:
        return Acceptance(False, "query_coverage_below_threshold")
    if metrics.reference_coverage < thresholds.min_reference_coverage:
        return Acceptance(False, "reference_coverage_below_threshold")
    return Acceptance(True, "accepted")


def estimate_geometry(
    query_points: np.ndarray,
    reference_points: np.ndarray,
    query_size: tuple[int, int],
    reference_size: tuple[int, int],
    reprojection_px: float,
) -> GeometryMetrics:
    """Estimate a robust homography and spatial support for matched points."""
    query = np.asarray(query_points, dtype=np.float32)
    reference = np.asarray(reference_points, dtype=np.float32)
    matches = len(query) if query.ndim == 2 else 0
    empty = GeometryMetrics(matches, 0, 0.0, 0.0, 0.0)
    if (
        query.ndim != 2
        or reference.ndim != 2
        or query.shape != reference.shape
        or query.shape[1:] != (2,)
        or matches < 4
        or not np.all(np.isfinite(query))
        or not np.all(np.isfinite(reference))
        or reprojection_px <= 0
    ):
        return empty

    homography, mask = cv2.findHomography(
        query,
        reference,
        cv2.USAC_MAGSAC,
        reprojection_px,
    )
    if homography is None or mask is None or not np.all(np.isfinite(homography)):
        return empty
    inlier_mask = np.asarray(mask).reshape(-1).astype(bool)
    if inlier_mask.shape != (matches,):
        return empty
    inliers = int(np.count_nonzero(inlier_mask))
    ratio = inliers / matches
    query_coverage = _convex_hull_coverage(query[inlier_mask], query_size)
    reference_coverage = _convex_hull_coverage(reference[inlier_mask], reference_size)
    values = (ratio, query_coverage, reference_coverage)
    if not all(math.isfinite(value) for value in values):
        return empty
    return GeometryMetrics(matches, inliers, ratio, query_coverage, reference_coverage)


class LightGlueVerifier:
    """Pinned DISK + LightGlue verifier with robust geometric measurements."""

    def __init__(
        self,
        device: str = "cpu",
        model_dir: Path | None = None,
        fetch_bytes: Callable[[str], bytes] | None = None,
        max_num_keypoints: int = 2048,
    ) -> None:
        self.device = torch.device(device)
        self.model_dir = model_dir or Path(__file__).parents[2] / "runtime" / "models"
        fetcher = fetch_bytes or _fetch_artifact

        disk_path = ensure_artifact(DISK_DEPTH_ARTIFACT, self.model_dir, fetcher)
        lightglue_path = ensure_artifact(LIGHTGLUE_DISK_ARTIFACT, self.model_dir, fetcher)
        checkpoint_dir = Path(torch.hub.get_dir()) / "checkpoints"
        _seed_verified_artifact(disk_path, checkpoint_dir / DISK_HUB_FILENAME, DISK_DEPTH_ARTIFACT)
        _seed_verified_artifact(
            lightglue_path,
            checkpoint_dir / LIGHTGLUE_HUB_FILENAME,
            LIGHTGLUE_DISK_ARTIFACT,
        )

        self.extractor = DISK(max_num_keypoints=max_num_keypoints).eval().to(self.device)
        self.matcher = LightGlue(features="disk").eval().to(self.device)

    def verify(
        self,
        query: Image.Image | Path,
        reference: Image.Image | Path,
        reprojection_px: float = 5.0,
    ) -> GeometryMetrics:
        query_image = _load_rgb(query)
        reference_image = _load_rgb(reference)
        query_tensor = _image_tensor(query_image, self.device)
        reference_tensor = _image_tensor(reference_image, self.device)

        with torch.inference_mode():
            query_features = self.extractor.extract(query_tensor, resize=1024)
            reference_features = self.extractor.extract(reference_tensor, resize=1024)
            matches = self.matcher({"image0": query_features, "image1": reference_features})

        query_features, reference_features, matches = [
            rbd(value) for value in (query_features, reference_features, matches)
        ]
        pairs = matches["matches"].detach().cpu().numpy()
        if pairs.ndim != 2 or pairs.shape[1:] != (2,):
            return GeometryMetrics(0, 0, 0.0, 0.0, 0.0)
        query_points = query_features["keypoints"][pairs[:, 0]].detach().cpu().numpy()
        reference_points = reference_features["keypoints"][pairs[:, 1]].detach().cpu().numpy()
        query_points, query_size = _scale_to_verification_space(query_points, query_image.size)
        reference_points, reference_size = _scale_to_verification_space(
            reference_points,
            reference_image.size,
        )
        return estimate_geometry(
            query_points,
            reference_points,
            query_size,
            reference_size,
            reprojection_px,
        )


def _convex_hull_coverage(points: np.ndarray, image_size: tuple[int, int]) -> float:
    width, height = image_size
    if len(points) < 3 or width <= 0 or height <= 0:
        return 0.0
    hull = cv2.convexHull(np.asarray(points, dtype=np.float32))
    return min(1.0, max(0.0, float(cv2.contourArea(hull)) / float(width * height)))


def _scale_to_verification_space(
    points: np.ndarray,
    image_size: tuple[int, int],
) -> tuple[np.ndarray, tuple[int, int]]:
    width, height = image_size
    if width >= height:
        resized_width = 1024
        resized_height = max(1, int(1024 * height / width))
    else:
        resized_width = max(1, int(1024 * width / height))
        resized_height = 1024
    scaled_size = (resized_width, resized_height)
    scale = np.array([resized_width / width, resized_height / height], dtype=np.float32)
    return np.asarray(points, dtype=np.float32) * scale, scaled_size


def _load_rgb(image: Image.Image | Path) -> Image.Image:
    if isinstance(image, Image.Image):
        return ImageOps.exif_transpose(image).convert("RGB")
    inspect_safe_image(image, verify=True)
    with Image.open(image) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def _image_tensor(image: Image.Image, device: torch.device) -> torch.Tensor:
    array = np.asarray(image, dtype=np.uint8).copy()
    return torch.from_numpy(array).permute(2, 0, 1).to(device=device, dtype=torch.float32) / 255.0


def _fetch_artifact(url: str) -> bytes:
    specs_by_url = {
        DISK_DEPTH_ARTIFACT.url: DISK_DEPTH_ARTIFACT,
        LIGHTGLUE_DISK_ARTIFACT.url: LIGHTGLUE_DISK_ARTIFACT,
    }
    try:
        spec = specs_by_url[url]
    except KeyError as exc:
        raise ValueError("verifier artifact URL is not pinned") from exc
    request = Request(url, headers={"User-Agent": "DecorMatcher/0.1"})
    with urlopen(request, timeout=120) as response:
        payload = response.read(spec.size + 1)
    if len(payload) > spec.size:
        raise ValueError("verifier artifact is larger than its pinned size")
    return payload


def _seed_verified_artifact(source: Path, destination: Path, spec: ArtifactSpec) -> None:
    """Atomically seed Torch Hub only after the copied bytes validate exactly."""
    validate_artifact(source, spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            validate_artifact(destination, spec)
            return
        except ValueError:
            pass

    with source.open("rb") as source_file, tempfile.NamedTemporaryFile(
        dir=destination.parent,
        delete=False,
    ) as temporary:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            temporary.write(chunk)
        temporary_path = Path(temporary.name)
    try:
        validate_artifact(temporary_path, spec)
        os.replace(temporary_path, destination)
        validate_artifact(destination, spec)
    finally:
        temporary_path.unlink(missing_ok=True)
