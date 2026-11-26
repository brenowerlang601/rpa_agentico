"""
Script Playwright do fluxo (build_script) e o que gira em torno dele: ler os steps de volta
(read_script_steps), caminho do script salvo, process_json e a linha de comando.

O script gerado é executado aqui DENTRO do processo, com tools falsas e um async_playwright
falso: nada de browser, CDP ou ERP.
"""

import asyncio
import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest

import json_processor as jp
import tool_generator
from testes_unitarios.gravacao_exemplo import gravacao

MODULO = Path(jp.__file__).resolve()


# ---------------------------------------------------------------------------
# Execução do script gerado com Playwright e tools falsos
# ---------------------------------------------------------------------------

class PaginaFalsa:
    def __init__(self):
        self.chamadas = []


class ContextoFalso:
    def __init__(self, paginas):
        self.pages = paginas
        self.novas = 0

    async def new_page(self):
        self.novas += 1
        p = PaginaFalsa()
        self.pages.append(p)
        return p


class BrowserFalso:
    def __init__(self, contextos):
        self.contexts = contextos
        self.paginas_novas = []

    async def new_page(self):
        p = PaginaFalsa()
        self.paginas_novas.append(p)
        return p

    async def new_context(self):
        c = ContextoFalso([])
        self.contexts.append(c)
        return c


class PlaywrightFalso:
    def __init__(self, contextos_cdp):
        self.registro = {"launch": [], "cdp": []}
        self.browser_cdp = BrowserFalso(contextos_cdp)
        self.browser_local = BrowserFalso([])
        pw = self

        class Chromium:
            async def launch(self, headless):
                pw.registro["launch"].append(headless)
                return pw.browser_local

            async def connect_over_cdp(self, url):
                pw.registro["cdp"].append(url)
                return pw.browser_cdp

        self.chromium = Chromium()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def pagina_usada(self):
        if self.browser_local.paginas_novas:
            return self.browser_local.paginas_novas[0]
        return self.browser_cdp.contexts[0].pages[0]


TOOLS_FALSAS = {
    "passo_ok": "return True",
    "passo_none": "return None",
    "passo_falso": "return False",
    "passo_explode": 'raise RuntimeError("elemento sumiu\\nsegunda linha")',
    "passo_explode_mudo": "raise RuntimeError()",
}


@pytest.fixture
def executar(tmp_path, monkeypatch):
    """
    Grava o script em tests/, cria tools falsas em tools/ e roda run() com Playwright falso.
    Devolve (exit_code, linhas_impressas, playwright_falso).
    """
    # __init__.py: um pacote regular vence o tools/ do repositório, mesmo vindo depois no sys.path
    (tmp_path / "tools" / "__init__.py").write_text("", encoding="utf-8")
    for nome, corpo in TOOLS_FALSAS.items():
        (tmp_path / "tools" / f"{nome}.py").write_text(
            f"async def {nome}(page):\n    page.chamadas.append({nome!r})\n    {corpo}\n", encoding="utf-8")

    # O pacote tools/ real pode já estar importado: esconde durante o teste
    for k in [k for k in sys.modules if k == "tools" or k.startswith("tools.")]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.setattr(sys, "path", list(sys.path))
    carregados = []

    def _executar(code, contextos_cdp=None):
        script = tmp_path / "tests" / f"script_{len(carregados)}.py"
        script.write_text(code, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"rpa_script_teste_{len(carregados)}", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        carregados.append(mod)
        pw = PlaywrightFalso([ContextoFalso([PaginaFalsa()])] if contextos_cdp is None else contextos_cdp)
        mod.async_playwright = pw
        with pytest.raises(SystemExit) as saida:
            asyncio.run(mod.run())
        return saida.value.code, pw, mod

    yield _executar
    for k in [k for k in sys.modules if k == "tools" or k.startswith("tools.")]:
        del sys.modules[k]


def _linhas(capsys):
    return capsys.readouterr().out.strip().splitlines()


def test_script_com_todos_os_passos_ok_termina_true_e_codigo_0(executar, capsys):
    code, pw, _ = executar(jp.build_script("Fluxo", ["passo_ok", "passo_none"], headless=True))
    linhas = _linhas(capsys)

    assert code == 0
    assert linhas[-1] == "true"
    assert "__RUNNING__0:passo_ok" in linhas
    assert "__STEP_RESULT__0:ok:passo_ok" in linhas
    assert "__STEP_RESULT__1:ok:passo_none" in linhas  # None conta como PASS
    assert "  Passaram: 2 | Falhou: 0 | Não executados: 0" in linhas
    assert "Relatório — Fluxo" in linhas
    assert pw.pagina_usada().chamadas == ["passo_ok", "passo_none"]


def test_passo_que_retorna_false_falha_e_para_o_fluxo(executar, capsys):
    code, pw, _ = executar(jp.build_script("F", ["passo_ok", "passo_falso", "passo_none"], headless=True))
    linhas = _linhas(capsys)

    assert code == 1
    assert linhas[-1] == "false"
    assert "__STEP_RESULT__1:fail:passo_falso" in linhas
    assert "  [FAIL] passo_falso: a tool retornou False" in linhas
    assert not any("passo_none" in x for x in linhas)
    assert "  Passaram: 1 | Falhou: 1 | Não executados: 1" in linhas
    assert pw.pagina_usada().chamadas == ["passo_ok", "passo_falso"]


def test_excecao_no_passo_falha_com_a_primeira_linha_da_mensagem(executar, capsys):
    code, _, _ = executar(jp.build_script("F", ["passo_explode", "passo_ok"], headless=True))
    linhas = _linhas(capsys)

    assert code == 1
    assert "  [FAIL] passo_explode: elemento sumiu" in linhas
    assert "__STEP_RESULT__0:fail:passo_explode" in linhas
    assert "  Passaram: 0 | Falhou: 1 | Não executados: 1" in linhas


def test_excecao_sem_mensagem_mostra_o_tipo_do_erro(executar, capsys):
    executar(jp.build_script("F", ["passo_explode_mudo"], headless=True))

    assert "  [FAIL] passo_explode_mudo: RuntimeError" in _linhas(capsys)


def test_start_index_numera_os_marcadores_pela_posicao_no_fluxo(executar, capsys):
    executar(jp.build_script("F", ["passo_ok", "passo_none"], headless=True, start_index=3))
    linhas = _linhas(capsys)

    assert "__RUNNING__3:passo_ok" in linhas
    assert "__STEP_RESULT__4:ok:passo_none" in linhas


def test_tool_repetida_e_importada_uma_vez_e_executada_duas(executar, capsys):
    code = jp.build_script("F", ["passo_ok", "passo_ok"], headless=True)

    assert code.count("from tools.passo_ok import passo_ok") == 1
    _, pw, _ = executar(code)
    assert pw.pagina_usada().chamadas == ["passo_ok", "passo_ok"]


def test_headless_abre_browser_proprio_sem_cdp(executar, capsys):
    _, pw, _ = executar(jp.build_script("F", ["passo_ok"], headless=True))

    assert pw.registro == {"launch": [True], "cdp": []}


def test_sem_headless_conecta_no_cdp_informado_e_usa_a_aba_aberta(executar, capsys):
    _, pw, _ = executar(jp.build_script("F", ["passo_ok"], headless=False, cdp_url="http://127.0.0.1:9333"))

    assert pw.registro == {"launch": [], "cdp": ["http://127.0.0.1:9333"]}
    assert pw.browser_cdp.contexts[0].pages[0].chamadas == ["passo_ok"]


def test_cdp_sem_contexto_nem_aba_cria_os_dois(executar, capsys):
    _, pw, _ = executar(jp.build_script("F", ["passo_ok"], headless=False, cdp_url="localhost:9333"), contextos_cdp=[])

    assert pw.registro["cdp"] == ["http://localhost:9333"]
    assert pw.browser_cdp.contexts[0].novas == 1
    assert pw.browser_cdp.contexts[0].pages[0].chamadas == ["passo_ok"]


def test_script_salvo_decide_headless_pela_variavel_rpa_headless(executar, capsys, monkeypatch):
    code = jp.build_script("F", ["passo_ok"])
    monkeypatch.setenv("RPA_HEADLESS", "1")

    _, pw, _ = executar(code)

    assert pw.registro["launch"] == [True]


def test_script_salvo_sem_variaveis_conecta_no_cdp_padrao(executar, capsys, monkeypatch):
    monkeypatch.delenv("RPA_HEADLESS", raising=False)
    monkeypatch.delenv("CDP_URL", raising=False)

    _, pw, _ = executar(jp.build_script("F", ["passo_ok"]))

    assert pw.registro == {"launch": [], "cdp": ["http://localhost:9222"]}


def test_script_salvo_usa_a_variavel_cdp_url(executar, capsys, monkeypatch):
    monkeypatch.setenv("RPA_HEADLESS", "0")
    monkeypatch.setenv("CDP_URL", "http://localhost:9444")

    _, pw, _ = executar(jp.build_script("F", ["passo_ok"]))

    assert pw.registro["cdp"] == ["http://localhost:9444"]


def test_cdp_endpoint_do_script_normaliza_o_endereco(executar, capsys, monkeypatch):
    _, _, mod = executar(jp.build_script("F", ["passo_ok"], headless=True))
    resolvidos = {"host.docker.internal": "172.17.0.1"}

    def resolver(host):
        if host in resolvidos:
            return resolvidos[host]
        raise OSError("sem DNS no teste")

    monkeypatch.setattr(mod.socket, "gethostbyname", resolver)

    assert mod._cdp_endpoint("localhost") == "http://localhost:9222"
    assert mod._cdp_endpoint("https://localhost:1234") == "https://localhost:1234"
    assert mod._cdp_endpoint("http://host.docker.internal:9222") == "http://172.17.0.1:9222"
    assert mod._cdp_endpoint("nome-sem-dns:9333") == "http://nome-sem-dns:9333"


def test_nome_do_workflow_com_aspas_e_quebra_de_linha_vai_intacto(executar, capsys):
    nome = 'Pedido "urgente"\n\'teste\' {chaves} C:\\temp\\novo \'\'\''
    _, _, mod = executar(jp.build_script(nome, ["passo_ok"], headless=True))

    assert mod.WORKFLOW_NAME == nome


# ---------------------------------------------------------------------------
# build_script — validação e conteúdo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome", ["../x", "tools.x", "2x", "a b", "", "x;import os"])
def test_build_script_recusa_nome_de_tool_invalido(nome):
    with pytest.raises(ValueError, match="Nome de tool inválido"):
        jp.build_script("F", ["passo_ok", nome])


def test_build_script_gera_codigo_python_valido():
    compile(jp.build_script("F", ["a", "b"]), "script.py", "exec")
    compile(jp.build_script("F", [], headless=True, cdp_url="http://localhost:1"), "script.py", "exec")


def test_build_script_escreve_os_steps_no_cabecalho():
    assert jp.build_script("F", ["a", "b"]).splitlines()[0] == '# rpa_steps: ["a", "b"]'


def test_build_script_fixa_headless_e_cdp_quando_informados():
    code = jp.build_script("F", ["a"], headless=False, cdp_url="http://localhost:9333")

    assert "HEADLESS = False" in code
    assert "CDP_URL = 'http://localhost:9333'" in code
    assert "RPA_HEADLESS" not in code


# ---------------------------------------------------------------------------
# read_script_steps — ida e volta com build_script
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("passos", [
    [], ["a"], ["fazer_login_forms", "preencher_e_confirmar_pagina", "fazer_login_forms"], ["x_v2", "_y", "Z9"],
])
def test_read_script_steps_le_de_volta_o_que_build_script_escreveu(passos):
    assert jp.read_script_steps(jp.build_script("F", passos)) == passos
    assert jp.read_script_steps(jp.build_script("F", passos, headless=True, start_index=5)) == passos


def test_read_script_steps_de_texto_vazio_devolve_lista_vazia():
    assert jp.read_script_steps("") == []


def test_read_script_steps_le_o_formato_antigo():
    antigo = (
        "import asyncio\n"
        "async def run():\n"
        "    if True:\n"
        "        results['fazer_login'] = await fazer_login(page)\n"
        "        results['preencher_form'] = await preencher_form(page)\n"
        "    results['fora_do_bloco'] = 1\n"
    )

    assert jp.read_script_steps(antigo) == ["fazer_login", "preencher_form"]


def test_read_script_steps_com_cabecalho_quebrado_cai_no_formato_antigo():
    code = "# rpa_steps: [quebrado\n        results['a'] = await a(page)\n"

    assert jp.read_script_steps(code) == ["a"]


def test_read_script_steps_de_script_qualquer_devolve_lista_vazia():
    assert jp.read_script_steps("print('oi')\n") == []


# ---------------------------------------------------------------------------
# test_script_path e save_test_script
# ---------------------------------------------------------------------------

def test_caminho_do_script_usa_o_slug_do_workflow(tmp_path):
    assert jp.test_script_path("Pedido de Venda", tmp_path) == tmp_path / "pedido_de_venda.py"


def test_caminho_do_script_usa_o_nome_informado_sanitizado(tmp_path):
    assert jp.test_script_path("Ignorado", tmp_path, "meu-script.v2") == tmp_path / "meu_script_v2.py"


def test_nome_informado_nao_sai_da_pasta(tmp_path):
    p = jp.test_script_path("F", tmp_path, "../../etc/passwd")

    assert p.parent == tmp_path
    assert p.name == "______etc_passwd.py"


def test_nome_informado_vazio_usa_o_workflow(tmp_path):
    assert jp.test_script_path("Fluxo A", tmp_path, "") == tmp_path / "fluxo_a.py"


def test_workflow_sem_letras_vira_workflow(tmp_path):
    assert jp.test_script_path("!!!", tmp_path) == tmp_path / "workflow.py"


def test_save_test_script_grava_o_script_do_fluxo(tmp_path, capsys):
    passos = [{"tool_name": "navegar_form", "actions": []}, {"tool_name": "preencher_form", "actions": []}]

    p = jp.save_test_script("Fluxo A", passos, tmp_path / "tests")

    assert p == tmp_path / "tests" / "fluxo_a.py"
    code = p.read_text(encoding="utf-8")
    assert code == jp.build_script("Fluxo A", ["navegar_form", "preencher_form"])
    assert jp.read_script_steps(code) == ["navegar_form", "preencher_form"]


def test_save_test_script_com_nome_proprio(tmp_path, capsys):
    p = jp.save_test_script("Fluxo A", [{"tool_name": "a"}], tmp_path / "tests", script_name="outro")

    assert p == tmp_path / "tests" / "outro.py"
    assert p.exists()


# ---------------------------------------------------------------------------
# process_json e list_json_rpa
# ---------------------------------------------------------------------------

def test_list_json_rpa_lista_so_json_em_ordem(tmp_path):
    pasta = tmp_path / "json_rpa"
    for n in ["b.json", "a.json", "c.txt"]:
        (pasta / n).write_text("{}", encoding="utf-8")

    assert jp.list_json_rpa(pasta) == [pasta / "a.json", pasta / "b.json"]


def test_list_json_rpa_pasta_vazia(tmp_path):
    assert jp.list_json_rpa(tmp_path / "json_rpa") == []


@pytest.fixture
def save_tools_falso(monkeypatch):
    chamadas = []
    monkeypatch.setattr(jp, "save_tools", lambda named, tools_dir, existing: chamadas.append((named, tools_dir, existing)))
    return chamadas


def test_process_json_agrupa_nomeia_e_manda_gerar_as_tools(tmp_path, save_tools_falso, capsys):
    (tmp_path / "tools" / "ja_existe.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "__init__.py").write_text("", encoding="utf-8")
    arq = tmp_path / "json_rpa" / "cadastro.json"
    arq.write_text(json.dumps(gravacao()), encoding="utf-8")

    jp.process_json(arq)

    (named, tools_dir, existing), = save_tools_falso
    assert [s["tool_name"] for s in named] == ["preencher_e_confirmar_form"]
    assert [a["type"] for a in named[0]["actions"]] == ["navigation", "fill", "click"]
    assert tools_dir == jp.TOOLS_DIR
    assert existing == {"ja_existe"}
    out = capsys.readouterr().out
    assert "Workflow: Cadastro de exemplo" in out
    assert "Total de acoes brutas: 4" in out
    assert "preencher_e_confirmar_form  (3 acoes)" in out


def test_process_json_sem_nome_usa_o_nome_do_arquivo(tmp_path, save_tools_falso, capsys):
    arq = tmp_path / "json_rpa" / "sem_nome.json"
    arq.write_text(json.dumps({"steps": []}), encoding="utf-8")

    jp.process_json(arq)

    assert "Workflow: sem_nome" in capsys.readouterr().out
    assert save_tools_falso[0][0] == []


# ---------------------------------------------------------------------------
# Linha de comando (python json_processor.py) — gerador falso, nada de OpenAI
# ---------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Roda o bloco __main__ com input() e o gerador de tools falsos."""
    geradas = []

    def gerar(func_name, actions, fingerprint="", expect_url_change=False):
        geradas.append(func_name)
        return None  # usuário rejeita: nada é salvo

    monkeypatch.setattr(tool_generator, "generate_tool_from_actions", gerar)

    def rodar(escolha):
        monkeypatch.setattr("builtins.input", lambda _="": escolha)
        runpy.run_path(str(MODULO), run_name="__main__")
        return geradas

    return rodar


def _gravar(tmp_path, arquivo, nome, url):
    g = gravacao()
    g["name"] = nome
    for s in g["steps"]:
        s["url"] = url
    (tmp_path / "json_rpa" / arquivo).write_text(json.dumps(g), encoding="utf-8")


def test_cli_sem_gravacoes_sai_com_erro(cli, capsys):
    with pytest.raises(SystemExit) as saida:
        cli("1")

    assert saida.value.code == 1
    assert "Nenhum arquivo JSON encontrado" in capsys.readouterr().out


def test_cli_processa_a_gravacao_escolhida(cli, tmp_path, capsys):
    _gravar(tmp_path, "a.json", "Fluxo A", "https://erp.exemplo.local/app/tela_a")
    _gravar(tmp_path, "b.json", "Fluxo B", "https://erp.exemplo.local/app/tela_b")

    assert cli("2") == ["preencher_e_confirmar_tela_b"]
    out = capsys.readouterr().out
    assert "1. a.json" in out and "2. b.json" in out
    assert "0. Processar todos" in out


def test_cli_zero_processa_todas(cli, tmp_path, capsys):
    _gravar(tmp_path, "a.json", "Fluxo A", "https://erp.exemplo.local/app/tela_a")
    _gravar(tmp_path, "b.json", "Fluxo B", "https://erp.exemplo.local/app/tela_b")

    assert cli("0") == ["preencher_e_confirmar_tela_a", "preencher_e_confirmar_tela_b"]


def test_cli_marca_gravacao_com_script_ja_gerado(cli, tmp_path, capsys):
    _gravar(tmp_path, "a.json", "Fluxo A", "https://erp.exemplo.local/app/tela_a")
    _gravar(tmp_path, "b.json", "Fluxo B", "https://erp.exemplo.local/app/tela_b")
    (tmp_path / "tests" / "fluxo_a.py").write_text("", encoding="utf-8")

    cli("1")

    out = capsys.readouterr().out
    assert "1. a.json [já gerado]" in out
    assert "2. b.json\n" in out


def test_cli_uma_gravacao_nao_oferece_processar_todas(cli, tmp_path, capsys):
    _gravar(tmp_path, "a.json", "Fluxo A", "https://erp.exemplo.local/app/tela_a")

    assert cli("1") == ["preencher_e_confirmar_tela_a"]
    assert "Processar todos" not in capsys.readouterr().out


@pytest.mark.parametrize("escolha", ["9", "abc", ""])
def test_cli_escolha_invalida_sai_com_erro(cli, tmp_path, capsys, escolha):
    _gravar(tmp_path, "a.json", "Fluxo A", "https://erp.exemplo.local/app/tela_a")
    _gravar(tmp_path, "b.json", "Fluxo B", "https://erp.exemplo.local/app/tela_b")

    with pytest.raises(SystemExit) as saida:
        cli(escolha)

    assert saida.value.code == 1
    assert "Escolha inválida." in capsys.readouterr().out


@pytest.mark.xfail(strict=True, reason="BUG: escolha 0/negativa vira índice negativo (files[-1]) e processa "
                                       "uma gravação em vez de dar 'Escolha inválida'")
@pytest.mark.parametrize("escolha, arquivos", [("0", ["a.json"]), ("-1", ["a.json", "b.json", "c.json"])])
def test_cli_escolha_zero_ou_negativa_fora_das_opcoes_e_invalida(cli, tmp_path, capsys, escolha, arquivos):
    for i, arq in enumerate(arquivos):
        _gravar(tmp_path, arq, f"Fluxo {i}", f"https://erp.exemplo.local/app/tela_{i}")

    with pytest.raises(SystemExit):
        cli(escolha)
