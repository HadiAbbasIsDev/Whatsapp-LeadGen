from io import BytesIO
from pathlib import Path
import sys
from urllib.request import Request, urlopen

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.verification import LightGlueVerifier


CATALOG_IMAGE_URL = (
    "https://cdn.shopify.com/s/files/1/0607/1402/5023/files/"
    "LunaLoungeChair.jpg?v=1778655014"
)


@pytest.fixture(scope="session")
def cached_catalog_image():
    cache_path = Path(__file__).parents[1] / "runtime" / "test-images" / "luna-lounge-chair.jpg"
    if not cache_path.exists():
        request = Request(CATALOG_IMAGE_URL, headers={"User-Agent": "DecorMatcher/0.1"})
        with urlopen(request, timeout=120) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            pytest.fail("catalog integration image exceeded 20 MiB")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
    with Image.open(cache_path) as source:
        return source.convert("RGB")


def resize_and_jpeg(image, scale, quality):
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    payload = BytesIO()
    resized.save(payload, format="JPEG", quality=quality)
    payload.seek(0)
    with Image.open(payload) as recompressed:
        return recompressed.convert("RGB")


@pytest.mark.models
def test_real_lightglue_verifies_resized_recompressed_copy(cached_catalog_image):
    transformed = resize_and_jpeg(cached_catalog_image, scale=0.65, quality=60)

    metrics = LightGlueVerifier(device="cpu").verify(transformed, cached_catalog_image)

    assert metrics.inliers >= 20
    assert metrics.inlier_ratio >= 0.30
