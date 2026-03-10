# actions_fp: f4391dd6f347
from playwright.async_api import Page

async def fazer_login_forms_v3(page) -> bool:
    errors: list[tuple[str, str]] = []

    # Ação 1: fill - USUÁRIO
    try:
        await page.goto("https://erp.systextil.com.br/systextil/")
        await page.wait_for_load_state('networkidle')
        usuario_locator = page.locator('//*[@id="box1."]/div[3]/input')
        await usuario_locator.click()
        await usuario_locator.fill("INTERSYS")
    except Exception as e:
        errors.append(("fill", str(e)))

    # Ação 2: click - USUÁRIO
    try:
        await page.locator('//*[@id="box1."]/div[3]/input').click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("click", str(e)))

    # Ação 3: click - USUÁRIO
    try:
        await page.locator('//*[@id="box1."]/div[3]/input').click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("click", str(e)))

    # Ação 4: key_press - Control+c
    try:
        await page.keyboard.press("Control+c")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press", str(e)))

    # Ação 5: click - USUÁRIO
    try:
        await page.locator('//*[@id="box1."]/div[3]/input').click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("click", str(e)))

    # Ação 6: key_press - Control+v
    try:
        await page.keyboard.press("Control+v")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press", str(e)))

    # Ação 7: fill - USUÁRIO
    try:
        usuario_locator = page.locator('//*[@id="box1."]/div[3]/input')
        await usuario_locator.click()
        await usuario_locator.fill("INTERSYSINTERSYS")
    except Exception as e:
        errors.append(("fill", str(e)))

    # Ação 8: key_press - Control+v x2
    try:
        for _ in range(2):
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press", str(e)))



    if not errors:
        print("[OK] fazer_login_forms_v3")
        return True
    else:
        print(f"[FAIL] fazer_login_forms_v3: {len(errors)} erro(s)")
        for error in errors:
            print(f"Erro: {error[0]}, Detalhes: {error[1]}")
        return False