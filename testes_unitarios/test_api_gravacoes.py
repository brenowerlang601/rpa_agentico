"""Endpoints de importação e abertura de gravações (/api/upload, /api/json-files, /api/load-json)."""

from testes_unitarios.gravacao_exemplo import arquivo, gravacao


def _upload(client, nome, conteudo):
    return client.post("/api/upload", data=arquivo(nome, conteudo),
                       content_type="multipart/form-data")


def test_upload_salva_a_gravacao_e_ela_aparece_na_lista(client):
    r = _upload(client, "pedido.json", gravacao())

    assert r.status_code == 200
    assert r.json["workflow_name"] == "Cadastro de exemplo"
    assert r.json["steps"]
    assert client.get("/api/json-files").json == [{"name": "pedido.json", "stem": "pedido"}]


def test_gravacao_importada_reabre_com_os_mesmos_steps(client):
    importado = _upload(client, "pedido.json", gravacao()).json

    reaberto = client.post("/api/load-json", json={"filename": "pedido.json"})

    assert reaberto.status_code == 200
    assert reaberto.json["steps"] == importado["steps"]
    assert reaberto.json["named_steps"] == importado["named_steps"]


def test_upload_de_json_quebrado_e_recusado_e_nao_fica_salvo(client, pasta_json):
    r = _upload(client, "ruim.json", b"{isso nao e json")

    assert r.status_code == 400
    assert "JSON inválido" in r.json["error"]
    assert list(pasta_json.iterdir()) == []


def test_upload_sem_lista_de_steps_e_recusado_e_nao_fica_salvo(client, pasta_json):
    r = _upload(client, "lista.json", [1, 2, 3])

    assert r.status_code == 400
    assert "steps" in r.json["error"]
    assert list(pasta_json.iterdir()) == []


def test_upload_com_nome_que_nao_e_json_e_recusado(client):
    r = _upload(client, "pedido.txt", gravacao())

    assert r.status_code == 400
    assert "Nome de arquivo inválido" in r.json["error"]


def test_abrir_gravacao_inexistente_da_404(client):
    r = client.post("/api/load-json", json={"filename": "nao_existe.json"})

    assert r.status_code == 404


def test_gerar_tools_de_gravacao_inexistente_da_404_antes_de_chamar_o_llm(client):
    r = client.post("/api/generate-tools", json={"filename": "nao_existe.json"})

    assert r.status_code == 404


def test_banco_fora_do_ar_responde_500_em_vez_de_nao_encontrado(client, monkeypatch):
    # Porta 1 recusa a conexão na hora: simula o banco de produção indisponível
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:s@127.0.0.1:1/rpa_teste")

    abrir = client.post("/api/load-json", json={"filename": "pedido.json"})
    importar = _upload(client, "pedido.json", gravacao())

    assert abrir.status_code == 500
    assert importar.status_code == 500
    assert "banco" in abrir.json["error"].lower()


def test_lista_de_gravacoes_com_banco_fora_do_ar_responde_500_em_vez_de_lista_vazia(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:s@127.0.0.1:1/rpa_teste")

    r = client.get("/api/json-files")

    assert r.status_code == 500
    assert "banco" in r.json["error"].lower()
