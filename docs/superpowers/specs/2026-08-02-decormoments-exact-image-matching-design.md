# Decor Moments Exact Catalog Image Matching — Design

## Purpose

Build and evaluate a standalone image matcher for the Decor Moments catalog before changing the WhatsApp bot. The matcher identifies an exact catalog product when a customer sends a copy of a catalog image, including a screenshot, crop, resize, recompressed image, or image with light UI/text overlays. If exact catalog identity cannot be established confidently, it returns a handoff decision.

The system is intentionally an exact-copy recognizer, not a visual recommendation engine. A live photograph of furniture must not be mapped to a merely similar catalog product, even if it resembles one closely.

## Current Context

- Source repository: `HadiAbbasIsDev/Whatsapp-LeadGen`.
- Source branch: `DecorMomentsBot`.
- Catalog source: `workspace/data/products.json`.
- At source SHA `8f768f9`, the branch catalog contains 256 unique products and 256 primary public Shopify image URLs. The `934 products` statement in the product-catalog skill is stale.
- The live Shopify product feed currently contains the same 256 products, 671 gallery images, and 1,549 variants.
- The live bot currently hands every incoming image or video to a human, notifies admins, tags the chat `hot leads`, and goes silent.
- This project does not change that live behavior. Integration is a separate phase that is allowed only after the standalone benchmark passes.

## Scope

### Included

- Read canonical product metadata from `products.json` and all available reference-image URLs from the public Shopify product feed.
- Cache reference images locally and build a reproducible search index.
- Match an input image by visual copy evidence without requiring text.
- Optionally recognize a visible product name and match it to the catalog.
- Return a machine-readable decision with evidence and diagnostics.
- Generate transformed screenshot-style positive tests.
- Evaluate against similar catalog products, real furniture photographs, and unrelated images.
- Run on CPU; use a GPU automatically when one is available.

### Excluded

- WhatsApp, Kapso, or OpenClaw integration.
- Changing the current human-handoff flow.
- Visually similar product recommendations.
- YOLO training or a 256-class product detector.
- Fine-tuning a vision model during the first prototype.
- Customer-facing replies or real WhatsApp test sends.
- Video matching. Videos continue to require human handoff.

## Decision Semantics

The matcher produces one of three outcomes:

1. `catalog_match`: exact catalog identity is supported by an accepted evidence path.
2. `handoff`: no exact catalog identity was verified. This includes live photographs, similar-but-not-identical furniture, ambiguous matches, and weak scores.
3. `error`: matching could not run safely because an input, model, index, or dependency failed. Consumers must treat `error` the same as `handoff`; errors never become matches.

The matcher does not need to prove that an image is a live photograph. It only needs to prove an exact catalog copy. Everything not proven is handed off.

## Recommended Models and Methods

### Candidate retrieval: SSCD `sscd_disc_mixup`

Use Meta's pretrained SSCD ResNet-50 TorchScript model from `https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt`. It produces a 512-dimensional, L2-normalized descriptor designed for image-copy detection under transformations such as resizing, compression, cropping, and overlays.

The SSCD repository and model use the MIT license. The repository was archived in 2023, so the implementation pins the 98,791,638-byte artifact, records its checksum, and vendors it through the project's artifact-fetch process instead of relying on an unpinned runtime download. Compatibility with the selected current PyTorch build must be proven by a load-and-forward smoke test before feature code proceeds.

For 671 current reference images, store the descriptors as a NumPy matrix and retrieve candidates with a cosine-similarity matrix multiplication. A vector database or FAISS service is unnecessary at this size.

### Exact verification: DISK + LightGlue + RANSAC

Use pretrained DISK local features and LightGlue to match keypoints between the query and each of the top SSCD candidates. Pin LightGlue to commit `eb42fee2d71449efb0aa5c10549752b5d75384d8` and use Kornia's `DISK.from_pretrained("depth")`. LightGlue and DISK are Apache-2.0 licensed; do not substitute the restrictively licensed official SuperPoint weights. Use OpenCV RANSAC or USAC-MAGSAC to estimate a homography and measure geometrically consistent inliers.

Verification features include:

- number of LightGlue matches;
- number and ratio of RANSAC inliers;
- spatial coverage of inliers in the reference image;
- spatial coverage of inliers in the query image; and
- the margin between the best and second-best SSCD candidates.

This stage rejects furniture that is semantically similar but does not reuse the catalog image's geometry.

### Fast path: perceptual hashing

Store perceptual hashes for each reference image. A near-zero Hamming distance provides a cheap high-confidence route for unmodified, resized, or lightly recompressed copies. Perceptual hashing is not used alone for difficult crops or overlays.

### Optional text path: PaddleOCR + normalized name matching

Run OCR independently of visual retrieval. Keep PaddleOCR in an optional dependency group because it adds a second ML runtime. Normalize recognized text and product names by case-folding, Unicode normalization, punctuation removal, and whitespace collapse. Use token-aware fuzzy matching for minor OCR errors.

A name-only match is accepted only when:

- the recognized phrase maps unambiguously to one catalog product;
- the OCR and fuzzy-name scores exceed calibrated thresholds; and
- the matched phrase is sufficiently specific, not a generic category such as `chair`, `sofa`, or `bed`.

Ambiguous or generic OCR results may help rank visual candidates but cannot create a match.

## Architecture

### 1. Catalog loader

Reads `workspace/data/products.json`, validates required fields, and normalizes product IDs and names. It then reads the public Shopify product feed and joins every gallery image to its canonical branch product ID. The current target is 256 products and 671 references. If a Shopify product cannot be joined unambiguously to the branch catalog, the loader reports and skips it rather than inventing a mapping.

The saved manifest captures the source branch SHA and a content hash of the Shopify response so an index remains reproducible even when the live catalog changes.

### 2. Reference cache

Downloads catalog images with bounded timeouts, validates that each payload decodes as an image, and stores it under a deterministic product/reference key. A manifest records the source URL, content hash, retrieval status, dimensions, and build timestamp.

One failed image must not invalidate the entire index. The build report lists every skipped reference.

### 3. Index builder

For each valid reference image, compute and store:

- SSCD descriptor;
- perceptual hash;
- DISK keypoints and descriptors, where serialization is supported reliably;
- product metadata and normalized name aliases; and
- a content hash tying features to the exact cached image.

The index is rebuilt when the catalog or reference content changes. Generated model weights, downloaded images, and indexes are not committed to Git.

### 4. Query preparation

Validate the input MIME type, decoded dimensions, and byte size. Normalize orientation from EXIF metadata and convert to RGB. Produce a small deterministic set of query views: the full image and limited center/large-rectangle crops intended to remove screenshot UI without creating an unbounded search.

Each query view is processed by perceptual hashing and SSCD. OCR may run once on the full input.

### 5. Candidate retrieval

Retrieve the top five unique product candidates across all query views. Preserve the best score, originating view, and score margin. If a perceptual-hash fast path succeeds, the same product is still checked for catalog/index consistency before returning.

### 6. Candidate verification

Run DISK + LightGlue only for the retrieved candidates, then apply RANSAC geometric verification. Combine calibrated global and local evidence using explicit rules, not an opaque weighted score.

Initial rule shape:

- accept a near-identical perceptual-hash match;
- otherwise require an SSCD score above the calibrated floor and a successful geometric verification;
- allow a unique, high-confidence, sufficiently specific OCR product name as a separate accepted path; and
- return `handoff` for all other cases.

Exact numeric thresholds are deliberately not fixed in this design. Paper or benchmark thresholds are not assumed to transfer to the Decor Moments distribution. The test harness selects conservative thresholds from Decor Moments positives and hard negatives, and records them in a versioned configuration file.

### 7. CLI and library interface

The standalone CLI accepts a local image path and emits JSON only on standard output. Diagnostic logs go to standard error.

Example:

```console
python -m image_matcher match customer-image.jpg
```

Successful response:

```json
{
  "decision": "catalog_match",
  "product_id": "8163201155135",
  "product_name": "Aura Chair",
  "confidence": 0.97,
  "matched_reference": "primary",
  "evidence": ["sscd", "lightglue", "ransac"]
}
```

Fallback response:

```json
{
  "decision": "handoff",
  "reason": "no_verified_catalog_copy",
  "candidates": []
}
```

The Python library exposes the same result structure so later integration does not parse human-readable output.

## Testing Strategy

### Unit tests

- Catalog and index schema validation.
- Stable product-name normalization and ambiguity detection.
- Deterministic hashing and index invalidation.
- Input validation and error-to-handoff behavior.
- Candidate aggregation across query views.
- Decision rules around configured thresholds.

### Generated positive benchmark

Create deterministic variants of catalog images that simulate:

- JPEG and WhatsApp-style recompression;
- resizing and downsampling;
- center and off-center crops;
- phone/browser screenshot borders;
- text, price, and UI overlays;
- modest brightness, contrast, and color changes; and
- combinations of the transformations above.

The generator records the true product ID and transformation parameters.

### Negative and hard-negative benchmark

- Every non-matching catalog product is a potential hard negative.
- Give special weight to the nearest SSCD neighbors, which are likely to be similar sofas, chairs, beds, or tables.
- Include genuine room and customer-style furniture photographs.
- Include unrelated screenshots and photographs.
- Include images containing only generic furniture words.

Real historical customer images are preferred when they can be used lawfully. If they are unavailable, begin with appropriately licensed public negatives and replace or supplement them before integration.

### Acceptance gate

The standalone prototype is eligible for integration work only when:

- it produces zero false catalog matches on the agreed live-photo and hard-negative benchmark;
- it achieves at least 95% recall on the agreed screenshot/copy transformations;
- every failure and ambiguity returns `handoff` or `error`, never a guessed product;
- thresholds and model/index versions are reproducible; and
- CPU latency is measured and acceptable for a WhatsApp response path.

The zero-false-match result applies to the benchmark, not as a claim of universal perfection. Production telemetry must continue to favor handoff when uncertain.

## Operational and Security Requirements

- No API keys or customer-image uploads to third-party services are required.
- Reject unsupported formats and decompression-bomb-sized inputs.
- Bound download, model, OCR, and verification work with timeouts.
- Do not retain customer query images by default.
- Pin dependency and model artifact versions and record model checksums.
- Cache all runtime artifacts locally so inference does not require internet access.
- Treat missing/corrupt weights, partial indexes, and catalog mismatches as `error`.
- Keep the matcher isolated from WhatsApp credentials and customer databases.

## Expected Resource Profile

- CPU-only execution is supported; CUDA is optional.
- The SSCD descriptor matrix is approximately 1.4 MB for 671 gallery images before metadata.
- The SSCD, LightGlue, and DISK weights total approximately 145 MB.
- Precomputed DISK features for all references are expected to consume roughly 0.4–0.8 GB, depending on the final keypoint cap.
- Cached images, PyTorch dependencies, model weights, and optional local descriptors require substantially more disk; provision several gigabytes for a comfortable development environment.
- Index construction is an offline batch job. Query inference loads the completed index and does no catalog network requests.
- Exact latency and memory targets will be set from the feasibility spike and measured benchmark, not estimated as guarantees.

## Future Integration Contract

Integration remains out of scope for the prototype, but the intended flow is:

```text
Incoming image
    |
    v
Standalone matcher library
    |-- catalog_match --> send matched product through send_product.py
    `-- handoff/error --> existing notify_admins + hot-leads handoff
```

The integration must preserve the existing silence rules, one-message behavior, category gate, admin notification, and `send_product.py` delivery-confirmation contract. No product is claimed or sent unless the existing send script confirms it.

## Alternatives Considered

### Google Vision Product Search

It is the most relevant managed API and supports home goods, but it returns visually and semantically similar products. It adds recurring cost, third-party image transfer, and still needs strict local verification. It is reserved as a benchmark alternative, not the first implementation.

### YOLO

YOLO can locate broad objects such as chairs or sofas, but exact identification across 256 SKUs would require many labeled views per SKU and bounding-box training data that the catalog does not provide. It is unnecessary for the screenshot-copy problem and is excluded from the first prototype.

### CLIP, SigLIP, DINO, or a vision LLM as the final matcher

These models are useful for semantic similarity or candidate retrieval, but can rank a live photograph beside a similar catalog product. They do not replace copy-specific retrieval plus geometric verification for this precision-first requirement.

## Delivery Sequence

1. Validate model artifacts, dependencies, licenses, and CPU compatibility.
2. Implement the isolated catalog loader, cache, and index builder.
3. Implement SSCD and perceptual-hash candidate retrieval.
4. Implement DISK + LightGlue + RANSAC verification.
5. Add optional OCR name matching.
6. Build the deterministic positive and negative benchmark.
7. Calibrate thresholds and publish the benchmark report.
8. Decide whether the evidence justifies a separate WhatsApp integration plan.
