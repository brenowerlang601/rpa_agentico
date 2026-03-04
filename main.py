import asyncio
from pathlib import Path

from browser_use import BrowserSession, ChatOpenAI

from orchestrator import run_orchestrator
from task_decomposer import decompose_task

TOOLS_DIR = Path("tools")

TASK = """
Acesse o ERP da systextil e faça login com usuario: INTERSYS , senha: A%h^9l89
Site:
- LINK ERP: https://devs.systextil.com.br/systextil/
"""


async def main():
    llm = ChatOpenAI(model='gpt-5.1')

    # Uma única sessão de browser compartilhada entre todos os steps
    browser_session = BrowserSession(cdp_url='http://localhost:9222')
    await browser_session.start()

    print("[main] Decompondo task em steps...")
    steps = await decompose_task(TASK, TOOLS_DIR, llm)
    for step in steps:
        cached = (TOOLS_DIR / f"{step.tool_name}.py").exists()
        status = "cached" if cached else "LLM"
        print(f"  Step {step.order}: [{status}] {step.tool_name}")

    await run_orchestrator(steps, browser_session, llm, TOOLS_DIR)
    print("[main] Task completa.")


if __name__ == '__main__':
    asyncio.run(main())
