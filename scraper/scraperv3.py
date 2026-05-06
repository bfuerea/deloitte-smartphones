"""
scraperv3.py — eMAG filter-based price scraper (v3)
────────────────────────────────────────────────────
Fixes two critical bugs from v2:
  1. _build_filtered_url now keeps the full model slug verbatim
     (e.g. model-f9396,galaxy-s26-plus-v-14963102) instead of stripping
     the "model-fXXXX," prefix which produced invalid URLs.
  2. discover() waits for networkidle so the JS-rendered filter sidebar is
     fully loaded, and uses the brand page (not /search/) for reliable slug
     extraction.

Usage:
    python -m scraper.scraperv3 data.json               # update data.json prices
    python -m scraper.scraperv3 data.json --rediscover  # force-refresh filter catalog
    python -m scraper.scraperv3 --discover-only         # only rebuild filter catalog
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

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
      'Galaxy A06 (16)'   → 'galaxy a06'   (strips product-count suffix)
    """
    t = text.lower().strip()
    t = t.replace("+", " plus")
    t = re.sub(r"\s*\(.*?\)\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _normalize_storage(text: str) -> str:
    """
    '256 GB', '256GB', '256 gb' → '256gb'
    Also strips parenthesised suffix counts e.g. '256 GB (173)' → '256gb'
    """
    t = text.lower().strip()
    t = re.sub(r"\s*\(.*?\)\s*", "", t)
    return re.sub(r"\s+", "", t)


# ── FilterCatalog ─────────────────────────────────────────────────────────────


class FilterCatalog:
    """
    Discovers and caches eMAG filter slugs for Samsung phones.

    Catalog JSON on disk:
    {
        "models":  { "galaxy s26 plus": "model-f9396,galaxy-s26-plus-v-14963102", ... },
        "storage": { "256gb":           "memorie-interna-f9441,256-gb-v30057",    ... }
    }

    Slugs are stored verbatim — the full string from the eMAG filter URL,
    including the "model-fXXXX," and "memorie-interna-fXXXX," prefixes.
    """

    BRAND_URL = "https://www.emag.ro/telefoane-mobile/brand/samsung/c"

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = Path(cache_path)
        self._data: dict = {"models": {}, "storage": {}}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────

    def load(self) -> bool:
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

    def save(self) -> None:
        self.cache_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "Filter catalog saved → %s  (%d models, %d storage)",
            self.cache_path,
            len(self._data.get("models", {})),
            len(self._data.get("storage", {})),
        )

    def is_empty(self) -> bool:
        return not self._data.get("models") and not self._data.get("storage")

    # ── Discovery ─────────────────────────────────────────────────────

    async def discover(self, page: Page) -> None:
        """
        Navigate to the Samsung brand page and extract every model + storage
        filter slug from the sidebar.

        Key differences from v2:
          - Brand page URL (not /search/) so the sidebar shows all model filters
          - Waits for networkidle so JS-rendered filters are fully loaded
          - Stores full slugs verbatim (including "model-fXXXX," prefix)
          - Strips "samsung " prefix from display-name keys for consistency
            with get_model_slug() lookups
        """
        logger.info("Discovering filter catalog from: %s", self.BRAND_URL)

        try:
            await page.goto(self.BRAND_URL, wait_until="networkidle", timeout=30_000)
        except Exception:
            await page.goto(self.BRAND_URL, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(5)

        # Dismiss cookie banner
        for sel in (
            "button.js-accept",
            "#cookie-accept",
            "button[id*='cookie']",
            "button[class*='accept']",
        ):
            try:
                if await page.is_visible(sel, timeout=1500):
                    await page.click(sel)
                    break
            except Exception:
                pass

        # Expand any "Show more" buttons inside filter panels
        for sel in (
            ".js-show-more-refinements",
            ".show-more-filters",
            "[data-role='show-more']",
            "a.show-more",
        ):
            try:
                buttons = await page.query_selector_all(sel)
                for btn in buttons:
                    await btn.click()
                    await asyncio.sleep(0.4)
            except Exception:
                pass

        await asyncio.sleep(1)

        # Use JavaScript to enumerate all filter links.
        # a.href gives the absolute URL regardless of how the href attribute is written,
        # so we split on '/' and find path segments that start with "model-f" or
        # "memorie-interna-f" — no fragile regex needed on the href string.
        raw: dict = await page.evaluate("""() => {
            const result = {models: {}, storage: {}};

            document.querySelectorAll('a[href*="model-f"]').forEach(a => {
                const parts = a.href.split('/').filter(p => p.startsWith('model-f'));
                if (!parts.length) return;
                const slug = parts[0].split('?')[0].toLowerCase();
                const text = (a.innerText || a.textContent || '').trim();
                if (text && slug) result.models[text] = slug;
            });

            document.querySelectorAll('a[href*="memorie-interna-f"]').forEach(a => {
                const parts = a.href.split('/').filter(p => p.startsWith('memorie-interna-f'));
                if (!parts.length) return;
                const slug = parts[0].split('?')[0].toLowerCase();
                const text = (a.innerText || a.textContent || '').trim();
                if (text && slug) result.storage[text] = slug;
            });

            return result;
        }""")

        raw_models: dict = raw.get("models", {})
        raw_storage: dict = raw.get("storage", {})
        logger.info(
            "  JS extraction: %d model links, %d storage links",
            len(raw_models), len(raw_storage),
        )

        models: dict[str, str] = {}
        storage: dict[str, str] = {}

        for text, slug in raw_models.items():
            key = _normalize_filter_name(text)
            key = re.sub(r"^samsung\s+", "", key).strip()
            if key:
                models[key] = slug
                logger.debug("  Model: %-40s → %s", key, slug)

        for text, slug in raw_storage.items():
            key = _normalize_storage(text)
            if key:
                storage[key] = slug
                logger.debug("  Storage: %-40s → %s", key, slug)

        if models or storage:
            self._data = {"models": models, "storage": storage}
            self.save()
            logger.info(
                "Discovered %d models, %d storage options",
                len(models), len(storage),
            )
        else:
            logger.warning(
                "Discovery found 0 slugs — eMAG page structure may have changed.\n"
                "Run with --discover-only and inspect the filter sidebar HTML."
            )

    # ── Lookup ────────────────────────────────────────────────────────

    def get_model_slug(self, model: str) -> Optional[str]:
        """
        Resolve a model name to an eMAG filter slug.
        Matching priority:
          1. Exact normalised key match (after stripping 'samsung ' prefix)
          2. Token subset scoring — picks the longest catalog key whose tokens
             are all present in the query (prevents S26 matching S26 Plus).
        """
        key = _normalize_filter_name(normalize_model(model))
        key = re.sub(r"^samsung\s+", "", key).strip()
        catalog: dict[str, str] = self._data.get("models", {})

        if key in catalog:
            return catalog[key]

        # Token-based match
        query_tokens = set(re.findall(r"[a-z0-9]+", key))
        ignore_words = {"samsung", "galaxy", "telefon", "mobil", "phone", "5g", "4g"}
        query_tokens -= ignore_words

        if not query_tokens:
            return None

        best_slug = None
        best_score = -1

        for cat_key, slug in catalog.items():
            cat_tokens = set(re.findall(r"[a-z0-9]+", cat_key)) - ignore_words
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
        """Print the full catalog to stdout — useful during development."""
        print("\n── Models ──────────────────────────────────────────────────")
        for k, v in sorted(self._data.get("models", {}).items()):
            print(f"  {k:<45} {v}")
        print("\n── Storage ─────────────────────────────────────────────────")
        for k, v in sorted(self._data.get("storage", {}).items()):
            print(f"  {k:<45} {v}")


# ── EmagCrawlerV3 ─────────────────────────────────────────────────────────────


class EmagCrawlerV3(EmagCrawler):
    """
    Drop-in replacement for EmagCrawlerV2 with two critical fixes:
      1. _build_filtered_url keeps the full model slug (including "model-fXXXX," prefix)
         so the constructed URL matches the format eMAG's UI produces.
      2. Uses the brand page base URL, which eMAG's filter system expects, and
         includes sort-priceasc so the cheapest listing comes first.
    """

    BASE_BRAND = "https://www.emag.ro/telefoane-mobile/brand/samsung"

    def __init__(
        self,
        filter_cache: Path = Path("emag_filters.json"),
        force_rediscover: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.catalog = FilterCatalog(cache_path=filter_cache)
        if force_rediscover:
            self.catalog._data = {"models": {}, "storage": {}}
        self._catalog_ready = not self.catalog.is_empty() and not force_rediscover

    # ── URL builder ───────────────────────────────────────────────────

    def _build_filtered_url(self, model_slug: str, storage_slug: str) -> str:
        """
        Build the pre-filtered eMAG URL using the full verbatim slugs.

        Example output:
          https://www.emag.ro/telefoane-mobile/brand/samsung/filter/
            model-f9396,galaxy-s26-plus-v-14963102/
            memorie-interna-f9441,256-gb-v30057/sort-priceasc/c
        """
        return f"{self.BASE_BRAND}/filter/{model_slug}/{storage_slug}/sort-priceasc/c"

    # ── Core search ───────────────────────────────────────────────────

    async def search_min_price(
        self, model: str, storage: str, page: Page
    ) -> SearchResult:
        """
        1. Ensure filter catalog is populated (discover if needed).
        2. Resolve model + storage to filter slugs.
        3. Navigate to the pre-filtered URL, collect all card prices.
        4. Return min price + all variant prices.
        Falls back to v1 keyword search if slug resolution fails.
        """
        if not self._catalog_ready:
            logger.info("Filter catalog not ready — running discovery...")
            await self.catalog.discover(page)
            self._catalog_ready = True

        model_slug   = self.catalog.get_model_slug(model)
        storage_slug = self.catalog.get_storage_slug(storage)

        if not model_slug or not storage_slug:
            logger.warning(
                "⚠ Slug resolution failed for '%s %s' "
                "(model_slug=%s, storage_slug=%s) — falling back to v1 keyword search",
                model, storage, model_slug, storage_slug,
            )
            return await super().search_min_price(model, storage, page)

        url = self._build_filtered_url(model_slug, storage_slug)
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
                    price_val  = parse_romanian_price(price_text)
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
                model, storage, len(variant_prices), min_p,
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


# ── CLI ───────────────────────────────────────────────────────────────────────


async def main() -> None:
    import sys

    args             = sys.argv[1:]
    force_rediscover = "--rediscover" in args
    discover_only    = "--discover-only" in args
    json_args        = [a for a in args if not a.startswith("--")]
    json_path        = (
        Path(json_args[0]) if json_args
        else Path(__file__).parent.parent / "frontend" / "data.json"
    )

    crawler = EmagCrawlerV3(
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
        print("Filter catalog empty — discovering now (cached afterwards)...")
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
        status   = entry.get("eMAG_Price_Status", "?")
        price    = entry.get("eMAG_Price", "N/A")
        currency = entry.get("Currency", "")
        variants = entry.get("eMAG_Variants", [])
        var_str  = f"  {variants}" if variants else ""
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
