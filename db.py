"""
Persistência PostgreSQL para tools e workflows.

O banco é a fonte de verdade; o disco é cache necessário para imports Python.
Cada operação de escrita atinge banco + disco. No startup, restore_from_db
reconstitui o disco a partir do banco.

Variável de ambiente necessária no .env:
    DATABASE_URL=postgresql://user:password@host:5432/rpa_db
"""

import logging
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()

logger = logging.getLogger(__name__)


class DBError(Exception):
    """Falha de escrita no banco. O chamador NÃO deve alterar o disco quando isso ocorrer."""


def db_enabled() -> bool:
    """Sem DATABASE_URL o sistema funciona só com o disco (modo local)."""
    return bool(os.getenv("DATABASE_URL"))


def _write(sql: str, params: tuple, label: str) -> None:
    """
    Executa uma escrita no banco.
    Levanta DBError em caso de falha para que o chamador não deixe disco e banco divergentes
    (o restore_from_db do startup sobrescreveria o disco com a versão antiga do banco).
    """
    if not db_enabled():
        return
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    except Exception as e:
        logger.error(f"[DB ERROR] {label} falhou: {e}")
        raise DBError(f"Erro no banco de dados ({label}): {e}") from e


def _get_connection():
    from urllib.parse import urlparse
    url = urlparse(os.getenv("DATABASE_URL"))
    return psycopg2.connect(
        host=url.hostname,
        port=url.port or 5432,
        dbname=url.path.lstrip("/"),
        user=url.username,
        password=url.password,
    )


def init_db() -> None:
    """Cria tabelas se não existirem. Chamado no startup."""
    if not db_enabled():
        return
    sql = """
    CREATE TABLE IF NOT EXISTS tools (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        code        TEXT NOT NULL,
        fingerprint TEXT NOT NULL DEFAULT '',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS workflows (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        code        TEXT NOT NULL,
        steps       JSONB NOT NULL DEFAULT '[]',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS execucoes (
        id           SERIAL PRIMARY KEY,
        run_id       TEXT NOT NULL UNIQUE,
        workflow_id  INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
        workflow_name TEXT NOT NULL,
        steps        JSONB NOT NULL DEFAULT '[]',
        status       TEXT NOT NULL DEFAULT 'running',
        exit_code    INTEGER,
        iniciado_em  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finalizado_em TIMESTAMPTZ
    );

    CREATE TABLE IF NOT EXISTS secrets (
        name        TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS gravacoes (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        conteudo    JSONB NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS qa_generated_tests (
        id            SERIAL PRIMARY KEY,
        repo_url      TEXT NOT NULL,
        function_name TEXT NOT NULL,
        language      TEXT NOT NULL,
        framework     TEXT NOT NULL,
        filename      TEXT NOT NULL,
        test_code     TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        logger.info("[DB] Tabelas verificadas/criadas.")
    except Exception as e:
        logger.error(f"[DB ERROR] init_db falhou: {e}")


# ---------------------------------------------------------------------------
# Tools CRUD
# ---------------------------------------------------------------------------

def upsert_tool(name: str, code: str, fingerprint: str = "") -> None:
    """INSERT ou UPDATE de uma tool."""
    sql = """
    INSERT INTO tools (name, code, fingerprint, updated_at)
    VALUES (%s, %s, %s, NOW())
    ON CONFLICT (name) DO UPDATE
        SET code        = EXCLUDED.code,
            fingerprint = EXCLUDED.fingerprint,
            updated_at  = NOW();
    """
    _write(sql, (name, code, fingerprint), f"upsert_tool('{name}')")


def delete_tool(name: str) -> None:
    """Remove tool do banco por nome."""
    _write("DELETE FROM tools WHERE name = %s", (name,), f"delete_tool('{name}')")


def rename_tool(old_name: str, new_name: str, new_code: str) -> None:
    """Atualiza name e code em uma única transação. O fingerprint é mantido (as ações são as mesmas)."""
    sql = """
    UPDATE tools SET name = %s, code = %s, updated_at = NOW()
    WHERE name = %s;
    """
    _write(sql, (new_name, new_code, old_name), f"rename_tool('{old_name}' → '{new_name}')")


def get_tool_fingerprint(name: str) -> str | None:
    """Fingerprint salvo no banco — usado quando o arquivo não tem o cabeçalho # actions_fp."""
    if not db_enabled():
        return None
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT fingerprint FROM tools WHERE name = %s", (name,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None
    except Exception as e:
        logger.error(f"[DB ERROR] get_tool_fingerprint('{name}') falhou: {e}")
        return None


def get_all_tools() -> list[dict]:
    """Retorna todas as tools do banco."""
    if not db_enabled():
        return []
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT name, code, fingerprint, updated_at FROM tools ORDER BY name")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] get_all_tools falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# Workflows CRUD
# ---------------------------------------------------------------------------

def upsert_workflow(name: str, code: str, steps: list[str]) -> None:
    """INSERT ou UPDATE de um workflow."""
    import json
    sql = """
    INSERT INTO workflows (name, code, steps, updated_at)
    VALUES (%s, %s, %s, NOW())
    ON CONFLICT (name) DO UPDATE
        SET code       = EXCLUDED.code,
            steps      = EXCLUDED.steps,
            updated_at = NOW();
    """
    _write(sql, (name, code, json.dumps(steps)), f"upsert_workflow('{name}')")


def delete_workflow(name: str) -> None:
    """Remove workflow do banco por nome."""
    _write("DELETE FROM workflows WHERE name = %s", (name,), f"delete_workflow('{name}')")


def get_all_workflows() -> list[dict]:
    """Retorna todos os workflows do banco."""
    if not db_enabled():
        return []
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, name, code, steps, updated_at FROM workflows ORDER BY id")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] get_all_workflows falhou: {e}")
        return []


def get_workflow_by_id(workflow_id: int) -> dict | None:
    """Retorna um workflow pelo id."""
    if not db_enabled():
        return None
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, name, steps FROM workflows WHERE id = %s", (workflow_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB ERROR] get_workflow_by_id({workflow_id}) falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# Execuções CRUD
# ---------------------------------------------------------------------------

def inserir_execucao(run_id: str, workflow_name: str, steps: list[str], workflow_id: int | None = None) -> None:
    """Registra o início de uma execução."""
    if not db_enabled():
        return
    import json
    sql = """
    INSERT INTO execucoes (run_id, workflow_id, workflow_name, steps, status)
    VALUES (%s, %s, %s, %s, 'running');
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_id, workflow_id, workflow_name, json.dumps(steps)))
    except Exception as e:
        logger.error(f"[DB ERROR] inserir_execucao('{run_id}') falhou: {e}")


def finalizar_execucao(run_id: str, exit_code: int, status: str | None = None) -> None:
    """Atualiza status e exit_code ao fim da execução. `status` sobrescreve o valor deduzido do exit_code."""
    if not db_enabled():
        return
    if status is None:
        status = "sucesso" if exit_code == 0 else ("cancelado" if exit_code == -15 else "falha")
    sql = """
    UPDATE execucoes
    SET status = %s, exit_code = %s, finalizado_em = NOW()
    WHERE run_id = %s;
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status, exit_code, run_id))
    except Exception as e:
        logger.error(f"[DB ERROR] finalizar_execucao('{run_id}') falhou: {e}")


def get_execucoes(limit: int = 100) -> list[dict]:
    """Retorna as últimas execuções ordenadas pela mais recente."""
    if not db_enabled():
        return []
    sql = """
    SELECT id, run_id, workflow_id, workflow_name, steps, status, exit_code,
           iniciado_em, finalizado_em
    FROM execucoes
    ORDER BY iniciado_em DESC
    LIMIT %s;
    """
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (limit,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] get_execucoes falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# QA Generated Tests CRUD
# ---------------------------------------------------------------------------

def save_qa_test(
    repo_url: str,
    function_name: str,
    language: str,
    framework: str,
    filename: str,
    test_code: str,
) -> None:
    """Persiste um teste gerado pelo módulo QA."""
    if not db_enabled():
        return
    sql = """
    INSERT INTO qa_generated_tests (repo_url, function_name, language, framework, filename, test_code)
    VALUES (%s, %s, %s, %s, %s, %s);
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (repo_url, function_name, language, framework, filename, test_code))
    except Exception as e:
        logger.error(f"[DB ERROR] save_qa_test('{function_name}') falhou: {e}")


def get_qa_tests(limit: int = 100) -> list[dict]:
    """Retorna os últimos testes gerados, do mais recente para o mais antigo."""
    if not db_enabled():
        return []
    sql = """
    SELECT id, repo_url, function_name, language, framework, filename, created_at
    FROM qa_generated_tests
    ORDER BY created_at DESC
    LIMIT %s;
    """
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (limit,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] get_qa_tests falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# Secrets CRUD (valores sensíveis usados pelas tools via secret("NOME"))
# ---------------------------------------------------------------------------

def upsert_secret(name: str, value: str) -> None:
    sql = """
    INSERT INTO secrets (name, value, updated_at) VALUES (%s, %s, NOW())
    ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
    """
    _write(sql, (name, value), f"upsert_secret('{name}')")


def delete_secret(name: str) -> bool:
    """Remove o secret. Levanta DBError se o banco falhar."""
    if not db_enabled():
        return False
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM secrets WHERE name = %s", (name,))
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB ERROR] delete_secret('{name}') falhou: {e}")
        raise DBError(f"Erro no banco de dados (delete_secret): {e}") from e


def list_secrets() -> list[dict]:
    """Nomes e data de atualização — nunca os valores."""
    if not db_enabled():
        return []
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT name, updated_at FROM secrets ORDER BY name")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] list_secrets falhou: {e}")
        return []


def get_secret_value(name: str) -> str | None:
    if not db_enabled():
        return None
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM secrets WHERE name = %s", (name,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"[DB ERROR] get_secret_value('{name}') falhou: {e}")
        return None


def all_secret_values() -> dict[str, str]:
    if not db_enabled():
        return {}
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name, value FROM secrets")
                return {n: v for n, v in cur.fetchall()}
    except Exception as e:
        logger.error(f"[DB ERROR] all_secret_values falhou: {e}")
        return {}


# ---------------------------------------------------------------------------
# Gravações CRUD (JSONs gravados pela extensão)
# ---------------------------------------------------------------------------

def upsert_gravacao(name: str, conteudo: dict) -> None:
    """INSERT ou UPDATE de uma gravação. Levanta DBError se o banco falhar."""
    import json
    sql = """
    INSERT INTO gravacoes (name, conteudo, updated_at) VALUES (%s, %s, NOW())
    ON CONFLICT (name) DO UPDATE SET conteudo = EXCLUDED.conteudo, updated_at = NOW();
    """
    _write(sql, (name, json.dumps(conteudo, ensure_ascii=False)), f"upsert_gravacao('{name}')")


def get_gravacao(name: str) -> dict | None:
    """
    Conteúdo da gravação, ou None se não existir.
    Levanta DBError se o banco falhar — senão uma queda do banco apareceria como "não encontrado".
    """
    if not db_enabled():
        return None
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT conteudo FROM gravacoes WHERE name = %s", (name,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"[DB ERROR] get_gravacao('{name}') falhou: {e}")
        raise DBError(f"Erro no banco de dados (get_gravacao): {e}") from e


def list_gravacoes() -> list[str]:
    """Nomes das gravações (ex.: 'pedido_venda.json'), em ordem alfabética."""
    if not db_enabled():
        return []
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM gravacoes ORDER BY name")
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] list_gravacoes falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# Startup: banco → disco
# ---------------------------------------------------------------------------

def restore_from_db(tools_dir: Path, tests_dir: Path) -> None:
    """
    Lê banco e escreve todos os .py no disco.
    Só sobrescreve se o arquivo não existir ou se o conteúdo for diferente.
    Chamado no startup — funciona tanto com `python app.py` quanto com gunicorn.
    """
    tools_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    tools = get_all_tools()
    workflows = get_all_workflows()

    restored_tools = 0
    for row in tools:
        path = tools_dir / f"{row['name']}.py"
        if not path.exists() or path.read_text(encoding="utf-8") != row["code"]:
            path.write_text(row["code"], encoding="utf-8")
            restored_tools += 1

    restored_workflows = 0
    for row in workflows:
        path = tests_dir / f"{row['name']}.py"
        if not path.exists() or path.read_text(encoding="utf-8") != row["code"]:
            path.write_text(row["code"], encoding="utf-8")
            restored_workflows += 1

    logger.info(
        f"[DB] restore_from_db: {restored_tools} tool(s) e "
        f"{restored_workflows} workflow(s) restaurados do banco."
    )
