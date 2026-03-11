import asyncio
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError


async def login_erp(page: Page) -> bool:
    """Executa o passo 'login_erp'. Retorna True se todas as ações tiveram sucesso."""
    _url_inicial = await page.evaluate('location.href')
    errors: list[tuple[str, str]] = []

    try:
        await page.goto("https://devs.systextil.com.br/systextil/")
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_1_navigation', str(e)))

    try:
        xpath_usuario = "//html/body/div/div[4]/form/div[3]/fieldset/div[3]/input"
        try:
            locator_usuario = page.locator(f"xpath={xpath_usuario}")
            await locator_usuario.wait_for(timeout=3000)
        except Exception:
            locator_usuario = page.get_by_placeholder("USUÁRIO")
            if not await locator_usuario.count():
                locator_usuario = page.get_by_role("textbox", name="USUÁRIO")
        try:
            await locator_usuario.clear()
        except Exception:
            pass
        await locator_usuario.fill("INTERSYS")
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_2_fill_usuario', str(e)))

    try:
        xpath_senha = "//html/body/div/div[4]/form/div[3]/fieldset/div[4]/input"
        try:
            locator_senha = page.locator(f"xpath={xpath_senha}")
            await locator_senha.wait_for(timeout=3000)
        except Exception:
            locator_senha = page.get_by_placeholder("SENHA")
            if not await locator_senha.count():
                locator_senha = page.locator("input[type='password']")
        try:
            await locator_senha.clear()
        except Exception:
            pass
        await locator_senha.fill("A%h^9l89")
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_3_fill_senha', str(e)))

    try:
        xpath_acessar = "//html/body/div/div[4]/form/div[3]/fieldset/div[5]/input"
        try:
            locator_acessar = page.locator(f"xpath={xpath_acessar}")
            await locator_acessar.wait_for(timeout=3000)
        except Exception:
            locator_acessar = page.get_by_role("button", name="Acessar")
            if not await locator_acessar.count():
                locator_acessar = page.locator("#box1\\:button1\\.")
            if not await locator_acessar.count():
                locator_acessar = page.locator("input[value='Acessar']")
            if not await locator_acessar.count():
                locator_acessar = page.locator("input[title*='F9']")
        await locator_acessar.click()
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_4_click_acessar', str(e)))

    try:
        await page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass

    _url_final = await page.evaluate('location.href')
    if _url_final == _url_inicial:
        errors.append(('url_unchanged', 'URL não mudou após execução'))

    if errors:
        print(f"[FAIL] login_erp: {len(errors)} erro(s)")
        for label, msg in errors:
            print(f"  - {label}: {msg}")
        return False
    print(f"[OK] login_erp")
    return True


if __name__ == '__main__':
    from playwright.async_api import async_playwright
    import asyncio

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp('http://localhost:9222')
            page = browser.contexts[0].pages[0]
            await login_erp(page)

    asyncio.run(_run())
