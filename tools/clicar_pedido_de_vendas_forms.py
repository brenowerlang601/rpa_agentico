from playwright.async_api import Page

async def clicar_pedido_de_vendas_forms(page) -> bool:
    _url_inicial = await page.evaluate('location.href')
    errors = []

    try:
        await page.locator('xpath=id("found")/dt[1]').click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(('click_pedido_vendas', str(e)))

    _url_final = await page.evaluate('location.href')
    if _url_final == _url_inicial:
        errors.append(('url_unchanged', 'URL não mudou após execução'))

    if errors:
        print(f'[FAIL] clicar_pedido_de_vendas_forms: {len(errors)} erro(s) {errors}')
        return False
    print('[OK] clicar_pedido_de_vendas_forms')
    return True
