# actions_fp: 745064f24049
from playwright.async_api import Page

async def navegar_forms_v2(page) -> bool:
    errors: list[tuple[str, str]] = []

    # Navegação para a URL especificada
    try:
        await page.goto("https://erp.systextil.com.br/systextil/forms.systextil.senha.nxj?managerId=AMAN5_UAcy8hgmbMO7Ks-KbDpnPUd")
        await page.wait_for_load_state('networkidle')
    except Exception as e:
        errors.append(("navigation", str(e)))

    # Verificação de erros e impressão do resultado final
    if errors:
        print(f"[FAIL] navegar_forms_v2: {len(errors)} erro(s): {errors}")
        return False
    else:
        print("[OK] navegar_forms_v2")
        return True