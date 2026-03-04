import importlib.util
from pathlib import Path

from browser_use import Agent, BrowserSession, ChatOpenAI

from task_decomposer import Step
from tool_generator import generate_tool


async def run_orchestrator(steps: list[Step], browser_session: BrowserSession, llm: ChatOpenAI, tools_dir: Path):
    for step in steps:
        tool_path = tools_dir / f"{step.tool_name}.py"

        if tool_path.exists():
            print(f"[orchestrator] Step {step.order} - cached: {step.tool_name}")
            await _run_cached_tool(tool_path, step.tool_name)
        else:
            print(f"[orchestrator] Step {step.order} - LLM: {step.tool_name}")
            result = await _run_llm_agent(step, browser_session, llm)
            await generate_tool(step.tool_name, result, llm)
            print(f"[orchestrator] Tool gerada: tools/{step.tool_name}.py")


async def _run_cached_tool(tool_path: Path, tool_name: str):
    spec = importlib.util.spec_from_file_location(tool_name, str(tool_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    await module.run()


async def _run_llm_agent(step: Step, browser_session: BrowserSession, llm: ChatOpenAI):
    agent = Agent(
        browser=browser_session,   # reutiliza a mesma sessão — sem reconectar
        llm=llm,
        task=step.sub_task,
        directly_open_url=False,   # não extrai URL da task nem navega automaticamente
    )
    return await agent.run()
