import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

from tools.fazer_login_forms_v2 import fazer_login_forms_v2


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        results: dict[str, bool] = {}

        results['fazer_login_forms_v2'] = await fazer_login_forms_v2(page)

        print()
        print("=" * 50)
        print(f"Relatório QA — testcv")
        print("=" * 50)
        passed = [k for k, v in results.items() if v]
        failed = [k for k, v in results.items() if not v]
        for step in passed:
            print(f"  [PASS] {step}")
        for step in failed:
            print(f"  [FAIL] {step}")
        print("-" * 50)
        print(f"  Total: {len(results)} | Passed: {len(passed)} | Failed: {len(failed)}")
        print("=" * 50)
        sys.exit(0 if not failed else 1)


if __name__ == '__main__':
    asyncio.run(run())
