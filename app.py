"""
Interface web para gerenciar workflows RPA.
- Upload de JSON → gera tools automaticamente
- Canvas visual drag-and-drop de tools
- Editor de código inline por tool
- Preview do script gerado em tempo real
"""

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from flask import Flask, Response, jsonify, render_template, request

from json_processor import (
    TESTS_DIR,
    TOOLS_DIR,
    _slug,
    group_steps,
    name_steps,
    process_json,
    save_test_script,
)

app = Flask(__name__)
JSON_RPA_DIR = Path("json_rpa")
JSON_RPA_DIR.mkdir(exist_ok=True)


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
        if p.stem == "__init__":
            continue
        # Extrai a sequência de tools do script parseando os imports
        code = p.read_text(encoding="utf-8")
        steps = []
        for line in code.splitlines():
            if line.startswith("        results["):
                # results['tool_name'] = await tool_name(page)
                name = line.strip().split("'")[1]
                steps.append(name)
        workflows.append({"name": p.stem, "steps": steps, "file": p.name})
    return workflows


def _preview_script(workflow_name: str, steps: list[str]) -> str:
    """Gera preview do script de teste para uma sequência de steps."""
    import_lines = "\n".join(f"from tools.{s} import {s}" for s in steps)
    result_lines = "\n".join(f"        results[{s!r}] = await {s}(page)" for s in steps)
    return f"""\
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

{import_lines}


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = browser.contexts[0].pages[0]

        results: dict[str, bool] = {{}}

{result_lines}

        print()
        print("=" * 50)
        print(f"Relatório QA — {workflow_name}")
        print("=" * 50)
        passed = [k for k, v in results.items() if v]
        failed = [k for k, v in results.items() if not v]
        for step in passed:
            print(f"  [PASS] {{step}}")
        for step in failed:
            print(f"  [FAIL] {{step}}")
        print("-" * 50)
        print(f"  Total: {{len(results)}} | Passed: {{len(passed)}} | Failed: {{len(failed)}}")
        print("=" * 50)
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
    return jsonify(_all_workflows())


@app.route("/api/tool/<name>")
def api_tool_code(name: str):
    p = TOOLS_DIR / f"{name}.py"
    if not p.exists():
        return jsonify({"error": "Tool não encontrada"}), 404
    return jsonify({"name": name, "code": p.read_text(encoding="utf-8")})


@app.route("/api/tool/<name>", methods=["POST"])
def api_tool_save(name: str):
    """Salva código editado de uma tool."""
    p = TOOLS_DIR / f"{name}.py"
    code = request.json.get("code", "")
    p.write_text(code, encoding="utf-8")
    return jsonify({"ok": True})


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

    # Calcula os steps imediatamente (sem LLM)
    data = json.loads(dest.read_text(encoding="utf-8"))
    workflow_name = data.get("name", dest.stem)
    raw_steps = data.get("steps", [])
    grouped = group_steps(raw_steps)
    named = name_steps(grouped)
    steps = [s["tool_name"] for s in named]

    # Gera as tools em background para não bloquear o browser
    def _generate_tools():
        try:
            process_json(dest)
        except Exception as e:
            print(f"[upload] Erro ao gerar tools: {e}")

    t = threading.Thread(target=_generate_tools, daemon=True)
    t.start()

    return jsonify({
        "workflow_name": workflow_name,
        "workflow_slug": _slug(workflow_name),
        "steps": steps,
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
    steps = data.get("steps", [])
    named = [{"tool_name": s} for s in steps]
    path = save_test_script(workflow_name, named, TESTS_DIR)
    return jsonify({"ok": True, "file": path.name})


@app.route("/api/run", methods=["POST"])
def api_run():
    """
    Executa o script gerado para os steps fornecidos via SSE (Server-Sent Events).
    Salva um script temporário, executa com o Python do venv e faz stream do output linha a linha.
    """
    data = request.json
    workflow_name = data.get("workflow_name", "workflow")
    steps = data.get("steps", [])

    if not steps:
        return jsonify({"error": "Nenhum step para executar"}), 400

    # Gera o script em arquivo temporário para execução
    named = [{"tool_name": s} for s in steps]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="rpa_run_", dir=TESTS_DIR, delete=False, encoding="utf-8"
    ) as tf:
        tf.write(_preview_script(workflow_name, steps))
        tmp_path = Path(tf.name)

    python_bin = Path(sys.executable)

    def generate():
        try:
            proc = subprocess.Popen(
                [str(python_bin), str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(Path(__file__).parent),
            )
            for line in proc.stdout:
                # SSE: cada linha vira um evento
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            code = proc.returncode
            yield f"data: __EXIT__{code}\n\n"
        except Exception as e:
            yield f"data: [ERRO] {e}\n\n"
            yield "data: __EXIT__1\n\n"
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
