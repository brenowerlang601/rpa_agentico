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


def _screen_key(url: str) -> str:
    """
    Chave única de tela: path da URL (sem query string) + fragment.
    Ignora query string porque em SPAs como NXJ o managerId muda mas a tela é a mesma.
    O fragment (#0x, #1, etc.) identifica a tela dentro do SPA.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    # path sem query + fragment (sem query string interna ao fragment)
    frag = parsed.fragment.split("?")[0] if parsed.fragment else ""
    return parsed.scheme + "://" + parsed.netloc + parsed.path + "#" + frag


def _normalize_actions(raw_steps: list[dict]) -> list[dict]:
    """
    Primeira passagem: normaliza ações brutas.
    - Mescla click+input no mesmo xpath em um único 'fill'
    - Remove scroll e key_press isolados
    - Mantém navigation, click (sem input subsequente) e fill
    """
    actions = []
    i = 0
    while i < len(raw_steps):
        action = raw_steps[i]
        atype = action.get("type")

        if atype in ("scroll", "key_press"):
            i += 1
            continue

        if atype == "navigation":
            actions.append({"type": "navigation", "url": action.get("url", "")})
            i += 1
            continue

        if atype == "click":
            xpath = action.get("xpath", "")
            target = action.get("targetText", "").strip()
            # Pula scroll/key_press para ver se vem input no mesmo xpath
            j = i + 1
            while j < len(raw_steps) and raw_steps[j].get("type") in ("scroll", "key_press"):
                j += 1
            if j < len(raw_steps) and raw_steps[j].get("type") == "input" and raw_steps[j].get("xpath") == xpath:
                inp = raw_steps[j]
                actions.append({
                    "type": "fill",
                    "xpath": xpath,
                    "css_selector": inp.get("cssSelector") or action.get("cssSelector", ""),
                    "target_text": target or inp.get("targetText", "").strip(),
                    "value": inp.get("value", ""),
                    "url": action.get("url", ""),
                })
                i = j + 1
            else:
                actions.append({
                    "type": "click",
                    "xpath": xpath,
                    "css_selector": action.get("cssSelector", ""),
                    "target_text": target,
                    "url": action.get("url", ""),
                })
                i += 1
            continue

        if atype == "input":
            actions.append({
                "type": "fill",
                "xpath": action.get("xpath", ""),
                "css_selector": action.get("cssSelector", ""),
                "target_text": action.get("targetText", "").strip(),
                "value": action.get("value", ""),
                "url": action.get("url", ""),
            })
            i += 1
            continue

        i += 1

    return actions


def group_into_micro_processes(actions: list[dict]) -> list[dict]:
    """
    Segunda passagem: agrupa ações normalizadas em micro-processos.

    Critério de quebra de grupo:
    - Mudança de URL base (nova página) → novo grupo
    - Após um clique que submete/confirma (botão sem input subsequente na mesma URL)
      seguido de ações em URL diferente → quebra

    Cada grupo vira uma tool com todas as suas ações internas.
    """
    if not actions:
        return []

    groups: list[dict] = []
    current_actions: list[dict] = []
    current_url = _screen_key(actions[0].get("url", ""))

    for action in actions:
        action_url = _screen_key(action.get("url", ""))

        # Mudança de página: fecha grupo atual, abre novo
        if action_url != current_url and current_actions:
            groups.append({"url": current_url, "actions": current_actions})
            current_actions = []
            current_url = action_url

        current_actions.append(action)

    if current_actions:
        groups.append({"url": current_url, "actions": current_actions})

    return groups


def name_groups(groups: list[dict]) -> list[dict]:
    """
    Atribui tool_name semântico a cada grupo (micro-processo).

    Heurística:
    - Grupo com fills de usuário/senha/login → "fazer_login_<pagina>"
    - Grupo com vários fills → "preencher_formulario_<pagina>"
    - Grupo com navigation + fills → inclui contexto da página
    - Grupo só com clicks → "executar_acoes_<pagina>"
    - Grupos com nome de página identificável usam o slug da página
    """
    from urllib.parse import urlparse

    named = []
    name_counts: dict[str, int] = {}

    LOGIN_KEYWORDS = {"usuario", "senha", "login", "password", "user", "email", "pass"}

    for group in groups:
        url = group["url"]
        actions = group["actions"]

        # Nome base da página a partir da URL (path + fragment para SPAs NXJ)
        parsed = urlparse(url)
        path = parsed.path
        fragment = parsed.fragment  # ex: "0x", "1", "pedi_f130"
        path_slug = _slug(path.split("/")[-1].split(".")[0])
        # Inclui o fragment no slug apenas quando ele tem valor semântico (não é numero/0x)
        frag_slug = _slug(fragment) if fragment else ""
        if frag_slug and not re.fullmatch(r"[0-9]+x?", fragment):
            page_slug = f"{path_slug}_{frag_slug}" if path_slug else frag_slug
        else:
            page_slug = path_slug or "pagina"

        # Classifica o grupo pelo conteúdo das ações
        fills = [a for a in actions if a["type"] == "fill"]
        clicks = [a for a in actions if a["type"] == "click"]
        navigations = [a for a in actions if a["type"] == "navigation"]

        fill_targets = {_slug(f.get("target_text", "")) for f in fills}
        is_login = bool(fill_targets & LOGIN_KEYWORDS) or any(
            kw in page_slug for kw in ("login", "senha", "auth", "acesso")
        )

        if is_login:
            base = f"fazer_login_{page_slug}"
        elif fills and not clicks:
            base = f"preencher_{page_slug}"
        elif fills and clicks:
            base = f"preencher_e_confirmar_{page_slug}"
        elif clicks and not fills and not navigations:
            label = _slug(clicks[-1].get("target_text", "")) or "acao"
            base = f"clicar_{label}_{page_slug}"
        elif navigations and not fills and not clicks:
            base = f"navegar_{page_slug}"
        else:
            base = f"executar_{page_slug}"

        count = name_counts.get(base, 0) + 1
        name_counts[base] = count
        tool_name = base if count == 1 else f"{base}_{count}"

        named.append({
            "tool_name": tool_name,
            "url": url,
            "actions": actions,
        })

    return named


# Mantém compatibilidade com o restante do código
def group_steps(raw_steps: list[dict]) -> list[dict]:
    actions = _normalize_actions(raw_steps)
    return group_into_micro_processes(actions)


def name_steps(grouped: list[dict]) -> list[dict]:
    return name_groups(grouped)


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


def _action_block(action: dict, idx: int) -> list[str]:
    """
    Gera um bloco try/except para uma ação individual.
    Em caso de erro, registra na lista `errors` e não interrompe o fluxo.
    Retorna lista de linhas indentadas com 4 espaços (corpo da função).
    """
    atype = action["type"]
    label = f"acao_{idx + 1}_{atype}"
    lines = []

    if atype == "navigation":
        url = action["url"]
        body = [
            f'        await page.goto({url!r})',
            "        await page.wait_for_load_state('networkidle')",
        ]

    elif atype == "fill":
        locator = _locator_expr(action)
        value = action.get("value", "")
        target = action.get("target_text", "") or action.get("xpath", "")
        label = f"acao_{idx + 1}_fill_{_slug(target)[:30]}" if target else label
        body = [
            f'        await {locator}.click()',
            f'        await {locator}.fill({value!r})',
        ]

    elif atype == "click":
        locator = _locator_expr(action)
        target = action.get("target_text", "") or action.get("xpath", "")
        label = f"acao_{idx + 1}_click_{_slug(target)[:30]}" if target else label
        body = [
            f'        await {locator}.click()',
            "        await page.wait_for_timeout(500)",
        ]

    else:
        return []

    lines += [
        f'    try:',
    ] + body + [
        f'    except Exception as e:',
        f'        errors.append(({label!r}, str(e)))',
    ]
    return lines


def generate_tool_function(group: dict) -> str:
    """
    Gera uma função async Playwright para um micro-processo.
    Cada ação é envolta em try/except individual.
    Retorna True se nenhum erro ocorreu, False caso contrário.
    """
    name = group["tool_name"]
    actions = group["actions"]

    lines = [
        f"async def {name}(page) -> bool:",
        f'    """Executa o passo \'{name}\'. Retorna True se todas as ações tiveram sucesso."""',
        "    errors: list[tuple[str, str]] = []",
        "",
    ]

    for idx, action in enumerate(actions):
        block = _action_block(action, idx)
        if block:
            lines.extend(block)
            lines.append("")

    lines += [
        "    if errors:",
        f'        print(f"[FAIL] {name}: {{len(errors)}} erro(s)")',
        "        for label, msg in errors:",
        '            print(f"  - {label}: {msg}")',
        "        return False",
        f'    print(f"[OK] {name}")',
        "    return True",
        "",
    ]
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

    # corpo do teste: chama cada função e coleta resultado True/False
    result_lines = "\n".join(
        f"        results[{fn!r}] = await {fn}(page)" for fn in func_names
    )

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

    print(f"  Micro-processos identificados: {len(named)}")
    for s in named:
        n_actions = len(s["actions"])
        print(f"    - {s['tool_name']}  ({n_actions} acoes)")

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
