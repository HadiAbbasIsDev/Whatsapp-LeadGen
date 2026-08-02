from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from PIL import Image, ImageOps

from .index import DescriptorIndex, retrieve
from .types import Candidate, GeometryMetrics, MatchResult
from .verification import LightGlueVerifier, Thresholds, accept_candidate, make_query_views


MAX_QUERY_BYTES = 20 * 1024 * 1024


class Encoder(Protocol):
    def encode(self, images): ...


class Verifier(Protocol):
    def verify(self, query: Image.Image, reference: Path, reprojection_px: float = 5.0) -> GeometryMetrics: ...


class CatalogMatcher:
    """Fail-closed SSCD retrieval and geometric-verification orchestrator."""

    def __init__(
        self,
        encoder: Encoder,
        index: DescriptorIndex,
        verifier: LightGlueVerifier,
        thresholds: Thresholds | None = None,
        *,
        retriever: Callable[[np.ndarray, DescriptorIndex, int], list[Candidate]] = retrieve,
    ) -> None:
        self.encoder = encoder
        self.index = index
        self.verifier = verifier
        self.thresholds = thresholds or Thresholds()
        self.retriever = retriever

    def match(self, path: Path) -> MatchResult:
        try:
            query = _validated_query_image(path)
        except Exception:
            return _error_result("invalid_query_image")

        try:
            views = make_query_views(query)
            vectors = np.asarray(self.encoder.encode([image for _, image in views]), dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[0] != len(views) or not np.all(np.isfinite(vectors)):
                raise ValueError("encoder returned invalid query descriptors")

            candidates_by_product: dict[str, Candidate] = {}
            view_images = dict(views)
            for (view_name, _), vector in zip(views, vectors, strict=True):
                for candidate in self.retriever(vector, self.index, top_k=5):
                    candidate_for_view = replace(candidate, query_view=view_name)
                    current = candidates_by_product.get(candidate.product_id)
                    if current is None or candidate_for_view.score > current.score:
                        candidates_by_product[candidate.product_id] = candidate_for_view

            ranked = sorted(
                candidates_by_product.values(),
                key=lambda candidate: (-candidate.score, candidate.product_id),
            )[:5]
            evidence: list[str] = ["thresholds=experimental"]
            for position, candidate in enumerate(ranked):
                runner_up = ranked[position + 1] if position + 1 < len(ranked) else None
                metrics = self.verifier.verify(
                    view_images[candidate.query_view],
                    candidate.reference_path,
                    reprojection_px=self.thresholds.reprojection_px,
                )
                acceptance = accept_candidate(candidate, runner_up, metrics, self.thresholds)
                evidence.append(_evidence(candidate, metrics, acceptance.reason))
                if acceptance.accepted:
                    return MatchResult(
                        decision="catalog_match",
                        product_id=candidate.product_id,
                        product_name=candidate.product_name,
                        confidence=candidate.score,
                        reason=acceptance.reason,
                        evidence=tuple(evidence),
                        experimental=True,
                    )

            return MatchResult(
                decision="handoff",
                product_id=None,
                product_name=None,
                confidence=None,
                reason="no_candidate_passed",
                evidence=tuple(evidence),
                experimental=True,
            )
        except Exception:
            return _error_result("matching_failed")


def _validated_query_image(path: Path) -> Image.Image:
    input_path = Path(path)
    size = input_path.stat().st_size
    if size <= 0 or size >= MAX_QUERY_BYTES:
        raise ValueError("query image must be non-empty and under 20 MiB")
    with Image.open(input_path) as source:
        source.load()
        return ImageOps.exif_transpose(source).convert("RGB")


def _evidence(candidate: Candidate, metrics: GeometryMetrics, reason: str) -> str:
    return (
        f"product={candidate.product_id};view={candidate.query_view};sscd={candidate.score:.6f};"
        f"matches={metrics.matches};inliers={metrics.inliers};ratio={metrics.inlier_ratio:.6f};"
        f"query_coverage={metrics.query_coverage:.6f};"
        f"reference_coverage={metrics.reference_coverage:.6f};reason={reason}"
    )


def _error_result(reason: str) -> MatchResult:
    return MatchResult(
        decision="error",
        product_id=None,
        product_name=None,
        confidence=None,
        reason=reason,
        evidence=tuple(),
        experimental=True,
    )
