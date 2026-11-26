"""
Execução de fluxos pelo app.py: /api/run, /api/run-workflow/<id>, cancelamento, pausa/retomada,
lista de execuções ativas e histórico (/api/execucoes).

Nenhum teste roda o script gerado de verdade (ele abriria um Chromium ou conectaria na CDP):
- ou o conteúdo do script temporário é trocado por um Python inofensivo (troca do _preview_script),
  e o subprocess é real — assim pausa, cancelamento e timeout são testados com sinais de verdade;
- ou o subprocess.Popen é trocado por um falso, para conferir o comando, o ambiente e o script
  gerado sem executar nada.
"""

import io
import os
import queue
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import app as app_mod
import db
import secrets_store

_TimerOriginal = threading.Timer
# PID acima do pid_max do Linux (2**22): um sinal mandado por engano nunca atinge processo real
_PID_INEXISTENTE = 2**30


# ---------------------------------------------------------------------------
# Fixtures e auxiliares
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def estado_de_execucao_limpo():
    """Zera os registros globais de execução e mata o que algum teste deixar rodando."""
    app_mod._runs.clear()
    app_mod._cancel_pending.clear()
    yield
    for run in list(app_mod._runs.values()):
        proc = run["proc"]
        if isinstance(proc, subprocess.Popen) and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    app_mod._runs.clear()
    app_mod._cancel_pending.clear()


@pytest.fixture
def tools_existentes(tmp_path):
    """Tools tool_a, tool_b e tool_c em tools/ (pasta temporária do teste)."""
    for nome in ("tool_a", "tool_b", "tool_c"):
        (tmp_path / "tools" / f"{nome}.py").write_text(
            f"async def {nome}(page) -> bool:\n    return True\n", encoding="utf-8")
    return ["tool_a", "tool_b", "tool_c"]


class ScriptDoRun:
    """Troca o script gerado por um Python inofensivo e guarda com que argumentos foi pedido."""

    def __init__(self):
        self.codigo = "print('true')\n"
        self.chamadas = []

    def usar(self, codigo: str):
        self.codigo = textwrap.dedent(codigo)

    def __call__(self, workflow_name, steps, headless=None, cdp_url=None, start_index=0):
        self.chamadas.append({"workflow_name": workflow_name, "steps": list(steps),
                              "headless": headless, "cdp_url": cdp_url, "start_index": start_index})
        return self.codigo


@pytest.fixture
def script_do_run(monkeypatch):
    falso = ScriptDoRun()
    monkeypatch.setattr(app_mod, "_preview_script", falso)
    return falso


class LeitorSSE:
    """Lê um stream SSE numa thread, para o teste poder chamar outros endpoints no meio."""

    def __init__(self, resp):
        self.resp = resp
        self.fila: queue.Queue = queue.Queue()
        self.vistos: list[str] = []
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        try:
            for chunk in self.resp.response:
                texto = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                for parte in texto.split("\n\n"):
                    if parte:
                        self.fila.put(parte)
        finally:
            self.fila.put(None)

    def esperar(self, prefixo: str, timeout: float = 10) -> str:
        """Consome eventos até um `data: <prefixo>...` e devolve o resto do evento."""
        limite = time.time() + timeout
        while True:
            try:
                item = self.fila.get(timeout=max(0.01, limite - time.time()))
            except queue.Empty:
                raise AssertionError(f"evento {prefixo!r} não chegou; vistos: {self.vistos}")
            if item is None:
                raise AssertionError(f"stream terminou sem {prefixo!r}; vistos: {self.vistos}")
            self.vistos.append(item)
            if item.startswith("data: " + prefixo):
                return item[len("data: " + prefixo):]

    def ate_o_fim(self, timeout: float = 10) -> list[str]:
        limite = time.time() + timeout
        while True:
            try:
                item = self.fila.get(timeout=max(0.01, limite - time.time()))
            except queue.Empty:
                raise AssertionError(f"stream não terminou; vistos: {self.vistos}")
            if item is None:
                return self.vistos
            self.vistos.append(item)


def _eventos(texto: str) -> list[str]:
    """Corpo SSE completo → lista de dados (`data: X` → `X`), sem os pings."""
    return [p[len("data: "):] for p in texto.split("\n\n") if p.startswith("data: ")]


def _ate(condicao, timeout: float = 5.0) -> bool:
    limite = time.time() + timeout
    while time.time() < limite:
        if condicao():
            return True
        time.sleep(0.02)
    return condicao()


def _estado(pid: int) -> str | None:
    """Estado do processo em /proc (R, S, T = parado, Z = zumbi) ou None se não existe."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


def _scripts_temporarios(tmp_path) -> list[Path]:
    return sorted((tmp_path / "tests").glob("rpa_run_*"))


def _execucao(run_id: str) -> dict | None:
    return next((e for e in db.get_execucoes() if e["run_id"] == run_id), None)


_SCRIPT_LONGO = """
    import time
    print("inicio", flush=True)
    time.sleep(30)
"""

_SCRIPT_TIQUE = """
    import subprocess, sys, time
    filho = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(f"filho={filho.pid}", flush=True)
    i = 0
    while True:
        print(f"tique {i}", flush=True)
        i += 1
        time.sleep(0.05)
"""


# ---------------------------------------------------------------------------
# Execução headless (resposta JSON true/false)
# ---------------------------------------------------------------------------

def test_headless_com_ultima_linha_true_e_codigo_zero_responde_true(client, tools_existentes, script_do_run):
    script_do_run.usar("""
        print("passo 1")
        print("true")
    """)

    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": True})

    assert r.status_code == 200
    assert r.json["result"] == "true"
    assert r.json["exit_code"] == 0
    assert r.json["cancelled"] is False
    assert "error" not in r.json
    assert len(r.json["run_id"]) == 8


@pytest.mark.parametrize("codigo, saida", [
    ("print('false')", 0),        # script terminou bem mas o fluxo falhou
    ("print('true')", 1),         # imprimiu true mas saiu com erro
    ("pass", 0),                  # não imprimiu nada
])
def test_headless_sem_true_na_ultima_linha_ou_com_erro_responde_false(
        client, tools_existentes, script_do_run, codigo, saida):
    script_do_run.usar(f"import sys\n{codigo}\nsys.exit({saida})\n")

    r = client.post("/api/run", json={"steps": ["tool_a"]})

    assert r.json["result"] == "false"
    assert r.json["exit_code"] == saida


def test_headless_ignora_o_stderr_ao_decidir_o_resultado(client, tools_existentes, script_do_run):
    script_do_run.usar("""
        import sys
        print("true", flush=True)
        sys.stderr.write("aviso qualquer do driver\\n")
        sys.stderr.flush()
    """)

    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": True})

    assert r.json["result"] == "true"


def test_sem_headless_no_corpo_executa_em_modo_headless(client, tools_existentes, script_do_run):
    r = client.post("/api/run", json={"steps": ["tool_a"]})

    assert r.mimetype == "application/json"
    assert r.json["result"] == "true"
    assert script_do_run.chamadas[0]["headless"] is True


def test_script_temporario_fica_em_tests_com_prefixo_rpa_run_e_e_apagado_no_fim(
        client, tmp_path, tools_existentes, script_do_run):
    script_do_run.usar("""
        import os
        print(os.path.abspath(__file__))
        print("true")
    """)

    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": False})
    caminho = Path(_eventos(r.get_data(as_text=True))[1])

    assert caminho.parent == tmp_path / "tests"
    assert caminho.name.startswith("rpa_run_") and caminho.suffix == ".py"
    assert not caminho.exists()
    assert _scripts_temporarios(tmp_path) == []


def test_execucao_termina_e_sai_da_lista_de_ativas(client, tools_existentes, script_do_run):
    client.post("/api/run", json={"steps": ["tool_a"], "run_id": "fim00001"})

    assert "fim00001" not in app_mod._runs
    assert client.get("/api/run/list").json == []


def test_headless_passa_do_tempo_limite_e_mata_o_processo(
        client, monkeypatch, tmp_path, tools_existentes, script_do_run):
    monkeypatch.setattr(app_mod, "RUN_TIMEOUT", 1)
    script_do_run.usar(_SCRIPT_LONGO)

    inicio = time.time()
    r = client.post("/api/run", json={"steps": ["tool_a"], "run_id": "demora01"})

    assert time.time() - inicio < 10
    assert r.json["result"] == "false"
    assert r.json["exit_code"] == -signal.SIGKILL
    assert r.json["error"] == "Tempo limite de 1s excedido"
    assert r.json["cancelled"] is False
    assert "demora01" not in app_mod._runs
    assert _scripts_temporarios(tmp_path) == []


# ---------------------------------------------------------------------------
# Execução com browser (stream SSE)
# ---------------------------------------------------------------------------

def test_linhas_do_processo_viram_eventos_sse_e_o_fim_traz_o_codigo_de_saida(
        client, tools_existentes, script_do_run):
    script_do_run.usar("""
        import sys
        print("__RUNNING__1:tool_a")
        print("  [PASS] tool_a   ")
        sys.exit(3)
    """)

    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": False, "run_id": "sse00001"})

    assert r.mimetype == "text/event-stream"
    assert r.headers["Cache-Control"] == "no-cache"
    assert r.headers["X-Accel-Buffering"] == "no"
    assert _eventos(r.get_data(as_text=True)) == [
        "__RUN_ID__sse00001",
        "__RUNNING__1:tool_a",
        "  [PASS] tool_a",
        "__EXIT__3",
    ]
    assert "sse00001" not in app_mod._runs


def test_sse_manda_stderr_junto_com_a_saida(client, tools_existentes, script_do_run):
    script_do_run.usar("""
        import sys
        sys.stderr.write("Traceback falso\\n")
    """)

    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": False})

    assert "Traceback falso" in _eventos(r.get_data(as_text=True))


def test_script_recebe_os_secrets_como_variaveis_de_ambiente(client, tools_existentes, script_do_run):
    secrets_store.set_secret("ERP_SENHA", "valor-sintetico-123")
    script_do_run.usar("""
        import os
        print(os.environ.get("ERP_SENHA"))
        print(os.environ.get("PYTHONUNBUFFERED"))
    """)

    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": False})

    assert _eventos(r.get_data(as_text=True))[1:3] == ["valor-sintetico-123", "1"]


def test_sse_manda_heartbeat_enquanto_o_script_esta_quieto(
        client, monkeypatch, tools_existentes, script_do_run):
    monkeypatch.setattr(app_mod, "SSE_HEARTBEAT", 0.05)
    script_do_run.usar("""
        import time
        time.sleep(0.6)
        print("acordou")
    """)

    corpo = client.post("/api/run", json={"steps": ["tool_a"], "headless": False}).get_data(as_text=True)

    assert ": ping\n\n" in corpo
    assert _eventos(corpo)[-2:] == ["acordou", "__EXIT__0"]


def test_cliente_que_fecha_a_aba_encerra_o_processo_e_limpa_o_run(
        client, tmp_path, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_LONGO)
    resp = client.post("/api/run", json={"steps": ["tool_a"], "headless": False, "run_id": "aba00001"},
                       buffered=False)
    vistos = []
    for chunk in resp.response:
        vistos.append(chunk.decode("utf-8"))
        if "inicio" in vistos[-1]:
            break
    proc = app_mod._runs["aba00001"]["proc"]

    resp.close()

    assert proc.poll() == -signal.SIGTERM
    assert "aba00001" not in app_mod._runs
    assert _scripts_temporarios(tmp_path) == []


# ---------------------------------------------------------------------------
# Trecho executado: start_from / stop_before (nomes e índices)
# ---------------------------------------------------------------------------

def test_start_from_e_stop_before_por_nome_executam_so_o_trecho(client, tools_existentes, script_do_run):
    r = client.post("/api/run", json={
        "workflow_name": "Fluxo X", "steps": ["tool_a", "tool_b", "tool_c"],
        "headless": False, "start_from": "tool_b", "stop_before": "tool_c",
    })

    assert script_do_run.chamadas == [{"workflow_name": "Fluxo X", "steps": ["tool_b"], "headless": False,
                                       "cdp_url": None, "start_index": 1}]
    eventos = _eventos(r.get_data(as_text=True))
    assert eventos[1:3] == ["__START_FROM__tool_b", "__BREAKPOINT__tool_c"]


def test_fluxo_inteiro_nao_manda_start_from_nem_breakpoint(client, tools_existentes, script_do_run):
    r = client.post("/api/run", json={"steps": ["tool_a", "tool_b"], "headless": False})

    assert script_do_run.chamadas[0]["steps"] == ["tool_a", "tool_b"]
    assert script_do_run.chamadas[0]["start_index"] == 0
    assert script_do_run.chamadas[0]["workflow_name"] == "workflow"
    eventos = _eventos(r.get_data(as_text=True))
    assert not any(e.startswith(("__START_FROM__", "__BREAKPOINT__")) for e in eventos)


def test_indices_tem_prioridade_sobre_os_nomes(client, tools_existentes, script_do_run):
    client.post("/api/run", json={
        "steps": ["tool_a", "tool_b", "tool_c"], "start_from": "tool_a", "stop_before": "tool_b",
        "start_from_index": 1, "stop_before_index": 3,
    })

    assert script_do_run.chamadas[0]["steps"] == ["tool_b", "tool_c"]
    assert script_do_run.chamadas[0]["start_index"] == 1


def test_breakpoint_por_nome_procura_a_partir_do_inicio_escolhido(client, tools_existentes, script_do_run):
    # tool_a aparece duas vezes: o breakpoint é a ocorrência DEPOIS do início
    client.post("/api/run", json={
        "steps": ["tool_a", "tool_b", "tool_a", "tool_c"],
        "start_from_index": 1, "stop_before": "tool_a",
    })

    assert script_do_run.chamadas[0]["steps"] == ["tool_b"]


def test_cdp_url_vai_para_o_script(client, tools_existentes, script_do_run):
    client.post("/api/run", json={"steps": ["tool_a"], "cdp_url": "10.0.0.5"})

    assert script_do_run.chamadas[0]["cdp_url"] == "10.0.0.5"


@pytest.mark.parametrize("corpo, mensagem", [
    ({"steps": []}, "Nenhum step informado"),
    ({}, "Nenhum step informado"),
    ({"steps": ["nao_existe"]}, "Tool não encontrada"),
    ({"steps": ["tool_a"], "start_from_index": "abc"}, "Índice de início/breakpoint inválido"),
    ({"steps": ["tool_a"], "stop_before_index": [1]}, "Índice de início/breakpoint inválido"),
    ({"steps": ["tool_a"], "start_from_index": 5}, "fora do fluxo"),
    ({"steps": ["tool_a"], "start_from_index": -1}, "fora do fluxo"),
    ({"steps": ["tool_a"], "stop_before_index": 2}, "fora do fluxo"),
    ({"steps": ["tool_a", "tool_b"], "start_from_index": 1, "stop_before_index": 1}, "nenhum step"),
    ({"steps": ["tool_a", "tool_b"], "start_from": "tool_b", "stop_before_index": 0}, "nenhum step"),
])
def test_pedido_invalido_e_recusado_sem_iniciar_processo(
        client, tmp_path, tools_existentes, script_do_run, corpo, mensagem):
    r = client.post("/api/run", json=corpo)

    assert r.status_code == 400
    assert mensagem in r.json["error"]
    assert script_do_run.chamadas == []
    assert _scripts_temporarios(tmp_path) == []


# ---------------------------------------------------------------------------
# run_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("run_id", ["abc", "a" * 65, "com espaco", "../../x", 12345678, ["ab12cd34"]])
def test_run_id_invalido_e_recusado(client, tmp_path, tools_existentes, script_do_run, run_id):
    r = client.post("/api/run", json={"steps": ["tool_a"], "run_id": run_id})

    assert r.status_code == 400
    assert r.json["error"] == "run_id inválido"
    assert script_do_run.chamadas == []
    assert _scripts_temporarios(tmp_path) == []


def test_run_id_em_uso_e_recusado_com_409(client, tools_existentes, script_do_run):
    app_mod._runs["ocupado1"] = {"proc": None, "paused": False}

    r = client.post("/api/run", json={"steps": ["tool_a"], "run_id": "ocupado1"})

    assert r.status_code == 409
    assert script_do_run.chamadas == []


def test_execucoes_sem_run_id_ganham_ids_distintos(client, tools_existentes, script_do_run):
    ids = {client.post("/api/run", json={"steps": ["tool_a"]}).json["run_id"] for _ in range(3)}

    assert len(ids) == 3


def test_execucoes_paralelas_ficam_separadas_e_cancelar_uma_nao_afeta_a_outra(
        client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_LONGO)
    a = LeitorSSE(client.post("/api/run", json={"steps": ["tool_a"], "headless": False, "run_id": "para0001"},
                              buffered=False))
    b = LeitorSSE(client.post("/api/run", json={"steps": ["tool_b"], "headless": False, "run_id": "para0002"},
                              buffered=False))
    a.esperar("inicio")
    b.esperar("inicio")

    ativas = {r["run_id"] for r in client.get("/api/run/list").json}
    client.post("/api/run/cancel", json={"run_id": "para0001"})

    assert ativas == {"para0001", "para0002"}
    assert a.esperar("__EXIT__") == str(-signal.SIGTERM)
    assert app_mod._runs["para0002"]["proc"].poll() is None
    assert [r["run_id"] for r in client.get("/api/run/list").json] == ["para0002"]
    client.post("/api/run/cancel", json={"run_id": "para0002"})
    assert b.esperar("__EXIT__") == str(-signal.SIGTERM)


# ---------------------------------------------------------------------------
# Falha ao iniciar / Popen falso (comando, ambiente e script gerado)
# ---------------------------------------------------------------------------

class PopenFalso:
    """Popen que não executa nada: guarda o comando, os argumentos e o script gerado."""

    instancias: list["PopenFalso"] = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.script = Path(cmd[-1]).read_text(encoding="utf-8")
        self.pid = _PID_INEXISTENTE
        self.returncode = None
        self.stdout = io.StringIO("linha falsa\n")
        PopenFalso.instancias.append(self)

    def communicate(self, timeout=None):
        self.returncode = 0
        return "true\n", None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode


@pytest.fixture
def popen_falso(monkeypatch):
    PopenFalso.instancias = []
    monkeypatch.setattr(app_mod.subprocess, "Popen", PopenFalso)
    return PopenFalso.instancias


def test_processo_roda_o_script_gerado_na_pasta_do_app_em_grupo_proprio(
        client, tmp_path, tools_existentes, popen_falso):
    secrets_store.set_secret("ERP_SENHA", "valor-sintetico")

    r = client.post("/api/run", json={"workflow_name": "Fluxo Y", "steps": ["tool_a", "tool_b"],
                                      "start_from_index": 1, "cdp_url": "10.0.0.5"})

    assert r.json["result"] == "true"
    (p,) = popen_falso
    assert p.cmd[:2] == [sys.executable, "-u"]
    assert Path(p.cmd[2]).parent == tmp_path / "tests"
    assert p.kwargs["cwd"] == str(Path(app_mod.__file__).parent)
    assert p.kwargs["start_new_session"] is True
    assert p.kwargs["stdout"] == subprocess.PIPE
    assert p.kwargs["stderr"] == subprocess.DEVNULL
    assert p.kwargs["env"]["ERP_SENHA"] == "valor-sintetico"
    assert p.kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    # O script é o mesmo do preview, só com o trecho escolhido
    assert "from tools.tool_b import tool_b" in p.script
    assert "from tools.tool_a import" not in p.script
    assert "(1, 'tool_b', tool_b)" in p.script  # índice absoluto no fluxo
    assert "WORKFLOW_NAME = 'Fluxo Y'" in p.script
    assert "HEADLESS = True" in p.script
    assert "'http://10.0.0.5:9222'" in p.script
    assert not Path(p.cmd[2]).exists()


def test_modo_com_browser_junta_stderr_no_stdout_e_gera_script_sem_headless(
        client, tools_existentes, popen_falso):
    r = client.post("/api/run", json={"steps": ["tool_a"], "headless": False})

    assert _eventos(r.get_data(as_text=True))[-2:] == ["linha falsa", "__EXIT__0"]
    (p,) = popen_falso
    assert p.kwargs["stderr"] == subprocess.STDOUT
    assert "HEADLESS = False" in p.script


def test_falha_ao_iniciar_o_processo_responde_500_e_apaga_o_script(
        client, monkeypatch, tmp_path, tools_existentes):
    def quebra(*a, **k):
        raise OSError("sem permissão de execução")
    monkeypatch.setattr(app_mod.subprocess, "Popen", quebra)

    r = client.post("/api/run", json={"steps": ["tool_a"], "run_id": "quebra01"})

    assert r.status_code == 500
    assert "sem permissão de execução" in r.json["error"]
    assert _scripts_temporarios(tmp_path) == []
    assert "quebra01" not in app_mod._runs


# ---------------------------------------------------------------------------
# Cancelamento
# ---------------------------------------------------------------------------

def test_cancelar_execucao_sse_encerra_o_processo_com_sigterm(client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_LONGO)
    leitor = LeitorSSE(client.post("/api/run", json={"steps": ["tool_a"], "headless": False,
                                                     "run_id": "cancel01"}, buffered=False))
    leitor.esperar("inicio")

    r = client.post("/api/run/cancel", json={"run_id": "cancel01"})

    assert r.json == {"ok": True, "cancelled": ["cancel01"]}
    assert leitor.esperar("__EXIT__") == str(-signal.SIGTERM)
    leitor.ate_o_fim()
    assert "cancel01" not in app_mod._runs


def test_cancelar_execucao_headless_em_andamento_responde_cancelled(
        client, tools_existentes, script_do_run):
    from app import app as flask_app
    script_do_run.usar(_SCRIPT_LONGO)
    resposta = {}

    def rodar():
        resposta["r"] = flask_app.test_client().post("/api/run", json={"steps": ["tool_a"], "run_id": "headc001"})

    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    assert _ate(lambda: "headc001" in app_mod._runs)

    client.post("/api/run/cancel", json={"run_id": "headc001"})
    t.join(10)

    assert resposta["r"].json["cancelled"] is True
    assert resposta["r"].json["exit_code"] == -signal.SIGTERM
    assert resposta["r"].json["result"] == "false"


def test_cancelar_antes_de_o_run_ser_registrado_fica_pendente_e_e_aplicado_no_inicio(
        client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_LONGO)

    pedido = client.post("/api/run/cancel", json={"run_id": "cedo0001"})
    inicio = time.time()
    r = client.post("/api/run", json={"steps": ["tool_a"], "run_id": "cedo0001"})

    assert pedido.json == {"ok": True, "pending": True}
    assert time.time() - inicio < 10
    assert r.json["cancelled"] is True
    assert r.json["exit_code"] == -signal.SIGTERM
    assert app_mod._cancel_pending == {}


def test_cancelamento_pendente_de_outro_run_nao_afeta_a_execucao(client, tools_existentes, script_do_run):
    client.post("/api/run/cancel", json={"run_id": "outro001"})

    r = client.post("/api/run", json={"steps": ["tool_a"], "run_id": "meu00001"})

    assert r.json["result"] == "true"
    assert "outro001" in app_mod._cancel_pending


def test_pedidos_de_cancelamento_pendentes_antigos_sao_descartados(client):
    agora = time.time()
    app_mod._cancel_pending.update({"velho001": agora - 121, "recente1": agora - 60})

    client.post("/api/run/cancel", json={"run_id": "novo0001"})

    assert set(app_mod._cancel_pending) == {"recente1", "novo0001"}


@pytest.mark.parametrize("corpo", [{}, {"run_id": ""}, {"run_id": "abc"}, {"run_id": 12345678},
                                   {"run_id": "x y z w"}])
def test_cancelar_sem_run_id_valido_e_recusado_e_nao_cancela_nada(client, corpo):
    proc = PopenFalso.__new__(PopenFalso)
    proc.returncode = None
    app_mod._runs["intocado"] = {"proc": proc, "paused": False}

    r = client.post("/api/run/cancel", json=corpo)

    assert r.status_code == 400
    assert r.json["error"] == "run_id é obrigatório"
    assert app_mod._cancel_pending == {}


# ---------------------------------------------------------------------------
# Pausa e retomada
# ---------------------------------------------------------------------------

def test_pausar_e_retomar_param_e_soltam_o_grupo_inteiro_do_processo(client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_TIQUE)
    leitor = LeitorSSE(client.post("/api/run", json={"steps": ["tool_a"], "headless": False,
                                                     "run_id": "pausa001"}, buffered=False))
    filho = int(leitor.esperar("filho="))
    leitor.esperar("tique")
    pai = app_mod._runs["pausa001"]["proc"].pid

    pausa = client.post("/api/run/pause", json={"run_id": "pausa001"})
    parado = _ate(lambda: _estado(pai) == "T" and _estado(filho) == "T")
    lista = client.get("/api/run/list").json
    pausa_de_novo = client.post("/api/run/pause", json={"run_id": "pausa001"})

    retoma = client.post("/api/run/resume", json={"run_id": "pausa001"})
    andando = _ate(lambda: _estado(pai) not in ("T", None) and _estado(filho) not in ("T", None))
    retoma_de_novo = client.post("/api/run/resume", json={"run_id": "pausa001"})
    leitor.esperar("tique")

    assert pausa.json == {"ok": True, "paused": True}
    assert parado
    assert lista == [{"run_id": "pausa001", "paused": True}]
    assert pausa_de_novo.json == {"ok": False, "paused": True}
    assert retoma.json == {"ok": True, "paused": False}
    assert andando
    assert retoma_de_novo.json == {"ok": False, "paused": False}

    client.post("/api/run/cancel", json={"run_id": "pausa001"})
    assert leitor.esperar("__EXIT__") == str(-signal.SIGTERM)
    assert _ate(lambda: _estado(filho) in (None, "Z"))


def test_cancelar_execucao_pausada_encerra_logo_com_sigterm(client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_TIQUE)
    leitor = LeitorSSE(client.post("/api/run", json={"steps": ["tool_a"], "headless": False,
                                                     "run_id": "pausa002"}, buffered=False))
    leitor.esperar("tique")
    pai = app_mod._runs["pausa002"]["proc"].pid
    client.post("/api/run/pause", json={"run_id": "pausa002"})
    assert _ate(lambda: _estado(pai) == "T")

    inicio = time.time()
    client.post("/api/run/cancel", json={"run_id": "pausa002"})

    # Sem o SIGCONT o SIGTERM ficaria pendente e só o SIGKILL de 5 s depois mataria o processo
    assert leitor.esperar("__EXIT__", timeout=15) == str(-signal.SIGTERM)
    assert time.time() - inicio < 4


def test_cliente_que_fecha_a_aba_com_execucao_pausada_tambem_encerra(
        client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_TIQUE)
    resp = client.post("/api/run", json={"steps": ["tool_a"], "headless": False, "run_id": "pausa003"},
                       buffered=False)
    for chunk in resp.response:
        if b"tique" in chunk:
            break
    proc = app_mod._runs["pausa003"]["proc"]
    client.post("/api/run/pause", json={"run_id": "pausa003"})
    assert _ate(lambda: _estado(proc.pid) == "T")

    inicio = time.time()
    resp.close()

    assert proc.returncode == -signal.SIGTERM
    assert time.time() - inicio < 4
    assert "pausa003" not in app_mod._runs


@pytest.mark.parametrize("rota", ["/api/run/pause", "/api/run/resume"])
def test_pausar_ou_retomar_execucao_inexistente_da_404(client, rota):
    r = client.post(rota, json={"run_id": "naoexiste"})

    assert r.status_code == 404


@pytest.mark.parametrize("rota", ["/api/run/pause", "/api/run/resume"])
@pytest.mark.parametrize("corpo", [{}, {"run_id": "ab"}, {"run_id": None}])
def test_pausar_ou_retomar_sem_run_id_valido_da_400(client, rota, corpo):
    r = client.post(rota, json=corpo)

    assert r.status_code == 400
    assert r.json["error"] == "run_id é obrigatório"


def test_pausar_execucao_que_ja_terminou_nao_manda_sinal(client, monkeypatch):
    sinais = []
    monkeypatch.setattr(app_mod, "_signal_run", lambda proc, sig: sinais.append(sig))
    proc = PopenFalso.__new__(PopenFalso)
    proc.returncode = 0
    app_mod._runs["acabou01"] = {"proc": proc, "paused": False}

    r = client.post("/api/run/pause", json={"run_id": "acabou01"})

    assert r.json == {"ok": False, "paused": False}
    assert sinais == []
    assert app_mod._runs["acabou01"]["paused"] is False


def test_pausar_manda_sigstop_e_retomar_manda_sigcont(client, monkeypatch):
    sinais = []
    monkeypatch.setattr(app_mod, "_signal_run", lambda proc, sig: sinais.append(sig))
    proc = PopenFalso.__new__(PopenFalso)
    proc.returncode = None
    app_mod._runs["sinal001"] = {"proc": proc, "paused": False}

    client.post("/api/run/pause", json={"run_id": "sinal001"})
    client.post("/api/run/resume", json={"run_id": "sinal001"})

    assert sinais == [signal.SIGSTOP, signal.SIGCONT]


def test_lista_de_execucoes_vazia_sem_nada_rodando(client):
    assert client.get("/api/run/list").json == []


# ---------------------------------------------------------------------------
# _signal_run e _stop_run (com _force_kill)
# ---------------------------------------------------------------------------

def test_signal_run_atinge_o_grupo_inteiro_do_processo():
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import subprocess, sys, time
            f = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            print(f.pid, flush=True)
            time.sleep(60)
        """)],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    filho = int(proc.stdout.readline())
    try:
        app_mod._signal_run(proc, signal.SIGTERM)

        assert proc.wait(5) == -signal.SIGTERM
        assert _ate(lambda: _estado(filho) in (None, "Z"))
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.stdout.close()


def test_signal_run_em_processo_que_ja_sumiu_nao_levanta_erro():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    proc.wait()

    app_mod._signal_run(proc, 0)  # sinal 0: inofensivo mesmo se o PID for reaproveitado


def test_signal_run_sem_permissao_nao_levanta_erro(monkeypatch):
    def sem_permissao(pid, sig):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(app_mod.os, "killpg", sem_permissao)
    proc = PopenFalso.__new__(PopenFalso)
    proc.pid = _PID_INEXISTENTE

    app_mod._signal_run(proc, signal.SIGTERM)


class ProcControlado:
    """Processo falso: morre (poll != None) quando recebe o sinal indicado."""

    def __init__(self, vivo=True, morre_com=None):
        self.vivo = vivo
        self.morre_com = morre_com
        self.pid = _PID_INEXISTENTE

    def poll(self):
        return None if self.vivo else 0


class ListaDeSinais(list):
    """Sinais enviados, na ordem; `intervalos` guarda o atraso pedido para o SIGKILL."""

    def __init__(self):
        super().__init__()
        self.intervalos = []


@pytest.fixture
def sinais(monkeypatch):
    """Grava os sinais mandados por _stop_run e faz o timer do SIGKILL disparar na hora."""
    enviados = ListaDeSinais()
    intervalos = enviados.intervalos

    def gravar(proc, sig):
        enviados.append(sig)
        if sig == getattr(proc, "morre_com", None):
            proc.vivo = False

    class TimerRapido(_TimerOriginal):
        def __init__(self, interval, function, *a, **k):
            intervalos.append(interval)
            super().__init__(0.01, function, *a, **k)

    monkeypatch.setattr(app_mod, "_signal_run", gravar)
    monkeypatch.setattr(threading, "Timer", TimerRapido)
    return enviados


def test_stop_run_em_processo_ja_terminado_nao_manda_sinal(sinais):
    app_mod._stop_run(ProcControlado(vivo=False))
    time.sleep(0.1)

    assert sinais == []


def test_stop_run_manda_sigterm_e_sigkill_depois_de_5s_se_o_processo_nao_morrer(sinais):
    app_mod._stop_run(ProcControlado())

    assert _ate(lambda: len(sinais) == 2)
    assert sinais == [signal.SIGTERM, signal.SIGKILL]
    assert sinais.intervalos == [5]


def test_stop_run_nao_manda_sigkill_se_o_processo_morreu_com_o_sigterm(sinais):
    app_mod._stop_run(ProcControlado(morre_com=signal.SIGTERM))
    time.sleep(0.2)

    assert sinais == [signal.SIGTERM]


def test_stop_run_de_processo_pausado_manda_sigcont_antes_do_sigterm(sinais):
    app_mod._stop_run(ProcControlado(morre_com=signal.SIGTERM), paused=True)
    time.sleep(0.1)

    assert sinais == [signal.SIGCONT, signal.SIGTERM]


def test_force_kill_mata_processo_real_que_ignora_sigterm(monkeypatch):
    class TimerRapido(_TimerOriginal):
        def __init__(self, interval, function, *a, **k):
            super().__init__(0.2, function, *a, **k)
    monkeypatch.setattr(threading, "Timer", TimerRapido)
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ok', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        proc.stdout.readline()

        app_mod._stop_run(proc)

        assert proc.wait(5) == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.stdout.close()


# ---------------------------------------------------------------------------
# /api/execucoes e /api/run-workflow/<id> sem banco
# ---------------------------------------------------------------------------

def test_historico_sem_banco_e_vazio(client):
    assert client.get("/api/execucoes").json == []


@pytest.mark.parametrize("query, limite", [("", 100), ("?limit=7", 7), ("?limit=abc", 100)])
def test_historico_repassa_o_limite_pedido(client, monkeypatch, query, limite):
    pedidos = []
    monkeypatch.setattr(app_mod, "db_get_execucoes", lambda limit: pedidos.append(limit) or [])

    client.get("/api/execucoes" + query)

    assert pedidos == [limite]


def test_run_workflow_sem_banco_da_404(client):
    r = client.post("/api/run-workflow/1", json={})

    assert r.status_code == 404
    assert "1" in r.json["error"]


@pytest.mark.parametrize("steps", [[], None, "tool_a"])
def test_run_workflow_sem_lista_de_steps_e_recusado(client, monkeypatch, steps, script_do_run):
    monkeypatch.setattr(app_mod, "db_get_workflow_by_id",
                        lambda wid: {"id": wid, "name": "wf", "steps": steps})

    r = client.post("/api/run-workflow/3", json={})

    assert r.status_code == 400
    assert r.json["error"] == "Workflow não tem steps"
    assert script_do_run.chamadas == []


def test_run_workflow_executa_os_steps_do_banco_com_os_parametros_do_corpo(
        client, monkeypatch, tools_existentes, script_do_run):
    monkeypatch.setattr(app_mod, "db_get_workflow_by_id",
                        lambda wid: {"id": wid, "name": "Fluxo do banco", "steps": ["tool_a", "tool_b", "tool_c"]})
    registros = []
    monkeypatch.setattr(app_mod, "db_inserir_execucao",
                        lambda run_id, nome, steps, workflow_id=None: registros.append((run_id, nome, steps, workflow_id)))

    r = client.post("/api/run-workflow/42", json={"headless": False, "start_from_index": 1,
                                                  "stop_before": "tool_c", "cdp_url": "10.1.1.1:9300",
                                                  "run_id": "wf000042"})

    assert _eventos(r.get_data(as_text=True))[:3] == [
        "__RUN_ID__wf000042", "__START_FROM__tool_b", "__BREAKPOINT__tool_c"]
    assert script_do_run.chamadas == [{"workflow_name": "Fluxo do banco", "steps": ["tool_b"],
                                       "headless": False, "cdp_url": "10.1.1.1:9300", "start_index": 1}]
    assert registros == [("wf000042", "Fluxo do banco", ["tool_b"], 42)]


@pytest.mark.xfail(strict=True, reason="BUG: POST sem corpo JSON responde 415 (request.json levanta no Flask 3) "
                                        "em vez de usar os padrões do `request.json or {}`")
def test_run_workflow_sem_corpo_roda_headless(client, monkeypatch, tools_existentes, script_do_run):
    monkeypatch.setattr(app_mod, "db_get_workflow_by_id",
                        lambda wid: {"id": wid, "name": "wf", "steps": ["tool_a"]})

    r = client.post("/api/run-workflow/5")

    assert r.json["result"] == "true"


# ---------------------------------------------------------------------------
# Registro em execucoes (Postgres de teste)
# ---------------------------------------------------------------------------

def test_execucao_com_sucesso_fica_registrada_no_banco(pg, client, tools_existentes, script_do_run):
    r = client.post("/api/run", json={"workflow_name": "Fluxo Z", "steps": ["tool_a", "tool_b"],
                                      "start_from_index": 1, "run_id": "banco001"})

    assert r.json["result"] == "true"
    e = _execucao("banco001")
    assert e["status"] == "sucesso"
    assert e["exit_code"] == 0
    assert e["workflow_name"] == "Fluxo Z"
    assert e["steps"] == ["tool_b"]
    assert e["workflow_id"] is None
    assert e["finalizado_em"] is not None


def test_execucao_com_falha_fica_registrada_como_falha(pg, client, tools_existentes, script_do_run):
    script_do_run.usar("import sys; print('false'); sys.exit(1)")

    client.post("/api/run", json={"steps": ["tool_a"], "run_id": "banco002"})

    assert _execucao("banco002")["status"] == "falha"
    assert _execucao("banco002")["exit_code"] == 1


def test_execucao_em_andamento_fica_como_running_e_cancelada_como_cancelado(
        pg, client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_LONGO)
    leitor = LeitorSSE(client.post("/api/run", json={"steps": ["tool_a"], "headless": False,
                                                     "run_id": "banco003"}, buffered=False))
    leitor.esperar("inicio")
    durante = _execucao("banco003")

    client.post("/api/run/cancel", json={"run_id": "banco003"})
    leitor.ate_o_fim()

    assert durante["status"] == "running"
    assert durante["finalizado_em"] is None
    assert _execucao("banco003")["status"] == "cancelado"
    assert _execucao("banco003")["exit_code"] == -signal.SIGTERM


def test_execucao_que_estoura_o_tempo_fica_como_timeout(pg, client, monkeypatch, tools_existentes, script_do_run):
    monkeypatch.setattr(app_mod, "RUN_TIMEOUT", 1)
    script_do_run.usar(_SCRIPT_LONGO)

    client.post("/api/run", json={"steps": ["tool_a"], "run_id": "banco004"})

    assert _execucao("banco004")["status"] == "timeout"
    assert _execucao("banco004")["exit_code"] == -signal.SIGKILL


def test_aba_fechada_fica_como_interrompido(pg, client, tools_existentes, script_do_run):
    script_do_run.usar(_SCRIPT_LONGO)
    resp = client.post("/api/run", json={"steps": ["tool_a"], "headless": False, "run_id": "banco005"},
                       buffered=False)
    for chunk in resp.response:
        if b"inicio" in chunk:
            break

    resp.close()

    assert _execucao("banco005")["status"] == "interrompido"
    assert _execucao("banco005")["exit_code"] == -signal.SIGTERM


def test_historico_vem_do_banco_mais_recente_primeiro_e_respeita_o_limite(
        pg, client, tools_existentes, script_do_run):
    for i in range(3):
        client.post("/api/run", json={"steps": ["tool_a"], "run_id": f"hist000{i}"})

    tudo = client.get("/api/execucoes").json
    dois = client.get("/api/execucoes?limit=2").json

    assert [e["run_id"] for e in tudo] == ["hist0002", "hist0001", "hist0000"]
    assert [e["run_id"] for e in dois] == ["hist0002", "hist0001"]


def test_run_workflow_pelo_id_do_banco_registra_o_workflow_id(pg, client, tools_existentes, script_do_run):
    db.upsert_workflow("fluxo_banco", "# script", ["tool_a", "tool_b"])
    wid = next(w["id"] for w in db.get_all_workflows() if w["name"] == "fluxo_banco")

    r = client.post(f"/api/run-workflow/{wid}", json={"run_id": "wfbanco1"})
    inexistente = client.post(f"/api/run-workflow/{wid + 999}", json={})

    assert r.json["result"] == "true"
    assert script_do_run.chamadas[0]["steps"] == ["tool_a", "tool_b"]
    assert script_do_run.chamadas[0]["workflow_name"] == "fluxo_banco"
    assert _execucao("wfbanco1")["workflow_id"] == wid
    assert inexistente.status_code == 404
