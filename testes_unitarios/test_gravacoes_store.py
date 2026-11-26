"""Armazenamento das gravações no modo local (sem DATABASE_URL → pasta json_rpa/)."""

import pytest

import gravacoes_store
from testes_unitarios.gravacao_exemplo import gravacao


@pytest.mark.parametrize("nome", ["pedido.json", "recorded_workflow__2026-03-05T19-02-46.json"])
def test_nome_valido_aceita_arquivo_json_simples(nome):
    assert gravacoes_store.nome_valido(nome)


@pytest.mark.parametrize("nome", [
    "../app.json", "sub/pedido.json", "/tmp/pedido.json", "a\\b.json",
    ".oculto.json", "pedido.txt", "pedido", "", None,
])
def test_nome_valido_recusa_caminho_e_extensao_errada(nome):
    assert not gravacoes_store.nome_valido(nome)


def test_salvar_e_carregar_devolve_o_mesmo_conteudo(pasta_json):
    gravacoes_store.salvar("pedido.json", gravacao())

    assert gravacoes_store.carregar("pedido.json") == gravacao()
    assert (pasta_json / "pedido.json").exists()


def test_carregar_inexistente_devolve_none():
    assert gravacoes_store.carregar("nao_existe.json") is None


def test_carregar_nome_invalido_devolve_none_sem_ler_fora_da_pasta():
    assert gravacoes_store.carregar("../pyproject.json") is None


def test_salvar_com_nome_invalido_levanta_erro(pasta_json):
    with pytest.raises(ValueError):
        gravacoes_store.salvar("../fora.json", gravacao())
    assert list(pasta_json.iterdir()) == []


def test_salvar_mesmo_nome_substitui_a_gravacao():
    nova = gravacao()
    nova["name"] = "Versão nova"
    gravacoes_store.salvar("pedido.json", gravacao())
    gravacoes_store.salvar("pedido.json", nova)

    assert gravacoes_store.listar() == ["pedido.json"]
    assert gravacoes_store.carregar("pedido.json")["name"] == "Versão nova"


def test_listar_devolve_nomes_em_ordem_alfabetica():
    for nome in ["b.json", "a.json", "c.json"]:
        gravacoes_store.salvar(nome, gravacao())

    assert gravacoes_store.listar() == ["a.json", "b.json", "c.json"]
