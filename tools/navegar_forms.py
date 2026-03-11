from playwright.async_api import Page

async def navegar_forms(page) -> bool:
    _url_inicial = await page.evaluate('location.href')
    errors = []

    try:
        await page.goto('https://devs.systextil.com.br/systextil/')
        await page.wait_for_load_state('networkidle')
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(('navigation', str(e)))

    _url_final = await page.evaluate('location.href')
    if _url_final == _url_inicial:
        errors.append(('url_unchanged', 'URL não mudou após execução'))

    if errors:
        print(f'[FAIL] navegar_forms: {len(errors)} erro(s) {errors}')
        return False
    print('[OK] navegar_forms')
    return True
