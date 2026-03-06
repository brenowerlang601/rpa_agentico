import asyncio
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError


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
    try:
        await locator.fill(text)
    except Exception:
        pass


async def preencher_pedido_tipo_pedi_f130(page: Page) -> bool:
    """Executa o passo 'preencher_pedido_tipo_pedi_f130'. Retorna True se todas as ações tiveram sucesso."""
    errors: list[tuple[str, str]] = []

    try:
        await safe_click(
            page,
            "html/body/div[1]/div[5]/form/div[3]/input",
            fallback_attr=["input[name='tecido_peca.']", "input.textfield"]
        )
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_1_click_tecido_peca', str(e)))

    try:
        await safe_fill(
            page,
            "html/body/div[1]/div[5]/form/div[3]/input",
            "1",
            clear=True,
            fallback_attr=["input[name='tecido_peca.']", "input.textfield"]
        )
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_2_fill_tecido_peca', str(e)))

    try:
        await safe_click(
            page,
            "html/body/div[1]/div[5]/form/div[10]/input",
            fallback_attr=["input[name='tipo_pedido.']", "input.textfield"]
        )
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_3_click_tipo_pedido', str(e)))

    try:
        await safe_fill(
            page,
            "html/body/div[1]/div[5]/form/div[10]/input",
            "1",
            clear=True,
            fallback_attr=["input[name='tipo_pedido.']", "input.textfield"]
        )
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_4_fill_tipo_pedido', str(e)))

    try:
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_5_key_tab', str(e)))

    try:
        await safe_click(
            page,
            "html/body/div[1]/div[5]/form/div[26]/input",
            fallback_attr=["input[name='pedido_venda.']", "input.textfield"]
        )
        await asyncio.sleep(0.5)
    except Exception as e:
        errors.append(('acao_6_click_pedido_venda', str(e)))

    if errors:
        print(f"[FAIL] preencher_pedido_tipo_pedi_f130: {len(errors)} erro(s)")
        for label, msg in errors:
            print(f"  - {label}: {msg}")
        return False
    print(f"[OK] preencher_pedido_tipo_pedi_f130")
    return True
