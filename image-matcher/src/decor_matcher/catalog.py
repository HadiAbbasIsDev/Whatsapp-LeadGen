import ipaddress
import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .image_safety import MAX_IMAGE_BYTES, ImageSafetyError, inspect_safe_image
from .types import CatalogItem, ReferenceRecord


MAX_RESPONSE_BYTES = MAX_IMAGE_BYTES
MAX_FEED_PAGE_BYTES = 8 * 1024 * 1024
MAX_FEED_PAGES = 20
FEED_PAGE_LIMIT = 250
_BROWSER_USER_AGENT = "Mozilla/5.0 (compatible; DecorMatcher/0.1)"
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp"}
_ALLOWED_CATALOG_HOST = "cdn.shopify.com"
_ALLOWED_FEED_HOST = "decormoments.com"
_ALLOWED_FEED_PATH = "/products.json"


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True, slots=True)
class GalleryCatalog:
    references: tuple[ReferenceRecord, ...]
    page_sha256: tuple[str, ...]
    feed_product_count: int
    variant_count: int


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


def fetch_bytes(
    image_url: str,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    opener=None,
) -> bytes:
    """Fetch one image payload with bounded network and memory use."""
    parsed = _validated_catalog_url(image_url)
    addresses = resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("catalog image host did not resolve")
    for result in addresses:
        try:
            address = ipaddress.ip_address(result[4][0])
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("catalog image host returned an invalid address") from exc
        if not address.is_global or address.is_multicast:
            raise ValueError("catalog image host must resolve only to globally routable addresses")

    request = Request(image_url, headers={"User-Agent": _BROWSER_USER_AGENT})
    selected_opener = opener or build_opener(_RejectRedirects())
    with selected_opener.open(request, timeout=20) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError(f"image exceeds {MAX_RESPONSE_BYTES} byte limit")
    return payload


def fetch_gallery_page(
    feed_url: str,
    page: int,
    limit: int = FEED_PAGE_LIMIT,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    opener=None,
) -> bytes:
    """Fetch one bounded Shopify JSON page from the exact Decor Moments origin."""
    parsed = _validated_feed_url(feed_url)
    if not 1 <= page <= MAX_FEED_PAGES:
        raise ValueError(f"feed page must be between 1 and {MAX_FEED_PAGES}")
    if not 1 <= limit <= FEED_PAGE_LIMIT:
        raise ValueError(f"feed limit must be between 1 and {FEED_PAGE_LIMIT}")
    _require_global_addresses(parsed.hostname, resolver)

    page_url = f"https://{_ALLOWED_FEED_HOST}{_ALLOWED_FEED_PATH}?limit={limit}&page={page}"
    request = Request(
        page_url,
        headers={"User-Agent": _BROWSER_USER_AGENT, "Accept": "application/json"},
    )
    selected_opener = opener or build_opener(_RejectRedirects())
    with selected_opener.open(request, timeout=20) as response:
        payload = response.read(MAX_FEED_PAGE_BYTES + 1)
    if len(payload) > MAX_FEED_PAGE_BYTES:
        raise ValueError(f"feed page exceeds {MAX_FEED_PAGE_BYTES} byte limit")
    return payload


def load_gallery_references(
    canonical_items: Iterable[CatalogItem],
    feed_url: str,
    *,
    fetch_page: Callable[[str, int, int], bytes] = fetch_gallery_page,
) -> GalleryCatalog:
    """Join every canonical product to all unique gallery URLs by exact product ID."""
    _validated_feed_url(feed_url)
    canonical_by_id: dict[str, CatalogItem] = {}
    for item in canonical_items:
        if item.product_id in canonical_by_id:
            raise ValueError(f"duplicate canonical product ID: {item.product_id}")
        canonical_by_id[item.product_id] = item
    if not canonical_by_id:
        raise ValueError("canonical catalog must contain at least one product")

    feed_by_id: dict[str, tuple[str, ...]] = {}
    page_hashes: list[str] = []
    feed_product_count = 0
    variant_count = 0
    for page in range(1, MAX_FEED_PAGES + 1):
        payload = fetch_page(feed_url, page, FEED_PAGE_LIMIT)
        if not isinstance(payload, bytes) or len(payload) <= 0 or len(payload) > MAX_FEED_PAGE_BYTES:
            raise ValueError(f"feed page {page} has an invalid bounded payload")
        page_hashes.append(sha256(payload).hexdigest())
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"feed page {page} is not valid UTF-8 JSON") from exc
        products = document.get("products") if isinstance(document, dict) else None
        if not isinstance(products, list) or len(products) > FEED_PAGE_LIMIT:
            raise ValueError(f"feed page {page} must contain at most {FEED_PAGE_LIMIT} products")

        for position, feed_product in enumerate(products):
            if not isinstance(feed_product, dict):
                raise ValueError(f"feed product {page}:{position} must be an object")
            product_id = _feed_product_id(feed_product.get("id"), page, position)
            if product_id in feed_by_id:
                raise ValueError(f"duplicate feed product ID: {product_id}")
            images = feed_product.get("images")
            variants = feed_product.get("variants")
            if not isinstance(images, list) or not images:
                raise ValueError(f"feed product {product_id} must contain a non-empty images list")
            if not isinstance(variants, list):
                raise ValueError(f"feed product {product_id} must contain a variants list")

            unique_urls: list[str] = []
            seen_urls: set[str] = set()
            for image_position, image in enumerate(images):
                image_url = image.get("src") if isinstance(image, dict) else None
                if not isinstance(image_url, str) or not _is_public_http_url(image_url):
                    raise ValueError(
                        f"feed product {product_id} has an invalid image at position {image_position}"
                    )
                if image_url not in seen_urls:
                    seen_urls.add(image_url)
                    unique_urls.append(image_url)
            feed_by_id[product_id] = tuple(unique_urls)
            feed_product_count += 1
            variant_count += len(variants)

        if len(products) < FEED_PAGE_LIMIT:
            break
        if page == MAX_FEED_PAGES:
            raise ValueError(f"feed pagination exceeded {MAX_FEED_PAGES} pages")

    missing_ids = sorted(set(canonical_by_id) - set(feed_by_id))
    if missing_ids:
        preview = ", ".join(missing_ids[:10])
        raise ValueError(f"missing canonical product IDs in gallery feed: {preview}")

    references = tuple(
        ReferenceRecord(product_id, canonical_by_id[product_id].product_name, image_url, None, None)
        for product_id in canonical_by_id
        for image_url in feed_by_id[product_id]
    )
    if not references:
        raise ValueError("gallery feed did not produce any canonical references")
    return GalleryCatalog(
        references=references,
        page_sha256=tuple(page_hashes),
        feed_product_count=feed_product_count,
        variant_count=variant_count,
    )


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
        except ImageSafetyError as exc:
            failures.append(f"{record.product_id}: {exc}")
        except Exception as exc:
            failures.append(f"{record.product_id}: {exc}")

    return cached, failures


def _is_public_http_url(value: str) -> bool:
    try:
        _validated_catalog_url(value)
    except ValueError:
        return False
    return True


def _validated_catalog_url(value: str):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid catalog image URL") from exc
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or hostname != _ALLOWED_CATALOG_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("catalog image URL must use exact https://cdn.shopify.com host")
    return parsed


def _validated_feed_url(value: str):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid gallery feed URL") from exc
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or hostname != _ALLOWED_FEED_HOST
        or parsed.path != _ALLOWED_FEED_PATH
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("gallery feed must use exact https://decormoments.com/products.json")
    return parsed


def _require_global_addresses(hostname: str, resolver: Callable[..., list[tuple]]) -> None:
    addresses = resolver(hostname, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("gallery feed host did not resolve")
    for result in addresses:
        try:
            address = ipaddress.ip_address(result[4][0])
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("gallery feed host returned an invalid address") from exc
        if not address.is_global or address.is_multicast:
            raise ValueError("gallery feed host must resolve only to globally routable addresses")


def _feed_product_id(value: object, page: int, position: int) -> str:
    if isinstance(value, bool):
        raise ValueError(f"feed product {page}:{position} has an invalid ID")
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value and value == value.strip():
        return value
    raise ValueError(f"feed product {page}:{position} has an invalid ID")


def _image_suffix(image_url: str, payload: bytes) -> str:
    metadata = inspect_safe_image(payload, verify=True)
    url_suffix = Path(urlsplit(image_url).path).suffix.lower()
    if url_suffix in _FORMAT_SUFFIXES.values():
        return url_suffix
    return _FORMAT_SUFFIXES[metadata.image_format]


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
