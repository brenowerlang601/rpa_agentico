"""
Persistência no PostgreSQL (db.py): CRUD de tools, workflows, execuções, testes do QA,
secrets e gravações.

Três situações:
- modo local (sem DATABASE_URL): nada fala com o banco;
- banco fora do ar (DATABASE_URL apontando para 127.0.0.1:1): escrita levanta DBError,
  leitura devolve o valor "vazio" de cada função (ver comentários de cada teste);
- banco de teste (fixture `pg`, precisa de RPA_PG_TEST_DSN).
"""

import logging
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

import db
from db import DBError
from testes_unitarios.gravacao_exemplo import gravacao

BANCO_FORA_DO_AR = "postgresql://usuario:senha@127.0.0.1:1/rpa_teste"

_TABELAS = ["tools", "workflows", "execucoes", "secrets", "gravacoes", "qa_generated_tests"]


def _sql(query, params=()):
    """Consulta direta no banco de teste (para conferir o que as funções gravaram)."""
    with db._get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall() if cur.description else None


@pytest.fixture
def banco_fora_do_ar(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA_DO_AR)


@pytest.fixture
def conexao_proibida(monkeypatch):
    """Falha o teste se alguém tentar abrir conexão (modo local não pode falar com o banco)."""
    chamadas = []

    def _proibido():
        chamadas.append(True)
        raise AssertionError("conexão com o banco no modo local")

    monkeypatch.setattr(db, "_get_connection", _proibido)
    return chamadas


# ---------------------------------------------------------------------------
# db_enabled / _get_connection
# ---------------------------------------------------------------------------

def test_db_enabled_e_falso_sem_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    assert db.db_enabled() is False


def test_db_enabled_e_falso_quando_database_url_nao_existe(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.db_enabled() is False


def test_db_enabled_e_verdadeiro_com_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA_DO_AR)
    assert db.db_enabled() is True


@pytest.fixture
def connect_falso(monkeypatch):
    capturado = {}

    def _connect(**kwargs):
        capturado.update(kwargs)
        return "conexao"

    monkeypatch.setattr(db.psycopg2, "connect", _connect)
    return capturado


def test_get_connection_repassa_as_partes_da_database_url(monkeypatch, connect_falso):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fulano:segredo@db.exemplo.local:6543/rpa_db")

    assert db._get_connection() == "conexao"
    assert connect_falso == {
        "host": "db.exemplo.local", "port": 6543, "dbname": "rpa_db",
        "user": "fulano", "password": "segredo",
    }


def test_get_connection_usa_porta_5432_quando_url_nao_tem_porta(monkeypatch, connect_falso):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fulano:segredo@db.exemplo.local/rpa_db")

    db._get_connection()

    assert connect_falso["port"] == 5432


@pytest.mark.xfail(strict=True, reason=(
    "BUG: senha com caractere especial codificado na URL (%40 = @) chega ao psycopg2 ainda "
    "codificada — urlparse não decodifica, e a conexão falha por senha errada"))
def test_get_connection_decodifica_senha_com_caractere_especial(monkeypatch, connect_falso):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fulano:p%40ss%2Fw@db.exemplo.local/rpa_db")

    db._get_connection()

    assert connect_falso["password"] == "p@ss/w"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: parâmetros da URL (ex.: ?sslmode=require, exigido por Postgres gerenciado) são "
    "descartados — _get_connection só repassa host/porta/banco/usuário/senha"))
def test_get_connection_respeita_sslmode_da_url(monkeypatch, connect_falso):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fulano:segredo@db.exemplo.local/rpa_db?sslmode=require")

    db._get_connection()

    assert connect_falso.get("sslmode") == "require"


# ---------------------------------------------------------------------------
# _write
# ---------------------------------------------------------------------------

def test_write_no_modo_local_nao_abre_conexao(conexao_proibida):
    db._write("DELETE FROM tools", (), "teste")

    assert conexao_proibida == []


def test_write_com_banco_fora_do_ar_levanta_dberror_com_a_causa(banco_fora_do_ar, caplog):
    with caplog.at_level(logging.ERROR, logger="db"):
        with pytest.raises(DBError, match="minha_operacao") as erro:
            db._write("DELETE FROM tools", (), "minha_operacao")

    assert isinstance(erro.value.__cause__, psycopg2.OperationalError)
    assert "[DB ERROR] minha_operacao falhou" in caplog.text


def test_write_com_sql_invalido_levanta_dberror(pg):
    with pytest.raises(DBError):
        db._write("INSERT INTO tabela_que_nao_existe VALUES (%s)", (1,), "sql_invalido")


def test_write_grava_e_confirma_a_transacao(pg):
    db._write("INSERT INTO secrets (name, value) VALUES (%s, %s)", ("A", "1"), "teste")

    # Lido por outra conexão: só aparece se a escrita foi confirmada (commit)
    assert _sql("SELECT name, value FROM secrets") == [("A", "1")]


# ---------------------------------------------------------------------------
# Modo local: nenhuma função toca o banco
# ---------------------------------------------------------------------------

_ESCRITAS_LOCAIS = [
    lambda: db.upsert_tool("t", "c"),
    lambda: db.delete_tool("t"),
    lambda: db.rename_tool("t", "u", "c"),
    lambda: db.upsert_workflow("w", "c", []),
    lambda: db.delete_workflow("w"),
    lambda: db.inserir_execucao("r", "w", []),
    lambda: db.finalizar_execucao("r", 0),
    lambda: db.save_qa_test("repo", "f", "python", "pytest", "test_f.py", "codigo"),
    lambda: db.upsert_secret("A", "1"),
    lambda: db.upsert_gravacao("g.json", {}),
    db.init_db,
]


@pytest.mark.parametrize("escrita", _ESCRITAS_LOCAIS)
def test_escritas_no_modo_local_nao_abrem_conexao(escrita, conexao_proibida):
    assert escrita() is None
    assert conexao_proibida == []


@pytest.mark.parametrize("leitura, vazio", [
    (lambda: db.get_tool_fingerprint("t"), None),
    (db.get_all_tools, []),
    (db.get_all_workflows, []),
    (lambda: db.get_workflow_by_id(1), None),
    (db.get_execucoes, []),
    (db.get_qa_tests, []),
    (lambda: db.delete_secret("A"), False),
    (db.list_secrets, []),
    (lambda: db.get_secret_value("A"), None),
    (db.all_secret_values, {}),
    (lambda: db.get_gravacao("g.json"), None),
    (db.list_gravacoes, []),
])
def test_leituras_no_modo_local_devolvem_vazio_sem_abrir_conexao(leitura, vazio, conexao_proibida):
    assert leitura() == vazio
    assert conexao_proibida == []


# ---------------------------------------------------------------------------
# Banco fora do ar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("escrita", [
    lambda: db.upsert_tool("t", "c"),
    lambda: db.delete_tool("t"),
    lambda: db.rename_tool("t", "u", "c"),
    lambda: db.upsert_workflow("w", "c", []),
    lambda: db.delete_workflow("w"),
    lambda: db.upsert_secret("A", "1"),
    lambda: db.delete_secret("A"),
    lambda: db.upsert_gravacao("g.json", {}),
    lambda: db.get_gravacao("g.json"),
])
def test_banco_fora_do_ar_escrita_e_leitura_de_gravacao_levantam_dberror(escrita, banco_fora_do_ar):
    with pytest.raises(DBError):
        escrita()


@pytest.mark.parametrize("registro", [
    lambda: db.inserir_execucao("r", "w", []),
    lambda: db.finalizar_execucao("r", 0),
    lambda: db.save_qa_test("repo", "f", "python", "pytest", "test_f.py", "codigo"),
    db.init_db,
])
def test_banco_fora_do_ar_historico_e_init_db_so_registram_o_erro(registro, banco_fora_do_ar, caplog):
    """
    Comportamento atual (por desenho): histórico de execução, testes do QA e init_db não
    interrompem o fluxo — a execução segue mesmo sem conseguir registrar.
    """
    with caplog.at_level(logging.ERROR, logger="db"):
        assert registro() is None

    assert "[DB ERROR]" in caplog.text


@pytest.mark.parametrize("leitura, vazio", [
    (lambda: db.get_tool_fingerprint("t"), None),
    (db.get_all_tools, []),
    (db.get_all_workflows, []),
    (lambda: db.get_workflow_by_id(1), None),
    (db.get_execucoes, []),
    (db.get_qa_tests, []),
    (lambda: db.get_secret_value("A"), None),
    (db.all_secret_values, {}),
])
def test_banco_fora_do_ar_leituras_devolvem_vazio_e_registram_erro(leitura, vazio, banco_fora_do_ar, caplog):
    """
    Comportamento atual: a falha vira valor vazio + log. Para restore_from_db isso é seguro
    (não escreve nem apaga nada); para as listagens da interface, mascara a queda.
    """
    with caplog.at_level(logging.ERROR, logger="db"):
        assert leitura() == vazio

    assert "[DB ERROR]" in caplog.text


@pytest.mark.xfail(strict=True, reason=(
    "BUG: com o banco fora do ar list_gravacoes devolve [] e /api/json-files responde 200 com "
    "lista vazia — a queda aparece como 'nenhuma gravação' (get_gravacao levanta DBError "
    "justamente para evitar isso)"))
def test_list_gravacoes_com_banco_fora_do_ar_levanta_dberror(banco_fora_do_ar):
    with pytest.raises(DBError):
        db.list_gravacoes()


@pytest.mark.xfail(strict=True, reason=(
    "BUG: com o banco fora do ar list_secrets devolve [] — /api/secrets mostra a aba vazia e "
    "/api/generate-tools recusa com 'Secret não cadastrado' em vez de erro de banco"))
def test_list_secrets_com_banco_fora_do_ar_levanta_dberror(banco_fora_do_ar):
    with pytest.raises(DBError):
        db.list_secrets()


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def _tabelas_existentes():
    rows = _sql("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    return {r[0] for r in rows}


def test_init_db_cria_todas_as_tabelas_num_banco_vazio(pg):
    _sql(f"DROP TABLE IF EXISTS {', '.join(_TABELAS)} CASCADE")
    assert not set(_TABELAS) & _tabelas_existentes()

    db.init_db()

    assert set(_TABELAS) <= _tabelas_existentes()


def test_init_db_e_idempotente_e_preserva_os_dados(pg, caplog):
    db.upsert_tool("fazer_login", "codigo", "fp1")

    with caplog.at_level(logging.ERROR, logger="db"):
        db.init_db()
        db.init_db()

    assert "[DB ERROR]" not in caplog.text
    assert _sql("SELECT name, code, fingerprint FROM tools") == [("fazer_login", "codigo", "fp1")]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def test_upsert_tool_insere_nova_tool(pg):
    db.upsert_tool("fazer_login", "codigo v1", "fp1")

    assert _sql("SELECT name, code, fingerprint FROM tools") == [("fazer_login", "codigo v1", "fp1")]


def test_upsert_tool_sem_fingerprint_grava_vazio(pg):
    db.upsert_tool("fazer_login", "codigo")

    assert _sql("SELECT fingerprint FROM tools") == [("",)]


def test_upsert_tool_mesmo_nome_atualiza_sem_duplicar(pg):
    db.upsert_tool("fazer_login", "codigo v1", "fp1")
    antes = _sql("SELECT updated_at FROM tools")[0][0]

    db.upsert_tool("fazer_login", "codigo v2", "fp2")

    rows = _sql("SELECT name, code, fingerprint, updated_at FROM tools")
    assert [r[:3] for r in rows] == [("fazer_login", "codigo v2", "fp2")]
    assert rows[0][3] > antes


def test_delete_tool_remove_so_a_tool_pedida(pg):
    db.upsert_tool("a", "1")
    db.upsert_tool("b", "2")

    db.delete_tool("a")

    assert _sql("SELECT name FROM tools") == [("b",)]


def test_delete_tool_inexistente_nao_levanta_erro(pg):
    db.delete_tool("nao_existe")

    assert _sql("SELECT count(*) FROM tools") == [(0,)]


def test_rename_tool_troca_nome_e_codigo_e_mantem_fingerprint(pg):
    db.upsert_tool("fazer_login", "async def fazer_login", "fp1")
    db.upsert_tool("outra", "x", "fp9")

    db.rename_tool("fazer_login", "entrar_no_erp", "async def entrar_no_erp")

    assert _sql("SELECT name, code, fingerprint FROM tools ORDER BY name") == [
        ("entrar_no_erp", "async def entrar_no_erp", "fp1"),
        ("outra", "x", "fp9"),
    ]


def test_rename_tool_para_nome_ja_existente_levanta_dberror_e_nao_altera(pg):
    db.upsert_tool("a", "codigo a")
    db.upsert_tool("b", "codigo b")

    with pytest.raises(DBError):
        db.rename_tool("a", "b", "codigo novo")

    assert _sql("SELECT name, code FROM tools ORDER BY name") == [("a", "codigo a"), ("b", "codigo b")]


def test_rename_tool_inexistente_no_banco_nao_faz_nada(pg):
    """
    Comportamento atual: UPDATE de 0 linhas, sem erro e sem criar a tool com o nome novo.
    (Tool que só existe em disco continua fora do banco depois de renomeada.)
    """
    db.rename_tool("so_no_disco", "novo_nome", "codigo")

    assert _sql("SELECT count(*) FROM tools") == [(0,)]


def test_get_tool_fingerprint_devolve_o_fingerprint_salvo(pg):
    db.upsert_tool("fazer_login", "codigo", "abc123")

    assert db.get_tool_fingerprint("fazer_login") == "abc123"


def test_get_tool_fingerprint_vazio_ou_inexistente_devolve_none(pg):
    db.upsert_tool("sem_fp", "codigo")

    assert db.get_tool_fingerprint("sem_fp") is None
    assert db.get_tool_fingerprint("nao_existe") is None


def test_get_all_tools_devolve_todas_em_ordem_alfabetica(pg):
    db.upsert_tool("zeta", "z", "fz")
    db.upsert_tool("alfa", "a", "fa")

    tools = db.get_all_tools()

    assert [(t["name"], t["code"], t["fingerprint"]) for t in tools] == [
        ("alfa", "a", "fa"), ("zeta", "z", "fz"),
    ]
    assert all(isinstance(t["updated_at"], datetime) for t in tools)


def test_get_all_tools_banco_vazio_devolve_lista_vazia(pg):
    assert db.get_all_tools() == []


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

def test_upsert_workflow_grava_codigo_e_steps_como_lista(pg):
    db.upsert_workflow("pedido_venda", "codigo", ["fazer_login", "abrir_tela"])

    assert _sql("SELECT name, code, steps FROM workflows") == [
        ("pedido_venda", "codigo", ["fazer_login", "abrir_tela"]),
    ]


def test_upsert_workflow_mesmo_nome_atualiza_sem_duplicar_e_mantem_id(pg):
    db.upsert_workflow("pedido_venda", "v1", ["a"])
    id_original = _sql("SELECT id FROM workflows")[0][0]

    db.upsert_workflow("pedido_venda", "v2", ["a", "b"])

    assert _sql("SELECT id, name, code, steps FROM workflows") == [
        (id_original, "pedido_venda", "v2", ["a", "b"]),
    ]


def test_delete_workflow_remove_so_o_workflow_pedido(pg):
    db.upsert_workflow("a", "1", [])
    db.upsert_workflow("b", "2", [])

    db.delete_workflow("a")

    assert _sql("SELECT name FROM workflows") == [("b",)]


def test_get_all_workflows_ordena_por_id(pg):
    db.upsert_workflow("zeta", "z", ["x"])
    db.upsert_workflow("alfa", "a", [])

    rows = db.get_all_workflows()

    assert [(r["name"], r["code"], r["steps"]) for r in rows] == [("zeta", "z", ["x"]), ("alfa", "a", [])]
    assert rows[0]["id"] < rows[1]["id"]


def test_get_workflow_by_id_devolve_id_nome_e_steps(pg):
    db.upsert_workflow("outro", "o", [])
    db.upsert_workflow("pedido_venda", "codigo", ["fazer_login"])
    wf_id = _sql("SELECT id FROM workflows WHERE name = 'pedido_venda'")[0][0]

    assert db.get_workflow_by_id(wf_id) == {"id": wf_id, "name": "pedido_venda", "steps": ["fazer_login"]}


def test_get_workflow_by_id_inexistente_devolve_none(pg):
    assert db.get_workflow_by_id(999) is None


# ---------------------------------------------------------------------------
# Execuções
# ---------------------------------------------------------------------------

def test_inserir_execucao_registra_como_running_sem_fim(pg):
    db.upsert_workflow("pedido_venda", "c", ["a"])
    wf_id = _sql("SELECT id FROM workflows")[0][0]

    db.inserir_execucao("run-1", "pedido_venda", ["a", "b"], workflow_id=wf_id)

    assert _sql("SELECT run_id, workflow_id, workflow_name, steps, status, exit_code, finalizado_em "
                "FROM execucoes") == [("run-1", wf_id, "pedido_venda", ["a", "b"], "running", None, None)]


def test_inserir_execucao_sem_workflow_id_grava_nulo(pg):
    db.inserir_execucao("run-1", "avulso", [])

    assert _sql("SELECT workflow_id FROM execucoes") == [(None,)]


def test_inserir_execucao_run_id_repetido_nao_duplica_nem_levanta(pg):
    db.inserir_execucao("run-1", "primeiro", [])
    db.inserir_execucao("run-1", "segundo", [])

    assert _sql("SELECT workflow_name FROM execucoes") == [("primeiro",)]


@pytest.mark.parametrize("exit_code, status", [(0, "sucesso"), (-15, "cancelado"), (1, "falha"), (-9, "falha")])
def test_finalizar_execucao_deduz_status_do_exit_code(pg, exit_code, status):
    db.inserir_execucao("run-1", "w", [])

    db.finalizar_execucao("run-1", exit_code)

    rows = _sql("SELECT status, exit_code, finalizado_em FROM execucoes")
    assert rows[0][:2] == (status, exit_code)
    assert isinstance(rows[0][2], datetime)


def test_finalizar_execucao_status_explicito_sobrescreve_o_deduzido(pg):
    db.inserir_execucao("run-1", "w", [])

    db.finalizar_execucao("run-1", 0, status="falha")

    assert _sql("SELECT status, exit_code FROM execucoes") == [("falha", 0)]


def test_finalizar_execucao_atualiza_so_o_run_id_pedido(pg):
    db.inserir_execucao("run-1", "w", [])
    db.inserir_execucao("run-2", "w", [])

    db.finalizar_execucao("run-1", 0)

    assert _sql("SELECT run_id, status, finalizado_em IS NULL FROM execucoes ORDER BY run_id") == [
        ("run-1", "sucesso", False), ("run-2", "running", True),
    ]


def test_finalizar_execucao_run_id_inexistente_nao_levanta(pg):
    db.finalizar_execucao("nao_existe", 0)

    assert _sql("SELECT count(*) FROM execucoes") == [(0,)]


def _execucoes_com_horario(*run_ids):
    """Cria execuções com iniciado_em espaçado de 1 min, na ordem dada (a última é a mais nova)."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, run_id in enumerate(run_ids):
        db.inserir_execucao(run_id, "w", [])
        _sql("UPDATE execucoes SET iniciado_em = %s WHERE run_id = %s", (base + timedelta(minutes=i), run_id))


def test_get_execucoes_ordena_da_mais_recente_para_a_mais_antiga(pg):
    # Inserção fora da ordem cronológica: a ordem vem de iniciado_em, não do id
    _execucoes_com_horario("antiga", "meio", "nova")
    _sql("UPDATE execucoes SET iniciado_em = iniciado_em + interval '1 hour' WHERE run_id = 'antiga'")

    assert [e["run_id"] for e in db.get_execucoes()] == ["antiga", "nova", "meio"]


def test_get_execucoes_respeita_o_limit(pg):
    _execucoes_com_horario("r1", "r2", "r3", "r4")

    assert [e["run_id"] for e in db.get_execucoes(limit=2)] == ["r4", "r3"]


def test_get_execucoes_devolve_os_campos_da_execucao(pg):
    db.inserir_execucao("run-1", "pedido_venda", ["a"])
    db.finalizar_execucao("run-1", 1)

    (e,) = db.get_execucoes()

    assert set(e) == {"id", "run_id", "workflow_id", "workflow_name", "steps", "status",
                      "exit_code", "iniciado_em", "finalizado_em"}
    assert (e["workflow_name"], e["steps"], e["status"], e["exit_code"]) == ("pedido_venda", ["a"], "falha", 1)


# ---------------------------------------------------------------------------
# Testes gerados pelo QA
# ---------------------------------------------------------------------------

def test_save_qa_test_e_get_qa_tests_sem_devolver_o_codigo(pg):
    db.save_qa_test("file:///repo", "somar", "python", "pytest", "test_somar.py", "def test(): ...")

    (t,) = db.get_qa_tests()

    assert {k: v for k, v in t.items() if k not in ("id", "created_at")} == {
        "repo_url": "file:///repo", "function_name": "somar", "language": "python",
        "framework": "pytest", "filename": "test_somar.py",
    }
    assert _sql("SELECT test_code FROM qa_generated_tests") == [("def test(): ...",)]


def test_get_qa_tests_ordena_do_mais_recente_e_respeita_limit(pg):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, nome in enumerate(["f1", "f2", "f3"]):
        db.save_qa_test("repo", nome, "python", "pytest", f"test_{nome}.py", "x")
        _sql("UPDATE qa_generated_tests SET created_at = %s WHERE function_name = %s",
             (base + timedelta(minutes=i), nome))

    assert [t["function_name"] for t in db.get_qa_tests()] == ["f3", "f2", "f1"]
    assert [t["function_name"] for t in db.get_qa_tests(limit=1)] == ["f3"]


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def test_upsert_secret_insere_e_atualiza_sem_duplicar(pg):
    db.upsert_secret("ERP_SENHA", "senha1")
    antes = _sql("SELECT updated_at FROM secrets")[0][0]

    db.upsert_secret("ERP_SENHA", "senha2")

    rows = _sql("SELECT name, value, updated_at FROM secrets")
    assert [r[:2] for r in rows] == [("ERP_SENHA", "senha2")]
    assert rows[0][2] > antes


def test_delete_secret_devolve_true_ao_remover_e_false_se_nao_existe(pg):
    db.upsert_secret("A", "1")
    db.upsert_secret("B", "2")

    assert db.delete_secret("A") is True
    assert db.delete_secret("A") is False
    assert _sql("SELECT name FROM secrets") == [("B",)]


def test_list_secrets_devolve_nomes_em_ordem_e_nunca_os_valores(pg):
    db.upsert_secret("ZETA", "valor-z")
    db.upsert_secret("ALFA", "valor-a")

    itens = db.list_secrets()

    assert [i["name"] for i in itens] == ["ALFA", "ZETA"]
    assert all(set(i) == {"name", "updated_at"} for i in itens)
    assert "valor" not in repr(itens)


def test_get_secret_value_devolve_valor_ou_none(pg):
    db.upsert_secret("ERP_SENHA", "s3nh@")

    assert db.get_secret_value("ERP_SENHA") == "s3nh@"
    assert db.get_secret_value("OUTRO") is None


def test_all_secret_values_devolve_todos_como_dicionario(pg):
    db.upsert_secret("A", "1")
    db.upsert_secret("B", "2")

    assert db.all_secret_values() == {"A": "1", "B": "2"}


# ---------------------------------------------------------------------------
# Gravações
# ---------------------------------------------------------------------------

def test_upsert_gravacao_preserva_acentos_e_conteudo(pg):
    conteudo = gravacao()
    conteudo["name"] = "Pedido de São Paulo — ação"

    db.upsert_gravacao("pedido.json", conteudo)

    assert db.get_gravacao("pedido.json") == conteudo


def test_upsert_gravacao_mesmo_nome_substitui_sem_duplicar(pg):
    nova = gravacao()
    nova["name"] = "Versão nova"
    db.upsert_gravacao("pedido.json", gravacao())

    db.upsert_gravacao("pedido.json", nova)

    assert _sql("SELECT count(*) FROM gravacoes") == [(1,)]
    assert db.get_gravacao("pedido.json")["name"] == "Versão nova"


def test_get_gravacao_inexistente_devolve_none(pg):
    db.upsert_gravacao("outra.json", gravacao())

    assert db.get_gravacao("nao_existe.json") is None


def test_list_gravacoes_devolve_nomes_em_ordem_alfabetica(pg):
    for nome in ["b.json", "a.json", "c.json"]:
        db.upsert_gravacao(nome, gravacao())

    assert db.list_gravacoes() == ["a.json", "b.json", "c.json"]
