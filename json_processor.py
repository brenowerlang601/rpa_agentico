"""
Processa um JSON de fluxo RPA (json_rpa/) e gera:
1. Tools por step em tools/ (funções Playwright reutilizáveis)
2. Script de teste Playwright completo (tests/<nome>.py)
"""

import json
import re
from pathlib import Path

TOOLS_DIR = Path("tools")
TESTS_DIR = Path("tests")
TOOLS_DIR.mkdir(exist_ok=True)
TESTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Agrupamento de steps a partir das ações brutas do JSON
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Converte texto para snake_case sem acentos."""
    text = text.lower()
    text = re.sub(r"[áàãâä]", "a", text)
    text = re.sub(r"[éèêë]", "e", text)
    text = re.sub(r"[íìîï]", "i", text)
    text = re.sub(r"[óòõôö]", "o", text)
    text = re.sub(r"[úùûü]", "u", text)
    text = re.sub(r"[ç]", "c", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def group_steps(raw_steps: list[dict]) -> list[dict]:
    """
    Agrupa ações brutas em steps semânticos.
    Regras:
    - navigation  → step próprio
    - click + input (mesmo xpath) → step de preenchimento (usa só o input)
    - click sem input subsequente → step de clique
    - scroll → ignorado (não gera step, é comportamento implícito)
    - key_press → ignorado (capturado junto com input quando relevante)
    """
    steps = []
    i = 0
    while i < len(raw_steps):
        action = raw_steps[i]
        atype = action.get("type")

        if atype == "navigation":
            steps.append({
                "type": "navigation",
                "url": action.get("url", ""),
            })
            i += 1

        elif atype == "scroll":
            # ignorado como step independente
            i += 1

        elif atype == "key_press":
            # ignorado como step independente
            i += 1

        elif atype == "click":
            xpath = action.get("xpath", "")
            target = action.get("targetText", "").strip()
            # Verifica se o próximo não-scroll/não-keypress é um input no mesmo xpath
            j = i + 1
            while j < len(raw_steps) and raw_steps[j].get("type") in ("scroll", "key_press"):
                j += 1

            if j < len(raw_steps) and raw_steps[j].get("type") == "input" and raw_steps[j].get("xpath") == xpath:
                input_action = raw_steps[j]
                steps.append({
                    "type": "fill",
                    "xpath": xpath,
                    "css_selector": input_action.get("cssSelector") or action.get("cssSelector", ""),
                    "target_text": target or input_action.get("targetText", "").strip(),
                    "value": input_action.get("value", ""),
                    "url": action.get("url", ""),
                })
                i = j + 1
            else:
                steps.append({
                    "type": "click",
                    "xpath": xpath,
                    "css_selector": action.get("cssSelector", ""),
                    "target_text": target,
                    "url": action.get("url", ""),
                })
                i += 1

        elif atype == "input":
            # input sem click anterior (raro) — trata direto
            steps.append({
                "type": "fill",
                "xpath": action.get("xpath", ""),
                "css_selector": action.get("cssSelector", ""),
                "target_text": action.get("targetText", "").strip(),
                "value": action.get("value", ""),
                "url": action.get("url", ""),
            })
            i += 1

        else:
            i += 1

    return steps


def name_steps(grouped: list[dict]) -> list[dict]:
    """Atribui tool_name a cada step semântico."""
    named = []
    field_counts: dict[str, int] = {}

    for step in grouped:
        stype = step["type"]

        if stype == "navigation":
            from urllib.parse import urlparse
            path = urlparse(step["url"]).path
            slug = _slug(path.split("/")[-1].split(".")[0]) or "pagina"
            name = f"navegar_{slug}"

        elif stype == "fill":
            target = step.get("target_text", "")
            slug = _slug(target) if target else "campo"
            base = f"preencher_{slug}"
            count = field_counts.get(base, 0) + 1
            field_counts[base] = count
            name = base if count == 1 else f"{base}_{count}"

        elif stype == "click":
            target = step.get("target_text", "")
            slug = _slug(target) if target else "elemento"
            name = f"clicar_{slug}"

        else:
            name = f"acao_{stype}"

        named.append({**step, "tool_name": name})

    return named


# ---------------------------------------------------------------------------
# Geração de código Playwright
# ---------------------------------------------------------------------------

def _xpath_absolute(xpath: str) -> str:
    """Garante que o XPath começa com // para uso com page.locator."""
    if xpath and not xpath.startswith("//") and not xpath.startswith("id("):
        return "//" + xpath
    return xpath


def _locator_expr(step: dict) -> str:
    """Retorna a expressão de locator mais confiável para o step."""
    css = step.get("css_selector", "")
    xpath = step.get("xpath", "")
    if css:
        return f'page.locator({css!r})'
    elif xpath:
        abs_xpath = _xpath_absolute(xpath)
        # usa repr para escapar aspas internas corretamente
        locator_str = f"xpath={abs_xpath}"
        return f'page.locator({locator_str!r})'
    return 'page.locator("body")'


def generate_tool_function(step: dict) -> str:
    """Gera uma função async Playwright para um único step."""
    name = step["tool_name"]
    stype = step["type"]

    lines = [
        f"async def {name}(page):",
    ]

    if stype == "navigation":
        url = step["url"]
        lines += [
            f'    await page.goto({url!r})',
            "    await page.wait_for_load_state('networkidle')",
        ]

    elif stype == "fill":
        locator = _locator_expr(step)
        value = step.get("value", "")
        lines += [
            f'    await {locator}.click()',
            f'    await {locator}.fill({value!r})',
        ]

    elif stype == "click":
        locator = _locator_expr(step)
        lines += [
            f'    await {locator}.click()',
        ]

    lines.append("    await page.wait_for_timeout(500)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Saída: tools individuais
# ---------------------------------------------------------------------------

def save_tools(named_steps: list[dict], tools_dir: Path, existing_tools: set[str]):
    """Salva cada step como tool Python em tools/, sem sobrescrever existentes."""
    saved = []
    for step in named_steps:
        name = step["tool_name"]
        if name in existing_tools:
            print(f"  [skip] tool já existe: {name}.py")
            saved.append(name)
            continue

        func_code = generate_tool_function(step)
        header = (
            "from playwright.async_api import Page\n\n"
        )
        path = tools_dir / f"{name}.py"
        path.write_text(header + func_code, encoding="utf-8")
        print(f"  [tool] gerado: {path}")
        saved.append(name)
    return saved


# ---------------------------------------------------------------------------
# Saída: script de teste Playwright completo
# ---------------------------------------------------------------------------

def save_test_script(workflow_name: str, named_steps: list[dict], tests_dir: Path):
    """Gera script de teste Playwright completo que executa o fluxo inteiro."""
    func_names = [s["tool_name"] for s in named_steps]

    # imports das tools
    import_lines = "\n".join(
        f"from tools.{fn} import {fn}" for fn in func_names
    )

    # corpo do teste: chama cada função em sequência (8 espaços = dentro do async with)
    indented_calls = "\n".join(f"        await {fn}(page)" for fn in func_names)

    script = f"""\
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

{indented_calls}

        print("Fluxo '{workflow_name}' concluido.")


if __name__ == '__main__':
    asyncio.run(run())
"""

    path = tests_dir / f"{_slug(workflow_name)}.py"
    path.write_text(script, encoding="utf-8")
    print(f"  [test] gerado: {path}")
    return path


# ---------------------------------------------------------------------------
# Entrypoint principal
# ---------------------------------------------------------------------------

def process_json(json_path: Path) -> None:
    """Processa um json_rpa e gera tools + script de teste."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    workflow_name = data.get("name", json_path.stem)
    raw_steps = data.get("steps", [])

    print(f"\n[json_processor] Processando: {json_path.name}")
    print(f"  Workflow: {workflow_name}")
    print(f"  Total de acoes brutas: {len(raw_steps)}")

    grouped = group_steps(raw_steps)
    named = name_steps(grouped)

    print(f"  Steps semanticos identificados: {len(named)}")
    for s in named:
        print(f"    - [{s['type']}] {s['tool_name']}")

    existing = {p.stem for p in TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
    save_tools(named, TOOLS_DIR, existing)
    save_test_script(workflow_name, named, TESTS_DIR)

    print(f"\n[json_processor] Concluido.")


def list_json_rpa(json_rpa_dir: Path) -> list[Path]:
    return sorted(json_rpa_dir.glob("*.json"))


if __name__ == "__main__":
    import sys

    json_rpa_dir = Path("json_rpa")
    files = list_json_rpa(json_rpa_dir)

    if not files:
        print("Nenhum arquivo JSON encontrado em json_rpa/")
        sys.exit(1)

    if len(files) == 1:
        process_json(files[0])
    else:
        print("JSONs disponíveis em json_rpa/:")
        for idx, f in enumerate(files):
            print(f"  {idx + 1}. {f.name}")
        choice = input("Escolha o número do arquivo: ").strip()
        try:
            selected = files[int(choice) - 1]
        except (ValueError, IndexError):
            print("Escolha inválida.")
            sys.exit(1)
        process_json(selected)
