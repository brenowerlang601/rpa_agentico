from playwright.async_api import Page

async def fazer_login_forms(page) -> bool:
    
    errors = []

    try:
        await page.goto('https://erp.systextil.com.br/systextil/')
        await page.wait_for_load_state('networkidle')
    except Exception as e:
        errors.append(('navigation', str(e)))
        
    _url_inicial = await page.evaluate('location.href')
    print(_url_inicial)
    try:
        await page.locator('xpath=id("box1.")/div[3]/input[1]').click()
        await page.locator('xpath=id("box1.")/div[3]/input[1]').fill('INTERSYS')
    except Exception as e:
        errors.append(('fill_usuario', str(e)))

    try:
        await page.locator('xpath=id("box1.")/div[4]/input[1]').click()
        await page.locator('xpath=id("box1.")/div[4]/input[1]').fill('12345')
    except Exception as e:
        errors.append(('fill_senha', str(e)))

    try:
        await page.locator('xpath=id("box1:button1.")').click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(('click_acessar', str(e)))

    _url_final = await page.evaluate('location.href')
    if _url_final == _url_inicial:
        errors.append(('url_unchanged', 'URL não mudou após execução'))

    if errors:
        print(f'[FAIL] fazer_login_forms: {len(errors)} erro(s) {errors}')
        return False
    print('[OK] fazer_login_forms')
    return True
