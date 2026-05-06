import json
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from scraper import EmagCrawler, ProductInfo, SearchResult, parse_romanian_price, normalize_model, model_keywords


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def crawler():
    return EmagCrawler(headless=True, max_retries=2, timeout_ms=10_000)


@pytest.fixture
def sample_json(tmp_path) -> Path:
    data = {
        "subsidy": 271.0,
        "deloitteData": [
            {"Model": "Samsung A26", "Storage": "128GB", "Deloitte_Price": 220.14},
            {"Model": "S26 Ultra",   "Storage": "512GB", "Deloitte_Price": 1362.48},
        ],
        "emagData": [
            {"Model": "Samsung Galaxy A26", "Storage": "128GB", "eMAG_Price": 199.99, "eMAG_Rating": 4.2},
        ]
    }
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data, indent=2))
    return p


# ── Unit tests: price parser ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("1.299,99 Lei",  1299.99),
    ("810 Lei",        810.0),
    ("6.499,99 Lei",  6499.99),
    ("  999,00 Lei ",  999.0),
    ("no price here",  None),
    ("",               None),
])
def test_parse_romanian_price(text, expected):
    assert parse_romanian_price(text) == expected


# ── Unit tests: model normalisation ──────────────────────────────────────────

@pytest.mark.parametrize("model,expected_substr", [
    ("Samsung A26",   "Samsung Galaxy A26"),
    ("S26 PLUS",      "Samsung Galaxy S26 Plus"),
    ("S26 Ultra",     "Samsung Galaxy S26 Ultra"),
    ("Samsung S25 FE","Samsung Galaxy S25 FE"),
])
def test_normalize_model(model, expected_substr):
    assert normalize_model(model) == expected_substr


def test_model_keywords_excludes_generic():
    kws = model_keywords("Samsung S26")
    assert "s26" in kws
    assert "samsung" not in kws
    assert "galaxy" not in kws


def test_model_keywords_ultra():
    kws = model_keywords("S26 Ultra")
    assert "s26" in kws
    assert "ultra" in kws


# ── Unit tests: ProductInfo / SearchResult defaults ───────────────────────────

def test_product_info_defaults():
    p = ProductInfo(url="http://x.com", name="Test", price="100 Lei")
    assert p.status == "Success"
    assert p.retries == 0


def test_search_result_defaults():
    r = SearchResult(model="Samsung S26", storage="256GB", min_price=810.0,
                     variant_prices=[810.0, 850.0, 890.0])
    assert r.status == "Success"
    assert r.min_price == 810.0
    assert len(r.variant_prices) == 3


def test_search_result_no_price():
    r = SearchResult(model="Samsung X", storage="128GB", min_price=None,
                     status="Error: no matching variants")
    assert r.min_price is None
    assert "Error" in r.status


# ── Integration tests: live scrape ────────────────────────────────────────────

LIVE_URL = "https://www.emag.ro/telefon-mobil-apple-iphone-15-128gb-5g-black-mtp03rx-a/pd/DZ4H93YBM/"

@pytest.mark.asyncio
async def test_successful_extraction(crawler):
    result = await crawler.get_price(LIVE_URL)
    assert result.status == "Success", f"Unexpected: {result.status}"
    assert result.name != "N/A"
    assert result.price != "N/A"
    assert len(result.name) > 3
    assert any(kw in result.price for kw in ("Lei", "lei", "RON"))


@pytest.mark.asyncio
async def test_product_info_fields_populated(crawler):
    result = await crawler.get_price(LIVE_URL)
    assert result.url == LIVE_URL
    assert isinstance(result.retries, int)


@pytest.mark.asyncio
async def test_invalid_url_handling(crawler):
    result = await crawler.get_price("https://www.emag.ro/non-existent-product-12345")
    assert result.name == "N/A"
    assert "Error" in result.status


@pytest.mark.asyncio
async def test_retry_count_on_failure(crawler):
    result = await crawler.get_price("https://www.emag.ro/non-existent-99999")
    assert result.retries == crawler.max_retries


# ── Integration tests: search ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_returns_min_price(crawler):
    """Search for Samsung Galaxy S26 256GB and confirm a numeric min price."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser, context = await crawler._make_context(p)
        page = await context.new_page()
        result = await crawler.search_min_price("Samsung S26", "256GB", page)
        await browser.close()

    assert result.status == "Success", f"Search failed: {result.status}"
    assert result.min_price is not None
    assert result.min_price > 0
    assert len(result.variant_prices) >= 1
    assert result.min_price == min(result.variant_prices)


@pytest.mark.asyncio
async def test_search_min_is_lowest_variant(crawler):
    """min_price must equal min(variant_prices)."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser, context = await crawler._make_context(p)
        page = await context.new_page()
        result = await crawler.search_min_price("Samsung Galaxy S25", "128GB", page)
        await browser.close()

    if result.min_price is not None:
        assert result.min_price == min(result.variant_prices)


# ── Integration tests: JSON update ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_json_writes_prices(crawler, sample_json):
    """After update_json_file, all deloitteData entries should have a live eMAG price."""
    updated = await crawler.update_json_file(sample_json)

    emag = {e["Model"].lower() + "|" + e["Storage"].lower(): e for e in updated["emagData"]}

    for product in updated["deloitteData"]:
        from scraper import normalize_model
        key = normalize_model(product["Model"]).lower() + "|" + product["Storage"].lower()
        assert key in emag, f"Missing emagData entry for {product['Model']}"
        entry = emag[key]
        if entry.get("eMAG_Price_Status") == "live":
            assert isinstance(entry["eMAG_Price"], float)
            assert entry["eMAG_Price"] > 0


@pytest.mark.asyncio
async def test_update_json_adds_variant_list(crawler, sample_json):
    """Successful searches must add an eMAG_Variants list."""
    updated = await crawler.update_json_file(sample_json)
    for entry in updated["emagData"]:
        if entry.get("eMAG_Price_Status") == "live":
            assert "eMAG_Variants" in entry
            assert isinstance(entry["eMAG_Variants"], list)


@pytest.mark.asyncio
async def test_update_json_persists_to_disk(crawler, sample_json):
    """The file on disk must be updated (not just the in-memory dict)."""
    await crawler.update_json_file(sample_json)
    on_disk = json.loads(sample_json.read_text())
    assert "emagData" in on_disk
    # Subsidy must be preserved
    assert on_disk["subsidy"] == 271.0


# ── Batch tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_returns_correct_count(crawler):
    urls = [LIVE_URL, "https://www.emag.ro/non-existent-batch"]
    results = await crawler.get_prices_batch(urls)
    assert len(results) == 2
    assert all(isinstance(r, ProductInfo) for r in results)


@pytest.mark.asyncio
async def test_batch_preserves_order(crawler):
    urls = ["https://www.emag.ro/non-a", "https://www.emag.ro/non-b"]
    results = await crawler.get_prices_batch(urls)
    for url, result in zip(urls, results):
        assert result.url == url