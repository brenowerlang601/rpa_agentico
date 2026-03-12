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
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from flask import Flask, Response, jsonify, render_template, request

from db import delete_tool as db_delete_tool
from db import delete_workflow as db_delete_workflow
from db import finalizar_execucao as db_finalizar_execucao
from db import get_execucoes as db_get_execucoes
from db import get_workflow_by_id as db_get_workflow_by_id
from db import init_db, inserir_execucao as db_inserir_execucao
from db import rename_tool as db_rename_tool
from db import restore_from_db, upsert_tool as db_upsert_tool
from db import upsert_workflow as db_upsert_workflow
from json_processor import (
    TESTS_DIR,
    TOOLS_DIR,
    _slug,
    group_steps,
    name_steps,
    process_json,
    resolve_tool_names,
    save_test_script,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Flask(__name__)
JSON_RPA_DIR = Path("json_rpa")
JSON_RPA_DIR.mkdir(exist_ok=True)

# Startup: garante que banco e disco estão sincronizados.
# Roda tanto via `python app.py` quanto via gunicorn/uwsgi no container.
init_db()
restore_from_db(TOOLS_DIR, TESTS_DIR)

# Processos ativos de execução — keyed por run_id (suporta execuções paralelas)
# Estrutura: {run_id: {"proc": Popen, "paused": bool}}
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()

# Revisão de tools: canal de comunicação entre o SSE de geração e a resposta do usuário
_review_event = threading.Event()
_review_response: dict | None = None  # {"action": "approve"|"reject"|"revise", "feedback": str}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_tools() -> list[dict]:
    """Retorna todas as tools disponíveis em tools/ com seu código."""
    tools = []
    for p in sorted(TOOLS_DIR.glob("*.py")):
        if p.stem == "__init__":
            continue
        tools.append({"name": p.stem, "code": p.read_text(encoding="utf-8")})
    return tools


def _all_workflows() -> list[dict]:
    """Retorna todos os scripts de teste gerados em tests/."""
    workflows = []
    for p in sorted(TESTS_DIR.glob("*.py")):
        if p.stem == "__init__" or p.stem.startswith("rpa_run_"):
            continue
        code = p.read_text(encoding="utf-8")
        steps = []
        for line in code.splitlines():
            if line.startswith("        results["):
                name = line.strip().split("'")[1]
                steps.append(name)
        workflows.append({"name": p.stem, "steps": steps, "file": p.name})
    return workflows


def _preview_script(workflow_name: str, steps: list[str], headless: bool = True) -> str:
    """Gera preview do script de teste para uma sequência de steps."""
    import_lines = "\n".join(f"from tools.{s} import {s}" for s in steps)
    # Cada step é executado em try/except independente:
    # - retorno True  → PASS
    # - retorno False → FAIL
    # - retorno None  → PASS (tool sem retorno explícito = não lançou exceção)
    # - exceção       → FAIL com mensagem de erro
    result_lines = "\n".join(
        f"        print('__RUNNING__{s}', flush=True)\n"
        f"        sys.stdout.flush()\n"
        f"        try:\n"
        f"            _r = await {s}(page)\n"
        f"            results[{s!r}] = (_r is not False)\n"
        f"        except Exception as _e:\n"
        f"            results[{s!r}] = False\n"
        f"            errors[{s!r}] = str(_e)\n"
        f"        if not results.get({s!r}, True):\n"
        f"            print('false')\n"
        f"            sys.exit(1)"
        for s in steps
    )
    if headless:
        browser_setup = "        browser = await p.chromium.launch(headless=True)\n        page = await browser.new_page()"
    else:
        browser_setup = "        browser = await p.chromium.connect_over_cdp('http://localhost:9222')\n        page = browser.contexts[0].pages[0]"

    return f"""\
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

{import_lines}


async def run():
    async with async_playwright() as p:
{browser_setup}

        results: dict[str, bool] = {{}}
        errors: dict[str, str] = {{}}

{result_lines}

        failed = [k for k, v in results.items() if not v]
        print("true" if not failed else "false")
        sys.exit(0 if not failed else 1)


if __name__ == '__main__':
    asyncio.run(run())
"""


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
    """Executa um workflow pelo seu ID do banco via SSE."""
    row = db_get_workflow_by_id(workflow_id)
    if not row:
        return jsonify({"error": f"Workflow ID {workflow_id} não encontrado"}), 404

    steps = row["steps"] if isinstance(row["steps"], list) else []
    if not steps:
        return jsonify({"error": "Workflow não tem steps"}), 400

    data = request.json or {}
    return _run_steps(
        workflow_name=row["name"],
        steps=steps,
        headless=data.get("headless", False),
        start_from=data.get("start_from") or None,
        stop_before=data.get("stop_before") or None,
    )


@app.route("/api/json-files")
def api_json_files():
    """Retorna todos os arquivos JSON disponíveis na pasta json_rpa/."""
    files = []
    for p in sorted(JSON_RPA_DIR.glob("*.json")):
        files.append({"name": p.name, "stem": p.stem})
    return jsonify(files)


@app.route("/api/load-json", methods=["POST"])
def api_load_json():
    """Carrega e processa um JSON existente da pasta json_rpa/."""
    filename = request.json.get("filename", "")
    dest = JSON_RPA_DIR / filename
    if not dest.exists() or not filename.endswith(".json"):
        return jsonify({"error": "Arquivo não encontrado"}), 404

    data = json.loads(dest.read_text(encoding="utf-8"))
    workflow_name = data.get("name", dest.stem)
    raw_steps = data.get("steps", [])
    grouped = group_steps(raw_steps)
    named = name_steps(grouped)
    existing = {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
    steps = resolve_tool_names(named, TOOLS_DIR, existing)

    return jsonify({
        "workflow_name": workflow_name,
        "workflow_slug": _slug(workflow_name),
        "steps": steps,
        "named_steps": [{"tool_name": s["tool_name"], "actions": s["actions"]} for s in named],
        "tools": _all_tools(),
    })


@app.route("/api/tool/<name>")
def api_tool_code(name: str):
    p = TOOLS_DIR / f"{name}.py"
    if not p.exists():
        return jsonify({"error": "Tool não encontrada"}), 404
    return jsonify({"name": name, "code": p.read_text(encoding="utf-8")})


@app.route("/api/tool/new", methods=["POST"])
def api_tool_new():
    """Cria uma nova tool do zero."""
    name = request.json.get("name", "").strip()
    code = request.json.get("code", "")
    if not name:
        return jsonify({"error": "Nome inválido"}), 400
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    p = TOOLS_DIR / f"{name}.py"
    if p.exists():
        return jsonify({"error": f"Já existe uma tool chamada '{name}'"}), 409
    p.write_text(code, encoding="utf-8")
    db_upsert_tool(name, code)
    return jsonify({"ok": True, "name": name})


@app.route("/api/tool/<name>", methods=["POST"])
def api_tool_save(name: str):
    """Salva código editado de uma tool."""
    p = TOOLS_DIR / f"{name}.py"
    code = request.json.get("code", "")
    p.write_text(code, encoding="utf-8")
    db_upsert_tool(name, code)
    return jsonify({"ok": True})


@app.route("/api/tool/<name>", methods=["DELETE"])
def api_tool_delete(name: str):
    """Deleta uma tool da pasta tools/."""
    p = TOOLS_DIR / f"{name}.py"
    if not p.exists():
        return jsonify({"error": "Tool não encontrada"}), 404
    db_delete_tool(name)
    p.unlink()
    return jsonify({"ok": True})


@app.route("/api/tool/<name>/rename", methods=["POST"])
def api_tool_rename(name: str):
    """Renomeia uma tool (arquivo + nome da função dentro do código)."""
    new_name = request.json.get("new_name", "").strip()
    if not new_name:
        return jsonify({"error": "Novo nome inválido"}), 400
    new_name = re.sub(r"[^a-zA-Z0-9_]", "_", new_name)

    old_path = TOOLS_DIR / f"{name}.py"
    new_path = TOOLS_DIR / f"{new_name}.py"
    if not old_path.exists():
        return jsonify({"error": "Tool não encontrada"}), 404
    if new_path.exists():
        return jsonify({"error": f"Já existe uma tool chamada '{new_name}'"}), 409

    code = old_path.read_text(encoding="utf-8")
    # Substitui o nome da função principal no código
    code = re.sub(rf"\basync def {re.escape(name)}\b", f"async def {new_name}", code)
    code = re.sub(rf"\bdef {re.escape(name)}\b", f"def {new_name}", code)

    new_path.write_text(code, encoding="utf-8")
    old_path.unlink()
    db_rename_tool(name, new_name, code)
    return jsonify({"ok": True, "new_name": new_name})


@app.route("/api/tools/check", methods=["POST"])
def api_tools_check():
    """Verifica quais tools de uma lista existem em tools/."""
    steps = request.json.get("steps", [])
    existing = {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
    return jsonify({
        "existing": [s for s in steps if s in existing],
        "pending": [s for s in steps if s not in existing],
    })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Recebe upload de JSON, salva em json_rpa/ e processa."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".json"):
        return jsonify({"error": "Apenas arquivos .json são aceitos"}), 400

    dest = JSON_RPA_DIR / f.filename
    f.save(dest)

    data = json.loads(dest.read_text(encoding="utf-8"))
    workflow_name = data.get("name", dest.stem)
    raw_steps = data.get("steps", [])
    grouped = group_steps(raw_steps)
    named = name_steps(grouped)
    existing = {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
    steps = resolve_tool_names(named, TOOLS_DIR, existing)

    return jsonify({
        "workflow_name": workflow_name,
        "workflow_slug": _slug(workflow_name),
        "steps": steps,
        "named_steps": [{"tool_name": s["tool_name"], "actions": s["actions"]} for s in named],
        "tools": _all_tools(),
    })


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Gera preview do script para uma sequência de steps."""
    data = request.json
    workflow_name = data.get("workflow_name", "workflow")
    steps = data.get("steps", [])
    return jsonify({"script": _preview_script(workflow_name, steps)})


@app.route("/api/save-workflow", methods=["POST"])
def api_save_workflow():
    """Salva o workflow (sequência de steps) como script de teste."""
    data = request.json
    workflow_name = data.get("workflow_name", "workflow")
    script_name = data.get("script_name", "").strip()
    steps = data.get("steps", [])
    named = [{"tool_name": s} for s in steps]
    if script_name:
        script_name = re.sub(r"[^a-zA-Z0-9_]", "_", script_name)
        tmp_path = save_test_script(workflow_name, named, TESTS_DIR)
        if tmp_path.stem != script_name:
            path = TESTS_DIR / f"{script_name}.py"
            path.write_text(tmp_path.read_text(encoding="utf-8"), encoding="utf-8")
            tmp_path.unlink()
        else:
            path = tmp_path
    else:
        path = save_test_script(workflow_name, named, TESTS_DIR)
    db_upsert_workflow(path.stem, path.read_text(encoding="utf-8"), steps)
    return jsonify({"ok": True, "file": path.name})


def _run_steps(workflow_name: str, steps: list, headless: bool = True,
               start_from: str | None = None, stop_before: str | None = None):
    """Lógica central de execução: gera script temporário e faz stream SSE."""
    import uuid

    if not steps:
        return jsonify({"error": "Nenhum step para executar"}), 400

    if start_from and start_from in steps:
        steps = steps[steps.index(start_from):]
        if not steps:
            return jsonify({"error": "start_from aponta para o último step"}), 400

    if stop_before and stop_before in steps:
        steps = steps[:steps.index(stop_before)]
        if not steps:
            return jsonify({"error": "Breakpoint está no primeiro step — nenhum step para executar"}), 400

    run_id = str(uuid.uuid4())[:8]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="rpa_run_", dir=TESTS_DIR, delete=False, encoding="utf-8"
    ) as tf:
        tf.write(_preview_script(workflow_name, steps, headless=headless))
        tmp_path = Path(tf.name)

    python_bin = Path(sys.executable)

    def generate():
        yield f"data: __RUN_ID__{run_id}\n\n"
        if start_from:
            yield f"data: __START_FROM__{start_from}\n\n"
        if stop_before:
            yield f"data: __BREAKPOINT__{stop_before}\n\n"
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = None
        try:
            proc = subprocess.Popen(
                [str(python_bin), "-u", str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(Path(__file__).parent),
                env=env,
            )
            with _runs_lock:
                _runs[run_id] = {"proc": proc, "paused": False}
            db_inserir_execucao(run_id, workflow_name, steps)
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            yield f"data: __EXIT__{proc.returncode}\n\n"
        except Exception as e:
            yield f"data: [ERRO] {e}\n\n"
            yield "data: __EXIT__1\n\n"
        finally:
            exit_code = proc.returncode if proc else 1
            db_finalizar_execucao(run_id, exit_code)
            with _runs_lock:
                _runs.pop(run_id, None)
            try:
                tmp_path.unlink()
            except Exception:
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/run", methods=["POST"])
def api_run():
    """Executa steps fornecidos diretamente via SSE."""
    data = request.json
    return _run_steps(
        workflow_name=data.get("workflow_name", "workflow"),
        steps=data.get("steps", []),
        headless=data.get("headless", True),
        start_from=data.get("start_from") or None,
        stop_before=data.get("stop_before") or None,
    )


@app.route("/api/run/cancel", methods=["POST"])
def api_run_cancel():
    """Cancela uma execução. run_id opcional — sem ele cancela todas."""
    import signal
    run_id = (request.json or {}).get("run_id")
    with _runs_lock:
        targets = {run_id: _runs[run_id]} if run_id and run_id in _runs else dict(_runs)
    if not targets:
        return jsonify({"ok": False, "message": "Nenhuma execução em andamento"})
    for rid, run in targets.items():
        proc = run["proc"]
        if proc.poll() is None:
            if run.get("paused"):
                try:
                    proc.send_signal(signal.SIGCONT)
                except Exception:
                    pass
            proc.terminate()
    return jsonify({"ok": True, "cancelled": list(targets.keys())})


@app.route("/api/run/pause", methods=["POST"])
def api_run_pause():
    """Pausa uma execução pelo run_id."""
    import signal
    run_id = (request.json or {}).get("run_id")
    with _runs_lock:
        run = _runs.get(run_id) if run_id else (next(iter(_runs.values()), None))
    if run and run["proc"].poll() is None and not run.get("paused"):
        run["proc"].send_signal(signal.SIGSTOP)
        run["paused"] = True
        return jsonify({"ok": True, "paused": True})
    return jsonify({"ok": False, "paused": run.get("paused") if run else False})


@app.route("/api/run/resume", methods=["POST"])
def api_run_resume():
    """Retoma uma execução pausada pelo run_id."""
    import signal
    run_id = (request.json or {}).get("run_id")
    with _runs_lock:
        run = _runs.get(run_id) if run_id else (next(iter(_runs.values()), None))
    if run and run["proc"].poll() is None and run.get("paused"):
        run["proc"].send_signal(signal.SIGCONT)
        run["paused"] = False
        return jsonify({"ok": True, "paused": False})
    return jsonify({"ok": False, "paused": run.get("paused") if run else False})


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
    Para cada tool nova, envia __REVIEW__<name> + código e aguarda resposta do usuário.
    O usuário responde via /api/review-response com approve/reject/revise.
    """
    global _review_event, _review_response

    from json_processor import _actions_fingerprint, _read_tool_fingerprint, resolve_tool_names
    from tool_generator import _build_prompt, _call_llm, _save_tool

    data = request.json
    filename = data.get("filename", "")
    named_steps = data.get("named_steps")  # [{tool_name, actions}]

    # Suporte a passar filename OU named_steps diretamente
    if filename:
        dest = JSON_RPA_DIR / filename
        if not dest.exists():
            return jsonify({"error": "Arquivo não encontrado"}), 404
        raw = json.loads(dest.read_text(encoding="utf-8"))
        from json_processor import group_steps, name_steps
        grouped = group_steps(raw.get("steps", []))
        named_steps = name_steps(grouped)

    if not named_steps:
        return jsonify({"error": "Nenhum step para processar"}), 400

    existing = {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
    resolve_tool_names(named_steps, TOOLS_DIR, existing)  # muta tool_name com _v2 etc.

    def generate():
        global _review_event, _review_response

        for step in named_steps:
            name = step["tool_name"]
            actions = step["actions"]
            new_fp = _actions_fingerprint(actions)

            # Verifica se já existe com fingerprint idêntico
            tool_path = TOOLS_DIR / f"{name}.py"
            if tool_path.exists():
                existing_fp = _read_tool_fingerprint(tool_path)
                if existing_fp == new_fp:
                    yield f"data: __SKIP__{name}\n\n"
                    continue

            # Gera código via LLM
            yield f"data: __GENERATING__{name}\n\n"
            prompt = _build_prompt(name, actions)
            code = _call_llm(prompt)

            # Entra no loop de revisão
            feedback_history = []
            while True:
                # Manda código para o frontend revisar
                # Formato: __REVIEW__<name>\n<code completo>
                payload = json.dumps({"name": name, "code": code})
                yield f"data: __REVIEW__{payload}\n\n"

                # Aguarda resposta do usuário (timeout de 10 min)
                _review_event.clear()
                _review_response = None
                got = _review_event.wait(timeout=600)

                if not got or _review_response is None:
                    yield f"data: __REVIEW_TIMEOUT__{name}\n\n"
                    break

                action = _review_response.get("action")
                feedback = _review_response.get("feedback", "").strip()

                if action == "approve":
                    _save_tool(name, code, fingerprint=new_fp)
                    yield f"data: __APPROVED__{name}\n\n"
                    break
                elif action == "reject":
                    tool_path = TOOLS_DIR / f"{name}.py"
                    if tool_path.exists():
                        db_delete_tool(name)
                        tool_path.unlink()
                    yield f"data: __REJECTED__{name}\n\n"
                    yield "data: __GENERATE_DONE__\n\n"
                    return
                elif action == "revise":
                    yield f"data: __REVISING__{name}\n\n"
                    feedback_history.append(feedback)
                    # Regera com feedback acumulado
                    revision_note = "\n".join(
                        f"- {fb}" for fb in feedback_history
                    )
                    revised_prompt = (
                        prompt
                        + f"\n\nO usuário pediu as seguintes revisões (aplique TODAS):\n{revision_note}"
                    )
                    code = _call_llm(revised_prompt)
                    # Volta ao topo do while para nova revisão

        yield "data: __GENERATE_DONE__\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/review-response", methods=["POST"])
def api_review_response():
    """Recebe a resposta do usuário para uma tool em revisão."""
    global _review_event, _review_response
    _review_response = request.json  # {action, feedback}
    _review_event.set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
