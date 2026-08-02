from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.artifacts import ArtifactSpec, ArtifactValidationError, ensure_artifact, validate_artifact


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


def test_validate_artifact_hashes_incrementally_without_read_bytes(tmp_path, monkeypatch):
    payload = b"verified artifact"
    path = tmp_path / "model.pt"
    path.write_bytes(payload)
    spec = ArtifactSpec("model.pt", "https://models/model.pt", len(payload), sha256(payload).hexdigest())
    monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("artifact read must be chunked"))

    validate_artifact(path, spec)


def test_validate_artifact_rejects_wrong_stat_size_before_open(tmp_path, monkeypatch):
    path = tmp_path / "model.pt"
    with path.open("wb") as destination:
        destination.truncate(1024)
    spec = ArtifactSpec("model.pt", "https://models/model.pt", 12, "0" * 64)
    real_open = Path.open

    def reject_open(candidate, *args, **kwargs):
        if candidate == path:
            raise AssertionError("wrong-size artifact must not be read")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_open)

    with pytest.raises(ArtifactValidationError, match="size mismatch"):
        validate_artifact(path, spec)
