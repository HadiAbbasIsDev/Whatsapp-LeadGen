import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    filename: str
    url: str
    size: int
    sha256: str


SSCD_ARTIFACT = ArtifactSpec(
    "sscd_disc_mixup.torchscript.pt",
    "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt",
    98_791_638,
    "9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56",
)


class ArtifactValidationError(ValueError):
    """Raised when an artifact does not match its pinned identity."""


def ensure_artifact(
    spec: ArtifactSpec,
    model_dir: Path,
    fetch_bytes: Callable[[str], bytes],
) -> Path:
    """Return a locally cached artifact after verifying its exact pinned identity."""
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / spec.filename
    if target.exists():
        validate_artifact(target, spec)
        return target

    payload = fetch_bytes(spec.url)
    _validate_payload(payload, spec)
    with tempfile.NamedTemporaryFile(dir=model_dir, delete=False) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)

    validate_artifact(target, spec)
    return target


def validate_artifact(path: Path, spec: ArtifactSpec) -> None:
    """Verify artifact size and SHA-256 before a caller loads it."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArtifactValidationError(f"cannot read artifact {path}: {exc}") from exc
    _validate_payload(payload, spec)


def _validate_payload(payload: bytes, spec: ArtifactSpec) -> None:
    if len(payload) != spec.size:
        raise ArtifactValidationError(
            f"artifact size mismatch: expected {spec.size}, got {len(payload)}"
        )
    digest = sha256(payload).hexdigest()
    if digest != spec.sha256:
        raise ArtifactValidationError(
            f"artifact checksum mismatch: expected {spec.sha256}, got {digest}"
        )
