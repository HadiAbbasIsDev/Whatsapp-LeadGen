# Decor Moments local image matcher MVP

This is a standalone, local exact-copy test. It can retain the deterministic
100-primary-image feasibility index or build a separate index for every
canonical Decor Moments product and every current gallery reference from the
bounded public Shopify feed. It retrieves candidates with SSCD and verifies
them with DISK + LightGlue geometry. It is intended for catalog copies,
resized/recompressed images, crops, and screenshots with light UI clutter.

**It is not connected to WhatsApp.** A result from this MVP does not change the
current bot handoff behavior. The matching thresholds are experimental; a
non-match, ambiguity, invalid input, or runtime failure is shown as a human
handoff rather than a guessed product.

## Windows PowerShell setup

Run these commands from the repository root:

```powershell
python -m venv image-matcher/.venv
image-matcher/.venv/Scripts/python -m pip install --upgrade pip
image-matcher/.venv/Scripts/python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu
image-matcher/.venv/Scripts/python -m pip install -e "image-matcher[dev]"
image-matcher/.venv/Scripts/python -m decor_matcher build --catalog workspace/data/products.json --runtime image-matcher/runtime --limit 100
```

## Linux/macOS Bash setup

```bash
python3.12 -m venv image-matcher/.venv
image-matcher/.venv/bin/python -m pip install --upgrade pip
image-matcher/.venv/bin/python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu
image-matcher/.venv/bin/python -m pip install -e 'image-matcher[dev]'
image-matcher/.venv/bin/python -m decor_matcher build --catalog workspace/data/products.json --runtime image-matcher/runtime --limit 100
```

The build downloads and checksum-verifies the pinned SSCD, DISK, and LightGlue
artifacts, downloads the selected public catalog images, and creates the local
index. Matching works offline after that build completes. Generated model
weights, catalog images, indexes, fixtures, and uploads are ignored by Git.

## Build every current product and gallery image

The full build uses exact product IDs to join the branch catalog to the public
feed. It fails if a canonical product is missing, a feed ID is duplicated, an
image entry is incomplete, or any selected reference cannot be cached and
indexed. Live counts are reported by the command rather than hard-coded.

PowerShell:

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher build --catalog workspace/data/products.json --all-products --gallery-feed https://decormoments.com/products.json --runtime image-matcher/runtime-full
```

Bash:

```bash
image-matcher/.venv/bin/python -m decor_matcher build --catalog workspace/data/products.json --all-products --gallery-feed https://decormoments.com/products.json --runtime image-matcher/runtime-full
```

The current verified local build observed 256 canonical/feed products, 671
gallery references, and 1,549 variants. Those are dated evidence, not constants.
The 100-reference runtime remains at `image-matcher/runtime`; the full runtime
is separately ignored at `image-matcher/runtime-full`.

## Test with the local browser UI

PowerShell:

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher ui --runtime image-matcher/runtime-full --port 7860
```

Bash:

```bash
image-matcher/.venv/bin/python -m decor_matcher ui --runtime image-matcher/runtime-full --port 7860
```

Open [http://127.0.0.1:7860](http://127.0.0.1:7860), choose an image, and press
**Test image**. The page is deliberately bound to `127.0.0.1` only. It shows
the uploaded image next to the internally resolved cached reference for an
accepted match. With gallery products, the displayed reference is resolved by
the exact verified product ID and image digest, never by an upload-supplied
path. Uploads must be under 20 MiB and are deleted after every
request, including failures.

The screenshot can contain ordinary browser chrome, a price, text, or a border.
Matching gets harder when the catalog photo is tiny, off-center, heavily
covered, or one of several product images on the page; uncertain cases hand off.

## Match one image from the command line

PowerShell:

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher match C:/path/to/customer-screenshot.jpg --runtime image-matcher/runtime
```

Bash:

```bash
image-matcher/.venv/bin/python -m decor_matcher match /path/to/customer-screenshot.jpg --runtime image-matcher/runtime
```

Each command writes exactly one JSON object to standard output. A legitimate
`handoff` exits successfully; an operational `error` exits with code 1.

## Generate fixtures and run a bounded benchmark

Start with one product because CPU LightGlue verification can take many seconds
per candidate. Increase the bounds deliberately after the smoke run.

PowerShell:

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher make-fixtures --runtime image-matcher/runtime --output image-matcher/runtime/fixtures --max-products 1
image-matcher/.venv/Scripts/python -m decor_matcher benchmark --runtime image-matcher/runtime --fixtures image-matcher/runtime/fixtures --catalog workspace/data/products.json --max-negatives 1
```

Bash:

```bash
image-matcher/.venv/bin/python -m decor_matcher make-fixtures --runtime image-matcher/runtime --output image-matcher/runtime/fixtures --max-products 1
image-matcher/.venv/bin/python -m decor_matcher benchmark --runtime image-matcher/runtime --fixtures image-matcher/runtime/fixtures --catalog workspace/data/products.json --max-negatives 1
```

Fixture generation creates JPEG-55, 50%-resize, 12%-crop, screenshot-frame,
and text-overlay variants plus `truth.json`. The benchmark reports every case,
top-1 retrieval correctness, verified acceptance, false accepts, handoffs,
errors, negative download failures, and p50/p95 matching latency. The
`--max-negatives` bound is explicit and deterministic; it never filters or
hides a completed result.
