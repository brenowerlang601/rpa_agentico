from playwright.async_api import Page

async def preencher_senha(page):
    await page.locator('xpath=id("box1.")/div[4]/input[1]').click()
    await page.locator('xpath=id("box1.")/div[4]/input[1]').fill('********')
    await page.wait_for_timeout(500)
