"""
Rotas de workflows e interface: /, /api/workflows, /api/preview e /api/save-workflow.

/api/save-workflow grava banco + disco; com o banco recusando a escrita (Postgres de teste com
trigger) ou inacessível, o script em tests/ não pode mudar (docstring de db._write).
"""

from pathlib import Path

import pytest

import db
from json_processor import build_script

BANCO_FORA = "postgresql://u:s@127.0.0.1:1/rpa_teste"


def _tool(nome: str) -> Path:
    p = Path("tools") / f"{nome}.py"
    p.write_text(f"async def {nome}(page) -> bool:\n    return True\n", encoding="utf-8")
    return p


def _script(nome: str, steps: list[str]) -> Path:
    p = Path("tests") / f"{nome}.py"
    p.write_text(build_script(nome, steps), encoding="utf-8")
    return p


def _workflows_no_banco() -> dict[str, dict]:
    return {w["name"]: w for w in db.get_all_workflows()}


def _sql(sql: str) -> None:
    with db._get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


@pytest.fixture
def banco_recusa_workflows(pg):
    """Postgres de teste recusando qualquer escrita em workflows (DBError real)."""
    _sql("""
        CREATE OR REPLACE FUNCTION teste_recusa() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'escrita recusada pelo teste'; END $$ LANGUAGE plpgsql;
        CREATE TRIGGER teste_recusa_wf BEFORE INSERT OR UPDATE ON workflows
            FOR EACH ROW EXECUTE FUNCTION teste_recusa();
    """)
    yield
    _sql("DROP TRIGGER IF EXISTS teste_recusa_wf ON workflows")


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_pagina_inicial_entrega_a_interface_html(client):
    r = client.get("/")

    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert b"<html" in r.data.lower()


# ---------------------------------------------------------------------------
# GET /api/workflows
# ---------------------------------------------------------------------------

def test_lista_de_workflows_no_modo_local_vem_do_disco_sem_id(client):
    _script("pedido", ["fazer_login", "navegar"])
    _script("rpa_run_temp", ["fazer_login"])

    r = client.get("/api/workflows")

    assert r.status_code == 200
    assert r.json == [{"name": "pedido", "steps": ["fazer_login", "navegar"], "file": "pedido.py", "id": None}]


def test_lista_de_workflows_no_modo_banco_traz_o_id_do_banco(pg, client):
    _script("pedido", ["fazer_login"])
    _script("so_no_disco", ["navegar"])
    db.upsert_workflow("outro", "# x", [])
    db.upsert_workflow("pedido", build_script("pedido", ["fazer_login"]), ["fazer_login"])
    id_pedido = _workflows_no_banco()["pedido"]["id"]

    r = client.get("/api/workflows")

    por_nome = {w["name"]: w for w in r.json}
    assert set(por_nome) == {"pedido", "so_no_disco"}
    assert por_nome["pedido"]["id"] == id_pedido
    assert por_nome["so_no_disco"]["id"] is None


def test_lista_de_workflows_com_banco_inacessivel_ainda_mostra_o_disco(client, monkeypatch):
    _script("pedido", ["fazer_login"])
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.get("/api/workflows")

    assert r.status_code == 200
    assert [(w["name"], w["id"]) for w in r.json] == [("pedido", None)]


# ---------------------------------------------------------------------------
# POST /api/preview
# ---------------------------------------------------------------------------

def test_preview_devolve_o_mesmo_script_que_seria_salvo(client):
    r = client.post("/api/preview", json={"workflow_name": "Pedido", "steps": ["fazer_login", "navegar"]})

    assert r.status_code == 200
    assert r.json["script"] == build_script("Pedido", ["fazer_login", "navegar"])


def test_preview_sem_nome_usa_workflow(client):
    r = client.post("/api/preview", json={"steps": ["navegar"]})

    assert "WORKFLOW_NAME = 'workflow'" in r.json["script"]


def test_preview_sem_steps_devolve_script_vazio(client):
    assert client.post("/api/preview", json={"workflow_name": "x"}).json == {"script": ""}
    assert client.post("/api/preview", json={"steps": []}).json == {"script": ""}


def test_preview_com_nome_de_tool_invalido_e_recusado(client):
    r = client.post("/api/preview", json={"steps": ["ok", "os.system('x')"]})

    assert r.status_code == 400
    assert "os.system" in r.json["error"]


def test_preview_nao_exige_que_a_tool_ja_exista(client):
    r = client.post("/api/preview", json={"steps": ["ainda_nao_gerada"]})

    assert r.status_code == 200
    assert "from tools.ainda_nao_gerada import ainda_nao_gerada" in r.json["script"]


# ---------------------------------------------------------------------------
# POST /api/save-workflow
# ---------------------------------------------------------------------------

def test_salvar_workflow_grava_o_script_com_o_nome_escolhido(client):
    _tool("fazer_login")

    r = client.post("/api/save-workflow", json={
        "workflow_name": "Pedido de Venda", "script_name": "meu script", "steps": ["fazer_login"]})

    assert r.status_code == 200
    assert r.json == {"ok": True, "file": "meu_script.py"}
    assert Path("tests/meu_script.py").read_text(encoding="utf-8") == build_script(
        "Pedido de Venda", ["fazer_login"])


def test_salvar_workflow_sem_nome_de_script_usa_o_slug_do_workflow(client):
    _tool("fazer_login")

    r = client.post("/api/save-workflow", json={"workflow_name": "Pedido de Venda", "steps": ["fazer_login"]})

    assert r.json["file"] == "pedido_de_venda.py"


def test_salvar_workflow_sem_nome_nenhum_usa_workflow(client):
    _tool("fazer_login")

    r = client.post("/api/save-workflow", json={"script_name": "   ", "steps": ["fazer_login"]})

    assert r.json["file"] == "workflow.py"


def test_salvar_workflow_sobrescreve_so_o_script_escolhido(client):
    _tool("fazer_login")
    _tool("navegar")
    _script("pedido_de_venda", ["navegar"])

    client.post("/api/save-workflow", json={
        "workflow_name": "Pedido de Venda", "script_name": "copia", "steps": ["fazer_login"]})

    assert Path("tests/pedido_de_venda.py").read_text(encoding="utf-8") == build_script(
        "pedido_de_venda", ["navegar"])
    assert Path("tests/copia.py").exists()


@pytest.mark.parametrize("steps, trecho", [
    ([], "Nenhum step"),
    (None, "Nenhum step"),
    (["nao-pode"], "inválido"),
    (["nao_existe"], "não encontrada"),
])
def test_salvar_workflow_com_steps_invalidos_e_recusado_sem_gravar(client, steps, trecho):
    r = client.post("/api/save-workflow", json={"workflow_name": "x", "steps": steps})

    assert r.status_code == 400
    assert trecho in r.json["error"]
    assert list(Path("tests").iterdir()) == []


def test_salvar_workflow_com_nome_reservado_rpa_run_e_recusado(client):
    _tool("fazer_login")

    r = client.post("/api/save-workflow", json={"script_name": "rpa_run_meu", "steps": ["fazer_login"]})
    r2 = client.post("/api/save-workflow", json={"workflow_name": "RPA run teste", "steps": ["fazer_login"]})

    assert r.status_code == 400 and r2.status_code == 400
    assert "reservados" in r.json["error"]
    assert list(Path("tests").iterdir()) == []


@pytest.mark.xfail(strict=True, reason="BUG: script salvo como __init__ é gravado mas nunca aparece "
                                       "na lista de workflows (_workflow_files ignora __init__)")
def test_salvar_workflow_com_nome_init_e_recusado_ou_aparece_na_lista(client):
    _tool("fazer_login")

    r = client.post("/api/save-workflow", json={"script_name": "__init__", "steps": ["fazer_login"]})

    listados = [w["file"] for w in client.get("/api/workflows").json]
    assert r.status_code == 400 or r.json["file"] in listados


def test_salvar_workflow_no_modo_banco_grava_banco_e_disco(pg, client):
    _tool("fazer_login")
    _tool("navegar")

    r = client.post("/api/save-workflow", json={
        "workflow_name": "Pedido", "steps": ["fazer_login", "navegar"]})

    assert r.status_code == 200
    salvo = _workflows_no_banco()["pedido"]
    assert salvo["steps"] == ["fazer_login", "navegar"]
    assert salvo["code"] == Path("tests/pedido.py").read_text(encoding="utf-8")


def test_salvar_workflow_com_banco_recusando_mantem_o_script_antigo(banco_recusa_workflows, client):
    _tool("fazer_login")
    _tool("navegar")
    antigo = _script("pedido", ["navegar"]).read_text(encoding="utf-8")

    r = client.post("/api/save-workflow", json={"workflow_name": "Pedido", "steps": ["fazer_login"]})
    r_novo = client.post("/api/save-workflow", json={"workflow_name": "Novo", "steps": ["fazer_login"]})

    assert r.status_code == 500 and r_novo.status_code == 500
    assert "banco" in r.json["error"].lower()
    assert Path("tests/pedido.py").read_text(encoding="utf-8") == antigo
    assert not Path("tests/novo.py").exists()
    assert _workflows_no_banco() == {}


def test_salvar_workflow_com_banco_inacessivel_nao_grava_o_script(client, monkeypatch):
    _tool("fazer_login")
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.post("/api/save-workflow", json={"workflow_name": "Pedido", "steps": ["fazer_login"]})

    assert r.status_code == 500
    assert not Path("tests/pedido.py").exists()


# ---------------------------------------------------------------------------
# Gravações — casos que test_api_gravacoes.py não cobre
# ---------------------------------------------------------------------------

def test_upload_sem_arquivo_no_formulario_e_recusado(client):
    r = client.post("/api/upload", data={}, content_type="multipart/form-data")

    assert r.status_code == 400
    assert "Nenhum arquivo" in r.json["error"]


def test_upload_com_bytes_que_nao_sao_utf8_e_recusado_sem_gravar(client, pasta_json):
    from testes_unitarios.gravacao_exemplo import arquivo

    # JSON válido, mas codificado em latin-1 (o "ç" não é UTF-8)
    bruto = '{"name": "Cadastro ç", "steps": []}'.encode("latin-1")
    r = client.post("/api/upload", data=arquivo("ruim.json", bruto),
                    content_type="multipart/form-data")

    assert r.status_code == 400
    assert "JSON inválido" in r.json["error"]
    assert list(pasta_json.iterdir()) == []


@pytest.mark.parametrize("nome", ["../fora.json", "pasta/x.json", ".oculto.json", "x.txt", "", None])
def test_abrir_gravacao_com_nome_invalido_e_recusado(client, nome):
    r = client.post("/api/load-json", json={"filename": nome})

    assert r.status_code == 400
    assert "inválido" in r.json["error"]


def test_abrir_gravacao_corrompida_no_disco_e_recusado(client, pasta_json):
    (pasta_json / "quebrada.json").write_text("{nao e json", encoding="utf-8")

    r = client.post("/api/load-json", json={"filename": "quebrada.json"})

    assert r.status_code == 400
    assert "JSON inválido" in r.json["error"]


def test_abrir_gravacao_fora_do_formato_e_recusado(client, pasta_json):
    (pasta_json / "lista.json").write_text("[1, 2]", encoding="utf-8")

    r = client.post("/api/load-json", json={"filename": "lista.json"})

    assert r.status_code == 400
    assert "steps" in r.json["error"]


def test_lista_de_gravacoes_traz_nome_e_stem_em_ordem(client, pasta_json):
    (pasta_json / "b.json").write_text("{}", encoding="utf-8")
    (pasta_json / "a.json").write_text("{}", encoding="utf-8")
    (pasta_json / "ignorar.txt").write_text("{}", encoding="utf-8")

    assert client.get("/api/json-files").json == [
        {"name": "a.json", "stem": "a"}, {"name": "b.json", "stem": "b"}]
