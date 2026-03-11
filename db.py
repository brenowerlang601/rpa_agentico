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
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (name, code, fingerprint))
    except Exception as e:
        logger.error(f"[DB ERROR] upsert_tool('{name}') falhou: {e}")


def delete_tool(name: str) -> None:
    """Remove tool do banco por nome."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tools WHERE name = %s", (name,))
    except Exception as e:
        logger.error(f"[DB ERROR] delete_tool('{name}') falhou: {e}")


def rename_tool(old_name: str, new_name: str, new_code: str) -> None:
    """Atualiza name e code em uma única transação."""
    sql = """
    UPDATE tools SET name = %s, code = %s, fingerprint = '', updated_at = NOW()
    WHERE name = %s;
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (new_name, new_code, old_name))
    except Exception as e:
        logger.error(f"[DB ERROR] rename_tool('{old_name}' → '{new_name}') falhou: {e}")


def get_all_tools() -> list[dict]:
    """Retorna todas as tools do banco."""
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
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (name, code, json.dumps(steps)))
    except Exception as e:
        logger.error(f"[DB ERROR] upsert_workflow('{name}') falhou: {e}")


def delete_workflow(name: str) -> None:
    """Remove workflow do banco por nome."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workflows WHERE name = %s", (name,))
    except Exception as e:
        logger.error(f"[DB ERROR] delete_workflow('{name}') falhou: {e}")


def get_all_workflows() -> list[dict]:
    """Retorna todos os workflows do banco."""
    try:
        with _get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT name, code, steps, updated_at FROM workflows ORDER BY name")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB ERROR] get_all_workflows falhou: {e}")
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
