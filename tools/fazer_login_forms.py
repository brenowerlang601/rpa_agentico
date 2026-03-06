from playwright.async_api import Page

async def fazer_login_forms(page):
    await page.goto('https://erp.systextil.com.br/systextil/')
    await page.wait_for_load_state('networkidle')
    await page.locator('xpath=id("box1.")/div[3]/input[1]').click()
    await page.locator('xpath=id("box1.")/div[3]/input[1]').fill('INTERSYS')
    await page.locator('xpath=id("box1.")/div[4]/input[1]').click()
    await page.locator('xpath=id("box1.")/div[4]/input[1]').fill('1234')
    await page.locator('xpath=id("box1:button1.")').click()
    await page.wait_for_timeout(500)
