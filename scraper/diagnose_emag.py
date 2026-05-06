"""
diagnose_emag.py
────────────────
Run this ONCE to find out what eMAG is actually serving to Playwright.

    python diagnose_emag.py

Outputs (in the same folder):
  emag_dump.html   — full rendered DOM after JS execution
  emag_shot.png    — full-page screenshot
  emag_selectors.txt — all <h1> tags + candidate price nodes found

Then open emag_dump.html in your browser / editor and search for the
product title text to find the real CSS selector.
"""

import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.emag.ro/telefon-mobil-apple-iphone-15-128gb-5g-black-mtp03zd-a/pd/D070N6YBM/"

# Candidate selectors to probe — extend this list freely
PROBE_SELECTORS = [
    # Title candidates
    "h1", "h1.page-title", "h1[itemprop='name']", ".page-title",
    "[data-testid='product-title']", ".product-title", ".product-page-heading",
    # Price candidates
    "p.product-new-price", ".product-new-price", "[data-testid='product-price']",
    ".price-wrapper", "[itemprop='price']", ".price", ".product-price",
]


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)   # visible — helps bypass bot checks
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        print(f"→ Navigating to {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)

        # Give JS frameworks time to hydrate
        await asyncio.sleep(4)

        # Try to dismiss cookie banner
        for sel in ("button.js-accept", "#cookie-accept", "button[id*='cookie']"):
            try:
                if await page.is_visible(sel, timeout=1500):
                    await page.click(sel)
                    print(f"  Cookie dismissed via: {sel}")
                    break
            except Exception:
                pass

        await asyncio.sleep(2)  # wait for any overlay animation

        # ── Screenshot ──────────────────────────────────────────────────
        shot_path = Path("emag_shot.png")
        await page.screenshot(path=str(shot_path), full_page=True)
        print(f"✓ Screenshot saved → {shot_path}")

        # ── Full HTML dump ───────────────────────────────────────────────
        html = await page.content()
        html_path = Path("emag_dump.html")
        html_path.write_text(html, encoding="utf-8")
        print(f"✓ HTML dump saved  → {html_path}  ({len(html):,} bytes)")

        # ── Probe selectors ──────────────────────────────────────────────
        report_lines: list[str] = ["=== Selector probe results ===\n"]
        for sel in PROBE_SELECTORS:
            try:
                elements = await page.query_selector_all(sel)
                if elements:
                    texts = []
                    for el in elements[:3]:  # first 3 matches max
                        t = (await el.inner_text()).strip().replace("\n", " ")[:120]
                        if t:
                            texts.append(repr(t))
                    hit = f"  FOUND ({len(elements)})  →  {', '.join(texts) if texts else '(no text)'}"
                else:
                    hit = "  not found"
            except Exception as e:
                hit = f"  ERROR: {e}"
            line = f"{sel:<45} {hit}"
            print(line)
            report_lines.append(line)

        # ── All <h1> tags in the page ────────────────────────────────────
        report_lines.append("\n=== All <h1> elements ===")
        h1_matches = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
        for tag in h1_matches[:10]:
            clean = re.sub(r"<[^>]+>", "", tag).strip()[:200]
            report_lines.append(f"  {clean}")

        sel_path = Path("emag_selectors.txt")
        sel_path.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\n✓ Selector report  → {sel_path}")

        await browser.close()
        print("\nDone. Open emag_shot.png to see what the browser rendered,")
        print("then search emag_dump.html for the product title to find the selector.")


if __name__ == "__main__":
    asyncio.run(main())
