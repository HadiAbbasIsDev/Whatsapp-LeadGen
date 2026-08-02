import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 40_000_000
SSCD_RESIZE_SHORT_EDGE = 288
MAX_SSCD_RESIZED_DIMENSION = 8192
MAX_SSCD_RESIZED_PIXELS = 40_000_000
SUPPORTED_IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
}


class ImageSafetyError(ValueError):
    """Raised before pixel decoding when an image violates the shared safety policy."""


class UnsupportedImageFormatError(ImageSafetyError):
    def __init__(self, image_format: str) -> None:
        self.image_format = image_format
        super().__init__(f"unsupported image format: {image_format or 'unknown'}")


class UnsafeImageDimensionsError(ImageSafetyError):
    """Raised when source or predicted SSCD resize dimensions exceed safe bounds."""


@dataclass(frozen=True, slots=True)
class SafeImageMetadata:
    image_format: str
    mime_type: str
    width: int
    height: int


def inspect_safe_image(
    source: Path | bytes | bytearray | BinaryIO,
    *,
    verify: bool = False,
) -> SafeImageMetadata:
    """Validate format and dimensions from image metadata before full pixel decoding."""
    opened_source: BinaryIO | BytesIO | Path
    if isinstance(source, (bytes, bytearray)):
        opened_source = BytesIO(source)
    else:
        opened_source = source
    try:
        with Image.open(opened_source) as image:
            image_format = image.format or ""
            mime_type = SUPPORTED_IMAGE_MIME_TYPES.get(image_format)
            if mime_type is None:
                raise UnsupportedImageFormatError(image_format)
            width, height = image.size
            _validate_dimensions(width, height)
            if verify:
                image.verify()
    except ImageSafetyError:
        raise
    except Image.DecompressionBombError as exc:
        raise UnsafeImageDimensionsError("unsafe image dimensions") from exc
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ImageSafetyError("cannot identify image file") from exc
    return SafeImageMetadata(image_format, mime_type, width, height)


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise UnsafeImageDimensionsError("unsafe image dimensions")

    short_edge = min(width, height)
    long_edge = max(width, height)
    scale = SSCD_RESIZE_SHORT_EDGE / short_edge
    resized_short = SSCD_RESIZE_SHORT_EDGE
    resized_long = math.ceil(long_edge * scale)
    if (
        resized_long > MAX_SSCD_RESIZED_DIMENSION
        or resized_short * resized_long > MAX_SSCD_RESIZED_PIXELS
    ):
        raise UnsafeImageDimensionsError("unsafe image dimensions after SSCD resize")
