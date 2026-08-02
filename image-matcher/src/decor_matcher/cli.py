import argparse
import json
import time
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Sequence
from urllib.request import Request, urlopen

import numpy as np
from werkzeug.serving import make_server

from .artifacts import SSCD_ARTIFACT, ArtifactSpec, ensure_artifact
from .catalog import (
    cache_references,
    fetch_bytes,
    load_catalog,
    load_gallery_references,
    select_references,
)
from .fixtures import generate_fixture_set
from .index import build_index, load_index, retrieve
from .matcher import CatalogMatcher, create_catalog_matcher
from .sscd import SscdEncoder
from .types import MatchResult, ReferenceRecord
from .ui import create_app
from .verification import DISK_DEPTH_ARTIFACT, LIGHTGLUE_DISK_ARTIFACT, make_query_views


DEFAULT_RUNTIME = Path("image-matcher/runtime")
DEFAULT_FULL_RUNTIME = Path("image-matcher/runtime-full")
DEFAULT_CATALOG = Path("workspace/data/products.json")
DEFAULT_NEGATIVES = 5


def make_matcher(runtime: Path):
    runtime = Path(runtime)
    return create_catalog_matcher(
        runtime / "models" / SSCD_ARTIFACT.filename,
        runtime / "index",
        verifier_model_dir=runtime / "models",
    )


def build_runtime(
    catalog: Path,
    runtime: Path,
    limit: int | None = 100,
    *,
    all_products: bool = False,
    gallery_feed: str | None = None,
) -> dict[str, object]:
    if all_products and limit is not None:
        raise ValueError("--all-products is mutually exclusive with --limit")
    if not all_products and limit != 100:
        raise ValueError("the MVP must index exactly 100 references")
    if all_products and gallery_feed is None:
        raise ValueError("--gallery-feed is required with --all-products")
    if not all_products and gallery_feed is not None:
        raise ValueError("--gallery-feed requires --all-products")
    runtime = Path(runtime)
    items = load_catalog(Path(catalog))
    gallery = None
    if all_products:
        gallery = load_gallery_references(items, gallery_feed)
        selected = list(gallery.references)
    else:
        selected = select_references(items, limit=100)
        if len(selected) != 100:
            raise ValueError(f"catalog produced {len(selected)} valid unique references; exactly 100 required")

    cached, failures = cache_references(selected, runtime / "cache", fetch_bytes)
    if failures or len(cached) != len(selected):
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
    products_by_sha: dict[str, set[str]] = {}
    references_by_sha: dict[str, int] = {}
    for record in cached:
        if record.sha256 is None:
            continue
        products_by_sha.setdefault(record.sha256, set()).add(record.product_id)
        references_by_sha[record.sha256] = references_by_sha.get(record.sha256, 0) + 1
    duplicate_groups = [
        {
            "sha256": reference_sha256,
            "product_ids": sorted(product_ids),
            "reference_count": references_by_sha[reference_sha256],
        }
        for reference_sha256, product_ids in sorted(products_by_sha.items())
        if len(product_ids) > 1
    ]
    result: dict[str, object] = {
        "status": "ok",
        "selected": len(selected),
        "cached": len(cached),
        "indexed": len(index.records),
        "distinct_image_bytes": len(products_by_sha),
        "duplicate_content_group_count": len(duplicate_groups),
        "duplicate_content_groups": duplicate_groups,
        "artifacts": {spec.filename: spec.sha256 for spec in artifacts},
    }
    if gallery is not None:
        result.update(
            {
                "canonical_products": len(items),
                "feed_products": gallery.feed_product_count,
                "gallery_references": len(gallery.references),
                "variants": gallery.variant_count,
                "feed_page_sha256": list(gallery.page_sha256),
            }
        )
    return result


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


def make_ui_server(runtime: Path, port: int):
    """Initialize and bind the local server before announcing readiness."""
    app = create_app(Path(runtime))
    return make_server("127.0.0.1", port, app)


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
        try:
            server = make_ui_server(args.runtime, args.port)
        except (Exception, SystemExit):
            _print_json({"status": "error", "reason": "ui_startup_failed"})
            return 1
        _print_json({"status": "serving", "host": "127.0.0.1", "port": args.port})
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        except (Exception, SystemExit):
            return 1
        finally:
            try:
                server.server_close()
            except (Exception, SystemExit):
                pass
        return 0

    try:
        if args.command == "build":
            limit = None if args.all_products else (100 if args.limit is None else args.limit)
            runtime = args.runtime or (DEFAULT_FULL_RUNTIME if args.all_products else DEFAULT_RUNTIME)
            output = build_runtime(
                args.catalog,
                runtime,
                limit,
                all_products=args.all_products,
                gallery_feed=args.gallery_feed,
            )
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
        if (
            args.command == "build"
            and not getattr(args, "all_products", False)
            and getattr(args, "limit", None) not in (None, 100)
        ):
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

    build = commands.add_parser("build", help="build the 100-reference sample or full gallery index")
    build.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    build.add_argument("--runtime", type=Path)
    build_mode = build.add_mutually_exclusive_group()
    build_mode.add_argument("--limit", type=int)
    build_mode.add_argument("--all-products", action="store_true")
    build.add_argument("--gallery-feed")

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
