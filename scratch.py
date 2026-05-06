import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('https://www.emag.ro/search/telefoane-mobile/brand/samsung/sort-priceasc/c')
        await asyncio.sleep(5)
        
        # Dump HTML around "Model" filter
        html = await page.evaluate('''() => {
            const el = Array.from(document.querySelectorAll('.filter-body')).find(e => e.parentElement.textContent.includes('Model'));
            return el ? el.innerHTML : 'Not found';
        }''')
        print(html)
        
        await browser.close()

asyncio.run(main())
