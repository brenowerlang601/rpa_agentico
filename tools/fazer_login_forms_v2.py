# actions_fp: f4391dd6f347
from playwright.async_api import Page

async def fazer_login_forms_v2(page) -> bool:
    errors = []

    try:
        # Navegar para a URL
        await page.goto("https://erp.systextil.com.br/systextil/forms.systextil.senha.nxj?managerId=AMAN16_UAcy8hgmbMO7Ks-KbDpnPUd#0x")
        await page.wait_for_load_state('networkidle')
    except Exception as e:
        errors.append(("navigation", str(e)))

    try:
        # Preencher o campo USUÁRIO
        usuario_locator = page.locator("//id(\"box1.\")/div[3]/input[1]")
        await usuario_locator.click()
        await usuario_locator.fill("INTERSYS")
    except Exception as e:
        errors.append(("fill USUÁRIO", str(e)))

    try:
        # Clicar no campo USUÁRIO
        await usuario_locator.click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("click USUÁRIO", str(e)))

    try:
        # Clicar novamente no campo USUÁRIO
        await usuario_locator.click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("second click USUÁRIO", str(e)))

    try:
        # Pressionar Control+C
        await page.keyboard.press("Control+c")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Control+c", str(e)))

    try:
        # Clicar de novo no campo USUÁRIO
        await usuario_locator.click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("third click USUÁRIO", str(e)))

    try:
        # Pressionar Control+V
        await page.keyboard.press("Control+v")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Control+v", str(e)))

    try:
        # Preencher o campo USUÁRIO com INTERSYSINTERSYS
        await usuario_locator.click()
        await usuario_locator.fill("INTERSYSINTERSYS")
    except Exception as e:
        errors.append(("second fill USUÁRIO", str(e)))

    try:
        # Pressionar Control+V duas vezes
        for _ in range(2):
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Control+v x2", str(e)))

    try:
        # Preencher o campo USUÁRIO com INTERSYSINTERSYSINTERSYSINTERSYS
        await usuario_locator.click()
        await usuario_locator.fill("INTERSYSINTERSYSINTERSYSINTERSYS")
    except Exception as e:
        errors.append(("third fill USUÁRIO", str(e)))

    if errors:
        print(f"[FAIL] fazer_login_forms_v2: {len(errors)} erro(s): {errors}")
        return False
    else:
        print("[OK] fazer_login_forms_v2")
        return True