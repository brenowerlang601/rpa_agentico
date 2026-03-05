from playwright.async_api import Page

async def navegar_forms(page):
    await page.goto('https://devs.systextil.com.br/systextil/')
    await page.wait_for_load_state('networkidle')
    await page.wait_for_timeout(500)
