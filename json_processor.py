"""
Processa um JSON de fluxo RPA (json_rpa/) e gera:
1. Tools por step em tools/ (funções Playwright reutilizáveis)
2. Script de teste Playwright completo (tests/<nome>.py)
"""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

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


_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_tool_name(name) -> bool:
    """Nome de tool precisa ser um identificador Python: vira `from tools.x import x` no script."""
    return isinstance(name, str) and bool(_TOOL_NAME_RE.match(name))


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
            # Ignora atalhos de copiar/colar — não fazem sentido em automação
            if action.get("key", "") in ("CmdOrCtrl+C", "CmdOrCtrl+V"):
                i += 1
                continue
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
            actions.append({"type": "key_press", "key": key, "count": count, "url": url, "xpath": xpath,
                            "css_selector": css, "timestamp": action.get("timestamp")})
            i = j
            continue

        if atype == "navigation":
            actions.append({"type": "navigation", "url": action.get("url", ""),
                            "timestamp": action.get("timestamp")})
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
                    "timestamp": action.get("timestamp"),
                })
                i = j + 1
            else:
                actions.append({
                    "type": "click",
                    "xpath": xpath,
                    "css_selector": action.get("cssSelector", ""),
                    "target_text": target,
                    "element_text": (action.get("elementText") or "").strip(),
                    "url": action.get("url", ""),
                    "timestamp": action.get("timestamp"),
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
                "timestamp": action.get("timestamp"),
            })
            i += 1
            continue

        i += 1

    for a in actions:
        # A extensão grava campos de senha como "********": o valor real vem de um secret
        if a["type"] == "fill" and _MASKED_RE.match(a.get("value") or ""):
            a["masked"] = True
    _add_wait_timeouts(actions)
    return actions


# Valor mascarado pela extensão em campos de senha
_MASKED_RE = re.compile(r"^\*{3,}$")

# Espera mínima/máxima por elemento e intervalo que indica "algo demorado aconteceu"
DEFAULT_TIMEOUT_MS = 10000
MAX_TIMEOUT_MS = 180000
SLOW_GAP_MS = 3000
# Acima disso, uma sequência na mesma URL é quebrada em mais de uma tool
MAX_ACTIONS_PER_TOOL = 6


def _gap_ms(prev: dict | None, cur: dict | None) -> int:
    """Tempo entre duas ações na gravação (0 se não houver timestamps)."""
    if not prev or not cur or not prev.get("timestamp") or not cur.get("timestamp"):
        return 0
    return max(0, int(cur["timestamp"]) - int(prev["timestamp"]))


def _add_wait_timeouts(actions: list[dict]) -> None:
    """
    Define quanto cada ação pode esperar pelo seu elemento, a partir do tempo que o usuário
    esperou na gravação. Um elemento que levou 24 s para aparecer (ex.: resposta de IA) não
    pode ter o limite fixo de 10 s.
    """
    prev = None
    for a in actions:
        gap = _gap_ms(prev, a)
        a["timeout_ms"] = min(MAX_TIMEOUT_MS, max(DEFAULT_TIMEOUT_MS, gap * 2 + 5000))
        prev = a


LOGIN_KEYWORDS = {"usuario", "senha", "login", "password", "user", "email", "pass", "username"}


def is_login_field(action: dict) -> bool:
    """Campo de usuário/senha de uma tela de login."""
    if action.get("type") != "fill":
        return False
    if action.get("masked") or action.get("secret"):
        return True
    texto = _slug(action.get("target_text", ""))
    seletor = (action.get("xpath", "") + " " + action.get("css_selector", "")).lower()
    return texto in LOGIN_KEYWORDS or any(f'"{kw}"' in seletor or f"'{kw}'" in seletor for kw in LOGIN_KEYWORDS)


def _login_end_index(actions: list[dict]) -> int | None:
    """
    Índice da ação que envia o login (clique ou Enter depois do último campo de login),
    ou None se o trecho não faz login.
    """
    login_idx = [i for i, a in enumerate(actions) if is_login_field(a)]
    if not login_idx:
        return None
    for i in range(login_idx[-1] + 1, len(actions)):
        a = actions[i]
        if a["type"] == "click" or (a["type"] == "key_press" and a.get("key") == "Enter"):
            return i
    return None


def login_field(actions: list[dict]) -> dict | None:
    """Primeiro campo de login do trecho (usado para verificar se a sessão já está aberta)."""
    if _login_end_index(actions) is None:
        return None
    return next((a for a in actions if is_login_field(a)), None)


def _split_same_url(actions: list[dict]) -> list[list[dict]]:
    """
    Quebra uma sequência longa feita numa mesma URL (SPAs e ERPs que trocam de tela sem
    mudar a URL) em etapas menores, nos pontos naturais:
    - o login vira uma etapa própria (pode ser pulado se a sessão já estiver aberta);
    - depois de um clique/Enter seguido de espera longa (troca de tela, processamento);
    - depois de um clique que envia campos preenchidos (submit de formulário).
    Pedaços de uma ação só são juntados ao seguinte.
    """
    login_end = _login_end_index(actions)
    if len(actions) <= MAX_ACTIONS_PER_TOOL:
        # Curto: só separa o login, se houver mais coisa depois dele
        if login_end is not None and login_end < len(actions) - 1:
            return [actions[:login_end + 1], actions[login_end + 1:]]
        return [actions]

    parts: list[list[dict]] = []
    cur: list[dict] = []
    for k, a in enumerate(actions):
        cur.append(a)
        nxt = actions[k + 1] if k + 1 < len(actions) else None
        if nxt is None:
            break
        boundary = k == login_end
        if a["type"] == "click" or (a["type"] == "key_press" and a.get("key") == "Enter"):
            if _gap_ms(a, nxt) >= SLOW_GAP_MS:
                boundary = True
            elif a["type"] == "click" and any(x["type"] == "fill" for x in cur):
                boundary = True
        if len(cur) >= MAX_ACTIONS_PER_TOOL * 2:
            boundary = True
        if boundary:
            parts.append(cur)
            cur = []
    if cur:
        parts.append(cur)

    merged: list[list[dict]] = []
    carry: list[dict] = []
    for idx, part in enumerate(parts):
        part = carry + part
        carry = []
        is_login_part = _login_end_index(part) is not None
        if len(part) == 1 and idx < len(parts) - 1 and not is_login_part:
            carry = part
            continue
        merged.append(part)
    if carry:
        if merged and _login_end_index(merged[-1]) is None:
            merged[-1].extend(carry)
        else:
            merged.append(carry)
    return merged


def annotate_masked(named_steps: list[dict]) -> list[dict]:
    """
    Dá um id estável a cada campo mascarado (senha) e devolve a lista para a interface pedir
    o secret: [{id, tool_name, target_text, suggested_name}].
    """
    fields = []
    for gi, step in enumerate(named_steps):
        for ai, a in enumerate(step.get("actions") or []):
            if a.get("masked") and not a.get("secret"):
                a["masked_id"] = f"g{gi}a{ai}"
                host = _slug(urlparse(a.get("url", "")).hostname or "app").upper()
                campo = _slug(a.get("target_text", "")).upper() or "SENHA"
                fields.append({
                    "id": a["masked_id"],
                    "tool_name": step.get("tool_name"),
                    "target_text": a.get("target_text", ""),
                    "suggested_name": f"{host}_{campo}",
                })
    return fields


def apply_secrets(named_steps: list[dict], mapping: dict[str, str]) -> None:
    """
    Troca o valor mascarado pelo secret escolhido: {masked_id: NOME}.
    Levanta ValueError se algum campo mascarado ficar sem secret.
    """
    annotate_masked(named_steps)
    missing = []
    for step in named_steps:
        for a in step.get("actions") or []:
            if a.get("masked") and not a.get("secret"):
                name = mapping.get(a.get("masked_id", ""))
                if not name:
                    missing.append(a.get("target_text") or a.get("masked_id"))
                    continue
                a["secret"] = name
                a.pop("value", None)
    if missing:
        raise ValueError(f"Campos de senha sem secret definido: {', '.join(missing)}")


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

    # Mesma URL não quer dizer mesma tela: quebra sequências longas em etapas menores
    split: list[dict] = []
    for g in groups:
        for part in _split_same_url(g["actions"]):
            split.append({"url": g["url"], "actions": part})
    return split


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
            page_slug = path_slug or _slug(parsed.hostname or "") or "pagina"

        # Classifica o grupo pelo conteúdo das ações
        fills = [a for a in actions if a["type"] == "fill"]
        clicks = [a for a in actions if a["type"] == "click"]
        navigations = [a for a in actions if a["type"] == "navigation"]

        is_login = _login_end_index(actions) is not None or (
            bool(fills) and any(kw in page_slug for kw in ("login", "senha", "auth", "acesso"))
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

def _actions_fingerprint(actions: list[dict]) -> str:
    """
    Gera um fingerprint hash das ações normalizadas.
    Usa tipo + xpath/css + valor para identificar unicamente o conjunto de ações.
    """
    key_parts = []
    for a in actions:
        parts = [
            a.get("type", ""),
            a.get("xpath", ""),
            a.get("css_selector", ""),
            "<secret>" if (a.get("masked") or a.get("secret")) else a.get("value", ""),
            a.get("key", ""),
            str(a.get("count", 1)),
            a.get("url", ""),
        ]
        key_parts.append("|".join(parts))
    raw = "::".join(key_parts)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _read_tool_fingerprint(tool_path: Path) -> str | None:
    """
    Lê o fingerprint salvo no cabeçalho da tool (comentário # actions_fp: <hash>).
    Se o arquivo não tiver o cabeçalho (tool criada/editada à mão), usa o fingerprint do banco.
    """
    try:
        first_lines = tool_path.read_text(encoding="utf-8").splitlines()[:3]
        for line in first_lines:
            if line.startswith("# actions_fp:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        from db import get_tool_fingerprint
        return get_tool_fingerprint(tool_path.stem)
    except Exception:
        return None


def resolve_tool_names(named_steps: list[dict], tools_dir: Path, existing_tools: set[str]) -> list[str]:
    """
    Resolve os nomes finais das tools (com sufixo _v2, _v3 se necessário)
    sem gerar nada via LLM. Muta step["tool_name"] igual ao save_tools.
    Retorna lista de nomes resolvidos.
    """
    resolved = []
    for step in named_steps:
        base_name = step["tool_name"]
        actions = step["actions"]
        new_fp = _actions_fingerprint(actions)

        candidate = base_name
        version = 2
        while candidate in existing_tools:
            tool_path = tools_dir / f"{candidate}.py"
            existing_fp = _read_tool_fingerprint(tool_path)
            if existing_fp == new_fp:
                break
            candidate = f"{base_name}_v{version}"
            version += 1

        step["tool_name"] = candidate
        resolved.append(candidate)
    return resolved


def resolve_secrets_in_terminal(named_steps: list[dict]) -> None:
    """Linha de comando: pede um secret (existente ou novo) para cada campo de senha mascarado."""
    import getpass

    import secrets_store

    fields = annotate_masked(named_steps)
    if not fields:
        return
    existing = {s["name"] for s in secrets_store.list_secrets()}
    mapping = {}
    for f in fields:
        print(f"\nCampo de senha '{f['target_text']}' em {f['tool_name']} foi gravado como ********.")
        if existing:
            print(f"Secrets existentes: {', '.join(sorted(existing))}")
        while True:
            name = input(f"Nome do secret [{f['suggested_name']}]: ").strip() or f["suggested_name"]
            if secrets_store.is_valid_name(name):
                break
            print("Nome inválido: use MAIÚSCULAS, números e _ (ex.: ERP_SENHA)")
        if name not in existing:
            secrets_store.set_secret(name, getpass.getpass(f"Valor de {name}: "))
            existing.add(name)
        mapping[f["id"]] = name
    apply_secrets(named_steps, mapping)


def save_tools(named_steps: list[dict], tools_dir: Path, existing_tools: set[str]):
    """
    Gera tools para cada micro-processo via LLM (tool_generator).
    Compara fingerprint das ações com a tool existente:
    - Se fingerprint igual: reutiliza (mesmo comportamento)
    - Se fingerprint diferente: gera nova tool com sufixo _v2, _v3, etc.
    """
    from tool_generator import expects_url_change, generate_tool_from_actions

    resolve_secrets_in_terminal(named_steps)
    saved = []

    for index, step in enumerate(named_steps):
        base_name = step["tool_name"]
        actions = step["actions"]
        new_fp = _actions_fingerprint(actions)

        # Encontra o nome final da tool (pode ser base_name ou base_name_v2, etc.)
        candidate = base_name
        version = 2
        while candidate in existing_tools:
            tool_path = tools_dir / f"{candidate}.py"
            existing_fp = _read_tool_fingerprint(tool_path)
            if existing_fp == new_fp:
                # Mesmo comportamento — reutiliza
                print(f"  [skip] tool idêntica já existe: {candidate}.py")
                break
            # Ações diferentes — tenta próxima versão
            print(f"  [diff] ações diferentes de '{candidate}' (fp={new_fp[:6]}≠{(existing_fp or '?')[:6]}) → tentando versão {version}")
            candidate = f"{base_name}_v{version}"
            version += 1
        else:
            # candidate não existe — gera nova tool (só é salva se o usuário aprovar no terminal)
            path = generate_tool_from_actions(
                candidate, actions, fingerprint=new_fp,
                expect_url_change=expects_url_change(named_steps, index),
            )
            if path is None:
                step["tool_name"] = candidate
                step["rejected"] = True
                continue
            existing_tools.add(candidate)

        # Garante que o nome usado vá para o step (pode ter mudado para _v2 etc.)
        step["tool_name"] = candidate
        saved.append(candidate)

    return saved


# ---------------------------------------------------------------------------
# Saída: script de teste Playwright completo
# ---------------------------------------------------------------------------

def build_script(
    workflow_name: str,
    steps: list[str],
    headless: bool | None = None,
    cdp_url: str | None = None,
    start_index: int = 0,
) -> str:
    """
    Gera o script Playwright que executa as tools em sequência.
    É o mesmo código para o preview, para a execução pela interface e para o script salvo em tests/.

    - headless=None: decide em tempo de execução pela variável RPA_HEADLESS (scripts salvos).
    - cdp_url=None: usa a variável CDP_URL (padrão http://localhost:9222).
    - start_index: índice absoluto do primeiro step (usado ao começar de uma etapa específica),
      para que os marcadores __RUNNING__/__STEP_RESULT__ apontem para a posição certa no fluxo.

    Semântica de cada step: True/None → PASS; False ou exceção → FAIL. O fluxo para no primeiro
    FAIL, porque os próximos passos dependem da tela deixada pelo anterior.
    A última linha impressa é sempre "true" ou "false".
    """
    for s in steps:
        if not is_valid_tool_name(s):
            raise ValueError(f"Nome de tool inválido: {s!r}")

    import_lines = "\n".join(f"from tools.{s} import {s}" for s in dict.fromkeys(steps))
    step_lines = "\n".join(
        f"    ({start_index + i}, {s!r}, {s})," for i, s in enumerate(steps)
    )
    headless_expr = repr(headless) if headless is not None else 'os.environ.get("RPA_HEADLESS", "0") == "1"'
    cdp_expr = repr(cdp_url) if cdp_url else 'os.environ.get("CDP_URL") or "http://localhost:9222"'

    return f"""\
# rpa_steps: {json.dumps(list(steps))}
import asyncio
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

{import_lines}

WORKFLOW_NAME = {workflow_name!r}
HEADLESS = {headless_expr}
CDP_URL = {cdp_expr}

STEPS = [
{step_lines}
]


def _cdp_endpoint(url: str) -> str:
    \"\"\"
    Normaliza o endereço CDP. O Chrome recusa conexões cujo Host não seja um IP ou
    localhost, então nomes como host.docker.internal são resolvidos para IP aqui.
    \"\"\"
    parsed = urlparse(url if "://" in url else f"http://{{url}}")
    host = parsed.hostname or "localhost"
    if host != "localhost":
        try:
            host = socket.gethostbyname(host)
        except OSError:
            pass
    return f"{{parsed.scheme or 'http'}}://{{host}}:{{parsed.port or 9222}}"


async def run():
    async with async_playwright() as p:
        if HEADLESS:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
        else:
            browser = await p.chromium.connect_over_cdp(_cdp_endpoint(CDP_URL))
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

        passed: list[str] = []
        failed: str | None = None
        for idx, name, func in STEPS:
            print(f"__RUNNING__{{idx}}:{{name}}", flush=True)
            try:
                ok = (await func(page)) is not False
                erro = "" if ok else "a tool retornou False"
            except Exception as e:
                ok = False
                erro = (str(e).strip().splitlines() or [type(e).__name__])[0]
            print(f"__STEP_RESULT__{{idx}}:{{'ok' if ok else 'fail'}}:{{name}}", flush=True)
            if ok:
                passed.append(name)
                print(f"  [PASS] {{name}}", flush=True)
            else:
                failed = name
                print(f"  [FAIL] {{name}}: {{erro}}", flush=True)
                break

        print("=" * 50)
        print(f"Relatório — {{WORKFLOW_NAME}}")
        print("=" * 50)
        nao_executados = len(STEPS) - len(passed) - (1 if failed else 0)
        print(f"  Passaram: {{len(passed)}} | Falhou: {{1 if failed else 0}} | Não executados: {{nao_executados}}")
        print("=" * 50)
        print("true" if failed is None else "false", flush=True)
        sys.exit(0 if failed is None else 1)


if __name__ == '__main__':
    asyncio.run(run())
"""


def read_script_steps(code: str) -> list[str]:
    """Extrai a lista de steps de um script salvo (cabeçalho # rpa_steps, ou formato antigo)."""
    first = code.splitlines()[0] if code else ""
    if first.startswith("# rpa_steps:"):
        try:
            return json.loads(first.split(":", 1)[1])
        except ValueError:
            pass
    steps = []
    for line in code.splitlines():
        if line.startswith("        results["):
            steps.append(line.strip().split("'")[1])
    return steps


def test_script_path(workflow_name: str, tests_dir: Path, script_name: str | None = None) -> Path:
    """Caminho do script salvo: script_name (sanitizado) ou slug do nome do workflow."""
    stem = re.sub(r"[^a-zA-Z0-9_]", "_", script_name) if script_name else _slug(workflow_name)
    return tests_dir / f"{stem or 'workflow'}.py"


def save_test_script(workflow_name: str, named_steps: list[dict], tests_dir: Path,
                     script_name: str | None = None) -> Path:
    """Gera script de teste Playwright completo que executa o fluxo inteiro."""
    func_names = [s["tool_name"] for s in named_steps]
    path = test_script_path(workflow_name, tests_dir, script_name)
    path.write_text(build_script(workflow_name, func_names), encoding="utf-8")
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
