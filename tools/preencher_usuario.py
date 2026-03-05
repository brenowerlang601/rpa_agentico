from playwright.async_api import Page

async def preencher_usuario(page):
    await page.locator('xpath=id("box1.")/div[3]/input[1]').click()
    await page.locator('xpath=id("box1.")/div[3]/input[1]').fill('INTERSYS')
    await page.wait_for_timeout(500)
