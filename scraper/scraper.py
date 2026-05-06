import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ProductInfo:
    url: str
    name: str
    price: str
    status: str = "Success"
    retries: int = 0


@dataclass
class SearchResult:
    model: str
    storage: str
    min_price: Optional[float]
    variant_prices: list[float] = field(default_factory=list)
    status: str = "Success"


# ── Price parsing ─────────────────────────────────────────────────────────────

def parse_romanian_price(text: str) -> Optional[float]:
    """
    Convert eMAG price strings to float.
    Handles formats like: '1.299,99 Lei', '810 Lei', '6.499,99 Lei'
    Romanian convention: '.' = thousands separator, ',' = decimal separator.
    """
    # Keep only digits, dots, and commas
    cleaned = re.sub(r"[^\d.,]", "", text.strip())
    if not cleaned:
        return None
    # Remove thousands dots, swap decimal comma
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


# ── Name normalisation ────────────────────────────────────────────────────────

def normalize_model(model: str) -> str:
    """
    Produce a clean search query from a Deloitte model name.
    'Samsung A26'   → 'Samsung Galaxy A26 128GB'
    'S26 PLUS'      → 'Samsung Galaxy S26 Plus'
    'S26 Ultra'     → 'Samsung Galaxy S26 Ultra'
    """
    m = model.strip()
    # Ensure 'Samsung' prefix
    if not m.lower().startswith("samsung"):
        m = "Samsung " + m
    # Ensure 'Galaxy' infix after 'Samsung'
    if "galaxy" not in m.lower():
        m = m.replace("Samsung ", "Samsung Galaxy ", 1)
    # Title-case PLUS / ULTRA
    m = re.sub(r"\bPLUS\b", "Plus", m)
    m = re.sub(r"\bULTRA\b", "Ultra", m)
    return m


def model_keywords(model: str) -> list[str]:
    """
    Return lowercase keywords that a search result title MUST contain
    to count as a match for this model.
    """
    norm = normalize_model(model).lower()
    # Extract the core identifiers: model family + variant letters/numbers
    # e.g. ['samsung', 'galaxy', 's26', 'ultra']
    tokens = re.findall(r"[a-z0-9]+", norm)
    # Filter out very generic words
    skip = {"samsung", "galaxy", "mobil", "telefon"}
    return [t for t in tokens if t not in skip]


# ── Crawler ───────────────────────────────────────────────────────────────────

class EmagCrawler:
    """
    Async crawler for eMAG.
    Supports:
      - Single product page scraping  (get_price)
      - Search-based min-price lookup  (search_min_price)
      - Batch JSON update              (update_json_file)
    """

    # ── Selectors ─────────────────────────────────────────────────────
    TITLE_SELECTORS = ["h1"]
    PRICE_SELECTORS = ["p.product-new-price", ".product-new-price"]

    # Search-results page selectors
    SEARCH_CARD_SELECTOR   = ".card-v2"           # individual product card
    SEARCH_TITLE_SELECTOR  = ".card-v2-title"     # product name inside card
    SEARCH_PRICE_SELECTOR  = "p.product-new-price"

    COOKIE_SELECTORS = [
        "button.js-accept", "#cookie-accept",
        "button[id*='cookie']", "button[class*='cookie']",
    ]
    # ──────────────────────────────────────────────────────────────────

    def __init__(
        self,
        headless: bool = True,
        max_retries: int = 2,
        retry_delay: float = 2.0,
        timeout_ms: int = 10_000,
        concurrency: int = 3,
    ):
        self.headless = headless
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout_ms = timeout_ms
        self.concurrency = concurrency

    # ── Browser factory ───────────────────────────────────────────────

    async def _make_context(self, playwright):
        browser: Browser = await playwright.chromium.launch(headless=self.headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="ro-RO",
            timezone_id="Europe/Bucharest",
            extra_http_headers={"Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7"},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return browser, context

    # ── Helpers ───────────────────────────────────────────────────────

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        for sel in self.COOKIE_SELECTORS:
            try:
                if await page.is_visible(sel, timeout=2000):
                    await page.click(sel)
                    return
            except Exception:
                continue

    async def _resolve_first(self, page: Page, selectors: list[str]) -> Optional[str]:
        for sel in selectors:
            try:
                await page.wait_for_selector(sel, timeout=self.timeout_ms)
                text = await page.inner_text(sel)
                if text.strip():
                    return text.strip()
            except Exception:
                continue
        return None

    # ── Single product page ───────────────────────────────────────────

    async def get_price(self, url: str) -> ProductInfo:
        async with async_playwright() as p:
            browser, context = await self._make_context(p)
            page = await context.new_page()
            last_error: Exception = Exception("Unknown error")

            for attempt in range(1, self.max_retries + 1):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    await asyncio.sleep(3)
                    await self._dismiss_cookie_banner(page)

                    name  = await self._resolve_first(page, self.TITLE_SELECTORS)
                    price = await self._resolve_first(page, self.PRICE_SELECTORS)

                    if not name or not price:
                        missing = [l for l, v in (("title", name), ("price", price)) if not v]
                        raise ValueError(f"Could not resolve: {', '.join(missing)}")

                    result = ProductInfo(
                        url=url, name=name,
                        price=price.replace("\n", " ").strip(),
                        retries=attempt - 1,
                    )
                    logger.info("✓ %s | %s", result.name, result.price)
                    await browser.close()
                    return result

                except Exception as e:
                    last_error = e
                    logger.warning("Attempt %d/%d failed: %s", attempt, self.max_retries, e)
                    if attempt < self.max_retries:
                        await asyncio.sleep(self.retry_delay * attempt)

            await browser.close()
            return ProductInfo(url=url, name="N/A", price="N/A",
                               status=f"Error: {last_error}", retries=self.max_retries)

    # ── Search → min price ────────────────────────────────────────────

    async def search_min_price(
        self, model: str, storage: str, page: Page
    ) -> SearchResult:
        """
        Search eMAG for '{normalised model} {storage}', collect every price card
        whose title matches the model keywords, and return the minimum price.

        Runs inside a shared Page so the caller can reuse one browser session
        across multiple searches.
        """
        query_str = f"{normalize_model(model)} {storage}"
        search_url = (
            "https://www.emag.ro/search/"
            + query_str.replace(" ", "+")
            + "/"
        )
        keywords = model_keywords(model)
        logger.info("Searching: %s  →  %s", query_str, search_url)

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(3)
            await self._dismiss_cookie_banner(page)

            # Wait for at least one product card
            try:
                await page.wait_for_selector(self.SEARCH_CARD_SELECTOR, timeout=self.timeout_ms)
            except Exception:
                return SearchResult(model=model, storage=storage, min_price=None,
                                    status="Error: no product cards found")

            cards = await page.query_selector_all(self.SEARCH_CARD_SELECTOR)
            logger.info("  Found %d cards on search page", len(cards))

            variant_prices: list[float] = []

            for card in cards:
                # ── Get card title ─────────────────────────────────
                try:
                    title_el = await card.query_selector(self.SEARCH_TITLE_SELECTOR)
                    title = (await title_el.inner_text()).strip().lower() if title_el else ""
                except Exception:
                    title = ""

                # ── Filter: must contain all model keywords ────────
                if not all(kw in title for kw in keywords):
                    continue

                # ── Also check storage if present in title ─────────
                storage_num = re.sub(r"[^0-9]", "", storage)   # '128GB' → '128'
                if storage_num and storage_num not in title.replace(" ", ""):
                    continue

                # ── Get price from card ────────────────────────────
                try:
                    price_el = await card.query_selector(self.SEARCH_PRICE_SELECTOR)
                    price_text = (await price_el.inner_text()).strip() if price_el else ""
                    price_val = parse_romanian_price(price_text)
                    if price_val is not None:
                        variant_prices.append(price_val)
                        logger.debug("    Match: %s  →  %.2f Lei", title[:60], price_val)
                except Exception:
                    continue

            if not variant_prices:
                logger.warning("  No matching variants found for: %s %s", model, storage)
                return SearchResult(model=model, storage=storage, min_price=None,
                                    status="Error: no matching variants")

            min_p = min(variant_prices)
            logger.info(
                "  ✓ %s %s — %d variants, min price: %.2f Lei",
                model, storage, len(variant_prices), min_p
            )
            return SearchResult(
                model=model, storage=storage,
                min_price=min_p, variant_prices=sorted(variant_prices),
            )

        except Exception as e:
            logger.error("Search failed for %s %s: %s", model, storage, e)
            return SearchResult(model=model, storage=storage, min_price=None,
                                status=f"Error: {e}")

    # ── JSON file updater ─────────────────────────────────────────────

    async def update_json_file(self, json_path: str | Path) -> dict:
        """
        Read data.json, search eMAG for each product in deloitteData,
        update emagData with live min prices, and write back to disk.

        Returns the updated data dict.
        """
        path = Path(json_path)
        data = json.loads(path.read_text(encoding="utf-8"))

        deloitte_products = data.get("deloitteData", [])
        emag_data: list[dict] = data.get("emagData", [])

        # Build a lookup: normalised model+storage → emag entry index
        def emag_key(model_str: str, storage_str: str) -> str:
            return normalize_model(model_str).lower() + "|" + storage_str.lower()

        emag_index: dict[str, int] = {}
        for i, entry in enumerate(emag_data):
            k = emag_key(entry.get("Model", ""), entry.get("Storage", ""))
            emag_index[k] = i

        async with async_playwright() as p:
            browser, context = await self._make_context(p)
            page = await context.new_page()

            for product in deloitte_products:
                model   = product["Model"]
                storage = product["Storage"]

                result = await self.search_min_price(model, storage, page)
                await asyncio.sleep(1.5)   # polite delay between searches

                # Find the matching emagData entry (or create one)
                k = emag_key(normalize_model(model), storage)
                if k in emag_index:
                    idx = emag_index[k]
                    if result.min_price is not None:
                        emag_data[idx]["eMAG_Price"] = round(result.min_price, 2)
                        emag_data[idx]["eMAG_Variants"] = [round(v, 2) for v in result.variant_prices]
                        emag_data[idx]["eMAG_Price_Status"] = "live"
                    else:
                        emag_data[idx]["eMAG_Price_Status"] = result.status
                else:
                    # New entry — not previously in emagData
                    new_entry: dict = {
                        "Model":   normalize_model(model),
                        "Storage": storage,
                    }
                    if result.min_price is not None:
                        new_entry["eMAG_Price"]    = round(result.min_price, 2)
                        new_entry["eMAG_Variants"] = [round(v, 2) for v in result.variant_prices]
                        new_entry["eMAG_Price_Status"] = "live"
                    else:
                        new_entry["eMAG_Price_Status"] = result.status
                    emag_data.append(new_entry)
                    emag_index[k] = len(emag_data) - 1

            await browser.close()

        data["emagData"] = emag_data
        path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
        logger.info("✓ Written updated prices to %s", path)
        return data

    # ── Batch (original API kept intact) ─────────────────────────────

    async def get_prices_batch(self, urls: list[str]) -> list[ProductInfo]:
        semaphore = asyncio.Semaphore(self.concurrency)
        async def bounded(url: str) -> ProductInfo:
            async with semaphore:
                return await self.get_price(url)
        return await asyncio.gather(*[bounded(u) for u in urls])


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main():
    import sys
    crawler = EmagCrawler(headless=True, max_retries=2, timeout_ms=10_000)

    # If a json path is passed, run the full update
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data.json"
    if json_path.exists():
        print(f"\nUpdating prices from: {json_path}")
        updated = await crawler.update_json_file(json_path)
        print("\n── Updated emagData ──")
        for entry in updated["emagData"]:
            status = entry.get("eMAG_Price_Status", "?")
            price  = entry.get("eMAG_Price", "N/A")
            variants = entry.get("eMAG_Variants", [])
            variant_str = f"  variants: {variants}" if variants else ""
            print(f"  {entry['Model']} {entry['Storage']}: {price} Lei  [{status}]{variant_str}")
    else:
        # Fallback: single product scrape demo
        url = "https://www.emag.ro/telefon-mobil-apple-iphone-15-128gb-5g-black-mtp03rx-a/pd/DZ4H93YBM/"
        result = await crawler.get_price(url)
        print(f"Product : {result.name}\nPrice   : {result.price}\nStatus  : {result.status}")


if __name__ == "__main__":
    asyncio.run(main())