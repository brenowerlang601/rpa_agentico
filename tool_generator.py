"""
Gera scripts Playwright para tools RPA.

Dois modos de entrada:
1. Ações ao vivo (browser_use) — via generate_tool_from_agent_result()
2. Ações normalizadas do JSON — via generate_tool_from_actions()

Em ambos os casos, um LLM (GPT-4o via OpenAI) lê TODAS as ações e gera o código
Python, garantindo que nenhum passo seja omitido.

Nenhuma tool é salva sem aprovação do usuário: na interface web a revisão é feita
pelo modal (app.py /api/generate-tools); na linha de comando, por review_in_terminal().
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TOOLS_DIR = Path("tools")
TOOLS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Prompt para geração do código Playwright
# ---------------------------------------------------------------------------

_PROMPT = """\
Você é um especialista em automação web com Playwright (Python async).

Gere uma função Python async chamada `{func_name}` que executa exatamente \
a sequência de ações abaixo — SEM pular, reordenar ou omitir nenhuma delas.

Funções de apoio JÁ IMPORTADAS no arquivo (módulo rpa_runtime) — use-as, NÃO as defina:
- `await localizar(page, xpath=..., css=..., texto=[...], timeout=...)` → Locator de UM elemento visível.
  Ela escolhe entre os seletores gravados o que é único e espera o elemento aparecer.
- `await login_necessario(page, xpath=..., css=..., texto=[...], timeout=5000)` → True se a tela de login está presente.
- `await confirmar_login(page, xpath=..., css=..., texto=[...], timeout=15000)` → True se a tela de login sumiu.
- `secret("NOME")` → valor de um secret (senha). NUNCA escreva senhas no código.

Regras obrigatórias:
- Assinatura: `async def {func_name}(page) -> bool:`
- Cada ação deve estar dentro de um bloco `try/except` individual
- Em caso de erro em uma ação, adicione à lista `errors: list[tuple[str, str]]` e continue
- Ao final: retorne `True` se `errors` estiver vazio, `False` caso contrário
- Imprima `[OK] {func_name}` ou `[FAIL] {func_name}: N erro(s)` e, para cada erro, uma linha `  - <ação>: <erro>`
- `page` já está pronto (conectado via CDP) — não crie browser
- Para localizar elementos use SEMPRE `localizar(...)` — nunca `page.locator(...)` direto. Passe:
  `xpath` = campo "xpath" da ação (copie exatamente, sem acrescentar `//`), `css` = "css_selector",
  `texto` = lista com "element_text" e "target_text" que não estiverem vazios,
  `timeout` = campo "timeout_ms" da ação (é o tempo que o elemento levou para aparecer na gravação)
- `navigation`: `await page.goto(url)` + `await page.wait_for_load_state('networkidle')`
- `fill`: `campo = await localizar(...)` + `await campo.click()` + `await campo.fill(valor)`
- `fill` com campo "secret": `await campo.fill(secret("<valor do campo secret>"))` — nunca use "value"
- `click`: `campo = await localizar(...)` + `await campo.click()` + `await page.wait_for_timeout(500)`
- `key_press` com count=1: `await page.keyboard.press(key)` + `await page.wait_for_timeout(150)`
- `key_press` com count>1: `for _ in range(count): await page.keyboard.press(key); await page.wait_for_timeout(150)`
- NÃO inclua blocos markdown (```), apenas código Python puro
- NÃO inclua imports — a função será importada em um arquivo que já tem os imports
- Adicione um comentário inline em cada ação identificando o campo/elemento

{login_rule}

{url_rule}

Ações a executar (JSON com TODOS os passos — nenhum pode ser omitido):
{actions_json}

Lembre: você deve gerar exatamente {n_actions} blocos try/except, um por ação.
"""

# Só faz sentido exigir mudança de URL quando a gravação mostra que a tela mudou
# durante esta etapa. Um grupo que só preenche campos, o último passo do fluxo ou um
# grupo seguido de uma navegação explícita não mudam a URL — exigir isso reprovava
# tools corretas.
_URL_RULE_EXPECT = """Validação de navegação (OBRIGATÓRIO — esta etapa deve terminar em outra tela):
- No INÍCIO da função, capture a URL atual com: `_url_inicial = await page.evaluate('location.href')`
- No FINAL da função, ANTES de retornar, capture novamente: `_url_final = await page.evaluate('location.href')`
- Se `_url_final == _url_inicial`: adicione o erro `errors.append(('url_unchanged', 'URL não mudou após execução'))` — isso indica falha de navegação
- Use `location.href` (via evaluate) e NÃO `page.url`, pois `page.url` ignora fragmentos (#hash) da URL"""

_URL_RULE_NONE = """Validação de navegação:
- Esta etapa NÃO precisa mudar de tela. NÃO compare a URL inicial com a final e NÃO adicione erro de URL inalterada."""

# Etapas de login: a sessão pode já estar aberta (cookies do browser) — nesse caso o login
# é pulado; quando é feito, confirma que a tela de login sumiu.
_LOGIN_RULE = """Validação de login (OBRIGATÓRIO — esta etapa faz login):
- Execute normalmente as ações ANTERIORES ao primeiro campo de login (ex.: navigation).
- Antes do primeiro campo de login, verifique se a tela de login está presente:
  `if not await login_necessario(page, xpath={xpath!r}, css={css!r}, texto={texto!r}, timeout=5000):`
  `    print("[OK] {func_name}: sessão já autenticada — login ignorado")`
  `    return True`
- Depois da ação que envia o login (a última ação), confirme o login:
  `if not await confirmar_login(page, xpath={xpath!r}, css={css!r}, texto={texto!r}, timeout=15000):`
  `    errors.append(('login', 'a tela de login continua visível — usuário ou senha incorretos?'))`"""


def expects_url_change(named_steps: list[dict], index: int) -> bool:
    """
    Indica se a etapa `index` deve terminar em outra tela.
    Os grupos são separados por mudança de URL, então a troca de tela acontece dentro da
    etapa atual — exceto na última etapa, quando a próxima começa com uma navegação explícita
    (nesse caso quem muda a URL é a própria próxima etapa) e quando a próxima etapa continua
    na mesma URL (sequências longas quebradas em várias tools numa SPA).
    """
    from json_processor import _screen_key

    if index >= len(named_steps) - 1:
        return False
    actions = named_steps[index].get("actions") or []
    next_actions = named_steps[index + 1].get("actions") or []
    if not actions or not next_actions:
        return False
    if next_actions[0].get("type") == "navigation":
        return False
    return _screen_key(actions[-1].get("url", "")) != _screen_key(next_actions[0].get("url", ""))


def _prompt_actions(actions: list[dict]) -> list[dict]:
    """Ações como vão para o LLM: sem campos internos e sem valores mascarados."""
    out = []
    for a in actions:
        a = {k: v for k, v in a.items() if k not in ("timestamp", "masked", "masked_id") and v not in ("", None)}
        if a.get("secret"):
            a.pop("value", None)
        out.append(a)
    return out


def _build_prompt(func_name: str, actions: list[dict], expect_url_change: bool = False) -> str:
    from json_processor import login_field

    if any(a.get("masked") and not a.get("secret") for a in actions):
        raise ValueError(f"'{func_name}' tem campo de senha sem secret definido")

    campo = login_field(actions)
    login_rule = ""
    if campo:
        login_rule = _LOGIN_RULE.format(
            func_name=func_name,
            xpath=campo.get("xpath") or None,
            css=campo.get("css_selector") or None,
            texto=[campo["target_text"]] if campo.get("target_text") else None,
        )
    actions_json = json.dumps(_prompt_actions(actions), indent=2, ensure_ascii=False)
    return _PROMPT.format(
        func_name=func_name,
        actions_json=actions_json,
        n_actions=len(actions),
        login_rule=login_rule,
        url_rule=_URL_RULE_EXPECT if expect_url_change else _URL_RULE_NONE,
    )


def _call_llm(prompt: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    code = response.choices[0].message.content.strip()
    # Remove markdown fences caso o LLM inclua
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:])
        if code.endswith("```"):
            code = code[:-3].rstrip()
        elif "\n```" in code:
            code = code[: code.rfind("\n```")]
    return code


def _save_tool(func_name: str, func_code: str, fingerprint: str = "") -> Path:
    """
    Grava a tool no banco e depois no disco.
    Levanta db.DBError se o banco falhar — nesse caso nada é gravado no disco,
    evitando que o restore do startup reverta a tool.
    """
    from db import upsert_tool

    fp_comment = f"# actions_fp: {fingerprint}\n" if fingerprint else ""
    header = (
        f"{fp_comment}from playwright.async_api import Page\n"
        "from rpa_runtime import confirmar_login, localizar, login_necessario, secret\n\n"
    )
    full_code = header + func_code
    tool_path = TOOLS_DIR / f"{func_name}.py"
    upsert_tool(func_name, full_code, fingerprint)
    tool_path.write_text(full_code, encoding="utf-8")
    print(f"[tool_generator] Salvo: {tool_path}")
    return tool_path


def review_in_terminal(func_name: str, prompt: str) -> str | None:
    """
    Revisão de tool pela linha de comando (mesmo fluxo do modal da interface):
    mostra o código e pergunta aprovar / rejeitar / revisar.
    Retorna o código aprovado ou None se rejeitado.
    """
    code = _call_llm(prompt)
    feedback_history: list[str] = []
    while True:
        print("\n" + "=" * 70)
        print(f"Revisar tool: {func_name}")
        print("=" * 70)
        print(code)
        print("=" * 70)
        choice = input("[a] aprovar  [r] rejeitar  [v] pedir revisão: ").strip().lower()
        if choice == "a":
            return code
        if choice == "r":
            print(f"[tool_generator] Rejeitada: {func_name}")
            return None
        if choice == "v":
            feedback = input("O que deve mudar? ").strip()
            if feedback:
                feedback_history.append(feedback)
            revision_note = "\n".join(f"- {fb}" for fb in feedback_history)
            code = _call_llm(
                prompt + f"\n\nO usuário pediu as seguintes revisões (aplique TODAS):\n{revision_note}"
            )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def generate_tool_from_actions(func_name: str, actions: list[dict], fingerprint: str = "",
                               expect_url_change: bool = False) -> Path | None:
    """
    Gera uma tool Playwright a partir de ações normalizadas do JSON.
    O LLM recebe a lista completa e deve gerar um bloco por ação.
    A tool só é salva se o usuário aprovar no terminal. Retorna None se rejeitada.
    """
    print(f"[tool_generator] Gerando '{func_name}' com {len(actions)} ações via LLM...")
    prompt = _build_prompt(func_name, actions, expect_url_change=expect_url_change)
    code = review_in_terminal(func_name, prompt)
    if code is None:
        return None
    return _save_tool(func_name, code, fingerprint=fingerprint)


async def generate_tool_from_agent_result(task_name: str, result) -> Path | None:
    """
    Gera uma tool Playwright a partir do histórico de ações de um agente ao vivo.
    Converte model_actions() para o formato de ações normalizadas e usa o mesmo LLM.
    A tool só é salva se o usuário aprovar no terminal. Retorna None se rejeitada.
    """
    raw_actions = result.model_actions()
    # Converte para formato genérico compatível com o prompt
    actions = [{"type": "agent_action", "data": json.dumps(a, default=str)} for a in raw_actions]
    print(f"[tool_generator] Gerando '{task_name}' de agent result ({len(actions)} ações) via LLM...")
    prompt = _build_prompt(task_name, actions)
    code = review_in_terminal(task_name, prompt)
    if code is None:
        return None
    return _save_tool(task_name, code)
