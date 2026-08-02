from pathlib import Path
import sys
from urllib.request import urlopen

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.artifacts import SSCD_ARTIFACT, ensure_artifact
from decor_matcher.sscd import SscdEncoder


@pytest.fixture(scope="session")
def downloaded_sscd():
    model_dir = Path(__file__).parents[1] / "runtime" / "models"

    def download(url):
        with urlopen(url, timeout=120) as response:
            return response.read()

    return ensure_artifact(SSCD_ARTIFACT, model_dir, download)


@pytest.mark.models
def test_real_sscd_loads_and_emits_normalized_512_vector(downloaded_sscd):
    encoder = SscdEncoder(downloaded_sscd, device="cpu")
    vector = encoder.encode([Image.new("RGB", (320, 240), "red")])

    assert vector.shape == (1, 512)
    assert np.linalg.norm(vector[0]) == pytest.approx(1.0, abs=1e-5)
