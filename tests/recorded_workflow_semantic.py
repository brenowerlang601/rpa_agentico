import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

from tools.navegar_forms import navegar_forms
from tools.preencher_usuario import preencher_usuario
from tools.preencher_senha import preencher_senha
from tools.clicar_acessar import clicar_acessar


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        await navegar_forms(page)
        await preencher_usuario(page)
        await preencher_senha(page)
        await clicar_acessar(page)

        print("Fluxo 'Recorded Workflow (Semantic)' concluido.")


if __name__ == '__main__':
    asyncio.run(run())
