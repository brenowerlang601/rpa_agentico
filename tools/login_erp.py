import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        # Ação 1: Navegar para a URL
        await page.goto("https://devs.systextil.com.br/systextil/")
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(0.5)

        # Ação 2: Input usuário
        xpath_usuario = "//html/body/div/div[4]/form/div[3]/fieldset/div[3]/input"
        try:
            locator_usuario = page.locator(f"xpath={xpath_usuario}")
            await locator_usuario.wait_for(timeout=5000)
        except PlaywrightTimeoutError:
            # Fallback por ax_name ou atributos
            locator_usuario = page.get_by_placeholder("USUÁRIO")
            if not await locator_usuario.count():
                locator_usuario = page.get_by_role("textbox", name="USUÁRIO")
        try:
            await locator_usuario.clear()
        except Exception:
            # Alguns elementos não suportam clear(); usar fill direto
            pass
        await locator_usuario.fill("INTERSYS")
        await asyncio.sleep(0.5)

        # Ação 3: Input senha
        xpath_senha = "//html/body/div/div[4]/form/div[3]/fieldset/div[4]/input"
        try:
            locator_senha = page.locator(f"xpath={xpath_senha}")
            await locator_senha.wait_for(timeout=5000)
        except PlaywrightTimeoutError:
            # Fallback por placeholder ou tipo password
            locator_senha = page.get_by_placeholder("SENHA")
            if not await locator_senha.count():
                locator_senha = page.locator("input[type='password']")
        try:
            await locator_senha.clear()
        except Exception:
            pass
        await locator_senha.fill("A%h^9l89")
        await asyncio.sleep(0.5)

        # Ação 4: Click botão Acessar
        xpath_acessar = "//html/body/div/div[4]/form/div[3]/fieldset/div[5]/input"
        try:
            locator_acessar = page.locator(f"xpath={xpath_acessar}")
            await locator_acessar.wait_for(timeout=5000)
        except PlaywrightTimeoutError:
            # Fallback por ax_name, id, value ou title
            locator_acessar = page.get_by_role("button", name="Acessar")
            if not await locator_acessar.count():
                locator_acessar = page.locator("#box1\\:button1\\.")
            if not await locator_acessar.count():
                locator_acessar = page.locator("input[value='Acessar']")
            if not await locator_acessar.count():
                locator_acessar = page.locator("input[title*='F9']")
        await locator_acessar.click()
        await asyncio.sleep(0.5)

        # Opcional: aguardar carregamento pós-login
        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
        except PlaywrightTimeoutError:
            pass

        # Não fechamos o browser porque estamos conectados via CDP externo
        # await browser.close()


if __name__ == '__main__':
    asyncio.run(run())