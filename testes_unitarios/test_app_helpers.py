"""
Funções auxiliares do app.py (_error, listagem de tools/workflows, validação de steps,
URL do CDP, preview do script e montagem do workflow a partir de uma gravação).

As pastas tools/ e tests/ são as temporárias do conftest (o teste roda com cwd em tmp_path).
"""

from pathlib import Path

import pytest

import app
from json_processor import build_script
from testes_unitarios.gravacao_exemplo import gravacao


def _tool(nome: str, codigo: str | None = None) -> Path:
    p = Path("tools") / f"{nome}.py"
    p.write_text(codigo if codigo is not None else f"async def {nome}(page) -> bool:\n    return True\n",
                 encoding="utf-8")
    return p


def _script(nome: str, steps: list[str]) -> Path:
    p = Path("tests") / f"{nome}.py"
    p.write_text(build_script(nome, steps), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _error
# ---------------------------------------------------------------------------

def test_error_devolve_json_com_mensagem_status_e_campos_extras():
    with app.app.app_context():
        resposta, status = app._error("deu ruim", 409, workflows=["a"])

    assert status == 409
    assert resposta.get_json() == {"error": "deu ruim", "workflows": ["a"]}


def test_error_sem_status_usa_400():
    with app.app.app_context():
        resposta, status = app._error("faltou campo")

    assert status == 400
    assert resposta.get_json() == {"error": "faltou campo"}


# ---------------------------------------------------------------------------
# _existing_tools / _all_tools
# ---------------------------------------------------------------------------

def test_existing_tools_lista_os_nomes_sem_o_init_e_sem_arquivos_que_nao_sao_py():
    _tool("fazer_login")
    _tool("navegar")
    _tool("__init__", "")
    Path("tools/anotacao.txt").write_text("x", encoding="utf-8")

    assert app._existing_tools() == {"fazer_login", "navegar"}


def test_existing_tools_com_pasta_vazia_devolve_conjunto_vazio():
    assert app._existing_tools() == set()


def test_all_tools_devolve_nome_e_codigo_em_ordem_alfabetica_sem_o_init():
    _tool("zeta", "# z\n")
    _tool("alfa", "# á com acento\n")
    _tool("__init__", "")

    assert app._all_tools() == [
        {"name": "alfa", "code": "# á com acento\n"},
        {"name": "zeta", "code": "# z\n"},
    ]


# ---------------------------------------------------------------------------
# _workflow_files / _all_workflows / _workflows_using
# ---------------------------------------------------------------------------

def test_workflow_files_ignora_init_e_scripts_temporarios_de_execucao():
    _script("pedido", ["a"])
    _script("cadastro", ["a"])
    _script("rpa_run_abc123", ["a"])
    Path("tests/__init__.py").write_text("", encoding="utf-8")

    assert [p.name for p in app._workflow_files()] == ["cadastro.py", "pedido.py"]


def test_all_workflows_devolve_nome_steps_e_arquivo_de_cada_script():
    _script("pedido", ["fazer_login", "navegar"])
    _script("rpa_run_temp", ["fazer_login"])

    assert app._all_workflows() == [
        {"name": "pedido", "steps": ["fazer_login", "navegar"], "file": "pedido.py"},
    ]


def test_all_workflows_le_script_no_formato_antigo_sem_cabecalho_rpa_steps():
    Path("tests/antigo.py").write_text(
        "async def run():\n"
        "        results['fazer_login'] = await fazer_login(page)\n",
        encoding="utf-8",
    )

    assert app._all_workflows()[0]["steps"] == ["fazer_login"]


def test_workflows_using_so_casa_o_nome_exato_da_tool():
    _script("usa_login", ["fazer_login", "navegar"])
    _script("usa_v2", ["fazer_login_v2"])
    _script("rpa_run_x", ["fazer_login"])

    assert [p.stem for p in app._workflows_using("fazer_login")] == ["usa_login"]
    assert [p.stem for p in app._workflows_using("fazer_login_v2")] == ["usa_v2"]
    assert app._workflows_using("inexistente") == []


# ---------------------------------------------------------------------------
# _validate_steps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("steps", [None, [], "fazer_login", {"fazer_login": 1}])
def test_validate_steps_recusa_lista_vazia_ou_que_nao_e_lista(steps):
    assert app._validate_steps(steps) == "Nenhum step informado"


def test_validate_steps_recusa_nome_que_nao_e_identificador_python():
    _tool("ok")

    erro = app._validate_steps(["ok", "../../etc", "outro-ruim"])

    assert erro == "Nome de tool inválido: '../../etc'"


def test_validate_steps_lista_as_tools_que_nao_existem_em_ordem():
    _tool("existe")

    assert app._validate_steps(["zeta", "existe", "alfa", "zeta"]) == "Tool não encontrada: alfa, zeta"


def test_validate_steps_aceita_tools_existentes_mesmo_repetidas():
    _tool("fazer_login")
    _tool("navegar")

    assert app._validate_steps(["fazer_login", "navegar", "navegar"]) is None


# ---------------------------------------------------------------------------
# _normalize_cdp_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entrada, esperado", [
    (None, None),
    ("", None),
    ("10.0.0.5", "http://10.0.0.5:9222"),
    ("localhost", "http://localhost:9222"),
    ("10.0.0.5:9333", "http://10.0.0.5:9333"),
    ("http://10.0.0.5:9333", "http://10.0.0.5:9333"),
    ("https://cdp.exemplo.local", "https://cdp.exemplo.local"),
])
def test_normalize_cdp_url_completa_esquema_e_porta_padrao(entrada, esperado):
    assert app._normalize_cdp_url(entrada) == esperado


@pytest.mark.xfail(strict=True, reason="BUG: host cujo nome começa com 'http' é tratado como URL "
                                       "completa (startswith('http')) e fica sem esquema e porta")
def test_normalize_cdp_url_completa_host_cujo_nome_comeca_com_http():
    assert app._normalize_cdp_url("httpserver.local") == "http://httpserver.local:9222"


# ---------------------------------------------------------------------------
# _preview_script
# ---------------------------------------------------------------------------

def test_preview_script_e_o_mesmo_codigo_do_script_salvo_quando_sem_opcoes():
    assert app._preview_script("Pedido", ["fazer_login", "navegar"]) == build_script(
        "Pedido", ["fazer_login", "navegar"])


def test_preview_script_repassa_headless_indice_inicial_e_cdp_normalizado():
    codigo = app._preview_script("Pedido", ["navegar"], headless=True,
                                 cdp_url="10.0.0.5", start_index=3)

    assert "HEADLESS = True" in codigo
    assert "CDP_URL = 'http://10.0.0.5:9222'" in codigo
    assert "(3, 'navegar', navegar)," in codigo


def test_preview_script_com_nome_de_tool_invalido_levanta_erro():
    with pytest.raises(ValueError):
        app._preview_script("Pedido", ["nao-pode"])


# ---------------------------------------------------------------------------
# _gravacao_invalida
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dados", [[1, 2], "texto", None, 7, {"steps": "nao_e_lista"}, {"steps": {}}])
def test_gravacao_invalida_recusa_o_que_nao_e_objeto_com_lista_steps(dados):
    assert "steps" in app._gravacao_invalida(dados)


@pytest.mark.parametrize("dados", [{"steps": []}, {}, {"name": "x", "steps": [{"type": "click"}]}])
def test_gravacao_invalida_aceita_objeto_com_steps_lista_ou_sem_steps(dados):
    assert app._gravacao_invalida(dados) is None


# ---------------------------------------------------------------------------
# _workflow_from_json
# ---------------------------------------------------------------------------

def test_workflow_from_json_monta_nome_slug_steps_e_lista_de_tools():
    _tool("ja_existente", "# codigo\n")

    wf = app._workflow_from_json("pedido.json", gravacao())

    assert wf["workflow_name"] == "Cadastro de exemplo"
    assert wf["workflow_slug"] == "cadastro_de_exemplo"
    assert wf["steps"] and wf["steps"] == [s["tool_name"] for s in wf["named_steps"]]
    assert all(s["actions"] for s in wf["named_steps"])
    assert wf["masked_fields"] == []
    assert wf["tools"] == [{"name": "ja_existente", "code": "# codigo\n"}]


def test_workflow_from_json_sem_nome_usa_o_nome_do_arquivo():
    dados = gravacao()
    del dados["name"]

    wf = app._workflow_from_json("Pedido Venda.json", dados)

    assert wf["workflow_name"] == "Pedido Venda"
    assert wf["workflow_slug"] == "pedido_venda"


def test_workflow_from_json_sem_steps_devolve_listas_vazias():
    wf = app._workflow_from_json("vazio.json", {"name": "Vazio"})

    assert wf["steps"] == [] and wf["named_steps"] == [] and wf["masked_fields"] == []


def test_workflow_from_json_cria_versao_v2_quando_a_tool_existe_com_outras_acoes():
    primeiro = app._workflow_from_json("pedido.json", gravacao())["steps"][0]
    _tool(primeiro, "# actions_fp: outro_fingerprint\n")

    segundo = app._workflow_from_json("pedido.json", gravacao())["steps"][0]

    assert segundo == f"{primeiro}_v2"


def test_workflow_from_json_aponta_campo_de_senha_mascarado():
    dados = gravacao()
    dados["steps"][2]["value"] = "********"

    wf = app._workflow_from_json("pedido.json", dados)

    assert len(wf["masked_fields"]) == 1
