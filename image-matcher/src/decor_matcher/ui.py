import base64
import hmac
import stat
import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from flask import Flask, render_template_string, request
from werkzeug.exceptions import RequestEntityTooLarge

from .artifacts import SSCD_ARTIFACT
from .index import DescriptorIndex, load_index
from .image_safety import (
    ImageSafetyError,
    UnsafeImageDimensionsError,
    UnsupportedImageFormatError,
    inspect_safe_image,
)
from .matcher import MAX_QUERY_BYTES, create_catalog_matcher
from .types import MatchResult


class Matcher(Protocol):
    def match(self, path: Path) -> MatchResult: ...


class UnsafeImageError(ValueError):
    """Raised when Pillow rejects unsafe decoded image dimensions."""


class ReferenceLookup:
    """Resolve only immutable index records; request values never become paths."""

    def __init__(self, index: DescriptorIndex | None) -> None:
        self._records = {} if index is None else {record.product_id: record for record in index.records}

    def image_data_uri(self, product_id: str | None) -> str | None:
        if product_id is None:
            return None
        record = self._records.get(product_id)
        if record is None or record.cache_path is None or record.sha256 is None:
            return None
        try:
            metadata = record.cache_path.stat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size <= 0
                or metadata.st_size >= MAX_QUERY_BYTES
            ):
                return None
            with record.cache_path.open("rb") as source:
                payload = source.read(MAX_QUERY_BYTES)
        except OSError:
            return None
        if len(payload) != metadata.st_size:
            return None
        if not hmac.compare_digest(sha256(payload).hexdigest(), record.sha256):
            return None
        return _image_data_uri(payload)


def create_app(
    runtime: Path,
    *,
    matcher: Matcher | None = None,
    index: DescriptorIndex | None = None,
    upload_dir: Path | None = None,
) -> Flask:
    """Create the loopback-only test application around the production matcher."""
    runtime = Path(runtime)
    if index is None:
        try:
            index = load_index(runtime / "index")
        except Exception:
            index = None
    if matcher is None:
        matcher = create_catalog_matcher(
            runtime / "models" / SSCD_ARTIFACT.filename,
            runtime / "index",
            verifier_model_dir=runtime / "models",
        )
    lookup = ReferenceLookup(index)
    temporary_dir = Path(upload_dir or runtime / "uploads")
    temporary_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    # Multipart framing is extra; the file stream itself is capped below.
    app.config["MAX_CONTENT_LENGTH"] = MAX_QUERY_BYTES + 2 * 1024 * 1024

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return _render_handoff("upload_too_large", message="Images must be under 20 MiB."), 413

    @app.route("/", methods=["GET", "POST"])
    def home():
        if request.method == "GET":
            return _render_page()

        upload = request.files.get("image")
        if upload is None or not upload.filename:
            return _render_handoff("image_required", message="Choose an image to test."), 400

        payload = upload.stream.read(MAX_QUERY_BYTES + 1)
        if len(payload) <= 0:
            return _render_handoff("empty_upload", message="Choose a non-empty image."), 400
        if len(payload) >= MAX_QUERY_BYTES:
            return _render_handoff("upload_too_large", message="Images must be under 20 MiB."), 413

        try:
            image_format, preview_mime = _inspect_image(payload)
        except UnsafeImageError:
            return _render_handoff(
                "decompression_bomb",
                message="The decoded image dimensions are unsafe.",
            ), 413
        if image_format is not None and preview_mime is None:
            return _render_handoff(
                "unsupported_image_format",
                message="Use a JPEG, PNG, WebP, GIF, or BMP image.",
            ), 400

        temporary_path: Path | None = None
        try:
            try:
                with tempfile.NamedTemporaryFile(
                    dir=temporary_dir,
                    suffix=".upload",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(payload)
            except Exception:
                return _render_handoff(
                    "upload_storage_failed",
                    message="The upload could not be stored safely for matching.",
                ), 500

            try:
                result = matcher.match(temporary_path)
            except Exception:
                result = MatchResult("error", None, None, None, "matching_failed", (), True)

            input_data_uri = _data_uri(payload, preview_mime) if preview_mime is not None else None
            if result.decision == "error":
                status = 400 if result.reason == "invalid_query_image" else 500
                return _render_handoff(
                    result.reason or "matching_failed",
                    input_data_uri=input_data_uri,
                    message="The image could not be checked safely.",
                ), status
            if input_data_uri is None:
                return _render_handoff(
                    "unsupported_image_format",
                    message="Use a JPEG, PNG, WebP, GIF, or BMP image.",
                ), 400
            if result.decision != "catalog_match":
                return _render_handoff(
                    result.reason or "no_verified_catalog_copy",
                    input_data_uri=input_data_uri,
                )

            reference_data_uri = lookup.image_data_uri(result.product_id)
            if reference_data_uri is None:
                safe_result = replace(
                    result,
                    decision="handoff",
                    product_id=None,
                    product_name=None,
                    confidence=None,
                    reason="catalog_reference_unavailable",
                )
                return _render_result(safe_result, input_data_uri, None)
            return _render_result(result, input_data_uri, reference_data_uri)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    return app


def _image_data_uri(payload: bytes) -> str | None:
    try:
        _image_format, mime = _inspect_image(payload)
    except UnsafeImageError:
        return None
    return _data_uri(payload, mime) if mime is not None else None


def _inspect_image(payload: bytes) -> tuple[str | None, str | None]:
    try:
        metadata = inspect_safe_image(payload, verify=True)
    except UnsupportedImageFormatError as exc:
        return exc.image_format, None
    except UnsafeImageDimensionsError as exc:
        raise UnsafeImageError("decoded image dimensions are unsafe") from exc
    except ImageSafetyError:
        return None, None
    return metadata.image_format, metadata.mime_type


def _data_uri(payload: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _render_handoff(
    reason: str,
    *,
    input_data_uri: str | None = None,
    message: str = "No exact catalog copy was verified.",
) -> str:
    result = MatchResult("handoff", None, None, None, reason, (), True)
    return _render_result(result, input_data_uri, None, message=message)


def _render_page(
    *,
    result: MatchResult | None = None,
    input_data_uri: str | None = None,
    reference_data_uri: str | None = None,
    message: str | None = None,
) -> str:
    return render_template_string(
        _PAGE,
        result=result,
        input_data_uri=input_data_uri,
        reference_data_uri=reference_data_uri,
        message=message,
    )


def _render_result(
    result: MatchResult,
    input_data_uri: str | None,
    reference_data_uri: str | None,
    *,
    message: str | None = None,
) -> str:
    return _render_page(
        result=result,
        input_data_uri=input_data_uri,
        reference_data_uri=reference_data_uri,
        message=message,
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Decor Moments Image Matcher</title>
  <style>
    :root { color-scheme: light; font-family: Inter, system-ui, sans-serif; background: #f5f1ea; color: #26221d; }
    body { margin: 0; padding: 2rem 1rem; }
    main { max-width: 980px; margin: auto; background: white; border-radius: 20px; padding: 2rem; box-shadow: 0 12px 40px #4b3b2718; }
    h1 { margin-top: 0; } .muted { color: #6c6257; }
    form { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; padding: 1rem; background: #faf8f4; border-radius: 12px; }
    button { background: #31271f; color: white; border: 0; border-radius: 9px; padding: .75rem 1.2rem; cursor: pointer; }
    .decision { margin-top: 1.5rem; padding: 1rem; border-radius: 12px; }
    .match { background: #eaf7ec; border: 1px solid #8ac696; } .handoff { background: #fff3e3; border: 1px solid #e0ac68; }
    .images { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; margin-top: 1rem; }
    figure { margin: 0; } figcaption { font-weight: 650; margin-bottom: .5rem; }
    img { display: block; width: 100%; max-height: 440px; object-fit: contain; background: #eee; border-radius: 10px; }
    code { overflow-wrap: anywhere; }
  </style>
</head>
<body><main>
  <h1>Decor Moments Image Matcher</h1>
  <p class="muted">Upload catalog copies or screenshots. This local MVP is not connected to WhatsApp and does not retain uploads.</p>
  <form method="post" enctype="multipart/form-data">
    <input type="file" name="image" accept="image/jpeg,image/png,image/webp,image/gif,image/bmp" required>
    <button type="submit">Test image</button>
  </form>
  {% if result %}
    {% if result.decision == 'catalog_match' and reference_data_uri %}
      <section class="decision match">
        <h2>Exact catalog copy matched</h2>
        <p><strong>{{ result.product_name }}</strong></p>
        <p><strong>Product ID:</strong> {{ result.product_id }}</p>
        <p><strong>Confidence:</strong> {{ '%.2f'|format(result.confidence * 100) }}%</p>
        <p><strong>Evidence:</strong> {{ result.evidence|join(', ') }}</p>
      </section>
    {% else %}
      <section class="decision handoff">
        <h2>Human handoff</h2><p>{{ message or 'No exact catalog copy was verified.' }}</p>
        <p><strong>Reason:</strong> <code>{{ result.reason }}</code></p>
      </section>
    {% endif %}
    <section class="images">
      {% if input_data_uri %}<figure><figcaption>Uploaded image</figcaption><img src="{{ input_data_uri }}" alt="Uploaded image"></figure>{% endif %}
      {% if reference_data_uri %}<figure><figcaption>Catalog reference</figcaption><img src="{{ reference_data_uri }}" alt="Matched catalog reference"></figure>{% endif %}
    </section>
  {% endif %}
</main></body></html>"""
