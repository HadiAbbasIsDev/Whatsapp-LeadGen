import argparse
import json
import time
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Sequence
from urllib.request import Request, urlopen

import numpy as np

from .artifacts import SSCD_ARTIFACT, ArtifactSpec, ensure_artifact
from .catalog import cache_references, fetch_bytes, load_catalog, select_references
from .fixtures import generate_fixture_set
from .index import build_index, load_index, retrieve
from .matcher import CatalogMatcher, create_catalog_matcher
from .sscd import SscdEncoder
from .types import MatchResult, ReferenceRecord
from .ui import run_local_ui
from .verification import DISK_DEPTH_ARTIFACT, LIGHTGLUE_DISK_ARTIFACT, make_query_views


DEFAULT_RUNTIME = Path("image-matcher/runtime")
DEFAULT_CATALOG = Path("workspace/data/products.json")
DEFAULT_NEGATIVES = 5


def make_matcher(runtime: Path):
    runtime = Path(runtime)
    return create_catalog_matcher(
        runtime / "models" / SSCD_ARTIFACT.filename,
        runtime / "index",
        verifier_model_dir=runtime / "models",
    )


def build_runtime(catalog: Path, runtime: Path, limit: int = 100) -> dict[str, object]:
    if limit != 100:
        raise ValueError("the MVP must index exactly 100 references")
    runtime = Path(runtime)
    items = load_catalog(Path(catalog))
    selected = select_references(items, limit=limit)
    if len(selected) != 100:
        raise ValueError(f"catalog produced {len(selected)} valid unique references; exactly 100 required")

    cached, failures = cache_references(selected, runtime / "cache", fetch_bytes)
    if failures or len(cached) != 100:
        raise RuntimeError(
            f"reference caching failed: cached={len(cached)}, failures={len(failures)}"
        )

    artifacts = [SSCD_ARTIFACT, DISK_DEPTH_ARTIFACT, LIGHTGLUE_DISK_ARTIFACT]
    artifact_paths = {
        spec.filename: ensure_artifact(spec, runtime / "models", _artifact_fetcher(spec))
        for spec in artifacts
    }
    encoder = SscdEncoder(artifact_paths[SSCD_ARTIFACT.filename], device="cpu")
    index = build_index(cached, encoder, runtime / "index")
    return {
        "status": "ok",
        "selected": len(selected),
        "cached": len(cached),
        "indexed": len(index.records),
        "artifacts": {spec.filename: spec.sha256 for spec in artifacts},
    }


def generate_fixtures_runtime(
    runtime: Path,
    output: Path,
    max_products: int = 20,
    seed: int = 20260802,
) -> dict[str, object]:
    index = load_index(Path(runtime) / "index")
    manifest = generate_fixture_set(index.records, Path(output), max_products=max_products, seed=seed)
    return {
        "status": "ok",
        "products": manifest["products"],
        "fixtures": len(manifest["fixtures"]),
        "manifest": str(Path(output) / "truth.json"),
    }


def benchmark_runtime(
    runtime: Path,
    fixtures: Path,
    catalog: Path,
    max_negatives: int = DEFAULT_NEGATIVES,
) -> dict[str, object]:
    if max_negatives < 0:
        raise ValueError("max_negatives must be non-negative")
    runtime = Path(runtime)
    fixtures = Path(fixtures)
    index = load_index(runtime / "index")
    matcher = make_matcher(runtime)
    truth = json.loads((fixtures / "truth.json").read_text(encoding="utf-8"))
    fixture_items = truth.get("fixtures")
    if not isinstance(fixture_items, list):
        raise ValueError("fixture manifest must contain a fixtures list")

    positives: list[dict[str, object]] = []
    latencies_ms: list[float] = []
    for item in fixture_items:
        if not isinstance(item, dict):
            raise ValueError("fixture manifest entry must be an object")
        expected_id = item.get("product_id")
        relative_path = item.get("path")
        transformation = item.get("transformation")
        if not all(isinstance(value, str) and value for value in (expected_id, relative_path, transformation)):
            raise ValueError("fixture manifest entry is incomplete")
        query_path = _within(fixtures, relative_path)
        top1_id = _retrieve_top1(matcher, query_path)
        started = time.perf_counter()
        result = matcher.match(query_path)
        latencies_ms.append((time.perf_counter() - started) * 1000)
        positives.append(
            {
                "product_id": expected_id,
                "transformation": transformation,
                "top1_correct": top1_id == expected_id,
                "accepted_correct": result.decision == "catalog_match" and result.product_id == expected_id,
                "decision": result.decision,
                "returned_product_id": result.product_id,
                "reason": result.reason,
            }
        )

    indexed_ids = {record.product_id for record in index.records}
    unindexed = [item for item in load_catalog(Path(catalog)) if item.product_id not in indexed_ids]
    unindexed.sort(key=lambda item: sha256(item.product_id.encode()).hexdigest())
    selected_negatives = unindexed[:max_negatives]
    negative_records = [
        ReferenceRecord(item.product_id, item.product_name, item.image_url, None, None)
        for item in selected_negatives
    ]
    cached_negatives, negative_failures = cache_references(
        negative_records,
        runtime / "benchmark-negatives",
        fetch_bytes,
    )
    negatives: list[dict[str, object]] = []
    for record in cached_negatives:
        if record.cache_path is None:
            continue
        started = time.perf_counter()
        result = matcher.match(record.cache_path)
        latencies_ms.append((time.perf_counter() - started) * 1000)
        negatives.append(
            {
                "product_id": record.product_id,
                "decision": result.decision,
                "returned_product_id": result.product_id,
                "reason": result.reason,
            }
        )

    latency = _latency_percentiles(latencies_ms)
    return {
        "status": "ok",
        "indexed_references": len(index.records),
        "positive_queries": len(positives),
        "positive_top1_correct": sum(bool(item["top1_correct"]) for item in positives),
        "positive_verified_correct": sum(bool(item["accepted_correct"]) for item in positives),
        "positive_handoffs": sum(item["decision"] == "handoff" for item in positives),
        "positive_errors": sum(item["decision"] == "error" for item in positives),
        "negative_requested": max_negatives,
        "negative_queries": len(negatives),
        "negative_download_failures": negative_failures,
        "false_accepts": sum(item["decision"] == "catalog_match" for item in negatives),
        "negative_handoffs": sum(item["decision"] == "handoff" for item in negatives),
        "negative_errors": sum(item["decision"] == "error" for item in negatives),
        "latency_ms": latency,
        "positives": positives,
        "negatives": negatives,
    }


def serve_ui(runtime: Path, port: int) -> None:
    run_local_ui(runtime, port)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.command == "match":
        try:
            result = make_matcher(args.runtime).match(args.image)
        except Exception:
            result = MatchResult("error", None, None, None, "matching_failed", (), True)
        _print_json(asdict(result))
        return 1 if result.decision == "error" else 0

    if args.command == "ui":
        if not 1 <= args.port <= 65535:
            _print_json({"status": "error", "reason": "invalid_port"})
            return 1
        _print_json({"status": "serving", "host": "127.0.0.1", "port": args.port})
        try:
            serve_ui(args.runtime, args.port)
        except Exception:
            return 1
        return 0

    try:
        if args.command == "build":
            if args.limit != 100:
                raise ValueError("the MVP must index exactly 100 references")
            output = build_runtime(args.catalog, args.runtime, args.limit)
        elif args.command == "make-fixtures":
            output = generate_fixtures_runtime(
                args.runtime,
                args.output,
                args.max_products,
                args.seed,
            )
        elif args.command == "benchmark":
            if args.max_negatives < 0:
                raise ValueError("max_negatives must be non-negative")
            output = benchmark_runtime(
                args.runtime,
                args.fixtures,
                args.catalog,
                args.max_negatives,
            )
        else:
            _print_json({"status": "error", "reason": "command_required"})
            return 1
    except Exception:
        reason = {
            "build": "build_failed",
            "make-fixtures": "make_fixtures_failed",
            "benchmark": "benchmark_failed",
        }.get(args.command, "operation_failed")
        if args.command == "build" and getattr(args, "limit", 100) != 100:
            reason = "the MVP must index exactly 100 references"
        elif args.command == "benchmark" and getattr(args, "max_negatives", 0) < 0:
            reason = "max_negatives must be non-negative"
        _print_json({"status": "error", "reason": reason})
        return 1

    _print_json(output)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decor_matcher", description="Decor Moments exact image matcher MVP")
    commands = parser.add_subparsers(dest="command")

    build = commands.add_parser("build", help="cache exactly 100 references and build the index")
    build.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    build.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    build.add_argument("--limit", type=int, default=100)

    match = commands.add_parser("match", help="match one local image")
    match.add_argument("image", type=Path)
    match.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)

    ui = commands.add_parser("ui", help="start the loopback-only browser UI")
    ui.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    ui.add_argument("--port", type=int, default=7860)

    fixture = commands.add_parser("make-fixtures", help="generate deterministic transformed positives")
    fixture.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    fixture.add_argument("--output", type=Path, default=DEFAULT_RUNTIME / "fixtures")
    fixture.add_argument("--max-products", type=int, default=20)
    fixture.add_argument("--seed", type=int, default=20260802)

    benchmark = commands.add_parser("benchmark", help="measure positives and bounded unindexed negatives")
    benchmark.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    benchmark.add_argument("--fixtures", type=Path, default=DEFAULT_RUNTIME / "fixtures")
    benchmark.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    benchmark.add_argument("--max-negatives", type=int, default=DEFAULT_NEGATIVES)
    return parser


def _artifact_fetcher(spec: ArtifactSpec):
    def fetch(url: str) -> bytes:
        if url != spec.url:
            raise ValueError("artifact URL does not match pinned specification")
        request = Request(url, headers={"User-Agent": "DecorMatcher/0.1"})
        with urlopen(request, timeout=120) as response:
            payload = response.read(spec.size + 1)
        if len(payload) > spec.size:
            raise ValueError("artifact is larger than its pinned size")
        return payload

    return fetch


def _retrieve_top1(matcher, path: Path) -> str | None:
    if not isinstance(matcher, CatalogMatcher):
        return None
    from .matcher import _validated_query_image

    try:
        views = make_query_views(_validated_query_image(path))
        vectors = np.asarray(matcher.encoder.encode([image for _, image in views]), dtype=np.float32)
        candidates = []
        for vector in vectors:
            candidates.extend(retrieve(vector, matcher.index, top_k=1))
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.score).product_id
    except Exception:
        return None


def _within(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("fixture path escapes fixture directory")
    if not candidate.is_file():
        raise ValueError("fixture image is missing")
    return candidate


def _latency_percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None}
    array = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError("latency samples must be finite")
    return {
        "p50": round(float(np.percentile(array, 50)), 3),
        "p95": round(float(np.percentile(array, 95)), 3),
    }


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
