from playwright.async_api import Page

async def clicar_acessar(page):
    await page.locator('xpath=id("box1:button1.")').click()
    await page.wait_for_timeout(500)
