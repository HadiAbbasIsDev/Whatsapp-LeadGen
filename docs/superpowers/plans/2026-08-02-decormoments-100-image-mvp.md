# Decor Moments 100-Image Matcher MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a standalone CPU-capable CLI and local browser UI that first prove a deterministic 100-primary-image slice, then expand to all canonical Decor Moments products and gallery images, retrieve exact copies with SSCD, verify them with DISK + LightGlue + homography, and return match-or-handoff results.

**Architecture:** A small Python package owns catalog sampling/caching, pinned model artifacts, a NumPy SSCD index, geometric verification, orchestration, and a CLI. Generated images, weights, indexes, and benchmark fixtures live under ignored runtime directories. Pure decision and data functions are tested without model downloads; explicit integration tests prove the real SSCD and LightGlue paths.

**Tech Stack:** Python 3.12, PyTorch 2.9.1 CPU, torchvision 0.24.1, SSCD `sscd_disc_mixup` TorchScript, Kornia 0.8.2 DISK, LightGlue pinned at `eb42fee2d71449efb0aa5c10549752b5d75384d8`, OpenCV 4.12, NumPy 2.2, Pillow 11, ImageHash, pytest.

## Global Constraints

- Work only in the isolated `feature/decormoments-image-matcher` worktree; do not modify the dirty checkout or the live bot flow.
- Source catalog is `workspace/data/products.json` from `DecorMomentsBot` SHA `8f768f9`; it has 256 unique products and 256 primary public image URLs.
- Select exactly 100 unique references by sorting valid products on `sha256(str(product_id))` and taking the first 100.
- MVP uses primary catalog images only. Gallery expansion, OCR, YOLO, WhatsApp/Kapso/OpenClaw integration, real message sends, and customer-image retention are out of scope.
- SSCD is retrieval only. Automatic acceptance requires DISK + LightGlue and a geometrically valid homography; every ambiguity, exception, corrupt input, or missing artifact returns `handoff` or `error`, never a guessed product.
- Pin and checksum all remote model artifacts. Runtime matching must work offline after setup/index build.
- Do not commit downloaded catalog images, model weights, generated fixtures, indexes, virtual environments, or customer images.
- Use test-driven development: each behavior test must be observed failing for the intended reason before production code is written.

---

## File Structure

- `image-matcher/pyproject.toml` — package metadata and non-PyTorch dependency pins.
- `image-matcher/README.md` — Windows/Linux setup and exact build/match/benchmark commands.
- `image-matcher/.gitignore` — isolates all generated runtime artifacts.
- `image-matcher/src/decor_matcher/types.py` — immutable catalog, candidate, verification, and result dataclasses.
- `image-matcher/src/decor_matcher/catalog.py` — catalog validation, deterministic sampling, and reference caching.
- `image-matcher/src/decor_matcher/artifacts.py` — pinned artifact manifest, download, size, and checksum validation.
- `image-matcher/src/decor_matcher/sscd.py` — SSCD preprocessing and descriptor encoder.
- `image-matcher/src/decor_matcher/index.py` — index build/load and cosine top-k retrieval.
- `image-matcher/src/decor_matcher/verification.py` — DISK/LightGlue inference, homography metrics, and acceptance rules.
- `image-matcher/src/decor_matcher/matcher.py` — end-to-end orchestration with fail-closed results.
- `image-matcher/src/decor_matcher/fixtures.py` — deterministic screenshot/crop/compression transformations.
- `image-matcher/src/decor_matcher/cli.py` and `__main__.py` — JSON-only command interface.
- `image-matcher/tests/` — unit and opt-in integration tests.

### Task 1: Package, catalog sampling, and local image cache

**Files:**
- Create: `image-matcher/pyproject.toml`
- Create: `image-matcher/.gitignore`
- Create: `image-matcher/src/decor_matcher/__init__.py`
- Create: `image-matcher/src/decor_matcher/types.py`
- Create: `image-matcher/src/decor_matcher/catalog.py`
- Create: `image-matcher/tests/test_catalog.py`

**Interfaces:**
- Produces: shared immutable dataclasses plus `load_catalog(path)`, `select_references(items, limit=100)`, and `cache_references(records, cache_dir, fetch_bytes)`.
- `CatalogItem(product_id: str, product_name: str, image_url: str)`.
- `ReferenceRecord(product_id: str, product_name: str, image_url: str, cache_path: Path | None, sha256: str | None)`.
- `Candidate(product_id: str, product_name: str, score: float, reference_path: Path, query_view: str = "full")`.
- `GeometryMetrics(matches: int, inliers: int, inlier_ratio: float, query_coverage: float, reference_coverage: float)`.
- `MatchResult(decision: Literal["catalog_match", "handoff", "error"], product_id: str | None, product_name: str | None, confidence: float | None, reason: str | None, evidence: tuple[str, ...], experimental: bool)`.

- [ ] **Step 1: Write failing catalog tests**

```python
def test_select_references_is_deterministic_and_exactly_100(tmp_path):
    products = [product(str(i), f"https://img/{i}.jpg") for i in range(125)]
    selected = select_references(products, limit=100)
    expected = sorted(products, key=lambda p: sha256(p.product_id.encode()).hexdigest())[:100]
    assert [r.product_id for r in selected] == [p.product_id for p in expected]

def test_select_references_skips_missing_and_duplicate_images():
    products = [product("1", ""), product("2", "https://img/a.jpg"), product("2", "https://img/a.jpg")]
    assert [r.product_id for r in select_references(products, limit=100)] == ["2"]

def test_cache_references_validates_decodable_images(tmp_path):
    records = [ReferenceRecord("2", "Chair", "https://img/a.jpg", None, None)]
    cached, failures = cache_references(records, tmp_path, lambda _: make_jpeg_bytes())
    assert len(cached) == 1
    assert cached[0].cache_path.exists()
    assert cached[0].sha256 == sha256(make_jpeg_bytes()).hexdigest()
    assert failures == []
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest image-matcher/tests/test_catalog.py -q`

Expected: collection fails because `decor_matcher.catalog` and its interfaces do not exist.

- [ ] **Step 3: Implement the package and minimal catalog/cache behavior**

Implement `load_catalog()` against the real `{"catalog": [...]}` schema. Validate string IDs, names, and public HTTP(S) image URLs. In `select_references()`, deduplicate by product ID and URL before SHA-256 sorting. In `cache_references()`, use an injected byte fetcher, Pillow `Image.verify()`, deterministic `<product_id>_<urlhash>.ext` names, atomic temporary-file replacement, and per-record failures rather than aborting the batch.

The production fetcher uses `urllib.request.Request` with a browser user agent, a 20-second timeout, and a 20 MiB response cap.

Set `requires-python = ">=3.12,<3.13"`. Pin runtime dependencies to `numpy==2.2.6`, `Pillow==11.3.0`, `opencv-python-headless==4.12.0.88`, `ImageHash==4.3.2`, `kornia==0.8.2`, and `lightglue @ git+https://github.com/cvg/LightGlue.git@eb42fee2d71449efb0aa5c10549752b5d75384d8`. Pin the `dev` extra to `pytest==8.4.1` and register the `models` marker. PyTorch and torchvision are installed by the documented CPU-index command before the editable package install.

- [ ] **Step 4: Run the catalog tests and full unit suite**

Run: `python -m pytest image-matcher/tests/test_catalog.py -q`

Expected: all catalog tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add image-matcher
git commit -m "feat: add deterministic 100-image catalog cache"
```

### Task 2: Pinned SSCD artifact, descriptor index, and retrieval

**Files:**
- Create: `image-matcher/src/decor_matcher/artifacts.py`
- Create: `image-matcher/src/decor_matcher/sscd.py`
- Create: `image-matcher/src/decor_matcher/index.py`
- Create: `image-matcher/tests/test_artifacts.py`
- Create: `image-matcher/tests/test_index.py`
- Create: `image-matcher/tests/test_sscd_integration.py`

**Interfaces:**
- Consumes: cached `ReferenceRecord` objects from Task 1.
- Produces: `ArtifactSpec`, `ensure_artifact(spec, model_dir, fetch_bytes)`, `SscdEncoder.encode(images) -> np.ndarray`, `build_index(records, encoder, index_dir)`, `load_index(index_dir)`, and `retrieve(query_vector, index, top_k=5) -> list[Candidate]`.
- Index files: `sscd_vectors.npy` float32 shape `(100, 512)` and `manifest.json` with ordered reference metadata and content hashes.

- [ ] **Step 1: Write failing artifact and index tests**

```python
def test_ensure_artifact_rejects_wrong_size_or_checksum(tmp_path):
    spec = ArtifactSpec("model.pt", "https://models/model.pt", 3, sha256(b"abc").hexdigest())
    with pytest.raises(ArtifactValidationError):
        ensure_artifact(spec, tmp_path, lambda _: b"abd")

def test_build_and_retrieve_returns_cosine_ranked_products(tmp_path):
    records = cached_records(["a", "b", "c"])
    encoder = FakeEncoder({"a": [1.0, 0.0], "b": [0.8, 0.2], "c": [0.0, 1.0]})
    index = build_index(records, encoder, tmp_path)
    candidates = retrieve(np.array([1.0, 0.0], dtype=np.float32), index, top_k=2)
    assert [c.product_id for c in candidates] == ["a", "b"]
    assert candidates[0].score == pytest.approx(1.0)

def test_index_loader_rejects_manifest_vector_mismatch(tmp_path):
    write_mismatched_index(tmp_path)
    with pytest.raises(IndexValidationError):
        load_index(tmp_path)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest image-matcher/tests/test_artifacts.py image-matcher/tests/test_index.py -q`

Expected: imports fail because artifact, encoder, and index modules are absent.

- [ ] **Step 3: Implement pinned artifact and SSCD index**

Use the official artifact URL `https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt`, exact byte size `98_791_638`, and SHA-256 `9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56`. Store these literal values in `ArtifactSpec` and verify both size and digest before every load.

`SscdEncoder` loads with `torch.jit.load(..., map_location=device).eval()`. Preprocess RGB images with resize-small-edge 288, `ToTensor`, and ImageNet normalization. Batch inference under `torch.inference_mode()`, cast to float32, and L2-normalize descriptors defensively.

`build_index()` requires exactly 100 records for the production CLI, encodes in small batches, writes `.npy` and JSON atomically, and records model checksum plus source image hashes. Test helpers may explicitly opt out of the 100-record requirement.

- [ ] **Step 4: Add and run the real SSCD smoke test**

```python
@pytest.mark.models
def test_real_sscd_loads_and_emits_normalized_512_vector(downloaded_sscd):
    encoder = SscdEncoder(downloaded_sscd, device="cpu")
    vector = encoder.encode([Image.new("RGB", (320, 240), "red")])
    assert vector.shape == (1, 512)
    assert np.linalg.norm(vector[0]) == pytest.approx(1.0, abs=1e-5)
```

Run: `python -m pytest image-matcher/tests/test_sscd_integration.py -m models -q`

Expected: PASS on Python 3.12/PyTorch 2.9.1 CPU. If loading fails, stop and pin the newest compatible PyTorch version demonstrated by the test; do not add compatibility guesses.

- [ ] **Step 5: Run all Task 2 tests and commit**

Run: `python -m pytest image-matcher/tests/test_artifacts.py image-matcher/tests/test_index.py image-matcher/tests/test_sscd_integration.py -q`

```bash
git add image-matcher
git commit -m "feat: build and query pinned SSCD catalog index"
```

### Task 3: DISK + LightGlue geometric verification and fail-closed matcher

**Files:**
- Create: `image-matcher/src/decor_matcher/verification.py`
- Create: `image-matcher/src/decor_matcher/matcher.py`
- Create: `image-matcher/tests/test_verification.py`
- Create: `image-matcher/tests/test_matcher.py`
- Create: `image-matcher/tests/test_lightglue_integration.py`

**Interfaces:**
- Consumes: top-five `Candidate` values and cached reference paths.
- Produces: `Thresholds(sscd_min: float, margin_min: float, min_inliers: int, min_inlier_ratio: float, min_query_coverage: float, min_reference_coverage: float, reprojection_px: float)`, `Acceptance(accepted: bool, reason: str)`, `make_query_views(image) -> list[tuple[str, Image.Image]]`, `LightGlueVerifier.verify(query, reference)`, `accept_candidate(candidate, runner_up, metrics, thresholds) -> Acceptance`, and `CatalogMatcher.match(path) -> MatchResult`.
- Default exploratory thresholds: SSCD score `0.80`, best/runner-up margin `0.03`, RANSAC inliers `20`, inlier ratio `0.30`, per-image inlier coverage `0.10`, reprojection threshold `5.0` pixels at maximum dimension 1024. These are labeled experimental in output metadata.

- [ ] **Step 1: Write failing pure decision and geometry tests**

```python
def test_accepts_only_when_global_and_geometric_gates_pass():
    best = Candidate("a", "Chair A", 0.92, Path("a.jpg"))
    runner_up = Candidate("b", "Chair B", 0.70, Path("b.jpg"))
    metrics = GeometryMetrics(matches=60, inliers=35, inlier_ratio=0.58, query_coverage=0.4, reference_coverage=0.5)
    assert accept_candidate(best, runner_up, metrics, Thresholds()).accepted is True

def test_rejects_similar_candidate_when_homography_is_weak():
    best = Candidate("a", "Chair A", 0.94, Path("a.jpg"))
    metrics = GeometryMetrics(matches=40, inliers=8, inlier_ratio=0.20, query_coverage=0.05, reference_coverage=0.07)
    assert accept_candidate(best, None, metrics, Thresholds()).accepted is False

def test_matcher_converts_verifier_exception_to_error():
    matcher = matcher_with(verifier=RaisingVerifier(RuntimeError("boom")))
    assert matcher.match(query_image()).decision == "error"

def test_query_views_are_named_and_deterministic():
    views = make_query_views(Image.new("RGB", (1000, 800), "white"))
    assert [(name, image.size) for name, image in views] == [
        ("full", (1000, 800)),
        ("center_90", (900, 720)),
        ("center_80", (800, 640)),
    ]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest image-matcher/tests/test_verification.py image-matcher/tests/test_matcher.py -q`

Expected: imports fail because the verifier and orchestrator do not exist.

- [ ] **Step 3: Implement verification and orchestration**

Pin and validate both verifier artifacts before model construction:

- LightGlue DISK URL `https://github.com/cvg/LightGlue/releases/download/v0.1_arxiv/disk_lightglue.pth`, size `47_631_405`, SHA-256 `b5b21d47ea24f2c5e501aec9c91b9716e4c8c3429a4dc1e615c133c4c9378335`, Torch Hub cache filename `disk_lightglue_v0-1_arxiv.pth`.
- DISK depth URL `https://raw.githubusercontent.com/cvlab-epfl/disk/master/depth-save.pth`, size `4_375_832`, SHA-256 `9c2ee4ded238892dfa51569941372601e35e4a74aa6f84ea80053d2ab1c07abe`, Torch Hub cache filename `depth-save.pth`.

Download through `ensure_artifact()`, then atomically seed the verified files into `torch.hub.get_dir()/checkpoints` so official model constructors work offline. Load DISK with `DISK(max_num_keypoints=2048)` and LightGlue with `LightGlue(features="disk")`, both on the selected device in evaluation mode. Resize inputs through the official LightGlue extractor to maximum dimension 1024. Match the top five candidates sequentially.

Convert matched keypoints to NumPy and estimate homography with `cv2.findHomography(..., cv2.USAC_MAGSAC, 5.0)`. Calculate match count, inliers, ratio, and normalized convex-hull coverage in each image. Reject fewer than four correspondences, missing homography/mask, non-finite metrics, and spatially concentrated matches.

`CatalogMatcher.match()` validates an input under 20 MiB, fixes EXIF orientation, creates deterministic full/90%-center/80%-center RGB query views, and encodes all views with SSCD. Retrieve five candidates per view, deduplicate by product ID using each product's maximum score, and retain the originating query view for LightGlue verification. Verify the five best unique products in score order and return the first accepted candidate. Catch validation/model/index/verifier errors and return a structured `error` without leaking stack traces in JSON.

- [ ] **Step 4: Add real LightGlue tests using a catalog image and transformed copy**

```python
@pytest.mark.models
def test_real_lightglue_verifies_resized_recompressed_copy(cached_catalog_image):
    transformed = resize_and_jpeg(cached_catalog_image, scale=0.65, quality=60)
    metrics = LightGlueVerifier(device="cpu").verify(transformed, cached_catalog_image)
    assert metrics.inliers >= 20
    assert metrics.inlier_ratio >= 0.30
```

Run: `python -m pytest image-matcher/tests/test_lightglue_integration.py -m models -q`

Expected: PASS with the pinned LightGlue/DISK weights. Record actual latency in the test report without enforcing a hardware-specific timing assertion.

- [ ] **Step 5: Run Task 3 tests and commit**

Run: `python -m pytest image-matcher/tests/test_verification.py image-matcher/tests/test_matcher.py image-matcher/tests/test_lightglue_integration.py -q`

```bash
git add image-matcher
git commit -m "feat: verify exact catalog copies with LightGlue"
```

### Task 4: CLI, local test UI, generated fixtures, and user-facing documentation

**Files:**
- Create: `image-matcher/src/decor_matcher/fixtures.py`
- Create: `image-matcher/src/decor_matcher/cli.py`
- Create: `image-matcher/src/decor_matcher/ui.py`
- Create: `image-matcher/src/decor_matcher/__main__.py`
- Create: `image-matcher/tests/test_fixtures.py`
- Create: `image-matcher/tests/test_cli.py`
- Create: `image-matcher/tests/test_ui.py`
- Modify: `image-matcher/pyproject.toml`
- Create: `image-matcher/README.md`

**Interfaces:**
- Produces commands: `build`, `match IMAGE`, `ui`, `make-fixtures`, and `benchmark`.
- All commands print one JSON object to stdout and diagnostics to stderr; exit codes are 0 for successful command execution (including a legitimate `handoff`) and 1 for operational `error`.
- `ui` serves a Flask upload form on `127.0.0.1` by default, reuses the production matcher, and renders a side-by-side input/reference result without retaining the upload.

- [ ] **Step 1: Write failing fixture and CLI tests**

```python
def test_fixture_generation_is_deterministic(tmp_path):
    first = generate_variants(source_image(), tmp_path / "a", seed=20260802)
    second = generate_variants(source_image(), tmp_path / "b", seed=20260802)
    assert [sha256(p.read_bytes()).hexdigest() for p in first] == [sha256(p.read_bytes()).hexdigest() for p in second]

def test_match_cli_prints_one_json_object(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("decor_matcher.cli.make_matcher", lambda _: MatchingStub("42", "Chair"))
    exit_code = main(["match", str(tmp_path / "query.jpg")])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["decision"] == "catalog_match"
    assert output["product_id"] == "42"

def test_ui_handoff_and_temp_cleanup(client, matcher_stub, upload_dir):
    response = client.post("/", data={"image": (valid_image(), "query.jpg")})
    assert response.status_code == 200
    assert b"Human handoff" in response.data
    assert list(upload_dir.iterdir()) == []
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest image-matcher/tests/test_fixtures.py image-matcher/tests/test_cli.py image-matcher/tests/test_ui.py -q`

Expected: imports fail because fixtures, CLI, and UI are absent.

- [ ] **Step 3: Implement commands and deterministic transforms**

`build` selects/caches exactly 100 references, ensures artifacts, and creates the index. `match` loads the completed runtime and emits the `MatchResult`. `make-fixtures` creates JPEG-55, 50%-resize, 12%-crop, white screenshot-frame, and text-overlay variants with a JSON truth manifest. `benchmark` evaluates selected indexed positives and unindexed catalog negatives, reports top-1 retrieval, verified acceptance, false accepts, handoffs, and per-query latency percentiles.

`ui` adds Flask as a pinned dependency and serves only on `127.0.0.1` unless a developer explicitly changes the code. It accepts common image formats up to 20 MiB, calls the same matcher as the CLI, deletes each temporary upload in a `finally` path, and presents either the uploaded image beside its matched cached catalog reference or a prominent `Human handoff` result. Test the match view, handoff view, oversize rejection, unsupported payload rejection, and temporary cleanup with Flask's test client.

README commands must be complete PowerShell and Bash examples, including isolated virtual-environment creation, PyTorch CPU installation, editable package installation, `build`, `ui`, `make-fixtures`, `benchmark`, and manual `match` usage. State clearly that the MVP is not connected to WhatsApp and its thresholds are experimental.

- [ ] **Step 4: Run CLI tests and an offline help smoke test**

Run: `python -m pytest image-matcher/tests/test_fixtures.py image-matcher/tests/test_cli.py image-matcher/tests/test_ui.py -q`

Run: `python -m decor_matcher --help`

Expected: tests pass and help lists `build`, `match`, `ui`, `make-fixtures`, and `benchmark`.

- [ ] **Step 5: Commit Task 4**

```bash
git add image-matcher
git commit -m "feat: add matcher CLI and local test UI"
```

### Task 5: Build the real 100-image MVP and publish its benchmark report

**Files:**
- Create: `image-matcher/reports/100-image-mvp.md`
- Modify only if tests expose a defect: files under `image-matcher/src/decor_matcher/` with a failing regression test first.

**Interfaces:**
- Consumes all prior commands.
- Produces a locally runnable ignored index/cache plus a committed evidence report containing exact environment, artifact checksums, selected product IDs, success/failure counts, latency, and known limitations.

- [ ] **Step 1: Create the isolated environment and run the full pre-build suite**

PowerShell:

```powershell
python -m venv image-matcher/.venv
image-matcher/.venv/Scripts/python -m pip install --upgrade pip
image-matcher/.venv/Scripts/python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu
image-matcher/.venv/Scripts/python -m pip install -e "image-matcher[dev]"
image-matcher/.venv/Scripts/python -m pytest image-matcher/tests -q
```

Expected: all unit and model integration tests pass.

- [ ] **Step 2: Build exactly 100 references**

Run:

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher build --catalog workspace/data/products.json --runtime image-matcher/runtime --limit 100
```

Expected JSON contains `"status":"ok"`, `"selected":100`, `"cached":100`, `"indexed":100`, and the pinned artifact checksums. If any reference fails, the command must exit 1 rather than silently building fewer than 100.

- [ ] **Step 3: Generate fixtures and run the benchmark**

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher make-fixtures --runtime image-matcher/runtime --output image-matcher/runtime/fixtures --max-products 1
image-matcher/.venv/Scripts/python -m decor_matcher benchmark --runtime image-matcher/runtime --fixtures image-matcher/runtime/fixtures --catalog workspace/data/products.json --max-negatives 1
```

Expected: the bounded CPU smoke benchmark completes and reports 100 indexed references, all five transformed positive results, one deterministic unindexed negative, false accepts, handoffs, and latency. Do not hide misses or adjust thresholds against the final reported cases. State prominently that this six-query smoke run is functional evidence, not threshold calibration or statistical validation; the larger customer-style benchmark remains required before WhatsApp integration.

- [ ] **Step 4: Write the evidence report and run final verification**

The report includes the exact commands and outputs, model/download checksums, product selection digest, positive recall by transformation, false accepts on unindexed catalog negatives, p50/p95 CPU latency, failures, and a prominent `NOT CONNECTED TO WHATSAPP` statement.

Run:

```powershell
image-matcher/.venv/Scripts/python -m pytest image-matcher/tests -q
git diff --check
git status --short
```

Expected: tests pass, diff check is clean, and only the intended report/source/test/documentation files are tracked or modified; runtime artifacts remain ignored.

- [ ] **Step 5: Commit Task 5**

```bash
git add image-matcher/reports/100-image-mvp.md image-matcher
git commit -m "test: validate 100-image catalog matcher MVP"
```

### Task 6: Expand the local tester to every product and gallery image

**Files:**
- Modify: `image-matcher/src/decor_matcher/types.py`
- Modify: `image-matcher/src/decor_matcher/catalog.py`
- Modify: `image-matcher/src/decor_matcher/index.py`
- Modify: `image-matcher/src/decor_matcher/matcher.py`
- Modify: `image-matcher/src/decor_matcher/ui.py`
- Modify: `image-matcher/src/decor_matcher/cli.py`
- Modify: `image-matcher/tests/test_catalog.py`
- Modify: `image-matcher/tests/test_index.py`
- Modify: `image-matcher/tests/test_matcher.py`
- Modify: `image-matcher/tests/test_ui.py`
- Modify: `image-matcher/tests/test_cli.py`
- Modify: `image-matcher/README.md`
- Create: `image-matcher/reports/full-catalog-local-mvp.md`

**Interfaces:**
- `build --all-products --gallery-feed https://decormoments.com/products.json` pages the bounded public feed, joins by exact product ID, and builds a separate full-catalog runtime.
- The normal `build --limit 100` path and its committed evidence remain reproducible.
- A production index supports a bounded positive reference count with exactly 512 descriptor columns and a manifest count that must match vectors and records.
- An accepted `MatchResult` carries an immutable reference digest so the UI can display the exact matched gallery image through index-only lookup.

- [ ] **Step 1: Write failing full-feed, variable-index, exact-reference, UI, and CLI tests**

Cover bounded pagination, exact-ID joining, missing/duplicate product failures, URL deduplication within one product, multiple gallery references per product, full-count manifest validation, exact accepted-reference propagation, safe UI lookup by product ID plus digest, and mutual exclusion of `--limit` with `--all-products`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest image-matcher/tests/test_catalog.py image-matcher/tests/test_index.py image-matcher/tests/test_matcher.py image-matcher/tests/test_ui.py image-matcher/tests/test_cli.py -q`

Expected: the new interfaces or assertions fail before implementation.

- [ ] **Step 3: Implement full-catalog ingestion and exact gallery-reference results**

Fetch at most 20 Shopify pages of at most 250 products each with bounded JSON responses, exact `https://decormoments.com` host validation, globally routable DNS, and no redirects. Join feed products to every canonical branch product by exact string ID and collect each product's unique validated `images[*].src` URLs. Fail rather than build a partial generation.

Generalize persisted production indexes to a bounded non-zero row count and exactly 512 columns while retaining manifest/vector/cache integrity checks. Retrieve and verify unique products as before. Propagate the accepted reference SHA-256 through `Candidate` and `MatchResult`; resolve UI references only by the `(product_id, reference_sha256)` pair already present in the verified index.

- [ ] **Step 4: Build and verify the real full gallery runtime**

Use separate ignored runtime `image-matcher/runtime-full`. Record live feed payload hashes and observed counts. At the time of planning, the feed contains 256 products, 671 image references, and 1,549 variants; build evidence must report the observed values and fail on incomplete download/indexing instead of assuming those counts forever.

Smoke-test at least one exact copy from a product outside the original 100 and one transformed secondary gallery image. Also run one unrelated synthetic handoff case, clearly labeled functional rather than statistical evidence. Do not alter thresholds from these cases.

- [ ] **Step 5: Document, verify, and commit**

The report includes commands, feed hashes/counts, canonical join counts, cached/indexed/distinct-image counts, duplicate-content ambiguity groups, smoke results, latency, and the same prominent `NOT CONNECTED TO WHATSAPP` warning.

Run:

```powershell
image-matcher/.venv/Scripts/python -m pytest image-matcher/tests -q
image-matcher/.venv/Scripts/python -m compileall -q image-matcher/src image-matcher/tests
git diff --check
git status --short
```

```bash
git add image-matcher docs/superpowers
git commit -m "feat: index the full Decor Moments gallery catalog"
```
