# actions_fp: 0e54ab1cda28
from playwright.async_api import Page

async def fazer_login_forms_v2(page) -> bool:
    errors: list[tuple[str, str]] = []

    # Primeira ação: fill
    try:
        await page.goto("https://erp.systextil.com.br/systextil/")
        await page.wait_for_load_state('networkidle')
        locator = page.locator('//*[@id="box1."]/div[3]/input')  # Campo USUÁRIO
        await locator.click()
        await locator.fill("INTERSYS")
    except Exception as e:
        errors.append(("fill", str(e)))

    # Segunda ação: click
    try:
        locator = page.locator('//*[@id="box1."]/div[3]/input')  # Campo USUÁRIO
        await locator.click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("click", str(e)))

    # Terceira ação: click
    try:
        locator = page.locator('//*[@id="box1."]/div[3]/input')  # Campo USUÁRIO
        await locator.click()
        await page.wait_for_timeout(500)
    except Exception as e:
        errors.append(("click", str(e)))

    # Quarta ação: fill
    try:
        locator = page.locator('//*[@id="box1."]/div[3]/input')  # Campo USUÁRIO
        await locator.click()
        await locator.fill("INTERSYS")
    except Exception as e:
        errors.append(("fill", str(e)))

    # Quinta ação: fill
    try:
        locator = page.locator('//*[@id="box1."]/div[3]/input')  # Campo USUÁRIO
        await locator.click()
        await locator.fill("INTERSYSINTERSYS")
    except Exception as e:
        errors.append(("fill", str(e)))

    if errors:
        print(f"[FAIL] fazer_login_forms_v2: {len(errors)} erro(s) {errors}")
        return False
    else:
        print("[OK] fazer_login_forms_v2")
        return True