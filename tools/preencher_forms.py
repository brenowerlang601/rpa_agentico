from playwright.async_api import Page

async def preencher_forms(page):
    await page.locator('xpath=id("nxj_main_div")/form[1]/div[3]/input[1]').click()
    await page.locator('xpath=id("nxj_main_div")/form[1]/div[3]/input[1]').fill('1')
    await page.locator('xpath=id("nxj_main_div")/form[1]/div[10]/input[1]').click()
    await page.locator('xpath=id("nxj_main_div")/form[1]/div[10]/input[1]').fill('1')
    await page.locator('xpath=id("nxj_main_div")/form[1]/div[13]/input[1]').click()
    await page.locator('xpath=id("nxj_main_div")/form[1]/div[13]/input[1]').fill('1')
    await page.locator('xpath=id("data_entr_venda.")').click()
    await page.locator('xpath=id("data_entr_venda.")').fill('0')
    await page.locator('xpath=id("data_entr_venda.")').click()
    await page.locator('xpath=id("data_entr_venda.")').fill('0603')
    await page.locator('xpath=id("data_prev_receb.")').click()
    await page.locator('xpath=id("data_prev_receb.")').fill('0')
    await page.locator('xpath=id("data_prev_receb.")').click()
    await page.locator('xpath=id("data_prev_receb.")').fill('0703')
    await page.wait_for_timeout(500)
