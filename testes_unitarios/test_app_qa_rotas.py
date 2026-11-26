"""
Camada HTTP do gerador de testes de QA: /api/qa/generate-tests (SSE) e /api/qa/history.

fetch_function_from_repo (clona repositório) e generate_tests (chama o LLM) são trocados por
falsos — aqui só interessa o que a rota faz com o pedido, com o resultado e com os erros.
"""

import json

import pytest

import db
import qa_code_analyzer
import qa_test_generator

_ANALISE = {
    "function_name": "calcular_total",
    "function_code": "def calcular_total(itens):\n    return sum(itens)\n",
    "file_context": "# módulo sintético\n",
    "language": "python",
    "framework": "pytest",
    "file_path": "pkg/calculo.py",
}
_RESULTADO = {
    "test_code": "def test_calcular_total():\n    assert calcular_total([1, 2]) == 3\n",
    "filename": "test_calculo.py",
    "framework": "pytest",
    "language": "python",
}


def _eventos(texto: str) -> list[str]:
    return [p[len("data: "):] for p in texto.split("\n\n") if p.startswith("data: ")]


class Gravador:
    """Função falsa que guarda os argumentos e devolve (ou levanta) o que foi configurado."""

    def __init__(self, retorno=None, erro: Exception | None = None):
        self.retorno = retorno
        self.erro = erro
        self.chamadas: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.chamadas.append((args, kwargs))
        if self.erro:
            raise self.erro
        return self.retorno


@pytest.fixture
def busca(monkeypatch):
    falso = Gravador(retorno=dict(_ANALISE))
    monkeypatch.setattr(qa_code_analyzer, "fetch_function_from_repo", falso)
    return falso


@pytest.fixture
def gerador(monkeypatch):
    falso = Gravador(retorno=dict(_RESULTADO))
    monkeypatch.setattr(qa_test_generator, "generate_tests", falso)
    return falso


@pytest.fixture
def salvar(monkeypatch):
    falso = Gravador()
    monkeypatch.setattr(db, "save_qa_test", falso)
    return falso


_PEDIDO = {"repo_url": "  file:///tmp/repo_sintetico  ", "function_name": "  calcular_total ",
           "file_path": " pkg/calculo.py ", "example_params": " itens=[1, 2] "}


# ---------------------------------------------------------------------------
# /api/qa/generate-tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("corpo", [
    {},
    {"repo_url": "file:///tmp/repo"},
    {"function_name": "f"},
    {"repo_url": "   ", "function_name": "f"},
    {"repo_url": "file:///tmp/repo", "function_name": "   "},
    {"repo_url": None, "function_name": None},
])
def test_sem_repo_ou_sem_funcao_e_recusado_sem_buscar_nada(client, busca, gerador, corpo):
    r = client.post("/api/qa/generate-tests", json=corpo)

    assert r.status_code == 400
    assert r.json["error"] == "repo_url e function_name são obrigatórios"
    assert busca.chamadas == []
    assert gerador.chamadas == []


def test_fluxo_completo_emite_os_eventos_na_ordem_com_os_dados(client, busca, gerador, salvar):
    r = client.post("/api/qa/generate-tests", json=_PEDIDO)

    assert r.mimetype == "text/event-stream"
    assert r.headers["Cache-Control"] == "no-cache"
    eventos = _eventos(r.get_data(as_text=True))
    assert eventos == [
        "__QA_FETCHING__",
        "__QA_FOUND__" + json.dumps({"language": "python", "framework": "pytest",
                                     "file_path": "pkg/calculo.py"}, ensure_ascii=False),
        "__QA_GENERATING__",
        "__QA_DONE__" + json.dumps(_RESULTADO, ensure_ascii=False),
    ]


def test_campos_do_pedido_chegam_sem_espacos_na_busca_e_na_geracao(client, busca, gerador, salvar):
    client.post("/api/qa/generate-tests", json=_PEDIDO).get_data()

    assert busca.chamadas == [(("file:///tmp/repo_sintetico", "calcular_total"),
                               {"hint_file_path": "pkg/calculo.py"})]
    assert gerador.chamadas == [((), {
        "function_name": "calcular_total",
        "function_code": _ANALISE["function_code"],
        "file_context": _ANALISE["file_context"],
        "language": "python",
        "framework": "pytest",
        "example_params": "itens=[1, 2]",
    })]


def test_campos_opcionais_ausentes_viram_texto_vazio(client, busca, gerador, salvar):
    client.post("/api/qa/generate-tests", json={"repo_url": "file:///tmp/r", "function_name": "f"}).get_data()

    assert busca.chamadas[0][1] == {"hint_file_path": ""}
    assert gerador.chamadas[0][1]["example_params"] == ""


def test_teste_gerado_e_salvo_no_historico(client, busca, gerador, salvar):
    client.post("/api/qa/generate-tests", json=_PEDIDO).get_data()

    assert salvar.chamadas == [((), {
        "repo_url": "file:///tmp/repo_sintetico",
        "function_name": "calcular_total",
        "language": "python",
        "framework": "pytest",
        "filename": "test_calculo.py",
        "test_code": _RESULTADO["test_code"],
    })]


def test_falha_ao_salvar_no_banco_nao_impede_de_entregar_o_teste(client, monkeypatch, busca, gerador):
    monkeypatch.setattr(db, "save_qa_test", Gravador(erro=RuntimeError("banco caiu")))

    eventos = _eventos(client.post("/api/qa/generate-tests", json=_PEDIDO).get_data(as_text=True))

    assert eventos[-1].startswith("__QA_DONE__")
    assert not any(e.startswith("__QA_ERROR__") for e in eventos)


def test_funcao_nao_encontrada_emite_erro_e_nao_gera(client, monkeypatch, gerador, salvar):
    monkeypatch.setattr(qa_code_analyzer, "fetch_function_from_repo",
                        Gravador(erro=ValueError("Função 'xyz' não encontrada")))

    eventos = _eventos(client.post("/api/qa/generate-tests", json=_PEDIDO).get_data(as_text=True))

    assert eventos == ["__QA_FETCHING__", "__QA_ERROR__Função 'xyz' não encontrada"]
    assert gerador.chamadas == []
    assert salvar.chamadas == []


def test_falha_do_llm_emite_erro_depois_de_gerando_e_nao_salva(client, monkeypatch, busca, salvar):
    monkeypatch.setattr(qa_test_generator, "generate_tests", Gravador(erro=RuntimeError("LLM indisponível")))

    eventos = _eventos(client.post("/api/qa/generate-tests", json=_PEDIDO).get_data(as_text=True))

    assert eventos[-2:] == ["__QA_GENERATING__", "__QA_ERROR__LLM indisponível"]
    assert salvar.chamadas == []


# ---------------------------------------------------------------------------
# /api/qa/history
# ---------------------------------------------------------------------------

def test_historico_sem_banco_e_vazio(client):
    assert client.get("/api/qa/history").json == []


def test_historico_devolve_o_que_o_banco_tem(client, monkeypatch):
    linhas = [{"id": 2, "function_name": "b"}, {"id": 1, "function_name": "a"}]
    monkeypatch.setattr(db, "get_qa_tests", lambda: linhas)

    assert client.get("/api/qa/history").json == linhas


def test_historico_com_erro_no_banco_devolve_lista_vazia(client, monkeypatch):
    monkeypatch.setattr(db, "get_qa_tests", Gravador(erro=RuntimeError("banco caiu")))

    r = client.get("/api/qa/history")

    assert r.status_code == 200
    assert r.json == []


def test_teste_gerado_aparece_no_historico_do_banco(pg, client, busca, gerador):
    client.post("/api/qa/generate-tests", json=_PEDIDO).get_data()

    historico = client.get("/api/qa/history").json

    assert len(historico) == 1
    assert historico[0]["repo_url"] == "file:///tmp/repo_sintetico"
    assert historico[0]["function_name"] == "calcular_total"
    assert historico[0]["filename"] == "test_calculo.py"
    assert "test_code" not in historico[0]
