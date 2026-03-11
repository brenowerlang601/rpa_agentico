from playwright.async_api import Page
 
import asyncio    
    
async def preencher_forms_2(page) -> bool:
    _url_inicial = await page.evaluate('location.href')
    errors = []
 
    try:
        # Fill: cd_it_pe_grupo.
        locator = page.locator('//*[@id="repeat1."]/div[17]/input')
        await locator.click()
        await locator.fill("pol18")
    except Exception as e:
        errors.append(("fill cd_it_pe_grupo.", str(e)))
        
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))

    try:
        # Fill: cd_it_pe_item.
        locator = page.locator('//*[@id="repeat1."]/div[18]/input')
        await locator.click()
        await locator.fill("000006")
    except Exception as e:
        errors.append(("fill cd_it_pe_item.", str(e)))
 
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))

    try:
        # Fill: cd_it_pe_subgrupo.
        locator = page.locator('//*[@id="repeat1."]/div[19]/input[1]')
        await locator.click()
        await locator.fill("p")
    except Exception as e:
        errors.append(("fill cd_it_pe_subgrupo.", str(e)))
        
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))

    try:
        # Fill: qtde_pedida.
        locator = page.locator('//*[@id="repeat1."]/div[22]/input')
        await locator.click()
        await page.keyboard.press("Backspace")
        await locator.fill("10")
    except Exception as e:
        errors.append(("fill qtde_pedida.", str(e)))
        
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))
        
        
    try:
        # Fill: codigo_deposito.
        locator = page.locator('//*[@id="repeat1."]/div[23]/input')
        await locator.click()
        await page.keyboard.press("Backspace")
        await locator.fill("001")
    except Exception as e:
        errors.append(("fill codigo_deposito.", str(e)))
 
    try:
        # ✅ Registra handler ANTES do Enter para capturar o dialog
        async def handle_dialog(dialog):
            print(f"[DIALOG] Mensagem: {dialog.message}")
            await dialog.accept()  # Clica em OK
 
        page.on("dialog", handle_dialog)
 
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)  # Aguarda o dialog aparecer e ser tratado
 
        # Remove o handler após uso para não interferir em outros dialogs
        page.remove_listener("dialog", handle_dialog)
 
    except Exception as e:
        errors.append(("key_press Enter + dialog deposito.", str(e)))
        
        
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))

    try:
        # Fill: valor_unitario.
        locator = page.locator('//*[@id="repeat1."]/div[25]/input')
        await locator.click()
        await page.keyboard.press("Backspace")
        await locator.fill("10")
    except Exception as e:
        errors.append(("fill valor_unitario.", str(e)))
    try:
        # Fill: cd_it_pe_item.
        locator = page.locator('//*[@id="nxj_main_div"]/form/div[30]/input')
        await locator.click()
        await locator.fill("2540")
    except Exception as e:
        errors.append(("fill cd_it_pe_item.", str(e)))
        
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.press("F9")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))
        
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.press("F1")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))

 
        
    _url_final = await page.evaluate('location.href')
    if _url_final == _url_inicial:
        errors.append(('url_unchanged', 'URL não mudou após execução'))

    if not errors:
        print("[OK] preencher_forms_2")
        return True
    else:
        print(f"[FAIL] preencher_forms_2: {len(errors)} erro(s)")
        for error in errors:
            print(f"Error in {error[0]}: {error[1]}")
        return False