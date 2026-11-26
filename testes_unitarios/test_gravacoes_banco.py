"""
Gravações no modo banco (produção): a tabela gravacoes é a fonte, o disco não é usado.

Precisa de Postgres de teste — sem RPA_PG_TEST_DSN os testes são pulados (ver fixture `pg`).
"""

import db
from testes_unitarios.gravacao_exemplo import arquivo, gravacao


def _upload(client, nome, conteudo):
    return client.post("/api/upload", data=arquivo(nome, conteudo),
                       content_type="multipart/form-data")


def test_upload_grava_no_banco_e_nao_no_disco(pg, client, pasta_json):
    r = _upload(client, "pedido.json", gravacao())

    assert r.status_code == 200
    assert db.list_gravacoes() == ["pedido.json"]
    assert db.get_gravacao("pedido.json") == gravacao()
    assert list(pasta_json.iterdir()) == []


def test_lista_e_reabertura_vem_do_banco(pg, client):
    importado = _upload(client, "pedido.json", gravacao()).json

    lista = client.get("/api/json-files").json
    reaberto = client.post("/api/load-json", json={"filename": "pedido.json"}).json

    assert lista == [{"name": "pedido.json", "stem": "pedido"}]
    assert reaberto["named_steps"] == importado["named_steps"]


def test_reimportar_o_mesmo_nome_substitui_sem_duplicar(pg, client):
    nova = gravacao()
    nova["name"] = "Versão nova"
    _upload(client, "pedido.json", gravacao())
    _upload(client, "pedido.json", nova)

    assert db.list_gravacoes() == ["pedido.json"]
    assert db.get_gravacao("pedido.json")["name"] == "Versão nova"


def test_gravacao_inexistente_no_banco_da_404(pg, client):
    r = client.post("/api/load-json", json={"filename": "nao_existe.json"})

    assert r.status_code == 404
