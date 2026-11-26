"""
Decomposição de task em steps (task_decomposer.py).

O AsyncOpenAI é trocado por um falso na fronteira: confere o que seria enviado e devolve
um JSON sintético.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import task_decomposer
from task_decomposer import Step


@pytest.fixture
def openai_falso(monkeypatch):
    """AsyncOpenAI falso: guarda os kwargs do create e devolve `estado['resposta']`."""
    estado = {"chamadas": [], "resposta": json.dumps({"steps": []})}

    class AsyncOpenAIFalso:
        def __init__(self, *a, **k):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            estado["chamadas"].append(kwargs)
            msg = SimpleNamespace(content=estado["resposta"])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(task_decomposer, "AsyncOpenAI", AsyncOpenAIFalso)
    return estado


LLM = SimpleNamespace(model="gpt-4o-mini")


def test_steps_voltam_em_ordem_com_nome_e_sub_task(openai_falso, tmp_path):
    openai_falso["resposta"] = json.dumps({"steps": [
        {"order": 2, "tool_name": "pesquisar_pedido", "sub_task": "Pesquise o pedido 1"},
        {"order": 1, "tool_name": "login_erp", "sub_task": "Entre no ERP"},
        {"order": 3, "tool_name": "imprimir", "sub_task": "Imprima"},
    ]})

    steps = asyncio.run(task_decomposer.decompose_task("tarefa", tmp_path / "tools", LLM))

    assert steps == [
        Step(tool_name="login_erp", sub_task="Entre no ERP", order=1),
        Step(tool_name="pesquisar_pedido", sub_task="Pesquise o pedido 1", order=2),
        Step(tool_name="imprimir", sub_task="Imprima", order=3),
    ]


def test_pedido_ao_llm_usa_o_modelo_e_exige_json(openai_falso, tmp_path):
    asyncio.run(task_decomposer.decompose_task("  Emitir nota  \n", tmp_path / "tools", LLM))

    (chamada,) = openai_falso["chamadas"]
    assert chamada["model"] == "gpt-4o-mini"
    assert chamada["response_format"] == {"type": "json_object"}
    (mensagem,) = chamada["messages"]
    assert mensagem["role"] == "user"
    assert mensagem["content"].endswith("Task a decompor:\nEmitir nota\n")
    assert '"steps": [' in mensagem["content"]  # chaves do exemplo escapadas no format


def test_prompt_lista_as_tools_existentes_para_reaproveitar_o_nome(openai_falso, tmp_path):
    for nome in ("login_erp", "__init__", "pesquisar"):
        (tmp_path / "tools" / f"{nome}.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "notas.txt").write_text("", encoding="utf-8")

    asyncio.run(task_decomposer.decompose_task("t", tmp_path / "tools", LLM))

    prompt = openai_falso["chamadas"][0]["messages"][0]["content"]
    linha = prompt.split("(reutilize o nome exato se cobrir o step):\n", 1)[1].splitlines()[0]
    assert sorted(linha.split(", ")) == ["login_erp", "pesquisar"]


def test_prompt_sem_tools_existentes_diz_nenhuma(openai_falso, tmp_path):
    asyncio.run(task_decomposer.decompose_task("t", tmp_path / "tools", LLM))

    prompt = openai_falso["chamadas"][0]["messages"][0]["content"]
    assert "(reutilize o nome exato se cobrir o step):\nnenhuma\n" in prompt


def test_resposta_que_nao_e_json_levanta_erro(openai_falso, tmp_path):
    openai_falso["resposta"] = "não sei"

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(task_decomposer.decompose_task("t", tmp_path / "tools", LLM))
