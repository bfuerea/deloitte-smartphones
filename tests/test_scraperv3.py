"""
test_scraperv3.py
Tests for EmagCrawlerV3 and FilterCatalog (v3).
Mirrors test_scraperv2.py — both share pytest.ini (asyncio_mode=auto).
"""

import json
import uuid
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import async_playwright

from scraper.scraperv3 import (
    EmagCrawlerV3,
    FilterCatalog,
    _normalize_filter_name,
    _normalize_storage,
)
from scraper import SearchResult, normalize_model


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def catalog_with_data(tmp_path) -> FilterCatalog:
    """FilterCatalog pre-loaded with realistic slugs (no network needed)."""
    data = {
        "models": {
            "galaxy a26":       "model-f9396,galaxy-a26-v-13568309",
            "galaxy a37":       "model-f9396,galaxy-a37-v-11000002",
            "galaxy a57":       "model-f9396,galaxy-a57-v-11000003",
            "galaxy s25":       "model-f9396,galaxy-s25-v-13515752",
            "galaxy s25 fe":    "model-f9396,galaxy-s25-fe-v-11000005",
            "galaxy s26":       "model-f9396,galaxy-s26-v-11000006",
            "galaxy s26 plus":  "model-f9396,galaxy-s26-plus-v-14963102",
            "galaxy s26 ultra": "model-f9396,galaxy-s26-ultra-v-11000008",
        },
        "storage": {
            "128gb": "memorie-interna-f9441,128-gb-v30056",
            "256gb": "memorie-interna-f9441,256-gb-v30057",
            "512gb": "memorie-interna-f9441,512-gb-v31226",
        },
    }
    p = tmp_path / "emag_filters.json"
    p.write_text(json.dumps(data))
    cat = FilterCatalog(cache_path=p)
    cat.load()
    return cat


@pytest.fixture
def sample_json(tmp_path) -> Path:
    data = {
        "subsidy": 271.0,
        "deloitteData": [
            {"Model": "Samsung S26",      "Storage": "256GB", "Deloitte_Price": 851.21},
            {"Model": "S26 PLUS",         "Storage": "256GB", "Deloitte_Price": 1059.09},
            {"Model": "S26 Ultra",        "Storage": "512GB", "Deloitte_Price": 1362.48},
        ],
        "emagData": [],
    }
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data, indent=2))
    return p


@pytest_asyncio.fixture(scope="function")
async def crawler_v3(tmp_path):
    cache = tmp_path / "emag_filters.json"
    return EmagCrawlerV3(
        filter_cache=cache,
        headless=True,
        max_retries=1,
        timeout_ms=8_000,
    )


# ── Unit: name normalisation ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Galaxy S26+",         "galaxy s26 plus"),
    ("Galaxy S26 Ultra",    "galaxy s26 ultra"),
    ("Galaxy S26 Plus",     "galaxy s26 plus"),
    ("Galaxy A26 5G",       "galaxy a26 5g"),
    ("  Galaxy S25 FE  ",   "galaxy s25 fe"),
    ("Galaxy A06 (16)",     "galaxy a06"),
    ("Galaxy S26 Plus (10)", "galaxy s26 plus"),
])
def test_normalize_filter_name(raw, expected):
    assert _normalize_filter_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("256 GB",  "256gb"),
    ("256GB",   "256gb"),
    ("128 gb",  "128gb"),
    ("512GB",   "512gb"),
])
def test_normalize_storage(raw, expected):
    assert _normalize_storage(raw) == expected


# ── Unit: FilterCatalog slug resolution ───────────────────────────────────────

def test_catalog_exact_model_match(catalog_with_data):
    slug = catalog_with_data.get_model_slug("S26 PLUS")
    assert slug == "model-f9396,galaxy-s26-plus-v-14963102"


def test_catalog_plus_sign_model(catalog_with_data):
    slug = catalog_with_data.get_model_slug("Samsung Galaxy S26 Plus")
    assert slug is not None
    assert "s26-plus" in slug


def test_catalog_ultra_model(catalog_with_data):
    slug = catalog_with_data.get_model_slug("S26 Ultra")
    assert slug == "model-f9396,galaxy-s26-ultra-v-11000008"


def test_catalog_token_subset_matching(catalog_with_data):
    """Tests the token scoring system correctly isolates model tiers."""
    slug_plus = catalog_with_data.get_model_slug("Samsung S26 Plus 5G")
    assert slug_plus == "model-f9396,galaxy-s26-plus-v-14963102"

    slug_base = catalog_with_data.get_model_slug("Samsung Galaxy S26")
    assert slug_base == "model-f9396,galaxy-s26-v-11000006"


def test_catalog_plain_s26_does_not_match_plus(catalog_with_data):
    s26_slug  = catalog_with_data.get_model_slug("Samsung S26")
    plus_slug = catalog_with_data.get_model_slug("S26 PLUS")
    assert s26_slug != plus_slug


def test_catalog_plain_s26_does_not_match_ultra(catalog_with_data):
    s26_slug   = catalog_with_data.get_model_slug("Samsung S26")
    ultra_slug = catalog_with_data.get_model_slug("S26 Ultra")
    assert s26_slug != ultra_slug


def test_catalog_storage_exact(catalog_with_data):
    assert catalog_with_data.get_storage_slug("256GB") == "memorie-interna-f9441,256-gb-v30057"
    assert catalog_with_data.get_storage_slug("128GB") == "memorie-interna-f9441,128-gb-v30056"
    assert catalog_with_data.get_storage_slug("512GB") == "memorie-interna-f9441,512-gb-v31226"


def test_catalog_unknown_model_returns_none(catalog_with_data):
    assert catalog_with_data.get_model_slug("Nokia 3310") is None


def test_catalog_unknown_storage_returns_none(catalog_with_data):
    assert catalog_with_data.get_storage_slug("1TB") is None


# ── Unit: FilterCatalog persistence ──────────────────────────────────────────

def test_catalog_save_and_reload(tmp_path):
    cat = FilterCatalog(cache_path=tmp_path / "filters.json")
    cat._data = {
        "models":  {"galaxy s26 plus": "model-f9396,galaxy-s26-plus-v-14963102"},
        "storage": {"256gb": "memorie-interna-f9441,256-gb-v30057"},
    }
    cat.save()

    cat2 = FilterCatalog(cache_path=tmp_path / "filters.json")
    loaded = cat2.load()
    assert loaded is True
    assert cat2.get_model_slug("S26 PLUS") == "model-f9396,galaxy-s26-plus-v-14963102"
    assert cat2.get_storage_slug("256GB")   == "memorie-interna-f9441,256-gb-v30057"


def test_catalog_missing_file_returns_false(tmp_path):
    cat = FilterCatalog(cache_path=tmp_path / "nonexistent.json")
    assert cat.load() is False
    assert cat.is_empty() is True


# ── Unit: URL builder ─────────────────────────────────────────────────────────

def test_build_filtered_url_contains_full_slugs(crawler_v3):
    url = crawler_v3._build_filtered_url(
        "model-f9396,galaxy-s26-plus-v-14963102",
        "memorie-interna-f9441,256-gb-v30057"
    )
    assert "model-f9396,galaxy-s26-plus-v-14963102" in url
    assert "memorie-interna-f9441,256-gb-v30057" in url
    assert "sort-priceasc" in url
    assert url.startswith("https://www.emag.ro/telefoane-mobile/brand/samsung")


def test_build_filtered_url_does_not_strip_prefix(crawler_v3):
    """Regression: v2 stripped 'model-fXXXX,' — v3 must keep the full slug."""
    url = crawler_v3._build_filtered_url(
        "model-f9396,galaxy-a26-v-13568309",
        "memorie-interna-f9441,128-gb-v30056"
    )
    assert "model-f9396,galaxy-a26-v-13568309" in url
    assert url.startswith("https://www.emag.ro/telefoane-mobile/brand/samsung/filter/model-f9396")


# ── Integration: live filter discovery ───────────────────────────────────────

@pytest.mark.asyncio
async def test_discover_populates_catalog(tmp_path):
    """Live test: discover() should find at least some Samsung models."""
    cache = tmp_path / f"test_filters_{uuid.uuid4()}.json"
    cat = FilterCatalog(cache_path=cache)

    async with async_playwright() as p:
        crawler = EmagCrawlerV3(filter_cache=cache, headless=True)
        browser, context = await crawler._make_context(p)
        page = await context.new_page()
        try:
            await cat.discover(page)
        finally:
            await browser.close()

    if cat.is_empty():
        pytest.skip(
            "Discovery returned no models/storage. "
            "eMAG page structure may have changed — selectors may need updating."
        )

    assert len(cat._data.get("models", {})) >= 1, "Expected at least 1 Samsung model"
    assert len(cat._data.get("storage", {})) >= 1, "Expected at least 1 storage option"
    assert cache.exists(), "Catalog was not saved to disk"


# ── Integration: live filtered search ────────────────────────────────────────

@pytest.mark.asyncio
async def test_v3_search_s26_plus_price(tmp_path):
    """S26 Plus filter must not bleed into S26 Ultra results."""
    cache = tmp_path / "filters.json"
    crawler = EmagCrawlerV3(filter_cache=cache, headless=True, max_retries=1)

    async with async_playwright() as p:
        browser, context = await crawler._make_context(p)
        page = await context.new_page()
        if crawler.catalog.is_empty():
            await crawler.catalog.discover(page)
            crawler._catalog_ready = True
        result = await crawler.search_min_price("S26 PLUS", "256GB", page)
        await browser.close()

    if result.status == "Success":
        assert result.min_price is not None
        assert result.min_price < 8000, (
            f"S26 Plus min price {result.min_price} looks too high — "
            "may be picking up S26 Ultra cards"
        )
        assert result.min_price == min(result.variant_prices)
    else:
        pytest.skip(f"Search unavailable: {result.status}")


@pytest.mark.asyncio
async def test_v3_s26_and_s26_plus_prices_differ(tmp_path):
    cache = tmp_path / "filters.json"
    crawler = EmagCrawlerV3(filter_cache=cache, headless=True, max_retries=1)

    async with async_playwright() as p:
        browser, context = await crawler._make_context(p)
        page = await context.new_page()
        if crawler.catalog.is_empty():
            await crawler.catalog.discover(page)
            crawler._catalog_ready = True
        s26      = await crawler.search_min_price("Samsung S26",  "256GB", page)
        s26_plus = await crawler.search_min_price("S26 PLUS",     "256GB", page)
        await browser.close()

    if s26.status == "Success" and s26_plus.status == "Success":
        assert s26.min_price != s26_plus.min_price, (
            "S26 and S26 Plus returned the same min price — filter may not be working"
        )
        assert s26_plus.min_price > s26.min_price, (
            f"S26 Plus ({s26_plus.min_price}) should cost more than S26 ({s26.min_price})"
        )
    else:
        pytest.skip("One or both searches unavailable")


# ── Integration: JSON update ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_v3_update_json(crawler_v3, sample_json):
    updated = await crawler_v3.update_json_file(sample_json)

    emag_by_key = {
        e["Model"].lower() + "|" + e["Storage"].lower(): e
        for e in updated["emagData"]
    }
    for product in updated["deloitteData"]:
        key = normalize_model(product["Model"]).lower() + "|" + product["Storage"].lower()
        assert key in emag_by_key, f"Missing entry for {product['Model']}"


@pytest.mark.asyncio
async def test_v3_update_json_preserves_subsidy(crawler_v3, sample_json):
    updated = await crawler_v3.update_json_file(sample_json)
    assert updated["subsidy"] == 271.0
