import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

from tools.navegar_forms import navegar_forms
from tools.fazer_login_forms import fazer_login_forms
from tools.pesquisa_tela_pedi_f130 import pesquisa_tela_pedi_f130
from tools.preencher_forms import preencher_forms


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        await navegar_forms(page)
        await fazer_login_forms(page)
        await pesquisa_tela_pedi_f130(page)
        await preencher_forms(page)

        print("Fluxo 'Recorded Workflow (Semantic)' concluido.")


if __name__ == '__main__':
    asyncio.run(run())
