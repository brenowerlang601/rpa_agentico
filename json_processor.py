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


def _normalize_key(raw_key: str) -> str:
    """
    Normaliza o nome da tecla do JSON para o formato aceito pelo Playwright.
    Ex: 'CmdOrCtrl+V' → 'Control+v', 'Return' → 'Enter'
    """
    key = raw_key.strip()
    # Atalhos compostos com CmdOrCtrl
    if key.startswith("CmdOrCtrl+"):
        letter = key.split("+", 1)[1]
        return f"Control+{letter.lower()}"
    # Aliases comuns
    aliases = {"Return": "Enter", "Esc": "Escape", "Del": "Delete", "Ins": "Insert"}
    return aliases.get(key, key)


def _normalize_actions(raw_steps: list[dict]) -> list[dict]:
    """
    Primeira passagem: normaliza ações brutas.
    - Mescla click+input no mesmo xpath em um único 'fill'
    - Agrupa key_press consecutivos da mesma tecla em um único 'key_press' com count
    - Remove scroll isolados
    - Mantém navigation, click (sem input subsequente), fill e key_press
    """
    actions = []
    i = 0
    while i < len(raw_steps):
        action = raw_steps[i]
        atype = action.get("type")

        if atype == "scroll":
            i += 1
            continue

        if atype == "key_press":
            key = _normalize_key(action.get("key", ""))
            url = action.get("url", "")
            xpath = action.get("xpath", "")
            css = action.get("cssSelector", "")
            # Agrupa repetições consecutivas da MESMA tecla NO MESMO ELEMENTO
            # Se o xpath/cssSelector mudou, é uma ação independente (navegação entre campos)
            count = 1
            j = i + 1
            while j < len(raw_steps):
                nxt = raw_steps[j]
                if nxt.get("type") != "key_press":
                    break
                if _normalize_key(nxt.get("key", "")) != key:
                    break
                nxt_xpath = nxt.get("xpath", "")
                nxt_css = nxt.get("cssSelector", "")
                # Só agrupa se for no mesmo elemento (mesmo xpath ou mesmo css)
                if (nxt_xpath and nxt_xpath != xpath) or (nxt_css and nxt_css != css):
                    break
                count += 1
                j += 1
            actions.append({"type": "key_press", "key": key, "count": count, "url": url, "xpath": xpath, "css_selector": css})
            i = j
            continue

        if atype == "navigation":
            actions.append({"type": "navigation", "url": action.get("url", "")})
            i += 1
            continue

        if atype == "click":
            xpath = action.get("xpath", "")
            target = action.get("targetText", "").strip()
            # Pula apenas scroll para ver se vem input no mesmo xpath
            # NÃO pula key_press — eles devem ser preservados como ações independentes
            j = i + 1
            while j < len(raw_steps) and raw_steps[j].get("type") == "scroll":
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
# Saída: tools individuais — delegada ao tool_generator (LLM)
# ---------------------------------------------------------------------------

def save_tools(named_steps: list[dict], tools_dir: Path, existing_tools: set[str]):
    """
    Gera tools para cada micro-processo via LLM (tool_generator).
    O LLM recebe a lista completa de ações e gera o código Python garantindo
    que nenhum passo seja omitido.
    Não sobrescreve tools já existentes.
    """
    from tool_generator import generate_tool_from_actions

    saved = []
    for step in named_steps:
        name = step["tool_name"]
        if name in existing_tools:
            print(f"  [skip] tool já existe: {name}.py")
            saved.append(name)
            continue

        generate_tool_from_actions(name, step["actions"])
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

    existing_tests = {p.stem for p in TESTS_DIR.glob("*.py")}

    print("\nArquivos disponíveis em json_rpa/:")
    print("-" * 40)
    for idx, f in enumerate(files, start=1):
        test_slug = _slug(json.loads(f.read_text(encoding="utf-8")).get("name", f.stem))
        status = " [já gerado]" if test_slug in existing_tests else ""
        print(f"  {idx}. {f.name}{status}")
    if len(files) > 1:
        print(f"  0. Processar todos")
    print("-" * 40)

    choice = input("Escolha: ").strip()

    if choice == "0" and len(files) > 1:
        for f in files:
            process_json(f)
    else:
        try:
            selected = files[int(choice) - 1]
        except (ValueError, IndexError):
            print("Escolha inválida.")
            sys.exit(1)
        process_json(selected)
