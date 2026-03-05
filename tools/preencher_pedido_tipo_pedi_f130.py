import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


async def safe_click(page, xpath, fallback_attr=None):
    locator = page.locator(f'xpath=//{xpath}')
    try:
        await locator.click()
        return
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    if fallback_attr:
        for selector in fallback_attr:
            try:
                await page.locator(selector).click()
                return
            except Exception:
                continue
    # As último recurso, ignorar erro
    try:
        await locator.click(force=True)
    except Exception:
        pass


async def safe_fill(page, xpath, text, clear=False, fallback_attr=None):
    locator = page.locator(f'xpath=//{xpath}')
    try:
        if clear:
            try:
                await locator.clear()
            except Exception:
                await locator.fill("")
        await locator.fill(text)
        return
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    if fallback_attr:
        for selector in fallback_attr:
            try:
                floc = page.locator(selector)
                if clear:
                    try:
                        await floc.clear()
                    except Exception:
                        await floc.fill("")
                await floc.fill(text)
                return
            except Exception:
                continue
    # Último recurso: tentar novamente sem tratamento
    try:
        await locator.fill(text)
    except Exception:
        pass


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        # A tela já está carregada segundo a descrição; não há ações de navegação no histórico.

        # 1) click input index 91 - xpath 'html/body/div[1]/div[5]/form/div[3]/input'
        await safe_click(
            page,
            "html/body/div[1]/div[5]/form/div[3]/input",
            fallback_attr=[
                "input[name='tecido_peca.']",
                "input.textfield"
            ]
        )
        await asyncio.sleep(0.5)

        # 2) input '1' clear=True no mesmo campo index 91
        await safe_fill(
            page,
            "html/body/div[1]/div[5]/form/div[3]/input",
            "1",
            clear=True,
            fallback_attr=[
                "input[name='tecido_peca.']",
                "input.textfield"
            ]
        )
        await asyncio.sleep(0.5)

        # 3) click input index 96 - xpath 'html/body/div[1]/div[5]/form/div[10]/input'
        await safe_click(
            page,
            "html/body/div[1]/div[5]/form/div[10]/input",
            fallback_attr=[
                "input[name='tipo_pedido.']",
                "input.textfield"
            ]
        )
        await asyncio.sleep(0.5)

        # 4) input '1' clear=True no campo index 96
        await safe_fill(
            page,
            "html/body/div[1]/div[5]/form/div[10]/input",
            "1",
            clear=True,
            fallback_attr=[
                "input[name='tipo_pedido.']",
                "input.textfield"
            ]
        )
        await asyncio.sleep(0.5)

        # 5) send_keys "Tab"
        try:
            await page.keyboard.press("Tab")
        except Exception:
            pass
        await asyncio.sleep(0.5)

        # 6) click input index 102 - xpath 'html/body/div[1]/div[5]/form/div[26]/input'
        await safe_click(
            page,
            "html/body/div[1]/div[5]/form/div[26]/input",
            fallback_attr=[
                "input[name='pedido_venda.']",
                "input.textfield"
            ]
        )
        await asyncio.sleep(0.5)

        # Ação "done" é apenas descritiva, não gera interação no script.


if __name__ == '__main__':
    asyncio.run(run())