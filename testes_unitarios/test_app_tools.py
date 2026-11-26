"""
Rotas de tools: /api/tools, /api/tool/<name> (GET/POST/DELETE), /api/tool/new,
/api/tool/<name>/rename e /api/tools/check.

Regra central (docstring de db._write): no modo banco, falha de escrita (DBError) não pode
deixar disco e banco divergentes — o restore_from_db do startup sobrescreveria o disco com a
versão antiga do banco. Por isso cada rota que grava é testada também com o banco recusando a
escrita (Postgres de teste com trigger) e com o banco inacessível (porta 1).
"""

from pathlib import Path

import pytest

import db
from json_processor import build_script, read_script_steps

BANCO_FORA = "postgresql://u:s@127.0.0.1:1/rpa_teste"


def _tool(nome: str, codigo: str | None = None) -> Path:
    p = Path("tools") / f"{nome}.py"
    p.write_text(codigo if codigo is not None else _codigo(nome), encoding="utf-8")
    return p


def _codigo(nome: str, fp: str = "abc123") -> str:
    return (
        f"# actions_fp: {fp}\n"
        "from playwright.async_api import Page\n"
        "from rpa_runtime import confirmar_login, localizar, login_necessario, secret\n\n"
        f"async def {nome}(page) -> bool:\n"
        "    return True\n"
    )


def _script(nome: str, steps: list[str]) -> Path:
    p = Path("tests") / f"{nome}.py"
    p.write_text(build_script(nome, steps), encoding="utf-8")
    return p


def _tools_no_banco() -> dict[str, dict]:
    return {t["name"]: t for t in db.get_all_tools()}


def _workflows_no_banco() -> dict[str, dict]:
    return {w["name"]: w for w in db.get_all_workflows()}


def _sql(sql: str) -> None:
    with db._get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


@pytest.fixture
def recusar(pg):
    """
    Faz o Postgres de teste recusar uma escrita (trigger que levanta exceção) — DBError real,
    sem mock. Uso: recusar("tools", "INSERT OR UPDATE", "NEW.name = 'x'").
    """
    _sql("""
        CREATE OR REPLACE FUNCTION teste_recusa() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'escrita recusada pelo teste'; END $$ LANGUAGE plpgsql;
    """)
    criados: list[tuple[str, str]] = []

    def _recusar(tabela: str, evento: str, condicao: str = "TRUE") -> None:
        nome = f"teste_recusa_{len(criados)}"
        _sql(f"CREATE TRIGGER {nome} BEFORE {evento} ON {tabela} FOR EACH ROW "
             f"WHEN ({condicao}) EXECUTE FUNCTION teste_recusa()")
        criados.append((nome, tabela))

    yield _recusar
    for nome, tabela in criados:
        _sql(f"DROP TRIGGER IF EXISTS {nome} ON {tabela}")


# ---------------------------------------------------------------------------
# GET /api/tools e GET /api/tool/<name>
# ---------------------------------------------------------------------------

def test_lista_de_tools_traz_nome_e_codigo_sem_o_init(client):
    _tool("fazer_login", "# a\n")
    _tool("__init__", "")

    r = client.get("/api/tools")

    assert r.status_code == 200
    assert r.json == [{"name": "fazer_login", "code": "# a\n"}]


def test_lista_de_tools_vazia(client):
    assert client.get("/api/tools").json == []


def test_abrir_tool_devolve_o_codigo(client):
    _tool("fazer_login", "# código\n")

    r = client.get("/api/tool/fazer_login")

    assert r.status_code == 200
    assert r.json == {"name": "fazer_login", "code": "# código\n"}


def test_abrir_tool_inexistente_da_404(client):
    assert client.get("/api/tool/nao_existe").status_code == 404


def test_abrir_tool_com_nome_invalido_e_recusado(client):
    Path("tools/com-traco.py").write_text("x", encoding="utf-8")

    r = client.get("/api/tool/com-traco")

    assert r.status_code == 400
    assert "inválido" in r.json["error"]


# ---------------------------------------------------------------------------
# POST /api/tool/new
# ---------------------------------------------------------------------------

def test_criar_tool_grava_o_arquivo(client):
    r = client.post("/api/tool/new", json={"name": "nova_tool", "code": "# novo\n"})

    assert r.status_code == 200
    assert r.json == {"ok": True, "name": "nova_tool"}
    assert Path("tools/nova_tool.py").read_text(encoding="utf-8") == "# novo\n"


def test_criar_tool_troca_caracteres_invalidos_por_sublinhado(client):
    r = client.post("/api/tool/new", json={"name": "  minha tool-2  ", "code": ""})

    assert r.json["name"] == "minha_tool_2"
    assert Path("tools/minha_tool_2.py").exists()


@pytest.mark.parametrize("nome", ["", "   ", "2tool", None])
def test_criar_tool_com_nome_invalido_e_recusado_sem_gravar(client, nome):
    r = client.post("/api/tool/new", json={"name": nome, "code": "x"})

    assert r.status_code == 400
    assert list(Path("tools").iterdir()) == []


def test_criar_tool_com_nome_ja_existente_da_409_e_nao_sobrescreve(client):
    _tool("fazer_login", "# original\n")

    r = client.post("/api/tool/new", json={"name": "fazer_login", "code": "# novo\n"})

    assert r.status_code == 409
    assert Path("tools/fazer_login.py").read_text(encoding="utf-8") == "# original\n"


def test_criar_tool_no_modo_banco_grava_banco_e_disco(pg, client):
    r = client.post("/api/tool/new", json={"name": "nova_tool", "code": "# novo\n"})

    assert r.status_code == 200
    assert _tools_no_banco()["nova_tool"]["code"] == "# novo\n"
    assert Path("tools/nova_tool.py").read_text(encoding="utf-8") == "# novo\n"


def test_criar_tool_com_banco_recusando_nao_cria_o_arquivo(recusar, client):
    recusar("tools", "INSERT OR UPDATE")

    r = client.post("/api/tool/new", json={"name": "nova_tool", "code": "# novo\n"})

    assert r.status_code == 500
    assert "banco" in r.json["error"].lower()
    assert not Path("tools/nova_tool.py").exists()
    assert _tools_no_banco() == {}


def test_criar_tool_com_banco_inacessivel_nao_cria_o_arquivo(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.post("/api/tool/new", json={"name": "nova_tool", "code": "# novo\n"})

    assert r.status_code == 500
    assert not Path("tools/nova_tool.py").exists()


@pytest.mark.xfail(strict=True, reason="BUG: código que não é texto (ex.: número) é gravado no "
                                       "banco e só depois falha ao escrever o disco — banco fica "
                                       "com a tool e o disco sem")
def test_criar_tool_com_codigo_que_nao_e_texto_e_recusado_sem_gravar_no_banco(pg, client):
    try:
        r = client.post("/api/tool/new", json={"name": "nova_tool", "code": 123})
        status = r.status_code
    except TypeError:
        status = 500
    no_banco = "nova_tool" in _tools_no_banco()
    no_disco = Path("tools/nova_tool.py").exists()

    assert no_banco == no_disco, f"divergência: banco={no_banco} disco={no_disco}"
    assert status == 400


# ---------------------------------------------------------------------------
# POST /api/tool/<name> (salvar código editado)
# ---------------------------------------------------------------------------

def test_salvar_tool_sobrescreve_o_codigo(client):
    _tool("fazer_login", "# antigo\n")

    r = client.post("/api/tool/fazer_login", json={"code": "# novo\n"})

    assert r.status_code == 200
    assert r.json == {"ok": True}
    assert Path("tools/fazer_login.py").read_text(encoding="utf-8") == "# novo\n"


def test_salvar_tool_com_nome_invalido_e_recusado(client):
    r = client.post("/api/tool/com-traco", json={"code": "# x\n"})

    assert r.status_code == 400
    assert not Path("tools/com-traco.py").exists()


def test_salvar_tool_usa_o_fingerprint_do_cabecalho_do_codigo_novo(pg, client):
    _tool("fazer_login", _codigo("fazer_login", "antigo"))

    client.post("/api/tool/fazer_login", json={"code": _codigo("fazer_login", "novo_fp")})

    assert _tools_no_banco()["fazer_login"]["fingerprint"] == "novo_fp"


def test_salvar_tool_sem_cabecalho_mantem_o_fingerprint_que_a_tool_ja_tinha(pg, client):
    _tool("fazer_login", _codigo("fazer_login", "fp_original"))

    r = client.post("/api/tool/fazer_login", json={"code": "async def fazer_login(page):\n    return True\n"})

    assert r.status_code == 200
    salvo = _tools_no_banco()["fazer_login"]
    assert salvo["fingerprint"] == "fp_original"
    assert salvo["code"].startswith("async def fazer_login")


def test_salvar_tool_nova_sem_cabecalho_fica_com_fingerprint_vazio(pg, client):
    client.post("/api/tool/fazer_login", json={"code": "async def fazer_login(page):\n    return True\n"})

    assert _tools_no_banco()["fazer_login"]["fingerprint"] == ""


def test_salvar_tool_so_procura_o_cabecalho_nas_tres_primeiras_linhas(pg, client):
    codigo = "# 1\n# 2\n# 3\n# actions_fp: tarde_demais\n"

    client.post("/api/tool/fazer_login", json={"code": codigo})

    assert _tools_no_banco()["fazer_login"]["fingerprint"] == ""


def test_salvar_tool_com_banco_recusando_mantem_o_disco_como_estava(recusar, client):
    _tool("fazer_login", "# antigo\n")
    db.upsert_tool("fazer_login", "# antigo\n")
    recusar("tools", "INSERT OR UPDATE")

    r = client.post("/api/tool/fazer_login", json={"code": "# novo\n"})

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").read_text(encoding="utf-8") == "# antigo\n"
    assert _tools_no_banco()["fazer_login"]["code"] == "# antigo\n"


def test_salvar_tool_com_banco_inacessivel_mantem_o_disco_como_estava(client, monkeypatch):
    _tool("fazer_login", "# antigo\n")
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.post("/api/tool/fazer_login", json={"code": "# novo\n"})

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").read_text(encoding="utf-8") == "# antigo\n"


# ---------------------------------------------------------------------------
# DELETE /api/tool/<name>
# ---------------------------------------------------------------------------

def test_apagar_tool_sem_uso_remove_o_arquivo(client):
    _tool("fazer_login")

    r = client.delete("/api/tool/fazer_login")

    assert r.status_code == 200
    assert r.json == {"ok": True, "workflows_affected": []}
    assert not Path("tools/fazer_login.py").exists()


def test_apagar_tool_inexistente_da_404(client):
    assert client.delete("/api/tool/nao_existe").status_code == 404


def test_apagar_tool_com_nome_invalido_e_recusado(client):
    assert client.delete("/api/tool/com-traco").status_code == 400


def test_apagar_tool_usada_em_workflow_pede_confirmacao_e_nao_apaga(client):
    _tool("fazer_login")
    _script("pedido", ["fazer_login"])
    _script("rpa_run_temp", ["fazer_login"])

    r = client.delete("/api/tool/fazer_login")

    assert r.status_code == 409
    assert r.json["workflows"] == ["pedido"]
    assert "pedido" in r.json["error"]
    assert Path("tools/fazer_login.py").exists()


def test_apagar_tool_usada_com_force_apaga_e_informa_os_workflows_afetados(client):
    _tool("fazer_login")
    _script("pedido", ["fazer_login"])

    r = client.delete("/api/tool/fazer_login?force=1")

    assert r.status_code == 200
    assert r.json["workflows_affected"] == ["pedido"]
    assert not Path("tools/fazer_login.py").exists()


def test_apagar_tool_no_modo_banco_remove_do_banco_e_do_disco(pg, client):
    _tool("fazer_login")
    db.upsert_tool("fazer_login", _codigo("fazer_login"))

    r = client.delete("/api/tool/fazer_login")

    assert r.status_code == 200
    assert _tools_no_banco() == {}
    assert not Path("tools/fazer_login.py").exists()


def test_apagar_tool_com_banco_recusando_mantem_o_arquivo(recusar, client):
    _tool("fazer_login")
    db.upsert_tool("fazer_login", _codigo("fazer_login"))
    recusar("tools", "DELETE")

    r = client.delete("/api/tool/fazer_login")

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").exists()
    assert "fazer_login" in _tools_no_banco()


def test_apagar_tool_com_banco_inacessivel_mantem_o_arquivo(client, monkeypatch):
    _tool("fazer_login")
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.delete("/api/tool/fazer_login")

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").exists()


# ---------------------------------------------------------------------------
# POST /api/tool/<name>/rename
# ---------------------------------------------------------------------------

def test_renomear_tool_troca_arquivo_e_nome_da_funcao(client):
    _tool("fazer_login", _codigo("fazer_login") + "\ndef fazer_login_aux():\n    pass\n")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar_no_erp"})

    assert r.status_code == 200
    assert r.json == {"ok": True, "new_name": "entrar_no_erp", "workflows_updated": []}
    assert not Path("tools/fazer_login.py").exists()
    codigo = Path("tools/entrar_no_erp.py").read_text(encoding="utf-8")
    assert "async def entrar_no_erp(page) -> bool:" in codigo
    # função auxiliar com nome maior não é tocada
    assert "def fazer_login_aux():" in codigo


def test_renomear_tool_sincrona_troca_o_def(client):
    _tool("fazer_login", "def fazer_login(page):\n    return True\n")

    client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert Path("tools/entrar.py").read_text(encoding="utf-8").startswith("def entrar(page):")


def test_renomear_tool_atualiza_os_scripts_que_a_usam(client):
    _tool("fazer_login")
    _tool("fazer_login_v2")
    _tool("navegar")
    _script("pedido", ["fazer_login", "navegar", "fazer_login"])
    _script("outro", ["fazer_login_v2"])
    _script("sem_login", ["navegar"])

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.json["workflows_updated"] == ["pedido"]
    pedido = Path("tests/pedido.py").read_text(encoding="utf-8")
    assert pedido == build_script("pedido", ["entrar", "navegar", "entrar"])
    assert read_script_steps(Path("tests/outro.py").read_text(encoding="utf-8")) == ["fazer_login_v2"]
    assert Path("tests/sem_login.py").read_text(encoding="utf-8") == build_script("sem_login", ["navegar"])


def test_renomear_tool_nao_troca_tool_de_nome_maior_no_mesmo_script(client):
    _tool("fazer_login")
    _tool("fazer_login_v2")
    _script("pedido", ["fazer_login", "fazer_login_v2"])

    client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert Path("tests/pedido.py").read_text(encoding="utf-8") == build_script(
        "pedido", ["entrar", "fazer_login_v2"])


def test_renomear_tool_saneia_o_novo_nome(client):
    _tool("fazer_login")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": " entrar no-erp "})

    assert r.json["new_name"] == "entrar_no_erp"
    assert Path("tools/entrar_no_erp.py").exists()


def test_renomear_tool_com_nome_atual_invalido_e_recusado(client):
    assert client.post("/api/tool/com-traco/rename", json={"new_name": "x"}).status_code == 400


@pytest.mark.parametrize("novo", ["", "9nome", None])
def test_renomear_tool_com_novo_nome_invalido_e_recusado(client, novo):
    _tool("fazer_login")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": novo})

    assert r.status_code == 400
    assert Path("tools/fazer_login.py").exists()


def test_renomear_tool_inexistente_da_404(client):
    assert client.post("/api/tool/nao_existe/rename", json={"new_name": "x"}).status_code == 404


def test_renomear_para_nome_ja_existente_da_409_e_nao_mexe_em_nada(client):
    _tool("fazer_login", "# a\n")
    _tool("entrar", "# b\n")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.status_code == 409
    assert Path("tools/fazer_login.py").read_text(encoding="utf-8") == "# a\n"
    assert Path("tools/entrar.py").read_text(encoding="utf-8") == "# b\n"


def test_renomear_tool_no_modo_banco_renomeia_tool_e_workflows_no_banco(pg, client):
    _tool("fazer_login", _codigo("fazer_login", "fp1"))
    db.upsert_tool("fazer_login", _codigo("fazer_login", "fp1"), "fp1")
    _script("pedido", ["fazer_login"])
    db.upsert_workflow("pedido", build_script("pedido", ["fazer_login"]), ["fazer_login"])

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.status_code == 200
    tools = _tools_no_banco()
    assert set(tools) == {"entrar"}
    assert tools["entrar"]["fingerprint"] == "fp1"
    assert tools["entrar"]["code"] == Path("tools/entrar.py").read_text(encoding="utf-8")
    wf = _workflows_no_banco()["pedido"]
    assert wf["steps"] == ["entrar"]
    assert wf["code"] == Path("tests/pedido.py").read_text(encoding="utf-8")


def test_renomear_tool_com_banco_recusando_nao_mexe_no_disco(recusar, client):
    _tool("fazer_login", _codigo("fazer_login"))
    db.upsert_tool("fazer_login", _codigo("fazer_login"))
    _script("pedido", ["fazer_login"])
    antes = Path("tests/pedido.py").read_text(encoding="utf-8")
    recusar("tools", "UPDATE")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").exists()
    assert not Path("tools/entrar.py").exists()
    assert Path("tests/pedido.py").read_text(encoding="utf-8") == antes
    assert set(_tools_no_banco()) == {"fazer_login"}


def test_renomear_para_nome_que_so_existe_no_banco_falha_sem_mexer_no_disco(pg, client):
    _tool("fazer_login", _codigo("fazer_login"))
    db.upsert_tool("fazer_login", _codigo("fazer_login"))
    db.upsert_tool("entrar", "# só no banco\n")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").exists()
    assert not Path("tools/entrar.py").exists()
    assert _tools_no_banco()["entrar"]["code"] == "# só no banco\n"


def test_renomear_tool_com_banco_inacessivel_nao_mexe_no_disco(client, monkeypatch):
    _tool("fazer_login")
    _script("pedido", ["fazer_login"])
    antes = Path("tests/pedido.py").read_text(encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.status_code == 500
    assert Path("tools/fazer_login.py").exists()
    assert not Path("tools/entrar.py").exists()
    assert Path("tests/pedido.py").read_text(encoding="utf-8") == antes


def test_renomear_tool_com_banco_recusando_um_workflow_avisa_e_mantem_esse_script_igual_ao_banco(
        recusar, client):
    _tool("fazer_login", _codigo("fazer_login"))
    db.upsert_tool("fazer_login", _codigo("fazer_login"))
    for nome in ("pedido", "cadastro"):
        _script(nome, ["fazer_login"])
        db.upsert_workflow(nome, build_script(nome, ["fazer_login"]), ["fazer_login"])
    recusar("workflows", "UPDATE", "NEW.name = 'cadastro'")

    r = client.post("/api/tool/fazer_login/rename", json={"new_name": "entrar"})

    assert r.status_code == 200
    assert r.json["workflows_updated"] == ["pedido"]
    assert "cadastro" in r.json["warning"]
    banco = _workflows_no_banco()
    # o que falhou no banco continua igual no disco (sem divergência)
    assert Path("tests/cadastro.py").read_text(encoding="utf-8") == banco["cadastro"]["code"]
    assert banco["cadastro"]["steps"] == ["fazer_login"]
    # o que passou foi atualizado nos dois
    assert Path("tests/pedido.py").read_text(encoding="utf-8") == banco["pedido"]["code"]
    assert banco["pedido"]["steps"] == ["entrar"]


# ---------------------------------------------------------------------------
# POST /api/tools/check
# ---------------------------------------------------------------------------

def test_check_separa_tools_existentes_e_pendentes_mantendo_a_ordem(client):
    _tool("fazer_login")
    _tool("navegar")

    r = client.post("/api/tools/check", json={"steps": ["navegar", "falta_1", "fazer_login", "falta_2"]})

    assert r.json == {"existing": ["navegar", "fazer_login"], "pending": ["falta_1", "falta_2"]}


def test_check_sem_steps_devolve_listas_vazias(client):
    assert client.post("/api/tools/check", json={}).json == {"existing": [], "pending": []}


def test_check_nao_conta_o_init_como_tool(client):
    _tool("__init__", "")

    assert client.post("/api/tools/check", json={"steps": ["__init__"]}).json["pending"] == ["__init__"]


@pytest.mark.xfail(strict=True, reason="BUG: steps nulo quebra a rota com TypeError (500) em "
                                       "vez de responder normalmente ou recusar com 400")
def test_check_com_steps_nulo_nao_quebra_a_rota(client):
    try:
        status = client.post("/api/tools/check", json={"steps": None}).status_code
    except TypeError:
        status = 500

    assert status < 500
