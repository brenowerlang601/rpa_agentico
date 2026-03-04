import json
from dataclasses import dataclass
from pathlib import Path

from openai import AsyncOpenAI

DECOMPOSE_PROMPT = """Você é um planejador de automação RPA.

Dada uma task completa, decomponha em steps sequenciais e atômicos.
Cada step deve ser uma ação web independente que pode ser salva como script reutilizável.

Regras para tool_name:
- Use snake_case
- Seja específico: "login_erp" é melhor que "login"
- Para pesquisas: "pesquisa_tela_pedi_f130", não apenas "pesquisa"
- Para navegação: "navegar_menu_producao", não apenas "navegar"
- Se uma tool da lista de existentes cobrir exatamente o step, USE O MESMO NOME

Retorne APENAS um JSON válido, sem texto adicional:
{{
  "steps": [
    {{
      "order": 1,
      "tool_name": "login_erp",
      "sub_task": "Descrição completa e autossuficiente do que fazer neste step, incluindo URLs, credenciais e contexto necessários para executar sem depender de steps anteriores."
    }}
  ]
}}

Tools já existentes (reutilize o nome exato se cobrir o step):
{existing_tools}

Task a decompor:
{task}
"""


@dataclass
class Step:
    tool_name: str
    sub_task: str
    order: int


async def decompose_task(task: str, tools_dir: Path, llm) -> list[Step]:
    existing_tools = [p.stem for p in tools_dir.glob("*.py") if p.stem != "__init__"]
    existing_str = ", ".join(existing_tools) if existing_tools else "nenhuma"

    prompt = DECOMPOSE_PROMPT.format(task=task.strip(), existing_tools=existing_str)

    client = AsyncOpenAI()
    response = await client.chat.completions.create(
        model=str(llm.model),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )

    raw = json.loads(response.choices[0].message.content)
    steps = [
        Step(
            tool_name=s["tool_name"],
            sub_task=s["sub_task"],
            order=s["order"],
        )
        for s in sorted(raw["steps"], key=lambda x: x["order"])
    ]
    return steps
