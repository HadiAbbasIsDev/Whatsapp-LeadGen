from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from decor_matcher.catalog import (
    MAX_FEED_PAGE_BYTES,
    cache_references,
    fetch_bytes,
    fetch_gallery_page,
    load_catalog,
    load_gallery_references,
    select_references,
)
from decor_matcher.types import CatalogItem, ReferenceRecord


def product(product_id: str, image_url: str) -> CatalogItem:
    return CatalogItem(product_id, f"Product {product_id}", image_url)


def make_image_bytes(image_format="JPEG", size=(1, 1)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color="white").save(buffer, format=image_format)
    return buffer.getvalue()


def make_jpeg_bytes() -> bytes:
    return make_image_bytes()


def feed_payload(products) -> bytes:
    import json

    return json.dumps({"products": products}, separators=(",", ":")).encode()


def feed_product(product_id, images, *, variants=()):
    return {
        "id": product_id,
        "title": f"Feed product {product_id}",
        "images": [{"src": image_url} for image_url in images],
        "variants": [{"id": variant_id} for variant_id in variants],
    }


def test_select_references_is_deterministic_and_exactly_100(tmp_path):
    products = [product(str(i), f"https://img/{i}.jpg") for i in range(125)]
    selected = select_references(products, limit=100)
    expected = sorted(products, key=lambda p: sha256(p.product_id.encode()).hexdigest())[:100]
    assert [r.product_id for r in selected] == [p.product_id for p in expected]


def test_select_references_skips_missing_and_duplicate_images():
    products = [product("1", ""), product("2", "https://img/a.jpg"), product("2", "https://img/a.jpg")]
    assert [r.product_id for r in select_references(products, limit=100)] == ["2"]


def test_cache_references_validates_decodable_images(tmp_path):
    records = [ReferenceRecord("2", "Chair", "https://img/a.jpg", None, None)]
    cached, failures = cache_references(records, tmp_path, lambda _: make_jpeg_bytes())
    assert len(cached) == 1
    assert cached[0].cache_path.exists()
    assert cached[0].sha256 == sha256(make_jpeg_bytes()).hexdigest()
    assert failures == []


def test_load_catalog_reads_the_catalog_schema(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        '{"catalog": [{"id": "42", "name": "Lamp", "image": "https://cdn.shopify.com/lamp.jpg"}]}',
        encoding="utf-8",
    )

    assert load_catalog(path) == [CatalogItem("42", "Lamp", "https://cdn.shopify.com/lamp.jpg")]


@pytest.mark.parametrize(
    "entry",
    [
        {"id": 42, "name": "Lamp", "image": "https://cdn.example/lamp.jpg"},
        {"id": "42", "name": 7, "image": "https://cdn.example/lamp.jpg"},
        {"id": "42", "name": "Lamp", "image": "file:///tmp/lamp.jpg"},
        {"id": "42", "name": "Lamp", "image": "https://localhost/lamp.jpg"},
    ],
)
def test_load_catalog_rejects_invalid_catalog_items(tmp_path, entry):
    path = tmp_path / "catalog.json"
    import json

    path.write_text(json.dumps({"catalog": [entry]}), encoding="utf-8")

    with pytest.raises(ValueError):
        load_catalog(path)


@pytest.mark.parametrize("host", ["100.64.0.1", "224.0.0.1"])
def test_load_catalog_rejects_cgnat_and_multicast_image_urls(tmp_path, host):
    path = tmp_path / "catalog.json"
    path.write_text(
        f'{{"catalog": [{{"id": "42", "name": "Lamp", "image": "https://{host}/lamp.jpg"}}]}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_catalog(path)


def test_load_catalog_rejects_non_shopify_https_host(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        '{"catalog": [{"id": "42", "name": "Lamp", "image": "https://images.example/lamp.jpg"}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_catalog(path)


def test_fetch_bytes_requires_every_resolved_shopify_address_to_be_global():
    opened = False

    class NeverOpener:
        def open(self, *_args, **_kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("unsafe host must not be opened")

    def resolver(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("151.101.1.124", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]

    with pytest.raises(ValueError, match="globally routable"):
        fetch_bytes(
            "https://cdn.shopify.com/image.jpg",
            resolver=resolver,
            opener=NeverOpener(),
        )

    assert opened is False


def test_fetch_bytes_revalidates_exact_host_when_called_directly():
    class NeverOpener:
        def open(self, *_args, **_kwargs):
            raise AssertionError("non-allowlisted URL must not be opened")

    with pytest.raises(ValueError, match="exact https://cdn.shopify.com"):
        fetch_bytes(
            "https://127.0.0.1/image.jpg",
            resolver=lambda *_args, **_kwargs: pytest.fail("disallowed host must not resolve"),
            opener=NeverOpener(),
        )


def test_fetch_bytes_uses_redirect_refusing_opener(monkeypatch):
    handlers = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            assert limit == 20 * 1024 * 1024 + 1
            return b"image"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://cdn.shopify.com/image.jpg"
            assert timeout == 20
            return Response()

    def fake_build_opener(*received_handlers):
        handlers.extend(received_handlers)
        return Opener()

    monkeypatch.setattr("decor_matcher.catalog.build_opener", fake_build_opener)
    resolver = lambda *_args, **_kwargs: [(2, 1, 6, "", ("151.101.1.124", 443))]

    assert fetch_bytes("https://cdn.shopify.com/image.jpg", resolver=resolver) == b"image"
    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, "Found", {}, "https://cdn.shopify.com/other") is None


def test_cache_references_continues_after_invalid_image(tmp_path):
    records = [
        ReferenceRecord("bad", "Broken", "https://img/bad.jpg", None, None),
        ReferenceRecord("good", "Chair", "https://img/good.jpg", None, None),
    ]

    cached, failures = cache_references(
        records,
        tmp_path,
        lambda url: b"not an image" if "bad" in url else make_jpeg_bytes(),
    )

    assert [record.product_id for record in cached] == ["good"]
    assert failures == ["bad: cannot identify image file"]


def test_cache_references_continues_after_fetcher_failure(tmp_path):
    records = [
        ReferenceRecord("unavailable", "Unavailable", "https://img/unavailable.jpg", None, None),
        ReferenceRecord("good", "Chair", "https://img/good.jpg", None, None),
    ]

    def fetch(url: str) -> bytes:
        if "unavailable" in url:
            raise RuntimeError("network unavailable")
        return make_jpeg_bytes()

    cached, failures = cache_references(records, tmp_path, fetch)

    assert [record.product_id for record in cached] == ["good"]
    assert failures == ["unavailable: network unavailable"]


@pytest.mark.parametrize(
    ("payload", "failure_fragment"),
    [
        (make_image_bytes("TIFF", (40, 30)), "unsupported image format"),
        (make_image_bytes("PNG", (8192, 1)), "unsafe image dimensions"),
    ],
    ids=["tiff", "8192x1"],
)
def test_cache_references_rejects_unsupported_or_dangerous_images_before_persistence(
    tmp_path,
    payload,
    failure_fragment,
):
    records = [ReferenceRecord("unsafe", "Unsafe", "https://cdn.shopify.com/unsafe", None, None)]

    cached, failures = cache_references(records, tmp_path, lambda _url: payload)

    assert cached == []
    assert len(failures) == 1
    assert failure_fragment in failures[0]
    assert list(tmp_path.iterdir()) == []


def test_gallery_feed_joins_exact_ids_and_deduplicates_urls_within_product():
    canonical = [
        product("42", "https://cdn.shopify.com/primary-42.jpg"),
        product("43", "https://cdn.shopify.com/primary-43.jpg"),
    ]
    pages = {
        1: feed_payload(
            [
                feed_product(
                    42,
                    [
                        "https://cdn.shopify.com/42-primary.jpg",
                        "https://cdn.shopify.com/42-primary.jpg",
                        "https://cdn.shopify.com/42-room.jpg",
                    ],
                    variants=(1, 2),
                ),
                feed_product("43", ["https://cdn.shopify.com/43-primary.jpg"], variants=(3,)),
            ]
        )
    }
    calls = []

    def fetch_page(_url, page, limit):
        calls.append((page, limit))
        return pages[page]

    gallery = load_gallery_references(
        canonical,
        "https://decormoments.com/products.json",
        fetch_page=fetch_page,
    )

    assert calls == [(1, 250)]
    assert [(record.product_id, record.product_name, record.image_url) for record in gallery.references] == [
        ("42", "Product 42", "https://cdn.shopify.com/42-primary.jpg"),
        ("42", "Product 42", "https://cdn.shopify.com/42-room.jpg"),
        ("43", "Product 43", "https://cdn.shopify.com/43-primary.jpg"),
    ]
    assert gallery.feed_product_count == 2
    assert gallery.variant_count == 3
    assert gallery.page_sha256 == (sha256(pages[1]).hexdigest(),)


def test_gallery_feed_requires_every_canonical_id_without_name_fallback():
    canonical = [product("042", "https://cdn.shopify.com/primary.jpg")]
    payload = feed_payload([feed_product(42, ["https://cdn.shopify.com/feed.jpg"])])

    with pytest.raises(ValueError, match="missing canonical product IDs"):
        load_gallery_references(
            canonical,
            "https://decormoments.com/products.json",
            fetch_page=lambda *_args: payload,
        )


def test_gallery_feed_rejects_duplicate_ids_across_pages():
    canonical = [product("42", "https://cdn.shopify.com/primary.jpg")]
    first_page = [
        feed_product(index, [f"https://cdn.shopify.com/{index}.jpg"])
        for index in range(1000, 1249)
    ] + [feed_product(42, ["https://cdn.shopify.com/42-a.jpg"])]
    second_page = feed_payload([feed_product(42, ["https://cdn.shopify.com/42-b.jpg"])])

    with pytest.raises(ValueError, match="duplicate feed product ID"):
        load_gallery_references(
            canonical,
            "https://decormoments.com/products.json",
            fetch_page=lambda _url, page, _limit: feed_payload(first_page) if page == 1 else second_page,
        )


@pytest.mark.parametrize(
    "bad_product",
    [
        {"id": 42, "title": "Missing images", "variants": []},
        {"id": 42, "title": "Empty images", "images": [], "variants": []},
        {"id": 42, "title": "Bad image", "images": [{}], "variants": []},
        {"id": 42, "title": "Bad variants", "images": [{"src": "https://cdn.shopify.com/42.jpg"}]},
    ],
)
def test_gallery_feed_rejects_incomplete_images_or_variants(bad_product):
    with pytest.raises(ValueError, match="feed product"):
        load_gallery_references(
            [product("42", "https://cdn.shopify.com/primary.jpg")],
            "https://decormoments.com/products.json",
            fetch_page=lambda *_args: feed_payload([bad_product]),
        )


def test_fetch_gallery_page_enforces_exact_host_global_dns_no_redirects_and_json_cap(monkeypatch):
    handlers = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            assert limit == MAX_FEED_PAGE_BYTES + 1
            return b'{"products":[]}'

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://decormoments.com/products.json?limit=250&page=3"
            assert timeout == 20
            return Response()

    def fake_build_opener(*received_handlers):
        handlers.extend(received_handlers)
        return Opener()

    monkeypatch.setattr("decor_matcher.catalog.build_opener", fake_build_opener)
    resolver = lambda *_args, **_kwargs: [(2, 1, 6, "", ("23.227.38.65", 443))]

    assert fetch_gallery_page(
        "https://decormoments.com/products.json", 3, 250, resolver=resolver
    ) == b'{"products":[]}'
    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, "Found", {}, "https://example.com") is None

    with pytest.raises(ValueError, match="exact https://decormoments.com/products.json"):
        fetch_gallery_page(
            "https://evil.example/products.json",
            1,
            250,
            resolver=lambda *_args, **_kwargs: pytest.fail("invalid host must not resolve"),
        )

    with pytest.raises(ValueError, match="globally routable"):
        fetch_gallery_page(
            "https://decormoments.com/products.json",
            1,
            250,
            resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
            opener=Opener(),
        )


def test_fetch_gallery_page_rejects_payload_over_explicit_json_cap():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            assert limit == MAX_FEED_PAGE_BYTES + 1
            return b"x" * limit

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 20
            return Response()

    with pytest.raises(ValueError, match="exceeds"):
        fetch_gallery_page(
            "https://decormoments.com/products.json",
            1,
            250,
            resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("23.227.38.65", 443))],
            opener=Opener(),
        )


def test_gallery_feed_fails_closed_when_twenty_pages_are_still_full():
    canonical = [product("1", "https://cdn.shopify.com/primary.jpg")]

    def fetch_page(_url, page, _limit):
        products = [
            feed_product(
                page * 10_000 + position,
                [f"https://cdn.shopify.com/{page}-{position}.jpg"],
            )
            for position in range(250)
        ]
        if page == 1:
            products[0]["id"] = 1
        return feed_payload(products)

    with pytest.raises(ValueError, match="exceeded 20 pages"):
        load_gallery_references(
            canonical,
            "https://decormoments.com/products.json",
            fetch_page=fetch_page,
        )
