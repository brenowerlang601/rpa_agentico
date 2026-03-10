# actions_fp: 0f42c69db735
from playwright.async_api import Page

async def navegar_forms_v3(page) -> bool:
    errors: list[tuple[str, str]] = []
    
    try:
        # Navegação para o URL especificado
        await page.goto("https://erp.systextil.com.br/systextil/forms.systextil.senha.nxj?managerId=AMAN16_UAcy8hgmbMO7Ks-KbDpnPUd")
        await page.wait_for_load_state('networkidle')
    except Exception as e:
        errors.append(("navigation", str(e)))
    
    if not errors:
        print("[OK] navegar_forms_v3")
        return True
    else:
        print(f"[FAIL] navegar_forms_v3: {len(errors)} erro(s) - {errors}")
        return False