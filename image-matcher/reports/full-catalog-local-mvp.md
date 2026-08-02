# Decor Moments full-catalog local MVP evidence

> **NOT CONNECTED TO WHATSAPP.** This is a standalone local tester. It does not
> change the existing image handoff, send customer replies, or authorize live
> automatic product matching.

Evidence date: 2026-08-02. Source branch baseline: `DecorMomentsBot` at
`8f768f9`. The live counts below are observed build evidence, not constants.

## Full build

Command:

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher build --catalog workspace/data/products.json --all-products --gallery-feed https://decormoments.com/products.json --runtime image-matcher/runtime-full
```

The feed transport used only the exact
`https://decormoments.com/products.json` origin, rejected redirects, required
globally routable DNS results, read no more than 8 MiB per JSON page, requested
250 products per page, and permitted at most 20 pages. Every branch product was
joined by exact string product ID; product names were not a fallback.

Observed result:

| Measure | Result |
|---|---:|
| Feed pages | 2 |
| Canonical branch products | 256 |
| Feed products | 256 |
| Variants | 1,549 |
| Gallery references selected | 671 |
| References cached | 671 |
| References indexed | 671 |
| SSCD vector shape | `671 x 512` |
| Distinct image byte digests | 600 |
| Cross-product duplicate-content ambiguity groups | 70 |
| Build elapsed time | 896.928 seconds |

Raw feed-page SHA-256 digests, in page order:

1. `d8a7f34dcf6a52d2cf5c66f6dc797f9b411f46dfeb17b880ac5e2e9e03020462`
2. `39f92e5e5fd030e4f4da8933ee91fa6a5da5f9efbff356df9d744e26b42d60f7`

The build completed with zero reference failures. The manifest is 304,789
bytes, declares 671 references, and is tied to the vector-file checksum. The
vector file is 1,374,336 bytes. The 600 distinct byte digests mean 71 of the
671 reference rows repeat already-seen bytes; 70 digest groups are shared by
different product IDs. Any candidate using one of those cross-product shared
digests is forced to human handoff.

Pinned artifact checksums revalidated by the build:

| Artifact | SHA-256 |
|---|---|
| SSCD | `9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56` |
| DISK depth | `9c2ee4ded238892dfa51569941372601e35e4a74aa6f84ea80053d2ab1c07abe` |
| LightGlue DISK | `b5b21d47ea24f2c5e501aec9c91b9716e4c8c3429a4dc1e615c133c4c9378335` |

## Functional smoke cases

These are functional smoke checks, **not threshold calibration and not a
statistical benchmark**. Thresholds were not changed from these three cases.
Timings are end-to-end CLI timings on the local CPU host and include model and
validated-index initialization.

| Case | Expected | Observed | End-to-end time |
|---|---|---|---:|
| Exact cached copy for `Aura Chair` (`8163201155135`), a product outside the original deterministic 100-product sample | Match exact product/reference | `catalog_match`, SSCD `1.000000`, 2,048/2,048 geometric inliers, reference SHA `b7c11b6200479918cfd5eb5bb97c5b7cbf02f5e8c31affc6b246b2fbc85225ed` | 45.847 s |
| Screenshot-frame transform of the second of four gallery images for `The Arcadian Media Stand` (`8063025872959`) | Match secondary gallery reference | `catalog_match`, center-80 view, SSCD `0.894102`, 1,700/1,707 inliers, reference SHA `a449de29e9bb43641f1c75a387a99552666f0cdccf867fdb65a8fdb6f2b173a6` | 45.677 s |
| Unrelated synthetic stripe/shape image | Handoff | `handoff`, reason `ambiguous_top_candidates`; no product returned | 20.911 s |

## Run the local UI

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher ui --runtime image-matcher/runtime-full --port 7860
```

Open `http://127.0.0.1:7860`. The server binds only to loopback. It deletes the
temporary upload after each request and displays a catalog image only when the
accepted `(product_id, reference_sha256)` pair exists in the verified index.

## Limitations and decision status

- The matcher proves reused catalog-image geometry; it is not a live-photo or
  visual-similarity recognizer. A different photograph of the same furniture
  should hand off.
- The thresholds remain experimental. Three smoke cases cannot establish the
  documented zero-false-match/95%-recall acceptance gate.
- Cross-product duplicate catalog bytes are common in this feed and are
  intentionally ineligible for automatic matching.
- CPU startup plus one accepted verification took about 46 seconds here.
- Screenshot clutter can work when the catalog image remains large and
  centered. Tiny, off-center, multi-product, or heavily covered images can hand
  off.
- No OCR/name-matching path is enabled in this MVP.

The full local runtime is ready for manual UI testing, but WhatsApp integration
remains a separate, unapproved phase pending a representative calibrated
positive and hard-negative benchmark.
