# Decor Moments 100-image matcher MVP evidence

Run date: 2026-08-02

> **NOT CONNECTED TO WHATSAPP.** This standalone local build does not change the
> current bot image handoff, send a customer message, or authorize integration.

## Result

The real build completed with exactly 100 deterministically selected catalog
products, 100 cached primary images, and 100 indexed SSCD descriptors. The
bounded CPU smoke then produced:

- transformed positives: 5/5 top-1 correct and 5/5 verified correct;
- unindexed catalog negatives: 1/1 handed off;
- false accepts: 0;
- matching errors: 0; and
- negative download failures: 0.

This is a **six-query functional smoke run, not threshold calibration or
statistical validation**. It uses five synthetic transformations of one catalog
image and one deterministic unindexed catalog negative. It is not evidence of
performance on real customer photos, a representative screenshot distribution,
or the full Decor Moments catalog. The larger customer-style benchmark in the
design remains required before any WhatsApp integration.

## Reproducible inputs

| Input | Value |
|---|---|
| Repository task start | `5ae3fa0bb4e2e2f04d7bb650d920a641fe845b2b` |
| Source catalog branch/SHA | `DecorMomentsBot` / `8f768f9` |
| Catalog path | `workspace/data/products.json` |
| Catalog SHA-256 | `3ef25fb01c0697b4525df1a7e5d5cad9980ac0c3cb06929a2cab65a822c2d398` |
| Selection | sort valid unique products by SHA-256 of string product ID; take 100 |
| Selection digest | `192d77264781d253edebd0c090d1f2fe98446e92a2dcfa53075541864d335998` |
| Digest serialization | UTF-8 product IDs in the order below, joined by LF, with no trailing LF |
| Cached reference files | 100 files, 25,912,359 bytes |
| Index manifest SHA-256 | `9e7832f271285d99bb69b786a6213fa6bb4d671180c72f13e1b71a77ffdb0e30` |
| SSCD vectors SHA-256 | `98768db864b8404677c01bc871e6cf77a954fc26984580e30cd02976b811058e` |
| Fixture truth SHA-256 | `794bf18cca2c5d8c1e470283d3310b89ae8c9b7539ec91a39877d9fc933337ef` |

### Selected product IDs

The complete ordered 100-product selection was:

```text
7853619937343
7852710199359
7809685684287
7891165741119
7811130556479
7812046815295
7822925627455
7963653308479
7811125706815
7848563769407
8105904209983
7818093887551
7934838374463
7901737746495
7925963685951
7925960704063
7822913208383
7922738823231
7812075323455
7852610322495
7819676876863
8063021809727
7901699407935
7818538418239
7901696163903
7812074733631
7809638039615
7812080926783
7966631166015
7848574124095
7966658363455
7811128000575
7848773713983
7966635950143
7857626284095
7818112794687
7805800054847
7966663016511
7966547640383
8163188572223
7973282840639
7978742415423
7963513028671
7822565539903
7822923202623
8062946181183
7973278711871
7818668441663
8105918562367
7818530979903
7922784206911
7963384184895
8105919086655
8103947141183
8105864527935
7925981708351
8116047216703
7819837374527
7966545117247
7909417025599
8163164913727
7934832377919
7901750231103
7973231657023
7819601084479
7966539448383
7891367821375
8063014436927
8063011225663
7904003031103
7903746162751
8163184246847
8163174088767
7853623214143
7812862279743
7973236113471
7963652915263
7848565080127
7973300076607
7897896288319
7809587183679
7852585156671
7819628838975
7978740023359
7901884809279
7822920450111
7802759249983
7818659790911
7848800321599
7818531799103
7853627211839
8105865478207
7852639158335
7819669078079
8163203153983
8163178905663
7818068525119
7978705551423
7809671331903
7891207159871
```

## Models and checksums

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `sscd_disc_mixup.torchscript.pt` | 98,791,638 | `9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56` |
| `disk_lightglue.pth` | 47,631,405 | `b5b21d47ea24f2c5e501aec9c91b9716e4c8c3429a4dc1e615c133c4c9378335` |
| `depth-save.pth` | 4,375,832 | `9c2ee4ded238892dfa51569941372601e35e4a74aa6f84ea80053d2ab1c07abe` |

LightGlue was installed from commit
`eb42fee2d71449efb0aa5c10549752b5d75384d8`. The default experimental
decision gates used SSCD >= 0.80, adjacent-candidate margin >= 0.03, at least
20 MAGSAC inliers, inlier ratio >= 0.30, query and reference coverage >= 0.10,
and a 5-pixel reprojection threshold. Retrieval used the full, centered 90%,
and centered 80% query views and verified at most five unique products.

## Environment

| Component | Version/value |
|---|---|
| Operating system | Microsoft Windows 11 Pro 10.0.22631 |
| CPU | Intel Core i5-8250U @ 1.60 GHz, 8 logical processors |
| Visible memory | 17,015,463,936 bytes |
| Python | 3.12.10 |
| PyTorch | 2.9.1+cpu; CUDA unavailable; 4 Torch threads; MKLDNN available |
| torchvision | 0.24.1+cpu |
| NumPy | 2.2.6 |
| Pillow | 11.3.0 |
| OpenCV headless | 4.12.0.88 |
| Kornia | 0.8.2 |
| LightGlue package | 0.0, pinned Git commit above |
| ImageHash | 4.3.2 |
| Flask | 3.1.1 |
| pytest | 8.4.1 |

## Commands and exact outputs

### Pre-build tests

```powershell
image-matcher/.venv/Scripts/python -m pytest image-matcher/tests -q
```

```text
........................................................................ [ 96%]
...                                                                      [100%]
75 passed in 34.54s
```

### Real 100-reference build

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher build --catalog workspace/data/products.json --runtime image-matcher/runtime --limit 100
```

```json
{"artifacts":{"depth-save.pth":"9c2ee4ded238892dfa51569941372601e35e4a74aa6f84ea80053d2ab1c07abe","disk_lightglue.pth":"b5b21d47ea24f2c5e501aec9c91b9716e4c8c3429a4dc1e615c133c4c9378335","sscd_disc_mixup.torchscript.pt":"9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56"},"cached":100,"indexed":100,"selected":100,"status":"ok"}
```

Exit code was 0. End-to-end elapsed time, including catalog image downloads
and indexing, was 121.180 seconds.

### Fixture generation

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher make-fixtures --runtime image-matcher/runtime --output image-matcher/runtime/fixtures --max-products 1
```

```json
{"fixtures":5,"manifest":"image-matcher\\runtime\\fixtures\\truth.json","products":1,"status":"ok"}
```

Exit code was 0 and elapsed time was 7.397 seconds. The selected positive was
product `7853619937343` (`Frida Settee`).

### Six-query smoke benchmark

```powershell
image-matcher/.venv/Scripts/python -m decor_matcher benchmark --runtime image-matcher/runtime --fixtures image-matcher/runtime/fixtures --catalog workspace/data/products.json --max-negatives 1
```

```json
{"false_accepts":0,"indexed_references":100,"latency_ms":{"p50":10783.922,"p95":12361.747},"negative_download_failures":[],"negative_errors":0,"negative_handoffs":1,"negative_queries":1,"negative_requested":1,"negatives":[{"decision":"handoff","product_id":"7765741862975","reason":"ambiguous_top_candidates","returned_product_id":null}],"positive_errors":0,"positive_handoffs":0,"positive_queries":5,"positive_top1_correct":5,"positive_verified_correct":5,"positives":[{"accepted_correct":true,"decision":"catalog_match","product_id":"7853619937343","reason":"accepted","returned_product_id":"7853619937343","top1_correct":true,"transformation":"jpeg55"},{"accepted_correct":true,"decision":"catalog_match","product_id":"7853619937343","reason":"accepted","returned_product_id":"7853619937343","top1_correct":true,"transformation":"resize50"},{"accepted_correct":true,"decision":"catalog_match","product_id":"7853619937343","reason":"accepted","returned_product_id":"7853619937343","top1_correct":true,"transformation":"crop12"},{"accepted_correct":true,"decision":"catalog_match","product_id":"7853619937343","reason":"accepted","returned_product_id":"7853619937343","top1_correct":true,"transformation":"screenshot"},{"accepted_correct":true,"decision":"catalog_match","product_id":"7853619937343","reason":"accepted","returned_product_id":"7853619937343","top1_correct":true,"transformation":"text_overlay"}],"status":"ok"}
```

Exit code was 0. The command's end-to-end elapsed time was 71.356 seconds.
The benchmark-emitted per-query latency was 10,783.922 ms p50 and 12,361.747
ms p95 on this CPU.

| Transformation | Top-1 | Verified result |
|---|---|---|
| JPEG quality 55 | 1/1 correct | 1/1 correct |
| 50% resize | 1/1 correct | 1/1 correct |
| 12% centered crop | 1/1 correct | 1/1 correct |
| screenshot frame | 1/1 correct | 1/1 correct |
| text overlay | 1/1 correct | 1/1 correct |

The negative was catalog product `7765741862975`, which was deliberately not
in the 100-reference index. It returned `handoff` with reason
`ambiguous_top_candidates`; it did not return an indexed product.

### Final verification

```powershell
image-matcher/.venv/Scripts/python -m pytest image-matcher/tests -q
image-matcher/.venv/Scripts/python -m compileall -q image-matcher/src image-matcher/tests
git diff --check
git status --short
```

```text
........................................................................ [ 96%]
...                                                                      [100%]
75 passed in 31.55s
compileall exit code: 0
git diff --check exit code: 0
?? image-matcher/reports/
```

Only this report was untracked. The `.venv`, model artifacts, reference cache,
fixtures, negative cache, and index remained ignored runtime data.

## Failures and limitations

- No build, download, positive-query, or negative-query failure occurred in
  this run. This fact must not be generalized beyond these six queries.
- The positive recall figure is five transformations of one source image, so it
  is a smoke check rather than a meaningful recall estimate.
- The negative set contains one unindexed catalog image and contains no live
  customer photos, similar-but-different room photos, multi-product screenshots,
  off-center tiny products, or heavily occluded products.
- Only 100 primary images from 100 of the catalog's 256 products are indexed.
  Gallery-image expansion is not included.
- The matcher is exact-copy oriented. A different or live photograph of the
  same furniture is expected to hand off rather than identify the SKU.
- OCR and product-name text matching are not implemented in this MVP. All
  reported decisions came from image pixels through SSCD plus DISK/LightGlue
  geometry.
- CPU latency is material: roughly 10.8 seconds p50 and 12.4 seconds p95 in this
  narrow run, excluding any WhatsApp delivery path. Broader latency testing and
  potential optimization are required before integration.
- Thresholds are experimental defaults and were not tuned on or changed after
  the reported cases. They cannot be treated as production thresholds.
- The local browser UI is only a test surface bound to `127.0.0.1`; it is not a
  service deployment and retains no uploaded query after the request.

The next evidence gate is the design's larger benchmark with representative
customer screenshots, live/different furniture photos, close catalog neighbors,
and unrelated images. That evaluation must still produce zero false catalog
matches on the agreed negative set before integration is considered.
