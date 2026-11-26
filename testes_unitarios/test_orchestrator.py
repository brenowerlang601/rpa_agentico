"""
Orquestrador (orchestrator.py).

Nada ao vivo: o agente browser_use, o Playwright conectado por CDP, o LLM (_call_llm) e o
input() do terminal são falsos na fronteira. run_from_json roda o pipeline real do
json_processor/tool_generator sobre uma gravação sintética em pasta temporária.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import orchestrator
import tool_generator
from task_decomposer import Step

URL_FORM = "https://erp.exemplo.local/app/form"
URL_LISTA = "https://erp.exemplo.local/app/lista"


# ---------------------------------------------------------------------------
# run_orchestrator (modo ao vivo)
# ---------------------------------------------------------------------------

@pytest.fixture
def ao_vivo(monkeypatch):
    """Troca as três fronteiras do modo ao vivo e registra a ordem das chamadas."""
    log = []
    estado = {"cache_ok": {}, "gerada": {}}

    async def cache(tool_path, tool_name, browser_session):
        log.append(("cache", tool_name, browser_session))
        return estado["cache_ok"].get(tool_name, True)

    async def agente(step, browser_session, llm):
        log.append(("agente", step.tool_name, browser_session, llm))
        return f"resultado_{step.tool_name}"

    async def gerar(task_name, result):
        log.append(("gerar", task_name, result))
        return estado["gerada"].get(task_name, f"tools/{task_name}.py")

    monkeypatch.setattr(orchestrator, "_run_cached_tool", cache)
    monkeypatch.setattr(orchestrator, "_run_llm_agent", agente)
    monkeypatch.setattr(orchestrator, "generate_tool_from_agent_result", gerar)
    return log, estado


def _steps(*nomes):
    return [Step(tool_name=n, sub_task=f"faça {n}", order=i + 1) for i, n in enumerate(nomes)]


def test_step_com_tool_salva_executa_a_tool_e_sem_tool_usa_o_agente(ao_vivo, tmp_path):
    log, _ = ao_vivo
    (tmp_path / "tools" / "login_erp.py").write_text("# tool", encoding="utf-8")

    ok = asyncio.run(orchestrator.run_orchestrator(_steps("login_erp", "pesquisar"), "SESSAO", "LLM",
                                                   tmp_path / "tools"))

    assert ok is True
    assert log == [
        ("cache", "login_erp", "SESSAO"),
        ("agente", "pesquisar", "SESSAO", "LLM"),
        ("gerar", "pesquisar", "resultado_pesquisar"),
    ]


def test_tool_salva_que_falha_interrompe_o_fluxo(ao_vivo, tmp_path, capsys):
    log, estado = ao_vivo
    (tmp_path / "tools" / "login_erp.py").write_text("# tool", encoding="utf-8")
    estado["cache_ok"]["login_erp"] = False

    ok = asyncio.run(orchestrator.run_orchestrator(_steps("login_erp", "pesquisar"), "S", "L",
                                                   tmp_path / "tools"))

    assert ok is False
    assert [c[0] for c in log] == ["cache"]
    assert "fluxo interrompido" in capsys.readouterr().out


def test_tool_rejeitada_no_modo_ao_vivo_nao_interrompe_o_fluxo(ao_vivo, tmp_path, capsys):
    log, estado = ao_vivo
    estado["gerada"]["login_erp"] = None

    ok = asyncio.run(orchestrator.run_orchestrator(_steps("login_erp", "pesquisar"), "S", "L",
                                                   tmp_path / "tools"))

    assert ok is True
    assert [c[:2] for c in log if c[0] == "gerar"] == [("gerar", "login_erp"), ("gerar", "pesquisar")]
    assert "Tool rejeitada: login_erp" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _run_cached_tool (Playwright conectado por CDP — falso)
# ---------------------------------------------------------------------------

class _PlaywrightFalso:
    """async_playwright() falso: registra o cdp_url e entrega uma page marcada."""

    def __init__(self, contextos):
        self.contextos = contextos
        self.cdp = []
        self.criados = []

    def __call__(self):
        return self

    async def __aenter__(self):
        falso = self

        class Contexto:
            def __init__(self, pages):
                self.pages = pages

            async def new_page(self):
                falso.criados.append("page")
                return "PAGE_NOVA"

        class Browser:
            contexts = [Contexto(p) for p in self.contextos]

            async def new_context(self):
                falso.criados.append("context")
                return Contexto([])

        async def connect_over_cdp(url):
            falso.cdp.append(url)
            return Browser()

        return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect_over_cdp))

    async def __aexit__(self, *exc):
        return False


def _tool(tmp_path, nome, retorno):
    caminho = tmp_path / "tools" / f"{nome}.py"
    caminho.write_text(
        f"VISTAS = []\nasync def {nome}(page):\n    VISTAS.append(page)\n    return {retorno}\n",
        encoding="utf-8",
    )
    return caminho


@pytest.mark.parametrize("retorno, esperado", [("True", True), ("None", True), ("False", False)])
def test_tool_salva_roda_na_page_do_browser_do_agente(monkeypatch, tmp_path, retorno, esperado):
    import playwright.async_api

    falso = _PlaywrightFalso([["PAGE_EXISTENTE"]])
    monkeypatch.setattr(playwright.async_api, "async_playwright", falso)
    caminho = _tool(tmp_path, "minha_tool", retorno)
    sessao = SimpleNamespace(cdp_url="http://127.0.0.1:1/falso")

    ok = asyncio.run(orchestrator._run_cached_tool(caminho, "minha_tool", sessao))

    assert ok is esperado
    assert falso.cdp == ["http://127.0.0.1:1/falso"]
    assert falso.criados == []


def test_tool_salva_cria_contexto_e_page_quando_o_browser_nao_tem(monkeypatch, tmp_path):
    import playwright.async_api

    falso = _PlaywrightFalso([])
    monkeypatch.setattr(playwright.async_api, "async_playwright", falso)
    caminho = _tool(tmp_path, "minha_tool", "True")

    ok = asyncio.run(orchestrator._run_cached_tool(caminho, "minha_tool", SimpleNamespace(cdp_url="x")))

    assert ok is True
    assert falso.criados == ["context", "page"]


def test_tool_salva_usa_a_page_que_ja_esta_aberta(monkeypatch, tmp_path):
    import playwright.async_api

    monkeypatch.setattr(playwright.async_api, "async_playwright", _PlaywrightFalso([["PAGE_EXISTENTE"]]))
    caminho = _tool(tmp_path, "minha_tool", "True")
    vistas = []
    monkeypatch.setattr(orchestrator.importlib.util, "module_from_spec",
                        _registrando_modulo(orchestrator.importlib.util.module_from_spec, vistas))

    asyncio.run(orchestrator._run_cached_tool(caminho, "minha_tool", SimpleNamespace(cdp_url="x")))

    assert vistas[0].VISTAS == ["PAGE_EXISTENTE"]


def _registrando_modulo(original, guardados):
    def falso(spec):
        modulo = original(spec)
        guardados.append(modulo)
        return modulo

    return falso


# ---------------------------------------------------------------------------
# _run_llm_agent (browser_use.Agent falso)
# ---------------------------------------------------------------------------

def test_agente_recebe_a_sub_task_do_step_e_nao_abre_url_sozinho(monkeypatch):
    import browser_use

    criados = []

    class AgentFalso:
        def __init__(self, **kwargs):
            criados.append(kwargs)

        async def run(self):
            return "HISTORICO"

    monkeypatch.setattr(browser_use, "Agent", AgentFalso)
    step = Step(tool_name="login_erp", sub_task="Entre no ERP", order=1)

    assert asyncio.run(orchestrator._run_llm_agent(step, "SESSAO", "LLM")) == "HISTORICO"
    assert criados == [{"browser": "SESSAO", "llm": "LLM", "task": "Entre no ERP", "directly_open_url": False}]


# ---------------------------------------------------------------------------
# run_from_json (gravação → tools aprovadas no terminal → script de teste)
# ---------------------------------------------------------------------------

def _gravacao_duas_telas(nome="Fluxo de exemplo"):
    dados = {
        "version": "1.0",
        "steps": [
            {"type": "navigation", "timestamp": "1000", "url": URL_FORM},
            {"type": "click", "timestamp": "2000", "url": URL_FORM, "xpath": 'id("nome")',
             "targetText": "NOME"},
            {"type": "input", "timestamp": "3000", "url": URL_FORM, "xpath": 'id("nome")', "value": "ABC"},
            {"type": "click", "timestamp": "4000", "url": URL_FORM, "xpath": 'id("ok")', "elementText": "OK"},
            {"type": "click", "timestamp": "5000", "url": URL_LISTA, "xpath": 'id("item")',
             "targetText": "Item 1"},
        ],
    }
    if nome:
        dados["name"] = nome
    return dados


@pytest.fixture
def terminal(monkeypatch):
    """LLM falso + respostas do terminal. Devolve (prompts enviados, fila de respostas)."""
    prompts, respostas = [], []

    def llm(prompt):
        prompts.append(prompt)
        nome = prompt.split("chamada `", 1)[1].split("`", 1)[0]
        return f"async def {nome}(page) -> bool:\n    return True\n"

    monkeypatch.setattr(tool_generator, "_call_llm", llm)
    monkeypatch.setattr("builtins.input", lambda _p="": respostas.pop(0))
    return prompts, respostas


def _escrever(tmp_path, dados, nome="fluxo.json"):
    caminho = tmp_path / "json_rpa" / nome
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return caminho


def test_json_com_todas_as_tools_aprovadas_gera_tools_e_script(terminal, tmp_path):
    prompts, respostas = terminal
    respostas += ["a", "a"]

    orchestrator.run_from_json(_escrever(tmp_path, _gravacao_duas_telas()))

    tools = sorted(p.name for p in (tmp_path / "tools").glob("*.py"))
    assert tools == ["clicar_item_1_lista.py", "preencher_e_confirmar_form.py"]
    for t in tools:
        assert (tmp_path / "tools" / t).read_text(encoding="utf-8").startswith("# actions_fp: ")
    script = (tmp_path / "tests" / "fluxo_de_exemplo.py").read_text(encoding="utf-8")
    assert script.splitlines()[0] == '# rpa_steps: ["preencher_e_confirmar_form", "clicar_item_1_lista"]'
    # Nenhuma ação gravada se perde: navegação + fill (click+input) + click, e o click da lista
    assert len(prompts) == 2
    assert "exatamente 3 blocos" in prompts[0] and '"type": "navigation"' in prompts[0]
    assert "exatamente 1 blocos" in prompts[1] and 'id(\\"item\\")' in prompts[1]


def test_json_com_tool_rejeitada_nao_gera_script(terminal, tmp_path, capsys):
    _, respostas = terminal
    respostas += ["a", "r"]

    orchestrator.run_from_json(_escrever(tmp_path, _gravacao_duas_telas()))

    assert [p.name for p in (tmp_path / "tools").glob("*.py")] == ["preencher_e_confirmar_form.py"]
    assert list((tmp_path / "tests").iterdir()) == []
    saida = capsys.readouterr().out
    assert "Tools rejeitadas: clicar_item_1_lista" in saida
    assert "Script de teste não gerado" in saida


def test_json_com_tools_identicas_ja_salvas_reaproveita_sem_chamar_o_llm(terminal, tmp_path):
    prompts, respostas = terminal
    respostas += ["a", "a"]
    caminho = _escrever(tmp_path, _gravacao_duas_telas())
    orchestrator.run_from_json(caminho)
    (tmp_path / "tests" / "fluxo_de_exemplo.py").unlink()
    prompts.clear()

    orchestrator.run_from_json(caminho)

    assert prompts == []
    assert (tmp_path / "tests" / "fluxo_de_exemplo.py").exists()


def test_json_sem_nome_usa_o_nome_do_arquivo_no_script(terminal, tmp_path):
    _, respostas = terminal
    respostas += ["a", "a"]

    orchestrator.run_from_json(_escrever(tmp_path, _gravacao_duas_telas(nome=None), "pedido_venda.json"))

    assert [p.name for p in (tmp_path / "tests").iterdir()] == ["pedido_venda.py"]


def test_json_com_senha_mascarada_pede_secret_e_nao_manda_a_senha_ao_llm(terminal, tmp_path, monkeypatch):
    import getpass

    import secrets_store

    prompts, respostas = terminal
    monkeypatch.setattr(getpass, "getpass", lambda _p="": "s3nha")
    dados = {
        "name": "Login",
        "steps": [
            {"type": "navigation", "timestamp": "1000", "url": URL_FORM},
            {"type": "click", "timestamp": "2000", "url": URL_FORM, "xpath": 'id("senha")', "targetText": "Senha"},
            {"type": "input", "timestamp": "3000", "url": URL_FORM, "xpath": 'id("senha")', "value": "********"},
            {"type": "click", "timestamp": "4000", "url": URL_FORM, "xpath": 'id("entrar")', "elementText": "Entrar"},
        ],
    }
    respostas += ["ERP_SENHA", "a"]

    orchestrator.run_from_json(_escrever(tmp_path, dados))

    assert secrets_store.get_secret("ERP_SENHA") == "s3nha"
    assert '"secret": "ERP_SENHA"' in prompts[0]
    assert "********" not in prompts[0]
    assert (tmp_path / "tests" / "login.py").exists()
