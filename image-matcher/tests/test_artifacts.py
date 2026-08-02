from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.artifacts import ArtifactSpec, ArtifactValidationError, ensure_artifact


@pytest.mark.parametrize("payload", [b"ab", b"abd"])
def test_ensure_artifact_rejects_wrong_size_or_checksum(tmp_path, payload):
    spec = ArtifactSpec("model.pt", "https://models/model.pt", 3, sha256(b"abc").hexdigest())

    with pytest.raises(ArtifactValidationError):
        ensure_artifact(spec, tmp_path, lambda _: payload)


def test_ensure_artifact_writes_valid_download_and_reuses_it(tmp_path):
    payload = b"abc"
    spec = ArtifactSpec("model.pt", "https://models/model.pt", len(payload), sha256(payload).hexdigest())
    fetches = []

    path = ensure_artifact(spec, tmp_path, lambda url: fetches.append(url) or payload)
    reused_path = ensure_artifact(spec, tmp_path, lambda _: pytest.fail("valid artifact was fetched again"))

    assert path == tmp_path / "model.pt"
    assert path.read_bytes() == payload
    assert reused_path == path
    assert fetches == ["https://models/model.pt"]
