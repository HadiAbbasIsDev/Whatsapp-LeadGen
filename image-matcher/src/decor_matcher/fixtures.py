import json
import random
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageOps

from .types import ReferenceRecord


TRANSFORMATIONS = ("jpeg55", "resize50", "crop12", "screenshot", "text_overlay")


def generate_variants(
    source: Image.Image | Path,
    output_dir: Path,
    *,
    seed: int = 20260802,
    stem: str = "query",
) -> list[Path]:
    """Create deterministic screenshot/copy transformations for one image."""
    output_dir.mkdir(parents=True, exist_ok=True)
    image = _load_rgb(source)
    random_source = random.Random(seed)
    variants: list[tuple[str, Image.Image, int]] = []

    variants.append(("jpeg55", image.copy(), 55))
    variants.append(
        (
            "resize50",
            image.resize(
                (max(1, image.width // 2), max(1, image.height // 2)),
                Image.Resampling.LANCZOS,
            ),
            82,
        )
    )

    crop_width = max(1, round(image.width * 0.88))
    crop_height = max(1, round(image.height * 0.88))
    crop_left = (image.width - crop_width) // 2
    crop_top = (image.height - crop_height) // 2
    variants.append(
        (
            "crop12",
            image.crop((crop_left, crop_top, crop_left + crop_width, crop_top + crop_height)),
            82,
        )
    )

    horizontal_margin = max(12, image.width // 9)
    top_margin = max(30, image.height // 5)
    bottom_margin = max(12, image.height // 10)
    screenshot = Image.new(
        "RGB",
        (image.width + horizontal_margin * 2, image.height + top_margin + bottom_margin),
        "white",
    )
    screenshot.paste(image, (horizontal_margin, top_margin))
    screenshot_draw = ImageDraw.Draw(screenshot)
    screenshot_draw.rectangle((0, 0, screenshot.width - 1, top_margin - 1), fill=(244, 244, 244))
    screenshot_draw.rounded_rectangle(
        (horizontal_margin, 8, screenshot.width - horizontal_margin, top_margin - 9),
        radius=6,
        fill=(225, 225, 225),
    )
    variants.append(("screenshot", screenshot, 82))

    overlay = image.copy()
    overlay_draw = ImageDraw.Draw(overlay, "RGBA")
    banner_height = max(18, image.height // 5)
    top = random_source.randint(0, max(0, image.height - banner_height))
    overlay_draw.rectangle((0, top, image.width, top + banner_height), fill=(255, 255, 255, 205))
    overlay_draw.text((8, top + 4), "DECOR MOMENTS  AED 999", fill=(20, 20, 20, 255))
    variants.append(("text_overlay", overlay, 82))

    paths: list[Path] = []
    for transformation, variant, quality in variants:
        target = output_dir / f"{stem}_{transformation}.jpg"
        variant.save(
            target,
            format="JPEG",
            quality=quality,
            subsampling=2,
            optimize=False,
            progressive=False,
        )
        paths.append(target)
    return paths


def generate_fixture_set(
    records: Sequence[ReferenceRecord],
    output_dir: Path,
    *,
    max_products: int = 20,
    seed: int = 20260802,
) -> dict[str, object]:
    """Generate transformed positives and a portable truth manifest."""
    if max_products < 0:
        raise ValueError("max_products must be non-negative")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures: list[dict[str, str]] = []
    chosen = tuple(records[:max_products])
    for position, record in enumerate(chosen):
        if record.cache_path is None or record.sha256 is None:
            raise ValueError(f"reference {record.product_id} is not cached")
        try:
            payload = record.cache_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read cached reference {record.product_id}: {exc}") from exc
        if sha256(payload).hexdigest() != record.sha256:
            raise ValueError(f"cached reference checksum mismatch for {record.product_id}")

        product_dir = output_dir / f"product_{position:03d}"
        paths = generate_variants(
            record.cache_path,
            product_dir,
            seed=seed + position,
            stem="query",
        )
        for transformation, path in zip(TRANSFORMATIONS, paths, strict=True):
            fixtures.append(
                {
                    "path": path.relative_to(output_dir).as_posix(),
                    "product_id": record.product_id,
                    "product_name": record.product_name,
                    "transformation": transformation,
                }
            )

    manifest: dict[str, object] = {
        "seed": seed,
        "products": len(chosen),
        "fixtures": fixtures,
    }
    (output_dir / "truth.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _load_rgb(source: Image.Image | Path) -> Image.Image:
    if isinstance(source, Image.Image):
        return ImageOps.exif_transpose(source).convert("RGB")
    with Image.open(source) as image:
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")
