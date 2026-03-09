from playwright.async_api import Page
import asyncio

async def preencher_forms(page) -> bool:
    await asyncio.sleep(0.5)
    errors = []
    
    try:
        locator = page.locator('//*[@id="nxj_main_div"]/form/div[3]/input')
        await locator.click()
        await locator.fill("1")
    except Exception as e:
        errors.append(("fill tecido_peca.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tecido_peca.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tipo_pedido.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tipo_prod_pedido.", str(e)))
   
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter promocao.", str(e)))
   
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter data_emis_venda.", str(e)))
    await asyncio.sleep(0.5)
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter data_entr_venda.", str(e)))
    await asyncio.sleep(0.5)
    try:
        locator = page.locator('//*[@id="data_prev_receb."]')
        await locator.click()
        from datetime import date, timedelta
        tomorrow = date.today() + timedelta(days=1)
        await locator.fill(tomorrow.strftime("%d%m"))
    except Exception as e:
        errors.append(("fill data_prev_receb.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter data_prev_receb.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter criterio_pedido.", str(e)))
   
    try:
        locator = page.locator('//*[@id="nxj_main_div"]/form/div[43]/input')
        await locator.click()
        await locator.fill("1")
    except Exception as e:
        errors.append(("fill cli_ped_cgc_cli9.", str(e)))
   
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cli_ped_cgc_cli9.", str(e)))
    
    try:
        locator = page.locator('//*[@id="nxj_main_div"]/form/div[44]/input')
        await locator.click()
        await locator.fill("1")
    except Exception as e:
        errors.append(("fill cli_ped_cgc_cli4.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cli_ped_cgc_cli4.", str(e)))
    
    try:
        locator = page.locator('//*[@id="nxj_main_div"]/form/div[45]/input')
        await locator.click()
        await locator.fill("36")
    except Exception as e:
        errors.append(("fill cli_ped_cgc_cli2.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cli_ped_cgc_cli2.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter classificacao_pedido.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cod_rep_cliente.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tipo_comissao.", str(e)))
   
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter perc_comis_venda.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter perc_comis_venda_real.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cod_ped_cliente.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter res.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter colecao_tabela.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter mes_tabela.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter sequencia_tabela.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter perc_desc_duplic.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter codigo_moeda.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cond_pgto_venda.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter desconto1.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter encargos.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter perc_comissao_fatu_repres.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter perc_comissao_fatu_adm.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter id_pedido_forca_vendas.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter seq_end_entrega.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter seq_end_cobranca.", str(e)))
   
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter desconto_item1.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter desconto_item2.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter desconto_item3.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter total_desc_itens.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter total_desc_itens_fci.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter total_desc_itens_exc.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cod_catalogo.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter tipo_frete.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cod_via_transp.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter trans_pv_forne9.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter trans_pv_forne4.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter trans_pv_forne2.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cod_forma_pagto.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter trans_re_forne9.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter trans_re_forne4.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter trans_re_forne2.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter cod_banco.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter natop_pv_nat_oper.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter natop_pv_est_oper.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))
    
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))     

    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))     
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
  
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
  
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
    try:
        await page.keyboard.press("F9")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e)))  
        
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e))) 
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e))) 
    try:
        await page.keyboard.press("F9")
        await page.wait_for_timeout(150)
    except Exception as e:
        errors.append(("key_press Enter numero_controle.", str(e))) 
  
    
    if not errors:
        print("[OK] preencher_forms")
        return True
    else:
        print(f"[FAIL] preencher_forms: {len(errors)} erro(s) - {errors}")
        return False