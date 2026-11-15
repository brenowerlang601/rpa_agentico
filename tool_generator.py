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

Regras obrigatórias:
- Assinatura: `async def {func_name}(page) -> bool:`
- Cada ação deve estar dentro de um bloco `try/except` individual
- Em caso de erro em uma ação, adicione à lista `errors: list[tuple[str, str]]` e continue
- Ao final: retorne `True` se `errors` estiver vazio, `False` caso contrário
- Imprima `[OK] {func_name}` ou `[FAIL] {func_name}: N erro(s)` com detalhes
- Conecte ao browser via CDP já estabelecida — `page` já está pronto, não crie browser
- Para `navigation`: `await page.goto(url)` + `await page.wait_for_load_state('networkidle')`
- Para `fill`: `await locator.wait_for(state='visible', timeout=10000)` + clique no locator + `await locator.fill(valor)` — use cssSelector se disponível, senão xpath
- Para `click`: `await locator.wait_for(state='visible', timeout=10000)` + `await locator.click()` + `await page.wait_for_timeout(500)`
- Para `key_press` com count=1: `await page.keyboard.press(key)` + `await page.wait_for_timeout(150)`
- Para `key_press` com count>1: `for _ in range(count): await page.keyboard.press(key); await page.wait_for_timeout(150)`
- Para xpath que começa sem `//`: adicione `//` no início
- Para cssSelector: use diretamente como string no locator
- NÃO inclua blocos markdown (```), apenas código Python puro
- NÃO inclua imports — a função será importada em um arquivo que já tem os imports
- Adicione um comentário inline em cada ação identificando o campo/elemento

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


def expects_url_change(named_steps: list[dict], index: int) -> bool:
    """
    Indica se a etapa `index` deve terminar em outra tela.
    Os grupos são separados por mudança de URL, então a troca de tela acontece dentro da
    etapa atual — exceto na última etapa e quando a próxima começa com uma navegação explícita
    (nesse caso quem muda a URL é a própria próxima etapa).
    """
    if index >= len(named_steps) - 1:
        return False
    next_actions = named_steps[index + 1].get("actions") or []
    if next_actions and next_actions[0].get("type") == "navigation":
        return False
    return True


def _build_prompt(func_name: str, actions: list[dict], expect_url_change: bool = False) -> str:
    actions_json = json.dumps(actions, indent=2, ensure_ascii=False)
    return _PROMPT.format(
        func_name=func_name,
        actions_json=actions_json,
        n_actions=len(actions),
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
    header = f"{fp_comment}from playwright.async_api import Page\n\n"
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
