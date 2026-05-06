
"""
scraperv2.py — eMAG filter-based price scraper
───────────────────────────────────────────────
Uses eMAG's own sidebar filters to build pre-filtered URLs instead of
keyword-matching search results. This fixes false matches like S26 Plus
cards appearing in S26 Ultra results.

Usage:
    python scraperv2.py data.json               # update data.json prices
    python scraperv2.py data.json --rediscover  # force-refresh filter catalog
    python scraperv2.py --discover-only         # only rebuild filter catalog
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import Page, async_playwright

from scraper import (
    EmagCrawler,
    ProductInfo,
    SearchResult,
    normalize_model,
    parse_romanian_price,
)

logger = logging.getLogger(__name__)

# ── Filter normalisation helpers ──────────────────────────────────────────────


def _normalize_filter_name(text: str) -> str:
    """
    Normalise an eMAG display name to a comparable lowercase key.
      'Galaxy S26+'       → 'galaxy s26 plus'
      'Galaxy S26 Ultra'  → 'galaxy s26 ultra'
      'Galaxy A26 5G'     → 'galaxy a26 5g'
    """
    t = text.lower().strip()
    # Handle "+" → " Plus" (e.g., "Galaxy S26+" → "galaxy s26 plus")
    t = t.replace("+", " Plus")
    # Also handle space + "+" → " Plus" (for consistency)
    t = t.replace(" Plus", " Plus")  # Redundant but safe
    t = re.sub(r"\s+", " ", t)  # Normalize multiple spaces
    return t.strip()


def _normalize_storage(text: str) -> str:
    """
    '256 GB', '256GB', '256 gb' → '256gb'
    Also strips parenthesised suffix counts e.g. '256 GB (173)' → '256gb'
    """
    t = text.lower().strip()
    t = re.sub(r"\s*\(.*?\)\s*", "", t)
    return re.sub(r"\s+", "", t)


class FilterCatalog:
    """
    Discovers and caches eMAG filter slugs for Samsung phones.

    Catalog JSON on disk:
    {
        "models":  { "galaxy s26 plus": "model-f9396,galaxy-s26-plus-v-14963102", ... },
        "storage": { "256gb":           "memorie-interna-f9441,256-gb-v30057",    ... }
    }
    """

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self._data = {"models": {}, "storage": {}}
        self._load()

    def _load(self) -> bool:
        """Load filter catalog from disk cache."""
        if self.cache_path.exists():
            try:
                self._data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                logger.info(
                    "Filter catalog loaded: %d models, %d storage options",
                    len(self._data.get("models", {})),
                    len(self._data.get("storage", {})),
                )
                return True
            except Exception as e:
                logger.warning("Could not load filter catalog: %s", e)
        return False

    def load(self) -> bool:
        """Public interface to load filter catalog from disk cache."""
        return self._load()

    def _save(self) -> None:
        """Save filter catalog to disk cache."""
        self.cache_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "Filter catalog saved → %s  (%d models, %d storage)",
            self.cache_path,
            len(self._data.get("models", {})),
            len(self._data.get("storage", {})),
        )

    def save(self) -> None:
        """Public interface to save filter catalog to disk cache."""
        return self._save()

    def is_empty(self) -> bool:
        """Check if catalog has no models or storage options."""
        return not self._data.get("models") and not self._data.get("storage")

    async def discover(self, page):
        """
        Populate the catalog by scraping the filter panel on eMAG's Samsung brand page.
        Extracts all available models and storage options from the sidebar filters.
        """
        # Navigate to the Samsung brand page (all models)
        brand_url = "https://www.emag.ro/telefoane-mobile/brand/samsung/c"
        await page.goto(brand_url, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        logger.info(f"Navigated to {brand_url}")

        # Wait for the filter panel to load (try multiple selectors)
        filter_panel_selectors = [
            ".filters-column",
            ".sidebar-filters",
            "[class*='filter']",
        ]
        filter_panel_selector = None
        for selector in filter_panel_selectors:
            try:
                await page.wait_for_selector(selector, timeout=5000)
                filter_panel_selector = selector
                logger.info(f"Found filter panel with selector: {selector}")
                break
            except Exception:
                continue
        
        if not filter_panel_selector:
            logger.error(f"Filter panel not found with any selector: {filter_panel_selectors}")
            return

        # --- Extract Models ---
        model_filters = await page.evaluate('''(selector) => {
            const links = Array.from(document.querySelectorAll(selector + ' a[href*="model-"]'));
            const models = new Set();
            links.forEach(a => {
                const href = a.href;
                const match = href.match(/model-(\w+),([^/]+)/);
                if (match) {
                    const modelId = match[1];
                    const modelName = match[2]
                        .replace(/-/g, ' ')
                        .replace('v ', '')
                        .trim();
                    models.add(modelName);
                }
            });
            return Array.from(models);
        }''', filter_panel_selector)

        logger.info(f"Found {len(model_filters)} models: {model_filters[:3]}..." if len(model_filters) > 3 else f"Found {len(model_filters)} models: {model_filters}")

        self._data["models"] = {}
        for model in model_filters:
            if not model.lower().startswith("samsung"):
                model = f"Samsung {model}"
            filter_key = f"model-{model.lower().replace(' ', '-')}"
            self._data["models"][model] = filter_key

        # --- Extract Storage Options ---
        storage_filters = await page.evaluate('''(selector) => {
            const links = Array.from(document.querySelectorAll(selector + ' a[href*="memorie-interna-"]'));
            const storageOptions = new Set();
            links.forEach(a => {
                const href = a.href;
                const match = href.match(/memorie-interna-[^,]+,([^/]+)/);
                if (match) {
                    let storage = match[1]
                        .replace(/-/g, ' ')
                        .replace('gb', 'GB')
                        .replace('tb', 'TB')
                        .trim();
                    storage = storage.split(' ')[0];
                    storageOptions.add(storage);
                }
            });
            return Array.from(storageOptions);
        }''', filter_panel_selector)

        logger.info(f"Found {len(storage_filters)} storage options: {storage_filters}")

        self._data["storage"] = {}
        for storage in storage_filters:
            filter_key = f"memorie-interna-{storage.lower().replace(' ', '-')}"
            self._data["storage"][storage] = filter_key

        self._save()
        logger.info(f"Discovered {len(self._data['models'])} models and {len(self._data['storage'])} storage options.")

    def get_model_slug(self, model: str) -> Optional[str]:
        """
        Resolve a Deloitte model name to an eMAG filter slug.
        Returns the full filter key: "model-f9396,galaxy-s26-plus-v-14963102"
        """
        key = _normalize_filter_name(normalize_model(model))
        key = re.sub(r"^samsung\s+", "", key).strip()
        catalog: dict[str, str] = self._data.get("models", {})

        if key in catalog:
            return catalog[key]

        query_tokens = set(re.findall(r"[a-z0-9]+", key))
        ignore_words = {"samsung", "galaxy", "telefon", "mobil", "phone", "5g", "4g"}
        query_tokens -= ignore_words

        if not query_tokens:
            return None

        best_slug = None
        best_score = -1

        for cat_key, slug in catalog.items():
            parts = slug.split(',', 1)
            if len(parts) != 2:
                continue
            cat_name = parts[1]
            cat_tokens = set(re.findall(r"[a-z0-9]+", cat_name)) - ignore_words
            
            if cat_tokens and cat_tokens.issubset(query_tokens):
                score = len(cat_tokens)
                if score > best_score:
                    best_score = score
                    best_slug = slug

        return best_slug

    def get_storage_slug(self, storage: str) -> Optional[str]:
        """Resolve '128GB', '256 GB', etc. to an eMAG storage filter slug."""
        key = _normalize_storage(storage)
        catalog: dict[str, str] = self._data.get("storage", {})

        if key in catalog:
            return catalog[key]

        digits = re.sub(r"\D", "", storage)
        for cat_key, slug in catalog.items():
            if digits and re.sub(r"\D", "", cat_key) == digits:
                return slug

        return None

    def debug_dump(self) -> None:
        """Print the full catalog to stdout."""
        print("\n── Models ──────────────────────────────────────────────────")
        for k, v in sorted(self._data.get("models", {}).items()):
            print(f"  {k:<45} {v}")
        print("\n── Storage ─────────────────────────────────────────────────")
        for k, v in sorted(self._data.get("storage", {}).items()):
            print(f"  {k:<45} {v}")


class EmagCrawlerV2(EmagCrawler):
    """
    Drop-in replacement for EmagCrawler that uses eMAG's filter system
    to build pre-filtered URLs instead of post-hoc keyword matching.
    """

    BASE_SEARCH = "https://www.emag.ro/search/telefoane-mobile/brand/samsung"

    def __init__(
        self,
        filter_cache: Path = Path("emag_filters.json"),
        force_rediscover: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.catalog = FilterCatalog(cache_path=filter_cache)
        if not force_rediscover:
            self.catalog.load()
        self._catalog_ready = not self.catalog.is_empty() and not force_rediscover

    def _extract_model_filter_info(self, href: str) -> Optional[Tuple[str, str]]:
        """
        Extract (model_id, model_name) from eMAG filter URL.
        Example: "model-f9396,galaxy-s26-plus-v-14963102" -> ("f9396", "galaxy-s26-plus-v-14963102")
        """
        match = re.search(r'model-(\w+),([^/]+)', href)
        if match:
            return (match.group(1), match.group(2))
        return None

    def _build_filtered_url(self, model_filter_key: str, storage_slug: str) -> str:
        """
        Build the exact filter URL that eMAG's UI would produce.
        model_filter_key format: "model-f9396,galaxy-s26-plus-v-14963102"
        """
        model_name_part = model_filter_key.split(',', 1)[1] if ',' in model_filter_key else model_filter_key
        
        return (
            f"{self.BASE_SEARCH}/filter/{model_name_part}/{storage_slug}/c"
        )

    async def search_min_price(
        self, model: str, storage: str, page: Page
    ) -> SearchResult:
        """
        1. Ensure filter catalog is populated (discover if needed)
        2. Resolve model + storage to filter slugs
        3. Navigate to pre-filtered URL, collect all card prices
        4. Return min price + all variant prices
        Falls back to parent (v1 keyword search) if resolution fails.
        """

        if not self._catalog_ready:
            logger.info("Filter catalog not ready — running discovery. ..")
            await self.catalog.discover(page)
            self._catalog_ready = True

        model_filter_key = self.catalog.get_model_slug(model)
        storage_slug = self.catalog.get_storage_slug(storage)

        if not model_filter_key or not storage_slug:
            logger.warning(
                "⚠ Slug resolution failed for '%s %s' "
                "(model_filter_key=%s, storage_slug=%s) — falling back to v1 keyword search",
                model,
                storage,
                model_filter_key,
                storage_slug,
            )
            return await super().search_min_price(model, storage, page)

        url = self._build_filtered_url(model_filter_key, storage_slug)
        logger.info("Filter URL → %s", url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(3)
            await self._dismiss_cookie_banner(page)

            try:
                await page.wait_for_selector(
                    self.SEARCH_CARD_SELECTOR, timeout=self.timeout_ms
                )
            except Exception:
                return SearchResult(
                    model=model,
                    storage=storage,
                    min_price=None,
                    status="Error: no product cards on filtered page",
                )

            cards = await page.query_selector_all(self.SEARCH_CARD_SELECTOR)
            logger.info("  Cards on filtered page: %d", len(cards))

            variant_prices: list[float] = []
            for card in cards:
                try:
                    price_el = await card.query_selector(self.SEARCH_PRICE_SELECTOR)
                    if not price_el:
                        continue
                    price_text = (await price_el.inner_text()).strip()
                    price_val = parse_romanian_price(price_text)
                    if price_val is not None:
                        variant_prices.append(price_val)
                        logger.debug("    %.2f Lei", price_val)
                except Exception:
                    continue

            if not variant_prices:
                return SearchResult(
                    model=model,
                    storage=storage,
                    min_price=None,
                    status="Error: no prices parsed from filtered page",
                )

            min_p = min(variant_prices)
            logger.info(
                "  ✓ %s %s — %d variants, min: %.2f Lei",
                model,
                storage,
                len(variant_prices),
                min_p,
            )
            return SearchResult(
                model=model,
                storage=storage,
                min_price=min_p,
                variant_prices=sorted(variant_prices),
            )

        except Exception as e:
            logger.error("Filtered search error for %s %s: %s", model, storage, e)
            return SearchResult(
                model=model, storage=storage, min_price=None, status=f"Error: {e}"
            )

    async def rediscover_filters(self) -> None:
        """Force-refresh the filter catalog (call when eMAG adds new models)."""
        async with async_playwright() as p:
            browser, context = await self._make_context(p)
            page = await context.new_page()
            await self.catalog.discover(page)
            await browser.close()
        self._catalog_ready = True


async def main() -> None:
    import sys

    args = sys.argv[1:]
    force_rediscover = "--rediscover" in args
    discover_only = "--discover-only" in args
    json_args = [a for a in args if not a.startswith("--")]
    if json_args:
        json_path = Path(json_args[0])
    else:
        json_path = Path(__file__).parent.parent / "frontend" / "data.json"

    crawler = EmagCrawlerV2(
        headless=True,
        max_retries=2,
        timeout_ms=10_000,
        force_rediscover=force_rediscover,
    )

    if discover_only:
        await crawler.rediscover_filters()
        crawler.catalog.debug_dump()
        return

    if crawler.catalog.is_empty():
        print("Filter catalog empty — discovering now (one-time, cached afterwards). ..")
        await crawler.rediscover_filters()

    if not json_path.exists():
        print(f"data.json not found at {json_path} — running demo search")
        async with async_playwright() as p:
            browser, context = await crawler._make_context(p)
            page = await context.new_page()
            result = await crawler.search_min_price("Samsung S26", "256GB", page)
            await browser.close()
        print(f"  Samsung S26 256GB — min: {result.min_price} Lei")
        print(f"  Variants: {result.variant_prices}")
        return

    print(f"\nUpdating prices in: {json_path}")
    updated = await crawler.update_json_file(json_path)

    print("\n── Updated emagData ────────────────────────────────────────────")
    for entry in updated["emagData"]:
        status = entry.get("eMAG_Price_Status", "?")
        price = entry.get("eMAG_Price", "N/A")
        currency = entry.get("Currency", "")
        variants = entry.get("eMAG_Variants", [])
        var_str = f"  {variants}" if variants else ""
        print(
            f"  {entry['Model']:<32} {entry['Storage']:<8} "
            f"{price} {currency}  [{status}]{var_str}"
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    asyncio.run(main())
