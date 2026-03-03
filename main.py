import asyncio
import importlib.util
from pathlib import Path

from browser_use import Agent, Browser, ChatOpenAI

from tool_generator import generate_tool

TASK_NAME = "login_erp"
TOOLS_DIR = Path("tools")


async def main():
    tool_path = TOOLS_DIR / f"{TASK_NAME}.py"

    # Se a tool já existe, executar diretamente (sem LLM)
    if tool_path.exists():
        print(f"[main] Executando tool cached: {tool_path}")
        spec = importlib.util.spec_from_file_location(TASK_NAME, tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        await module.run()
        return

    # 1ª execução: usar o agente LLM
    print("[main] Nenhuma tool encontrada. Executando com agente LLM...")

    browser = Browser(cdp_url='http://localhost:9222')
    llm = ChatOpenAI(model='gpt-5.1')

    task = f"""
    Acesse o ERP da systextil e faça login com usuario: INTERSYS , senha: A%h^9l89
    Site:
    - LINK ERP: https://devs.systextil.com.br/systextil/
    """

    agent = Agent(
        browser=browser,
        llm=llm,
        task=task,
    )

    result = await agent.run()

    # Gerar o script Playwright automaticamente a partir do histórico
    await generate_tool(TASK_NAME, result, llm)
    print("[main] Tool gerada! Próximas execuções serão sem LLM.")

    return result


if __name__ == '__main__':
    asyncio.run(main())
