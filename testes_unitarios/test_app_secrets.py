"""
Rotas de secrets: GET/POST /api/secrets e DELETE /api/secrets/<name>.

Modo local (secrets.json temporário do conftest) e modo banco (Postgres de teste). Os valores
nunca podem sair pela listagem.
"""

import json
from pathlib import Path

import pytest

import db
import secrets_store

BANCO_FORA = "postgresql://u:s@127.0.0.1:1/rpa_teste"


def _tool(nome: str, corpo: str) -> None:
    Path("tools", f"{nome}.py").write_text(corpo, encoding="utf-8")


# ---------------------------------------------------------------------------
# GET /api/secrets
# ---------------------------------------------------------------------------

def test_lista_vazia_sem_secrets(client):
    assert client.get("/api/secrets").json == []


def test_lista_traz_nome_data_e_tools_que_usam_sem_o_valor(client):
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "s3nh@-sintetica"})
    client.post("/api/secrets", json={"name": "OUTRO", "value": "x"})
    # criadas fora de ordem (nem a ordem de criação nem a inversa é alfabética)
    _tool("login_b", 'await campo.fill(secret("ERP_SENHA"))\n')
    _tool("login_c", 'await campo.fill(secret("ERP_SENHA"))\n')
    _tool("login_a", "await campo.fill(secret( 'ERP_SENHA' ))\n")
    _tool("sem_secret", "secret(variavel)\nsecret('minusculo')\n")

    r = client.get("/api/secrets")

    assert r.status_code == 200
    assert "s3nh@-sintetica" not in r.get_data(as_text=True)
    por_nome = {s["name"]: s for s in r.json}
    assert list(por_nome) == ["ERP_SENHA", "OUTRO"]
    assert por_nome["ERP_SENHA"]["used_by"] == ["login_a", "login_b", "login_c"]
    assert por_nome["OUTRO"]["used_by"] == []
    assert por_nome["ERP_SENHA"]["updated_at"]
    assert set(por_nome["OUTRO"]) == {"name", "updated_at", "used_by"}


def test_tool_que_usa_o_secret_duas_vezes_aparece_uma_vez_por_uso(client):
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "x"})
    _tool("login", 'secret("ERP_SENHA")\nsecret("ERP_SENHA")\n')

    assert client.get("/api/secrets").json[0]["used_by"] == ["login", "login"]


def test_lista_no_modo_banco_converte_a_data_para_iso(pg, client):
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "x"})

    item = client.get("/api/secrets").json[0]

    assert item["name"] == "ERP_SENHA"
    assert isinstance(item["updated_at"], str) and "T" in item["updated_at"]


# ---------------------------------------------------------------------------
# POST /api/secrets
# ---------------------------------------------------------------------------

def test_salvar_secret_grava_o_valor_no_arquivo_local(client, tmp_path):
    r = client.post("/api/secrets", json={"name": " ERP_SENHA ", "value": "abc"})

    assert r.status_code == 200
    assert r.json == {"ok": True, "name": "ERP_SENHA"}
    assert secrets_store.get_secret("ERP_SENHA") == "abc"
    assert json.loads((tmp_path / "secrets.json").read_text())["ERP_SENHA"]["value"] == "abc"


def test_salvar_secret_existente_atualiza_o_valor(client):
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "antigo"})
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "novo"})

    assert secrets_store.get_secret("ERP_SENHA") == "novo"
    assert len(client.get("/api/secrets").json) == 1


@pytest.mark.parametrize("valor", [None, "", 123, ["x"]])
def test_salvar_secret_sem_valor_texto_e_recusado(client, valor):
    r = client.post("/api/secrets", json={"name": "ERP_SENHA", "value": valor})

    assert r.status_code == 400
    assert "valor" in r.json["error"]
    assert secrets_store.list_secrets() == []


@pytest.mark.parametrize("nome", ["erp_senha", "1SENHA", "COM-TRACO", "", None])
def test_salvar_secret_com_nome_invalido_e_recusado(client, nome):
    r = client.post("/api/secrets", json={"name": nome, "value": "x"})

    assert r.status_code == 400
    assert "Nome inválido" in r.json["error"]
    assert secrets_store.list_secrets() == []


def test_salvar_secret_no_modo_banco_grava_no_banco_e_nao_no_arquivo(pg, client, tmp_path):
    r = client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "abc"})

    assert r.status_code == 200
    assert db.get_secret_value("ERP_SENHA") == "abc"
    assert not (tmp_path / "secrets.json").exists()


def test_lista_com_banco_inacessivel_da_500_em_vez_de_aba_vazia(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.get("/api/secrets")

    assert r.status_code == 500
    assert "banco" in r.json["error"].lower()


def test_salvar_secret_com_secrets_json_corrompido_e_recusado_e_preserva_o_arquivo(client, tmp_path):
    corrompido = '{"ERP_SENHA": {"value": "s3nh@", "updated_at": "x"},'
    (tmp_path / "secrets.json").write_text(corrompido, encoding="utf-8")

    r = client.post("/api/secrets", json={"name": "OUTRO", "value": "x"})

    assert r.status_code == 400
    assert "secrets.json" in r.json["error"]
    assert (tmp_path / "secrets.json").read_text(encoding="utf-8") == corrompido


def test_salvar_secret_com_banco_inacessivel_da_500(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "abc"})

    assert r.status_code == 500
    assert "banco" in r.json["error"].lower()
    assert not (tmp_path / "secrets.json").exists()


# ---------------------------------------------------------------------------
# DELETE /api/secrets/<name>
# ---------------------------------------------------------------------------

def test_apagar_secret_remove(client):
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "x"})

    r = client.delete("/api/secrets/ERP_SENHA")

    assert r.status_code == 200
    assert r.json == {"ok": True}
    assert secrets_store.get_secret("ERP_SENHA") is None


def test_apagar_secret_inexistente_da_404(client):
    assert client.delete("/api/secrets/NAO_EXISTE").status_code == 404


def test_apagar_secret_com_nome_invalido_e_recusado(client):
    assert client.delete("/api/secrets/minusculo").status_code == 400


def test_apagar_secret_no_modo_banco(pg, client):
    client.post("/api/secrets", json={"name": "ERP_SENHA", "value": "x"})

    assert client.delete("/api/secrets/ERP_SENHA").status_code == 200
    assert client.delete("/api/secrets/ERP_SENHA").status_code == 404
    assert db.get_secret_value("ERP_SENHA") is None


def test_apagar_secret_com_banco_inacessivel_da_500_em_vez_de_404(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA)

    r = client.delete("/api/secrets/ERP_SENHA")

    assert r.status_code == 500
    assert "banco" in r.json["error"].lower()
