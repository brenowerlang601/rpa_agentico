import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

from tools.fazer_login_forms import fazer_login_forms


async def run():
    async with async_playwright() as p:
        
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        await fazer_login_forms(page)

        print("Fluxo 'Recorded Workflow (Semantic)' concluido.")


if __name__ == '__main__':
    asyncio.run(run())
