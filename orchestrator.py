"""
Orquestrador RPA — dois modos de operação:

1. Modo "ao vivo" (run_orchestrator):
   - Recebe steps decompostos pelo task_decomposer
   - Executa cada step via agente browser_use ao vivo
   - Gera a tool via tool_generator após execução

2. Modo "from_json" (run_from_json):
   - Lê um arquivo json_rpa/ já gravado
   - Normaliza as ações via json_processor
   - Delega geração das tools ao tool_generator (LLM), com aprovação do usuário no terminal
   - Gera o script de teste final (só se nenhuma tool foi rejeitada)
   Em nenhum momento o orquestrador cria ou modifica tools manualmente.
"""

import importlib.util
from pathlib import Path

from tool_generator import generate_tool_from_agent_result


# ---------------------------------------------------------------------------
# Modo ao vivo (browser_use agent)
# ---------------------------------------------------------------------------

async def run_orchestrator(steps, browser_session, llm, tools_dir: Path):
    for step in steps:
        tool_path = tools_dir / f"{step.tool_name}.py"

        if tool_path.exists():
            print(f"[orchestrator] Step {step.order} - cached: {step.tool_name}")
            ok = await _run_cached_tool(tool_path, step.tool_name, browser_session)
            if not ok:
                print(f"[orchestrator] Step {step.order} falhou: {step.tool_name} — fluxo interrompido")
                return False
        else:
            print(f"[orchestrator] Step {step.order} - executando via LLM agent: {step.tool_name}")
            result = await _run_llm_agent(step, browser_session, llm)
            path = await generate_tool_from_agent_result(step.tool_name, result)
            if path:
                print(f"[orchestrator] Tool gerada: {path}")
            else:
                print(f"[orchestrator] Tool rejeitada: {step.tool_name}")
    return True


async def _run_cached_tool(tool_path: Path, tool_name: str, browser_session) -> bool:
    """
    Executa uma tool já salva. As tools definem `async def <tool_name>(page)` e esperam
    uma Page do Playwright, então conectamos via CDP ao mesmo browser do agente.
    """
    from playwright.async_api import async_playwright

    spec = importlib.util.spec_from_file_location(tool_name, str(tool_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    func = getattr(module, tool_name)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(browser_session.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        return (await func(page)) is not False


async def _run_llm_agent(step, browser_session, llm):
    from browser_use import Agent

    agent = Agent(
        browser=browser_session,
        llm=llm,
        task=step.sub_task,
        directly_open_url=False,
    )
    return await agent.run()


# ---------------------------------------------------------------------------
# Modo from_json (JSON gravado → LLM gera tools → script de teste)
# ---------------------------------------------------------------------------

def run_from_json(json_path: Path) -> None:
    """
    Pipeline completo: JSON gravado → normalização → LLM gera tools → script de teste.

    O tool_generator (LLM) recebe a lista completa de ações de cada micro-processo
    e é responsável por gerar o código Python sem omitir nenhum passo.
    """
    from json_processor import (
        _normalize_actions,
        group_into_micro_processes,
        name_groups,
        save_test_script,
        TOOLS_DIR,
        TESTS_DIR,
    )
    import json

    data = json.loads(json_path.read_text(encoding="utf-8"))
    workflow_name = data.get("name", json_path.stem)
    raw_steps = data.get("steps", [])

    print(f"\n[orchestrator] Processando JSON: {json_path.name}")
    print(f"  Workflow: {workflow_name}")
    print(f"  Total de ações brutas no JSON: {len(raw_steps)}")

    # Etapa 1: normaliza todas as ações sem perder nenhuma
    actions = _normalize_actions(raw_steps)
    print(f"  Ações normalizadas: {len(actions)} (nenhuma omitida)")

    # Etapa 2: agrupa por micro-processo (URL/tela)
    groups = group_into_micro_processes(actions)
    named = name_groups(groups)

    print(f"  Micro-processos identificados: {len(named)}")
    for g in named:
        print(f"    - {g['tool_name']}: {len(g['actions'])} ações")

    # Etapa 3: gera tools via LLM — um por micro-processo, cada uma aprovada pelo usuário no terminal
    existing = {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
    from json_processor import save_tools
    save_tools(named, TOOLS_DIR, existing)

    rejected = [g["tool_name"] for g in named if g.get("rejected")]
    if rejected:
        print(f"\n[orchestrator] Tools rejeitadas: {', '.join(rejected)}")
        print("[orchestrator] Script de teste não gerado — o fluxo ficaria incompleto.")
        return

    # Etapa 4: gera script de teste que encadeia todas as tools
    save_test_script(workflow_name, named, TESTS_DIR)

    print(f"\n[orchestrator] Pipeline concluído para: {workflow_name}")
