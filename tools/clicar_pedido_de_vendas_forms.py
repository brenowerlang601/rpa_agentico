from playwright.async_api import Page

async def clicar_pedido_de_vendas_forms(page):
    await page.locator('xpath=id("found")/dt[1]').click()
    await page.wait_for_timeout(500)
