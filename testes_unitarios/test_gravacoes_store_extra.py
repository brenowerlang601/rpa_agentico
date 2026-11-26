"""
Lacunas de gravacoes_store não cobertas por test_gravacoes_store.py / test_gravacoes_banco.py:
JSON corrompido em disco, pasta ausente, acentos e o modo banco chamado direto (sem a API).
"""

import json

import pytest

import db
import gravacoes_store
from db import DBError
from testes_unitarios.gravacao_exemplo import gravacao

BANCO_FORA_DO_AR = "postgresql://usuario:senha@127.0.0.1:1/rpa_teste"


# ---------------------------------------------------------------------------
# Modo local
# ---------------------------------------------------------------------------

def test_carregar_json_corrompido_levanta_valueerror(pasta_json):
    (pasta_json / "quebrado.json").write_text('{"steps": [', encoding="utf-8")

    with pytest.raises(ValueError):
        gravacoes_store.carregar("quebrado.json")


def test_carregar_arquivo_com_bytes_invalidos_levanta_valueerror(pasta_json):
    # UnicodeDecodeError é subclasse de ValueError — o /api/load-json trata os dois como "JSON inválido"
    (pasta_json / "binario.json").write_bytes(b"\xff\xfe\x00{")

    with pytest.raises(ValueError):
        gravacoes_store.carregar("binario.json")


def test_listar_ignora_arquivos_que_nao_sao_json(pasta_json):
    (pasta_json / "nota.txt").write_text("x", encoding="utf-8")
    (pasta_json / "pedido.json.bak").write_text("{}", encoding="utf-8")
    gravacoes_store.salvar("pedido.json", gravacao())

    assert gravacoes_store.listar() == ["pedido.json"]


def test_listar_sem_a_pasta_devolve_lista_vazia(pasta_json):
    pasta_json.rmdir()

    assert gravacoes_store.listar() == []


def test_carregar_sem_a_pasta_devolve_none(pasta_json):
    pasta_json.rmdir()

    assert gravacoes_store.carregar("pedido.json") is None


def test_salvar_cria_a_pasta_se_nao_existir(pasta_json):
    pasta_json.rmdir()

    gravacoes_store.salvar("pedido.json", gravacao())

    assert gravacoes_store.carregar("pedido.json") == gravacao()


def test_salvar_grava_acentos_legiveis_no_arquivo(pasta_json):
    conteudo = gravacao()
    conteudo["name"] = "Pedido de São Paulo"

    gravacoes_store.salvar("pedido.json", conteudo)

    texto = (pasta_json / "pedido.json").read_text(encoding="utf-8")
    assert "São Paulo" in texto
    assert json.loads(texto) == conteudo


def test_modo_local_nao_abre_conexao_com_o_banco(pasta_json, monkeypatch):
    def _proibido(*a, **kw):
        raise AssertionError("banco no modo local")

    for nome in ("list_gravacoes", "get_gravacao", "upsert_gravacao"):
        monkeypatch.setattr(db, nome, _proibido)

    gravacoes_store.salvar("pedido.json", gravacao())
    assert gravacoes_store.listar() == ["pedido.json"]
    assert gravacoes_store.carregar("pedido.json") == gravacao()


# ---------------------------------------------------------------------------
# Modo banco
# ---------------------------------------------------------------------------

def test_modo_banco_salvar_carregar_e_listar_sem_tocar_o_disco(pg, pasta_json):
    gravacoes_store.salvar("b.json", gravacao())
    gravacoes_store.salvar("a.json", gravacao())

    assert gravacoes_store.listar() == ["a.json", "b.json"]
    assert gravacoes_store.carregar("a.json") == gravacao()
    assert gravacoes_store.carregar("nao_existe.json") is None
    assert list(pasta_json.iterdir()) == []


def test_modo_banco_ignora_arquivos_da_pasta_local(pg, pasta_json):
    (pasta_json / "local.json").write_text(json.dumps(gravacao()), encoding="utf-8")

    assert gravacoes_store.listar() == []
    assert gravacoes_store.carregar("local.json") is None


def test_modo_banco_nome_invalido_nao_consulta_nem_grava_no_banco(pg, monkeypatch):
    chamadas = []
    monkeypatch.setattr(db, "get_gravacao", lambda *a: chamadas.append(a))
    monkeypatch.setattr(db, "upsert_gravacao", lambda *a: chamadas.append(a))

    assert gravacoes_store.carregar("../fora.json") is None
    with pytest.raises(ValueError):
        gravacoes_store.salvar("../fora.json", gravacao())

    assert chamadas == []


def test_banco_fora_do_ar_carregar_e_salvar_levantam_dberror_sem_gravar_em_disco(monkeypatch, pasta_json):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA_DO_AR)

    with pytest.raises(DBError):
        gravacoes_store.carregar("pedido.json")
    with pytest.raises(DBError):
        gravacoes_store.salvar("pedido.json", gravacao())

    assert list(pasta_json.iterdir()) == []
