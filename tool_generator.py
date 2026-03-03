import json
from pathlib import Path

from openai import AsyncOpenAI

TOOLS_DIR = Path("tools")
TOOLS_DIR.mkdir(exist_ok=True)

PLAYWRIGHT_PROMPT = """Você recebeu o histórico de ações de um agente de automação web.
Gere um script Python standalone usando Playwright (async) que replique exatamente estas ações.

Regras:
- Use `from playwright.async_api import async_playwright`
- Conecte ao browser via CDP: `await p.chromium.connect_over_cdp('http://localhost:9222')`
- Obtenha a página ativa: `page = browser.contexts[0].pages[0]`
- Para navigate: use `await page.goto(url)` seguido de `await page.wait_for_load_state('networkidle')`
- Para click_element: use o x_path do interacted_element -> `await page.locator('xpath=//XPATH').click()`
  - Converta o x_path de formato `html/body/div[1]/...` para XPath absoluto `//html/body/div[1]/...`
- Para input_text: use `await page.locator('xpath=//XPATH').fill('text')`
  - Se clear=True, limpe antes: `await page.locator('xpath=//XPATH').clear()`
- Para scroll: use `await page.mouse.wheel(0, 300)` para baixo ou `await page.mouse.wheel(0, -300)` para cima
- Adicione `await asyncio.sleep(0.5)` entre cada ação
- Função principal: `async def run():`
- Ao final: `if __name__ == '__main__': asyncio.run(run())`
- NÃO inclua blocos markdown (```), apenas código Python puro
- Use try/except para erros de elementos não encontrados com fallback por ax_name ou atributos

Histórico de ações (JSON):
{actions_json}
"""


async def generate_tool(task_name: str, result, llm) -> Path:
    """
    Gera um script Playwright a partir do histórico de ações do agente.

    Args:
        task_name: Nome da task (usado como nome do arquivo em tools/)
        result: AgentHistoryList retornado por agent.run()
        llm: Instância do LLM para gerar o script

    Returns:
        Path do arquivo gerado
    """
    actions = result.model_actions()
    actions_json = json.dumps(actions, indent=2, default=str)

    prompt = PLAYWRIGHT_PROMPT.format(actions_json=actions_json)

    client = AsyncOpenAI()
    response = await client.chat.completions.create(
        model=str(llm.model),
        messages=[{"role": "user", "content": prompt}],
    )

    script_code = response.choices[0].message.content.strip()

    # Remove markdown fences caso o LLM inclua
    if script_code.startswith("```"):
        lines = script_code.split('\n')
        script_code = '\n'.join(lines[1:-1])
        if script_code.endswith("```"):
            script_code = script_code[:-3].rstrip()

    tool_path = TOOLS_DIR / f"{task_name}.py"
    tool_path.write_text(script_code, encoding='utf-8')
    print(f"[tool_generator] Script gerado e salvo em: {tool_path}")
    return tool_path
