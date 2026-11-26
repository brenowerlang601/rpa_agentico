"""
Entrada pela linha de comando (main.py): escolha do JSON gravado.

run_from_json é trocado por um falso que só registra o arquivo escolhido; sys.argv e
input() são simulados. json_rpa/ é a pasta temporária do teste (conftest faz chdir).
"""

from pathlib import Path

import pytest

import main


@pytest.fixture
def escolhido(monkeypatch):
    chamados = []
    monkeypatch.setattr(main, "run_from_json", chamados.append)
    monkeypatch.setattr("sys.argv", ["main.py"])
    return chamados


def _sem_input(monkeypatch):
    def proibido(_p=""):
        raise AssertionError("input() não deveria ser chamado")

    monkeypatch.setattr("builtins.input", proibido)


def _jsons(pasta, *nomes):
    for n in nomes:
        (pasta / n).write_text("{}", encoding="utf-8")


def test_argumento_com_caminho_existente_roda_esse_arquivo(escolhido, monkeypatch, tmp_path):
    _sem_input(monkeypatch)
    arquivo = tmp_path / "outro.json"
    arquivo.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["main.py", str(arquivo)])

    main.main()

    assert escolhido == [arquivo]


def test_argumento_com_nome_busca_dentro_de_json_rpa(escolhido, monkeypatch, pasta_json):
    _sem_input(monkeypatch)
    _jsons(pasta_json, "a.json", "pedido.json")
    monkeypatch.setattr("sys.argv", ["main.py", "pedido.json"])

    main.main()

    assert escolhido == [Path("json_rpa") / "pedido.json"]


def test_argumento_inexistente_sai_com_erro(escolhido, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["main.py", "nao_existe.json"])

    with pytest.raises(SystemExit) as saida:
        main.main()

    assert saida.value.code == 1
    assert "Arquivo nao encontrado: nao_existe.json" in capsys.readouterr().out
    assert escolhido == []


def test_sem_argumento_e_sem_json_sai_com_erro(escolhido, capsys):
    with pytest.raises(SystemExit) as saida:
        main.main()

    assert saida.value.code == 1
    assert "Nenhum arquivo JSON encontrado" in capsys.readouterr().out
    assert escolhido == []


def test_um_unico_json_e_escolhido_sem_perguntar(escolhido, monkeypatch, pasta_json):
    _sem_input(monkeypatch)
    _jsons(pasta_json, "unico.json")
    (pasta_json / "leia_me.txt").write_text("", encoding="utf-8")

    main.main()

    assert escolhido == [Path("json_rpa") / "unico.json"]


def test_varios_jsons_lista_em_ordem_e_roda_o_numero_escolhido(escolhido, monkeypatch, pasta_json, capsys):
    _jsons(pasta_json, "c.json", "a.json", "b.json")
    monkeypatch.setattr("builtins.input", lambda _p="": " 2 ")

    main.main()

    assert escolhido == [Path("json_rpa") / "b.json"]
    saida = capsys.readouterr().out
    assert saida.index("1. a.json") < saida.index("2. b.json") < saida.index("3. c.json")


@pytest.mark.parametrize("resposta", ["abc", "", "4"])
def test_escolha_invalida_sai_com_erro(escolhido, monkeypatch, pasta_json, capsys, resposta):
    _jsons(pasta_json, "a.json", "b.json", "c.json")
    monkeypatch.setattr("builtins.input", lambda _p="": resposta)

    with pytest.raises(SystemExit) as saida:
        main.main()

    assert saida.value.code == 1
    assert "Escolha inválida." in capsys.readouterr().out
    assert escolhido == []


@pytest.mark.xfail(strict=True, reason="BUG: escolha 0 (ou negativa) vira índice negativo e roda outro arquivo")
@pytest.mark.parametrize("resposta", ["0", "-1"])
def test_escolha_zero_ou_negativa_sai_com_erro(escolhido, monkeypatch, pasta_json, resposta):
    _jsons(pasta_json, "a.json", "b.json", "c.json")
    monkeypatch.setattr("builtins.input", lambda _p="": resposta)

    with pytest.raises(SystemExit):
        main.main()
    assert escolhido == []
