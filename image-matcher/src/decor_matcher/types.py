from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class CatalogItem:
    product_id: str
    product_name: str
    image_url: str


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    product_id: str
    product_name: str
    image_url: str
    cache_path: Path | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class Candidate:
    product_id: str
    product_name: str
    score: float
    reference_path: Path
    query_view: str = "full"


@dataclass(frozen=True, slots=True)
class GeometryMetrics:
    matches: int
    inliers: int
    inlier_ratio: float
    query_coverage: float
    reference_coverage: float


@dataclass(frozen=True, slots=True)
class MatchResult:
    decision: Literal["catalog_match", "handoff", "error"]
    product_id: str | None
    product_name: str | None
    confidence: float | None
    reason: str | None
    evidence: tuple[str, ...]
    experimental: bool
