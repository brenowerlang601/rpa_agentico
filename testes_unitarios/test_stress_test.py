"""
Teste de carga (stress_test.py).

É um script que, ao ser importado, lê sys.argv e já dispara as requisições contra o servidor.
Aqui ele é carregado com sys.argv simulado e urllib.request.urlopen trocado por um falso que
devolve um stream SSE sintético — nenhuma requisição sai da máquina.
"""

import importlib.util
import threading
import urllib.request
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "stress_test.py"


class _RespostaSSE:
    def __init__(self, linhas):
        self.linhas = [l.encode("utf-8") for l in linhas]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.linhas)


@pytest.fixture
def servidor_falso(monkeypatch):
    """urlopen falso: registra as requisições e responde `estado['linhas']`."""
    estado = {"requisicoes": [], "linhas": ["data: __EXIT__0\n"], "erro": None}
    trava = threading.Lock()

    def urlopen(req, timeout=None):
        with trava:
            estado["requisicoes"].append((req, timeout))
        if estado["erro"]:
            raise estado["erro"]
        return _RespostaSSE(estado["linhas"])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return estado


def _carregar(monkeypatch, *args):
    """Executa o script como se fosse `python stress_test.py <args>` e devolve o módulo."""
    monkeypatch.setattr("sys.argv", ["stress_test.py", *args])
    spec = importlib.util.spec_from_file_location("stress_test_em_teste", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_sem_argumentos_usa_3_execucoes_no_servidor_local(servidor_falso, monkeypatch):
    m = _carregar(monkeypatch)

    assert m.N == 3
    assert m.URL == "http://localhost:5000/api/run-workflow/3"


def test_argumentos_definem_quantidade_e_servidor(servidor_falso, monkeypatch):
    m = _carregar(monkeypatch, "5", "http://srv.exemplo.local:8080/")

    assert m.N == 5
    assert m.URL == "http://srv.exemplo.local:8080/api/run-workflow/3"


def test_requisicao_pede_execucao_headless_via_post(servidor_falso, monkeypatch):
    m = _carregar(monkeypatch, "2")
    servidor_falso["requisicoes"].clear()

    m.executar(1)

    ((req, timeout),) = servidor_falso["requisicoes"]
    assert req.get_method() == "POST"
    assert req.full_url == "http://localhost:5000/api/run-workflow/3"
    assert req.data == b'{"headless": true}'
    assert req.get_header("Content-type") == "application/json"
    assert timeout == 300


def test_worker_mostra_run_id_mensagens_e_resultado(servidor_falso, monkeypatch, capsys):
    m = _carregar(monkeypatch, "2")
    capsys.readouterr()
    servidor_falso["linhas"] = [
        "data: __RUN_ID__abc123\n", "\n", ": comentario\n", "data: [OK] login\n", "data: __EXIT__0\n",
    ]

    m.executar(7)

    assert capsys.readouterr().out.splitlines() == [
        "[Worker 7] Iniciando...",
        "[Worker 7] run_id = abc123",
        "[Worker 7] [OK] login",
        "[Worker 7] Finalizado: OK",
    ]


def test_worker_mostra_codigo_de_saida_quando_a_execucao_falha(servidor_falso, monkeypatch, capsys):
    m = _carregar(monkeypatch, "2")
    capsys.readouterr()
    servidor_falso["linhas"] = ["data: __EXIT__1\n"]

    m.executar(1)

    assert "[Worker 1] Finalizado: FALHOU (exit 1)" in capsys.readouterr().out


def test_worker_mostra_erro_de_conexao_sem_derrubar_o_teste(servidor_falso, monkeypatch, capsys):
    m = _carregar(monkeypatch, "2")
    capsys.readouterr()
    servidor_falso["erro"] = ConnectionRefusedError("recusado")

    m.executar(2)

    assert "[Worker 2] ERRO: recusado" in capsys.readouterr().out


@pytest.mark.xfail(strict=True, reason="BUG: range(1, N + 0) dispara N-1 execuções em vez de N")
def test_dispara_exatamente_n_execucoes_simultaneas(servidor_falso, monkeypatch, capsys):
    _carregar(monkeypatch, "4")

    assert len(servidor_falso["requisicoes"]) == 4
    assert "Todas as 4 execuções concluídas" in capsys.readouterr().out
