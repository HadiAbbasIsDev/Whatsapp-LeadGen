import ipaddress
import json
import os
import re
import tempfile
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError

from .types import CatalogItem, ReferenceRecord


MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_BROWSER_USER_AGENT = "Mozilla/5.0 (compatible; DecorMatcher/0.1)"
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp"}


def load_catalog(path: Path) -> list[CatalogItem]:
    """Load valid primary-image records from the branch product catalog."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load catalog: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("catalog"), list):
        raise ValueError("catalog must be an object containing a catalog list")

    items: list[CatalogItem] = []
    for index, entry in enumerate(payload["catalog"]):
        if not isinstance(entry, dict):
            raise ValueError(f"catalog entry {index} must be an object")
        product_id = entry.get("id")
        product_name = entry.get("name")
        image_url = entry.get("image")
        if not isinstance(product_id, str) or not product_id.strip():
            raise ValueError(f"catalog entry {index} has an invalid id")
        if not isinstance(product_name, str) or not product_name.strip():
            raise ValueError(f"catalog entry {index} has an invalid name")
        if not isinstance(image_url, str) or not _is_public_http_url(image_url):
            raise ValueError(f"catalog entry {index} has an invalid image URL")
        items.append(CatalogItem(product_id, product_name, image_url))
    return items


def select_references(items: Iterable[CatalogItem], limit: int = 100) -> list[ReferenceRecord]:
    """Select a deterministic sample with no duplicate product IDs or URLs."""
    if limit < 0:
        raise ValueError("limit must be non-negative")

    seen_product_ids: set[str] = set()
    seen_urls: set[str] = set()
    unique_items: list[CatalogItem] = []
    for item in items:
        if not item.image_url or item.product_id in seen_product_ids or item.image_url in seen_urls:
            continue
        seen_product_ids.add(item.product_id)
        seen_urls.add(item.image_url)
        unique_items.append(item)

    selected = sorted(unique_items, key=lambda item: sha256(item.product_id.encode()).hexdigest())[:limit]
    return [ReferenceRecord(item.product_id, item.product_name, item.image_url, None, None) for item in selected]


def fetch_bytes(image_url: str) -> bytes:
    """Fetch one image payload with bounded network and memory use."""
    request = Request(image_url, headers={"User-Agent": _BROWSER_USER_AGENT})
    with urlopen(request, timeout=20) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError(f"image exceeds {MAX_RESPONSE_BYTES} byte limit")
    return payload


def cache_references(
    records: Iterable[ReferenceRecord],
    cache_dir: Path,
    fetch_bytes: Callable[[str], bytes],
) -> tuple[list[ReferenceRecord], list[str]]:
    """Fetch, validate, and atomically cache each reference without aborting a batch."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached: list[ReferenceRecord] = []
    failures: list[str] = []

    for record in records:
        try:
            payload = fetch_bytes(record.image_url)
            suffix = _image_suffix(record.image_url, payload)
            target = cache_dir / f"{_safe_product_id(record.product_id)}_{sha256(record.image_url.encode()).hexdigest()}{suffix}"
            _atomic_write(target, payload)
            cached.append(replace(record, cache_path=target, sha256=sha256(payload).hexdigest()))
        except UnidentifiedImageError:
            failures.append(f"{record.product_id}: cannot identify image file")
        except Exception as exc:
            failures.append(f"{record.product_id}: {exc}")

    return cached, failures


def _is_public_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return address.is_global and not address.is_multicast


def _image_suffix(image_url: str, payload: bytes) -> str:
    with Image.open(BytesIO(payload)) as image:
        image.verify()
        image_format = image.format
    url_suffix = Path(urlsplit(image_url).path).suffix.lower()
    if url_suffix in _FORMAT_SUFFIXES.values():
        return url_suffix
    return _FORMAT_SUFFIXES.get(image_format or "", ".img")


def _safe_product_id(product_id: str) -> str:
    return _SAFE_FILENAME.sub("_", product_id).strip("._") or sha256(product_id.encode()).hexdigest()


def _atomic_write(target: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
