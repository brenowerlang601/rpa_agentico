"""
Interface web para gerenciar workflows RPA.
- Upload de JSON → gera tools automaticamente
- Canvas visual drag-and-drop de tools
- Editor de código inline por tool
- Preview do script gerado em tempo real
"""

import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from flask import Flask, Response, jsonify, render_template, request

from db import DBError
from db import delete_tool as db_delete_tool
from db import finalizar_execucao as db_finalizar_execucao
from db import get_execucoes as db_get_execucoes
from db import get_workflow_by_id as db_get_workflow_by_id
from db import init_db, inserir_execucao as db_inserir_execucao
from db import rename_tool as db_rename_tool
from db import restore_from_db, upsert_tool as db_upsert_tool
from db import upsert_workflow as db_upsert_workflow
import gravacoes_store
import secrets_store
from json_processor import (
    TESTS_DIR,
    TOOLS_DIR,
    _read_tool_fingerprint,
    _slug,
    annotate_masked,
    apply_secrets,
    build_script,
    group_steps,
    is_valid_tool_name,
    name_steps,
    read_script_steps,
    resolve_tool_names,
    test_script_path,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Flask(__name__)
# Startup: garante que banco e disco estão sincronizados.
# Roda tanto via `python app.py` quanto via gunicorn/uwsgi no container.
init_db()
restore_from_db(TOOLS_DIR, TESTS_DIR)

# Tempo máximo de uma execução headless (sem janela), em segundos
RUN_TIMEOUT = int(os.environ.get("RPA_RUN_TIMEOUT", "900"))
# Tempo máximo esperando o usuário revisar uma tool gerada, em segundos
REVIEW_TIMEOUT = 600
# Intervalo de heartbeat dos streams SSE — sem escrita periódica o servidor não percebe
# que o cliente fechou a aba e o processo ficaria órfão
SSE_HEARTBEAT = 15

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# Processos ativos de execução — keyed por run_id (suporta execuções paralelas)
# Estrutura: {run_id: {"proc": Popen, "paused": bool}}
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()
# Cancelamentos pedidos antes de a execução ser registrada: {run_id: timestamp}
_cancel_pending: dict[str, float] = {}

# Gerações de tools em andamento — keyed por gen_id (cada aba tem a sua revisão)
# Estrutura: {gen_id: {"event": Event, "response": dict|None, "awaiting": bool}}
_generations: dict[str, dict] = {}
# Nomes de tool reservados por gerações em andamento: evita que duas gerações
# simultâneas escolham o mesmo nome _vN e uma sobrescreva a outra
_reserved_tool_names: set[str] = set()
_gen_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message: str, status: int = 400, **extra):
    return jsonify({"error": message, **extra}), status


def _existing_tools() -> set[str]:
    return {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}


def _all_tools() -> list[dict]:
    """Retorna todas as tools disponíveis em tools/ com seu código."""
    tools = []
    for p in sorted(TOOLS_DIR.glob("*.py")):
        if p.stem == "__init__":
            continue
        tools.append({"name": p.stem, "code": p.read_text(encoding="utf-8")})
    return tools


def _workflow_files() -> list[Path]:
    return [
        p for p in sorted(TESTS_DIR.glob("*.py"))
        if p.stem != "__init__" and not p.stem.startswith("rpa_run_")
    ]


def _all_workflows() -> list[dict]:
    """Retorna todos os scripts de teste gerados em tests/."""
    workflows = []
    for p in _workflow_files():
        steps = read_script_steps(p.read_text(encoding="utf-8"))
        workflows.append({"name": p.stem, "steps": steps, "file": p.name})
    return workflows


def _workflows_using(tool_name: str) -> list[Path]:
    """Scripts salvos em tests/ que usam a tool."""
    return [
        p for p in _workflow_files()
        if tool_name in read_script_steps(p.read_text(encoding="utf-8"))
    ]


def _validate_steps(steps) -> str | None:
    """Retorna mensagem de erro se a lista de steps for inválida, ou None."""
    if not isinstance(steps, list) or not steps:
        return "Nenhum step informado"
    invalid = [s for s in steps if not is_valid_tool_name(s)]
    if invalid:
        return f"Nome de tool inválido: {invalid[0]!r}"
    missing = sorted(set(steps) - _existing_tools())
    if missing:
        return f"Tool não encontrada: {', '.join(missing)}"
    return None


def _normalize_cdp_url(cdp_url: str | None) -> str | None:
    """Aceita "1.2.3.4", "1.2.3.4:9223" ou "http://..." e devolve uma URL completa."""
    if not cdp_url:
        return None
    if not cdp_url.startswith("http"):
        cdp_url = f"http://{cdp_url}" if ":" in cdp_url else f"http://{cdp_url}:9222"
    return cdp_url


def _preview_script(workflow_name: str, steps: list[str], headless: bool | None = None,
                    cdp_url: str | None = None, start_index: int = 0) -> str:
    """Gera o script de teste para uma sequência de steps (mesmo código do script salvo)."""
    return build_script(
        workflow_name, steps, headless=headless,
        cdp_url=_normalize_cdp_url(cdp_url), start_index=start_index,
    )


def _gravacao_invalida(data) -> str | None:
    """Mensagem de erro se o conteúdo não for uma gravação no formato esperado, ou None."""
    if not isinstance(data, dict) or not isinstance(data.get("steps", []), list):
        return "JSON fora do formato esperado (precisa ter a lista 'steps')"
    return None


def _workflow_from_json(filename: str, data: dict) -> dict:
    """Monta o workflow (steps agrupados e nomeados) a partir de uma gravação já validada."""
    workflow_name = data.get("name") or Path(filename).stem
    grouped = group_steps(data.get("steps", []))
    named = name_steps(grouped)
    steps = resolve_tool_names(named, TOOLS_DIR, _existing_tools())
    return {
        "workflow_name": workflow_name,
        "workflow_slug": _slug(workflow_name),
        "steps": steps,
        "named_steps": [{"tool_name": s["tool_name"], "actions": s["actions"]} for s in named],
        # Campos de senha gravados como ******** — a interface pede um secret para cada um
        "masked_fields": annotate_masked(named),
        "tools": _all_tools(),
    }


def _signal_run(proc: subprocess.Popen, sig: int) -> None:
    """
    Envia o sinal para o grupo de processos da execução (Python + driver Node do Playwright
    + Chromium headless). Pausar só o Python deixava o driver contando os timeouts.
    """
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_run(proc: subprocess.Popen, paused: bool = False) -> None:
    """Encerra a execução: SIGTERM no grupo e, se não terminar em 5 s, SIGKILL."""
    if proc.poll() is not None:
        return
    if paused:
        _signal_run(proc, signal.SIGCONT)
    _signal_run(proc, signal.SIGTERM)

    def _force_kill():
        if proc.poll() is None:
            _signal_run(proc, signal.SIGKILL)

    t = threading.Timer(5, _force_kill)
    t.daemon = True
    t.start()


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tools")
def api_tools():
    return jsonify(_all_tools())


@app.route("/api/workflows")
def api_workflows():
    from db import get_all_workflows
    rows = get_all_workflows()
    # Inclui o id do banco junto com os dados do disco
    db_map = {r["name"]: r["id"] for r in rows}
    result = []
    for w in _all_workflows():
        w["id"] = db_map.get(w["name"])
        result.append(w)
    return jsonify(result)


@app.route("/api/run-workflow/<int:workflow_id>", methods=["POST"])
def api_run_workflow_by_id(workflow_id: int):
    """Executa um workflow pelo seu ID do banco (SSE, ou JSON true/false em modo headless)."""
    row = db_get_workflow_by_id(workflow_id)
    if not row:
        return _error(f"Workflow ID {workflow_id} não encontrado", 404)

    steps = row["steps"] if isinstance(row["steps"], list) else []
    if not steps:
        return _error("Workflow não tem steps")

    data = request.json or {}
    return _run_steps(
        workflow_name=row["name"],
        steps=steps,
        headless=data.get("headless", True),
        start_from=data.get("start_from") or None,
        stop_before=data.get("stop_before") or None,
        start_from_index=data.get("start_from_index"),
        stop_before_index=data.get("stop_before_index"),
        cdp_url=data.get("cdp_url") or None,
        run_id=data.get("run_id") or None,
        workflow_id=workflow_id,
    )


@app.route("/api/json-files")
def api_json_files():
    """Retorna todas as gravações disponíveis (banco ou pasta json_rpa/)."""
    return jsonify([{"name": n, "stem": Path(n).stem} for n in gravacoes_store.listar()])


@app.route("/api/load-json", methods=["POST"])
def api_load_json():
    """Carrega e processa uma gravação já importada."""
    filename = (request.json or {}).get("filename", "")
    if not gravacoes_store.nome_valido(filename):
        return _error("Nome de arquivo inválido")
    try:
        data = gravacoes_store.carregar(filename)
    except DBError as e:
        return _error(str(e), 500)
    except (ValueError, UnicodeDecodeError) as e:
        return _error(f"JSON inválido: {e}")
    if data is None:
        return _error("Arquivo não encontrado", 404)
    err = _gravacao_invalida(data)
    if err:
        return _error(err)
    return jsonify(_workflow_from_json(filename, data))


@app.route("/api/tool/<name>")
def api_tool_code(name: str):
    if not is_valid_tool_name(name):
        return _error("Nome de tool inválido")
    p = TOOLS_DIR / f"{name}.py"
    if not p.exists():
        return _error("Tool não encontrada", 404)
    return jsonify({"name": name, "code": p.read_text(encoding="utf-8")})


@app.route("/api/tool/new", methods=["POST"])
def api_tool_new():
    """Cria uma nova tool do zero."""
    data = request.json or {}
    name = (data.get("name") or "").strip()
    code = data.get("code", "")
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not is_valid_tool_name(name):
        return _error("Nome inválido: use letras, números e _ (sem começar com número)")
    p = TOOLS_DIR / f"{name}.py"
    if p.exists():
        return _error(f"Já existe uma tool chamada '{name}'", 409)
    try:
        db_upsert_tool(name, code)
    except DBError as e:
        return _error(str(e), 500)
    p.write_text(code, encoding="utf-8")
    return jsonify({"ok": True, "name": name})


@app.route("/api/tool/<name>", methods=["POST"])
def api_tool_save(name: str):
    """Salva código editado de uma tool."""
    if not is_valid_tool_name(name):
        return _error("Nome de tool inválido")
    p = TOOLS_DIR / f"{name}.py"
    code = (request.json or {}).get("code", "")
    # Mantém o fingerprint: o cabeçalho do código editado ou o que a tool já tinha
    fingerprint = ""
    for line in code.splitlines()[:3]:
        if line.startswith("# actions_fp:"):
            fingerprint = line.split(":", 1)[1].strip()
            break
    if not fingerprint and p.exists():
        fingerprint = _read_tool_fingerprint(p) or ""
    try:
        db_upsert_tool(name, code, fingerprint)
    except DBError as e:
        return _error(str(e), 500)
    p.write_text(code, encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/tool/<name>", methods=["DELETE"])
def api_tool_delete(name: str):
    """
    Deleta uma tool da pasta tools/.
    Se ela é usada em workflows salvos, só deleta com ?force=1 (o frontend pede confirmação).
    """
    if not is_valid_tool_name(name):
        return _error("Nome de tool inválido")
    p = TOOLS_DIR / f"{name}.py"
    if not p.exists():
        return _error("Tool não encontrada", 404)
    using = [w.stem for w in _workflows_using(name)]
    if using and request.args.get("force") != "1":
        return _error(
            f"A tool '{name}' é usada nos workflows: {', '.join(using)}",
            409, workflows=using,
        )
    try:
        db_delete_tool(name)
    except DBError as e:
        return _error(str(e), 500)
    p.unlink()
    return jsonify({"ok": True, "workflows_affected": using})


@app.route("/api/tool/<name>/rename", methods=["POST"])
def api_tool_rename(name: str):
    """Renomeia uma tool (arquivo + nome da função) e atualiza os workflows salvos que a usam."""
    if not is_valid_tool_name(name):
        return _error("Nome de tool inválido")
    new_name = ((request.json or {}).get("new_name") or "").strip()
    new_name = re.sub(r"[^a-zA-Z0-9_]", "_", new_name)
    if not is_valid_tool_name(new_name):
        return _error("Novo nome inválido: use letras, números e _ (sem começar com número)")

    old_path = TOOLS_DIR / f"{name}.py"
    new_path = TOOLS_DIR / f"{new_name}.py"
    if not old_path.exists():
        return _error("Tool não encontrada", 404)
    if new_path.exists():
        return _error(f"Já existe uma tool chamada '{new_name}'", 409)

    code = old_path.read_text(encoding="utf-8")
    # Substitui o nome da função principal no código
    code = re.sub(rf"\basync def {re.escape(name)}\b", f"async def {new_name}", code)
    code = re.sub(rf"\bdef {re.escape(name)}\b", f"def {new_name}", code)

    try:
        db_rename_tool(name, new_name, code)
    except DBError as e:
        return _error(str(e), 500)
    new_path.write_text(code, encoding="utf-8")
    old_path.unlink()

    # Atualiza os scripts salvos: import, chamada e lista de steps.
    # \b não casa dentro de nomes maiores (fazer_login ≠ fazer_login_v2), pois _ é caractere de palavra.
    updated, failed = [], []
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    for wf in _workflows_using(name):
        wf_code = pattern.sub(new_name, wf.read_text(encoding="utf-8"))
        try:
            db_upsert_workflow(wf.stem, wf_code, read_script_steps(wf_code))
        except DBError:
            failed.append(wf.stem)
            continue
        wf.write_text(wf_code, encoding="utf-8")
        updated.append(wf.stem)

    result = {"ok": True, "new_name": new_name, "workflows_updated": updated}
    if failed:
        result["warning"] = f"Não foi possível atualizar os workflows: {', '.join(failed)}"
    return jsonify(result)


@app.route("/api/tools/check", methods=["POST"])
def api_tools_check():
    """Verifica quais tools de uma lista existem em tools/."""
    steps = (request.json or {}).get("steps", [])
    existing = _existing_tools()
    return jsonify({
        "existing": [s for s in steps if s in existing],
        "pending": [s for s in steps if s not in existing],
    })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Recebe upload de JSON, valida, salva (banco ou json_rpa/) e processa."""
    if "file" not in request.files:
        return _error("Nenhum arquivo enviado")
    f = request.files["file"]
    filename = f.filename or ""
    if not gravacoes_store.nome_valido(filename):
        return _error("Nome de arquivo inválido — envie um arquivo .json")

    # Valida antes de gravar: um JSON quebrado não pode ficar salvo
    try:
        data = json.loads(f.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return _error(f"JSON inválido: {e}")
    err = _gravacao_invalida(data)
    if err:
        return _error(err)

    try:
        gravacoes_store.salvar(filename, data)
    except DBError as e:
        return _error(str(e), 500)
    return jsonify(_workflow_from_json(filename, data))


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Gera preview do script para uma sequência de steps."""
    data = request.json or {}
    workflow_name = data.get("workflow_name") or "workflow"
    steps = data.get("steps", [])
    if not steps:
        return jsonify({"script": ""})
    invalid = [s for s in steps if not is_valid_tool_name(s)]
    if invalid:
        return _error(f"Nome de tool inválido: {invalid[0]!r}")
    return jsonify({"script": _preview_script(workflow_name, steps)})


@app.route("/api/save-workflow", methods=["POST"])
def api_save_workflow():
    """
    Salva o workflow (sequência de steps) como script de teste.
    Grava direto no nome escolhido — antes gravava no slug do workflow (sobrescrevendo
    outro script com o mesmo slug) e depois apagava esse arquivo.
    """
    data = request.json or {}
    workflow_name = data.get("workflow_name") or "workflow"
    script_name = (data.get("script_name") or "").strip()
    steps = data.get("steps", [])
    err = _validate_steps(steps)
    if err:
        return _error(err)

    path = test_script_path(workflow_name, TESTS_DIR, script_name or None)
    if path.stem.startswith("rpa_run_"):
        return _error("Nomes iniciados com rpa_run_ são reservados para execuções temporárias")
    code = build_script(workflow_name, steps)
    try:
        db_upsert_workflow(path.stem, code, steps)
    except DBError as e:
        return _error(str(e), 500)
    path.write_text(code, encoding="utf-8")
    return jsonify({"ok": True, "file": path.name})


def _run_steps(workflow_name: str, steps: list, headless: bool = True,
               start_from: str | None = None, stop_before: str | None = None,
               start_from_index: int | None = None, stop_before_index: int | None = None,
               cdp_url: str | None = None, run_id: str | None = None,
               workflow_id: int | None = None):
    """
    Lógica central de execução: gera script temporário e executa.
    - headless: responde JSON {"result": "true"|"false", ...} ao terminar.
    - com browser: faz stream SSE da saída.
    Índices (start_from_index/stop_before_index) são absolutos na lista de steps e têm
    prioridade sobre os nomes — com nomes, uma tool repetida no fluxo era ambígua.
    """
    err = _validate_steps(steps)
    if err:
        return _error(err)

    # Resolve o trecho a executar: [start, stop)
    try:
        start = int(start_from_index) if start_from_index is not None else (
            steps.index(start_from) if start_from in steps else 0)
        stop = int(stop_before_index) if stop_before_index is not None else (
            steps.index(stop_before, start) if stop_before in steps[start:] else len(steps))
    except (TypeError, ValueError):
        return _error("Índice de início/breakpoint inválido")
    if not (0 <= start < len(steps)) or not (0 <= stop <= len(steps)):
        return _error("Índice de início/breakpoint fora do fluxo")
    if stop <= start:
        return _error("Breakpoint está no primeiro step executado — nenhum step para executar")
    run_steps = steps[start:stop]

    if run_id is None:
        run_id = uuid.uuid4().hex[:8]
    elif not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        return _error("run_id inválido")
    with _runs_lock:
        if run_id in _runs:
            return _error("run_id já está em uso", 409)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="rpa_run_", dir=TESTS_DIR, delete=False, encoding="utf-8"
    ) as tf:
        tf.write(_preview_script(workflow_name, run_steps, headless=bool(headless),
                                 cdp_url=cdp_url, start_index=start))
        tmp_path = Path(tf.name)

    env = os.environ.copy()
    # Secrets da aba Secrets viram variáveis de ambiente — secret("NOME") lê daqui
    env.update(secrets_store.all_values())
    env["PYTHONUNBUFFERED"] = "1"
    try:
        # start_new_session: o processo vira líder de um grupo próprio, para que pausar/cancelar
        # atinja também o driver do Playwright e o browser headless
        proc = subprocess.Popen(
            [sys.executable, "-u", str(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if headless else subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(Path(__file__).parent),
            env=env,
            start_new_session=True,
        )
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return _error(f"Falha ao iniciar a execução: {e}", 500)

    with _runs_lock:
        _runs[run_id] = {"proc": proc, "paused": False}
        cancel_requested = _cancel_pending.pop(run_id, None) is not None
    db_inserir_execucao(run_id, workflow_name, run_steps, workflow_id=workflow_id)
    if cancel_requested:
        _stop_run(proc)

    def _cleanup(exit_code: int, status: str | None = None):
        db_finalizar_execucao(run_id, exit_code, status=status)
        with _runs_lock:
            _runs.pop(run_id, None)
        tmp_path.unlink(missing_ok=True)

    if headless:
        # Modo headless: executa e retorna apenas true/false
        timed_out = False
        try:
            out, _ = proc.communicate(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal_run(proc, signal.SIGKILL)
            out, _ = proc.communicate()
        exit_code = proc.returncode
        lines = (out or "").strip().splitlines()
        result = "true" if exit_code == 0 and lines and lines[-1].strip() == "true" else "false"
        _cleanup(exit_code, status="timeout" if timed_out else None)
        body = {
            "result": result,
            "run_id": run_id,
            "exit_code": exit_code,
            "cancelled": exit_code == -signal.SIGTERM,
        }
        if timed_out:
            body["error"] = f"Tempo limite de {RUN_TIMEOUT}s excedido"
        return jsonify(body)

    def generate():
        # Lê a saída numa thread: assim o stream pode mandar heartbeat enquanto o script
        # está quieto e perceber quando o cliente fecha a aba
        lines: queue.Queue = queue.Queue()

        def _reader():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=_reader, daemon=True).start()
        finished = False
        try:
            yield f"data: __RUN_ID__{run_id}\n\n"
            if start > 0:
                yield f"data: __START_FROM__{steps[start]}\n\n"
            if stop < len(steps):
                yield f"data: __BREAKPOINT__{steps[stop]}\n\n"
            while True:
                try:
                    line = lines.get(timeout=SSE_HEARTBEAT)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if line is None:
                    break
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            finished = True
            yield f"data: __EXIT__{proc.returncode}\n\n"
        finally:
            if not finished:
                # Cliente desconectou (GeneratorExit) ou erro: não deixa o script rodando
                # no ERP sem ninguém acompanhando
                with _runs_lock:
                    paused = _runs.get(run_id, {}).get("paused", False)
                _stop_run(proc, paused=paused)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _signal_run(proc, signal.SIGKILL)
                    proc.wait()
                _cleanup(proc.returncode if proc.returncode is not None else -9, status="interrompido")
            else:
                _cleanup(proc.returncode)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/run", methods=["POST"])
def api_run():
    """Executa steps fornecidos diretamente (SSE, ou JSON true/false em modo headless)."""
    data = request.json or {}
    return _run_steps(
        workflow_name=data.get("workflow_name") or "workflow",
        steps=data.get("steps", []),
        headless=data.get("headless", True),
        start_from=data.get("start_from") or None,
        stop_before=data.get("stop_before") or None,
        start_from_index=data.get("start_from_index"),
        stop_before_index=data.get("stop_before_index"),
        cdp_url=data.get("cdp_url") or None,
        run_id=data.get("run_id") or None,
    )


def _get_run_id():
    run_id = (request.json or {}).get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        return None
    return run_id


@app.route("/api/run/cancel", methods=["POST"])
def api_run_cancel():
    """
    Cancela UMA execução. run_id é obrigatório — antes, sem ele (ou com um id já
    encerrado) cancelava as execuções de todos os usuários.
    Se o id ainda não foi registrado, o pedido fica guardado e é aplicado assim que começar.
    """
    run_id = _get_run_id()
    if not run_id:
        return _error("run_id é obrigatório")
    now = time.time()
    with _runs_lock:
        # Descarta pedidos pendentes antigos
        for rid, ts in list(_cancel_pending.items()):
            if now - ts > 120:
                _cancel_pending.pop(rid, None)
        run = _runs.get(run_id)
        if run is None:
            _cancel_pending[run_id] = now
            return jsonify({"ok": True, "pending": True})
    _stop_run(run["proc"], paused=run.get("paused", False))
    return jsonify({"ok": True, "cancelled": [run_id]})


def _set_paused(paused: bool):
    run_id = _get_run_id()
    if not run_id:
        return _error("run_id é obrigatório")
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return _error("Execução não encontrada", 404)
    if run["proc"].poll() is None and run.get("paused") != paused:
        _signal_run(run["proc"], signal.SIGSTOP if paused else signal.SIGCONT)
        run["paused"] = paused
        return jsonify({"ok": True, "paused": paused})
    return jsonify({"ok": False, "paused": run.get("paused", False)})


@app.route("/api/run/pause", methods=["POST"])
def api_run_pause():
    """Pausa uma execução pelo run_id (o grupo inteiro: script, driver e browser headless)."""
    return _set_paused(True)


@app.route("/api/run/resume", methods=["POST"])
def api_run_resume():
    """Retoma uma execução pausada pelo run_id."""
    return _set_paused(False)


@app.route("/api/run/list", methods=["GET"])
def api_run_list():
    """Lista todas as execuções ativas com seus run_ids."""
    with _runs_lock:
        return jsonify([
            {"run_id": rid, "paused": r.get("paused", False)}
            for rid, r in _runs.items()
        ])


@app.route("/api/execucoes", methods=["GET"])
def api_execucoes():
    """Retorna histórico de execuções (últimas 100 por padrão)."""
    limit = request.args.get("limit", 100, type=int)
    return jsonify(db_get_execucoes(limit=limit))


@app.route("/api/generate-tools", methods=["POST"])
def api_generate_tools():
    """
    Gera tools via LLM com aprovação interativa via SSE.
    O primeiro evento é __GEN_ID__<id>; para cada tool nova envia __REVIEW__ + código e
    aguarda a resposta do usuário em /api/review-response (com o mesmo gen_id).
    Rejeição, timeout ou erro do LLM descartam só aquela tool — a geração continua.
    """
    from json_processor import _actions_fingerprint, group_steps, name_steps
    from tool_generator import _build_prompt, _call_llm, _save_tool, expects_url_change

    data = request.json or {}
    filename = data.get("filename", "")
    named_steps = data.get("named_steps")  # [{tool_name, actions}]
    secrets_map = data.get("secrets_map") or {}  # {masked_id: NOME_DO_SECRET}

    # Suporte a passar filename OU named_steps diretamente
    if filename:
        if not gravacoes_store.nome_valido(filename):
            return _error("Nome de arquivo inválido")
        try:
            raw = gravacoes_store.carregar(filename)
        except DBError as e:
            return _error(str(e), 500)
        except (ValueError, UnicodeDecodeError) as e:
            return _error(f"JSON inválido: {e}")
        if raw is None:
            return _error("Arquivo não encontrado", 404)
        err = _gravacao_invalida(raw)
        if err:
            return _error(err)
        named_steps = name_steps(group_steps(raw.get("steps", [])))

    if not named_steps or not isinstance(named_steps, list):
        return _error("Nenhum step para processar")

    # Campos de senha: o valor mascarado é trocado pelo secret escolhido na interface
    known = {x["name"] for x in secrets_store.list_secrets()}
    unknown = sorted(set(secrets_map.values()) - known)
    if unknown:
        return _error(f"Secret não cadastrado: {', '.join(unknown)}")
    try:
        apply_secrets(named_steps, secrets_map)
    except ValueError as e:
        return _error(str(e))

    gen_id = uuid.uuid4().hex[:12]
    state = {"event": threading.Event(), "response": None, "awaiting": False}
    with _gen_lock:
        # Nomes reservados por outras gerações contam como existentes
        resolve_tool_names(named_steps, TOOLS_DIR, _existing_tools() | _reserved_tool_names)
        invalid = [s["tool_name"] for s in named_steps if not is_valid_tool_name(s.get("tool_name"))]
        if invalid:
            return _error(f"Nome de tool inválido: {invalid[0]!r}")
        reserved = {s["tool_name"] for s in named_steps} - _existing_tools()
        _reserved_tool_names.update(reserved)
        _generations[gen_id] = state

    def wait_review():
        """Aguarda a resposta do usuário mandando heartbeat (detecta aba fechada). Gera eventos SSE."""
        state["event"].clear()
        state["response"] = None
        state["awaiting"] = True
        deadline = time.time() + REVIEW_TIMEOUT
        try:
            while time.time() < deadline:
                if state["event"].wait(timeout=SSE_HEARTBEAT):
                    return
                yield ": ping\n\n"
        finally:
            state["awaiting"] = False

    def generate():
        try:
            yield f"data: __GEN_ID__{gen_id}\n\n"
            for index, step in enumerate(named_steps):
                name = step["tool_name"]
                actions = step.get("actions") or []
                new_fp = _actions_fingerprint(actions)

                # Verifica se já existe com fingerprint idêntico
                tool_path = TOOLS_DIR / f"{name}.py"
                if tool_path.exists() and _read_tool_fingerprint(tool_path) == new_fp:
                    yield f"data: __SKIP__{name}\n\n"
                    continue

                # Gera código via LLM
                yield f"data: __GENERATING__{name}\n\n"
                prompt = _build_prompt(name, actions,
                                       expect_url_change=expects_url_change(named_steps, index))
                try:
                    code = _call_llm(prompt)
                except Exception as e:
                    logging.error(f"[generate-tools] LLM falhou para '{name}': {e}")
                    yield f"data: __GEN_ERROR__{json.dumps({'name': name, 'error': str(e)}, ensure_ascii=False)}\n\n"
                    continue

                # Loop de revisão
                feedback_history = []
                while True:
                    payload = json.dumps({"name": name, "code": code}, ensure_ascii=False)
                    yield f"data: __REVIEW__{payload}\n\n"

                    yield from wait_review()
                    response = state["response"]
                    if response is None:
                        yield f"data: __REVIEW_TIMEOUT__{name}\n\n"
                        break

                    action = response.get("action")
                    feedback = (response.get("feedback") or "").strip()

                    if action == "approve":
                        try:
                            _save_tool(name, code, fingerprint=new_fp)
                        except DBError as e:
                            yield f"data: __GEN_ERROR__{json.dumps({'name': name, 'error': str(e)}, ensure_ascii=False)}\n\n"
                            break
                        yield f"data: __APPROVED__{name}\n\n"
                        break
                    if action == "reject":
                        # Nada foi salvo; segue para a próxima tool
                        yield f"data: __REJECTED__{name}\n\n"
                        break
                    if action == "revise":
                        yield f"data: __REVISING__{name}\n\n"
                        if feedback:
                            feedback_history.append(feedback)
                        revision_note = "\n".join(f"- {fb}" for fb in feedback_history)
                        try:
                            code = _call_llm(
                                prompt
                                + f"\n\nO usuário pediu as seguintes revisões (aplique TODAS):\n{revision_note}"
                            )
                        except Exception as e:
                            logging.error(f"[generate-tools] LLM falhou na revisão de '{name}': {e}")
                            yield f"data: __GEN_ERROR__{json.dumps({'name': name, 'error': str(e)}, ensure_ascii=False)}\n\n"
                            break
                        # Volta ao topo do while para nova revisão

            yield "data: __GENERATE_DONE__\n\n"
        finally:
            with _gen_lock:
                _generations.pop(gen_id, None)
                _reserved_tool_names.difference_update(reserved)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/secrets", methods=["GET"])
def api_secrets_list():
    """Lista os secrets (só nomes, data e tools que usam — nunca os valores)."""
    usage: dict[str, list[str]] = {}
    for p in TOOLS_DIR.glob("*.py"):
        for name in re.findall(r"""secret\(\s*["']([A-Z_][A-Z0-9_]*)["']\s*\)""", p.read_text(encoding="utf-8")):
            usage.setdefault(name, []).append(p.stem)
    items = []
    for item in secrets_store.list_secrets():
        updated = item.get("updated_at")
        items.append({
            "name": item["name"],
            "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
            "used_by": sorted(usage.get(item["name"], [])),
        })
    return jsonify(items)


@app.route("/api/secrets", methods=["POST"])
def api_secrets_save():
    """Cria ou atualiza um secret: {name, value}."""
    data = request.json or {}
    name = (data.get("name") or "").strip()
    value = data.get("value")
    if not isinstance(value, str) or value == "":
        return _error("Informe o valor do secret")
    try:
        secrets_store.set_secret(name, value)
    except ValueError as e:
        return _error(str(e))
    except DBError as e:
        return _error(str(e), 500)
    return jsonify({"ok": True, "name": name})


@app.route("/api/secrets/<name>", methods=["DELETE"])
def api_secrets_delete(name: str):
    if not secrets_store.is_valid_name(name):
        return _error("Nome de secret inválido")
    try:
        removed = secrets_store.delete_secret(name)
    except DBError as e:
        return _error(str(e), 500)
    if not removed:
        return _error("Secret não encontrado", 404)
    return jsonify({"ok": True})


@app.route("/api/review-response", methods=["POST"])
def api_review_response():
    """Recebe a resposta do usuário para uma tool em revisão: {gen_id, action, feedback}."""
    data = request.json or {}
    if data.get("action") not in ("approve", "reject", "revise"):
        return _error("Ação inválida")
    with _gen_lock:
        state = _generations.get(data.get("gen_id") or "")
    if state is None:
        return _error("Geração não encontrada (já terminou ou o servidor reiniciou)", 404)
    if not state["awaiting"]:
        return _error("Esta geração não está aguardando revisão no momento", 409)
    state["response"] = {"action": data["action"], "feedback": data.get("feedback", "")}
    state["awaiting"] = False
    state["event"].set()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# QA — Gerador de testes a partir de repositórios de código-fonte
# ---------------------------------------------------------------------------

@app.route("/api/qa/generate-tests", methods=["POST"])
def api_qa_generate_tests():
    """
    SSE: recebe repo_url + function_name, clona o repo, extrai a função
    e gera código de teste via LLM.

    Eventos SSE emitidos:
      __QA_FETCHING__          — clonando/atualizando repositório
      __QA_FOUND__<json>       — função encontrada: {language, framework, file_path}
      __QA_GENERATING__        — chamando LLM para gerar testes
      __QA_DONE__<json>        — {test_code, filename, framework, language}
      __QA_ERROR__<mensagem>   — erro em qualquer etapa
    """
    from qa_code_analyzer import fetch_function_from_repo
    from qa_test_generator import generate_tests

    data = request.json or {}
    repo_url = (data.get("repo_url") or "").strip()
    function_name = (data.get("function_name") or "").strip()
    hint_file_path = (data.get("file_path") or "").strip()
    example_params = (data.get("example_params") or "").strip()

    if not repo_url or not function_name:
        return jsonify({"error": "repo_url e function_name são obrigatórios"}), 400

    def generate():
        import json as _json

        try:
            yield "data: __QA_FETCHING__\n\n"

            analysis = fetch_function_from_repo(repo_url, function_name, hint_file_path=hint_file_path)

            found_payload = _json.dumps({
                "language": analysis["language"],
                "framework": analysis["framework"],
                "file_path": analysis["file_path"],
            }, ensure_ascii=False)
            yield f"data: __QA_FOUND__{found_payload}\n\n"

            yield "data: __QA_GENERATING__\n\n"

            result = generate_tests(
                function_name=analysis["function_name"],
                function_code=analysis["function_code"],
                file_context=analysis["file_context"],
                language=analysis["language"],
                framework=analysis["framework"],
                example_params=example_params,
            )

            # Persiste no banco
            try:
                from db import save_qa_test
                save_qa_test(
                    repo_url=repo_url,
                    function_name=function_name,
                    language=result["language"],
                    framework=result["framework"],
                    filename=result["filename"],
                    test_code=result["test_code"],
                )
            except Exception as db_err:
                logging.warning(f"[QA] Falha ao salvar no banco: {db_err}")

            done_payload = _json.dumps({
                "test_code": result["test_code"],
                "filename": result["filename"],
                "framework": result["framework"],
                "language": result["language"],
            }, ensure_ascii=False)
            yield f"data: __QA_DONE__{done_payload}\n\n"

        except Exception as exc:
            import traceback
            logging.error(f"[QA] Erro ao gerar testes: {exc}\n{traceback.format_exc()}")
            yield f"data: __QA_ERROR__{str(exc)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/qa/history", methods=["GET"])
def api_qa_history():
    """Lista os testes gerados anteriormente."""
    try:
        from db import get_qa_tests
        return jsonify(get_qa_tests())
    except Exception as e:
        logging.error(f"[QA] Erro ao buscar histórico: {e}")
        return jsonify([])


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
