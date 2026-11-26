"""
Fixtures dos testes unitários.

Nenhum teste fala com o banco de produção, com a OpenAI ou com o ERP:
- DATABASE_URL e OPENAI_API_KEY são zerados ANTES de qualquer import do projeto. O app.py
  roda init_db() e restore_from_db() no import — com o .env carregado, importar o app num
  teste escreveria no banco real e sobrescreveria tools/ e tests/ com o que está lá.
- Gravações (json_rpa/) e secrets locais vão para uma pasta temporária por teste.

Teste que precisa de Postgres de verdade usa o fixture `pg` (pulado sem RPA_PG_TEST_DSN).
"""

import os

# Precisa vir antes de qualquer import do projeto: o load_dotenv do db.py não sobrescreve
# variável que já existe no ambiente, então o .env não religa o banco nem a OpenAI.
os.environ["DATABASE_URL"] = ""
os.environ["OPENAI_API_KEY"] = ""

from urllib.parse import urlparse  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def sem_servicos_externos(monkeypatch, tmp_path):
    """
    Modo local isolado: sem banco e com json_rpa/, secrets.json, tools/ e tests/ em pasta
    temporária. TOOLS_DIR/TESTS_DIR são caminhos relativos (Path("tools")), então mudar o
    diretório atual para a pasta do teste basta para nenhum teste tocar as tools reais.
    """
    import gravacoes_store
    import secrets_store

    monkeypatch.chdir(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    pasta = tmp_path / "json_rpa"
    pasta.mkdir()
    monkeypatch.setattr(gravacoes_store, "JSON_RPA_DIR", pasta)
    monkeypatch.setattr(secrets_store, "SECRETS_FILE", tmp_path / "secrets.json")
    return pasta


@pytest.fixture
def pasta_json(sem_servicos_externos):
    """Pasta json_rpa/ temporária do teste (modo local)."""
    return sem_servicos_externos


@pytest.fixture
def client():
    """Cliente HTTP do Flask (importa o app já com os serviços externos desligados)."""
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


_TABELAS = "gravacoes, execucoes, workflows, tools, secrets, qa_generated_tests"


@pytest.fixture
def pg(monkeypatch):
    """
    Liga o modo banco apontando para um Postgres DE TESTE, com as tabelas vazias.

    Exemplo (container descartável):
        docker run -d --rm --name rpa-pg-teste -e POSTGRES_PASSWORD=teste \\
            -e POSTGRES_DB=rpa_teste -p 55499:5432 postgres:16-alpine
        RPA_PG_TEST_DSN=postgresql://postgres:teste@127.0.0.1:55499/rpa_teste uv run pytest

    O fixture apaga o conteúdo das tabelas, por isso só aceita banco com "teste" no nome.
    """
    dsn = os.getenv("RPA_PG_TEST_DSN")
    if not dsn:
        pytest.skip("RPA_PG_TEST_DSN não definido — teste com Postgres pulado")
    if "teste" not in urlparse(dsn).path.lstrip("/"):
        pytest.fail("RPA_PG_TEST_DSN precisa apontar para um banco com 'teste' no nome")

    import db

    monkeypatch.setenv("DATABASE_URL", dsn)
    db.init_db()
    with db._get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {_TABELAS} RESTART IDENTITY CASCADE")
    return dsn
