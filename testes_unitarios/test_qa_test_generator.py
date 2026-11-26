"""
Geração de testes via LLM (qa_test_generator).

A OpenAI nunca é chamada: a classe OpenAI do módulo é trocada por um cliente falso que
guarda o pedido e devolve a resposta escolhida pelo teste.
"""

from types import SimpleNamespace

import pytest

import qa_test_generator as gen


# ---------------------------------------------------------------------------
# Cliente OpenAI falso
# ---------------------------------------------------------------------------

class _OpenAIFalso:
    """Imita OpenAI(api_key=...).chat.completions.create(...)."""

    def __init__(self, resposta=None, erro=None):
        self.resposta = resposta
        self.erro = erro
        self.chamadas = []
        self.api_keys = []

    def __call__(self, api_key=None):
        self.api_keys.append(api_key)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))

    def _create(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro:
            raise self.erro
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.resposta))])

    @property
    def prompt(self):
        return self.chamadas[-1]["messages"][0]["content"]


@pytest.fixture
def llm(monkeypatch):
    falso = _OpenAIFalso(resposta="// código de teste")
    monkeypatch.setattr(gen, "OpenAI", falso)
    return falso


def _gerar(**extra):
    args = dict(
        function_name="calcularDesconto",
        function_code="public int calcularDesconto(int v) {\n    return v / 10;\n}",
        file_context="package com.exemplo;\npublic class Precos {",
        language="java",
        framework="junit5",
    )
    args.update(extra)
    return gen.generate_tests(**args)


# ---------------------------------------------------------------------------
# generate_tests — prompt montado
# ---------------------------------------------------------------------------

def test_junit5_monta_prompt_com_codigo_contexto_e_nome_da_classe(llm):
    r = _gerar()

    p = llm.prompt
    assert "JUnit 5" in p and "NÃO use JUnit 4" in p
    assert "public int calcularDesconto(int v) {\n    return v / 10;\n}" in p
    assert "package com.exemplo;\npublic class Precos {" in p
    assert "Classe de teste: `CalcularDescontoTest`" in p
    assert r == {
        "test_code": "// código de teste",
        "filename": "CalcularDescontoTest.java",
        "framework": "junit5",
        "language": "java",
    }


def test_junit4_usa_prompt_de_junit4(llm):
    r = _gerar(framework="junit4")

    assert "JUnit 4 COMPLETA" in llm.prompt
    assert "@RunWith(MockitoJUnitRunner.class)" in llm.prompt
    assert r["filename"] == "CalcularDescontoTest.java"


def test_apex_usa_prompt_apex_e_arquivo_cls(llm):
    r = _gerar(function_name="calcularSaldo", language="apex", framework="apex-test")

    assert "Salesforce Apex" in llm.prompt and "@isTest" in llm.prompt
    assert r["filename"] == "CalcularSaldoTest.cls"


@pytest.mark.parametrize("language, ext", [("typescript", "ts"), ("javascript", "js")])
def test_jest_usa_extensao_da_linguagem_e_chaves_literais_no_prompt(llm, language, ext):
    r = _gerar(function_name="formatarCpf", function_code="function formatarCpf(c) { return c; }",
               language=language, framework="jest")

    assert f"Nome do arquivo de teste: `formatarCpf.test.{ext}`" in llm.prompt
    assert "describe('formatarCpf', () => { ... })" in llm.prompt
    assert "function formatarCpf(c) { return c; }" in llm.prompt
    assert r["filename"] == f"formatarCpf.test.{ext}"
    assert r["language"] == language and r["framework"] == "jest"


def test_jest_rtl_usa_prompt_do_testing_library(llm):
    r = _gerar(function_name="Botao", function_code="export function Botao() { return null; }",
               language="typescript", framework="jest-rtl")

    assert "React Testing Library" in llm.prompt
    assert "import { render, screen, fireEvent, waitFor } from '@testing-library/react'" in llm.prompt
    assert r["filename"] == "Botao.test.tsx"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: projeto React em JavaScript — o prompt manda o LLM gerar `Botao.test.js`, mas o "
    "arquivo devolvido se chama `Botao.test.tsx` (código JS salvo como TSX)"))
def test_jest_rtl_em_javascript_nome_do_arquivo_bate_com_o_do_prompt(llm):
    r = _gerar(function_name="Botao", function_code="export function Botao() { return null; }",
               language="javascript", framework="jest-rtl")

    assert f"`{r['filename']}`" in llm.prompt


def test_codigo_com_chaves_e_marcadores_de_format_chega_intacto_ao_prompt(llm):
    codigo = "function f(o) { return `${o.a}` + '{function_name}' + '{{x}}'; }"

    _gerar(function_name="f", function_code=codigo, language="javascript", framework="jest")

    assert codigo in llm.prompt


def test_sem_contexto_usa_marcador_no_prompt(llm):
    _gerar(file_context="")
    assert "(sem contexto adicional)" in llm.prompt


def test_parametros_de_exemplo_entram_no_prompt(llm):
    _gerar(example_params="valor=100, esperado=10")

    assert "```\nvalor=100, esperado=10\n```\nUse esses valores como base" in llm.prompt
    assert "Nenhum exemplo fornecido" not in llm.prompt


def test_sem_parametros_de_exemplo_pede_para_inferir(llm):
    _gerar()
    assert "Nenhum exemplo fornecido — infira valores razoáveis" in llm.prompt


def test_framework_nao_suportado_levanta_erro_sem_chamar_o_llm(llm):
    with pytest.raises(ValueError, match="Framework 'mocha' não suportado"):
        _gerar(framework="mocha")
    assert llm.chamadas == []


def test_erro_do_llm_propaga_para_quem_chamou(monkeypatch):
    falso = _OpenAIFalso(erro=RuntimeError("limite de requisições"))
    monkeypatch.setattr(gen, "OpenAI", falso)

    with pytest.raises(RuntimeError, match="limite de requisições"):
        _gerar()


# ---------------------------------------------------------------------------
# _call_llm — pedido e pós-processamento da resposta
# ---------------------------------------------------------------------------

def test_call_llm_pede_gpt4o_com_o_prompt_e_a_chave_do_ambiente(monkeypatch):
    falso = _OpenAIFalso(resposta="ok")
    monkeypatch.setattr(gen, "OpenAI", falso)
    monkeypatch.setenv("OPENAI_API_KEY", "chave-falsa")

    assert gen._call_llm("meu prompt") == "ok"

    assert falso.api_keys == ["chave-falsa"]
    assert falso.chamadas == [{
        "model": "gpt-4o",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "meu prompt"}],
    }]


@pytest.mark.parametrize("resposta, esperado", [
    ("```java\nclass ATest {}\n```", "class ATest {}"),
    ("```\nclass ATest {}\n```", "class ATest {}"),
    ("  \n```typescript\nit('x', () => {});\n```  \n", "it('x', () => {});"),
    ("```java\nclass ATest {}\n```\n\nExplicação: cobre o caminho feliz.", "class ATest {}"),
    ("```java\nclass ATest {}\n   \n```", "class ATest {}"),
    ("```java\nclass ATest {}", "class ATest {}"),
    ("  class ATest {}\n\n", "class ATest {}"),
    ("String s = \"```\";", "String s = \"```\";"),
])
def test_call_llm_remove_cercas_markdown(monkeypatch, resposta, esperado):
    monkeypatch.setattr(gen, "OpenAI", _OpenAIFalso(resposta=resposta))
    assert gen._call_llm("p") == esperado


@pytest.mark.xfail(strict=True, reason=(
    "BUG: quando o LLM escreve uma frase antes da cerca (```java ...```), a resposta volta "
    "inteira, com o texto e as cercas, como se fosse o código do teste"))
def test_call_llm_remove_texto_antes_da_cerca(monkeypatch):
    monkeypatch.setattr(gen, "OpenAI", _OpenAIFalso(resposta="Aqui está o teste:\n```java\nclass ATest {}\n```"))
    assert gen._call_llm("p") == "class ATest {}"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: resposta vazia do LLM — content None quebra com AttributeError e content '' é "
    "devolvido como teste vazio (salvo no histórico); o esperado é um ValueError claro"))
@pytest.mark.parametrize("resposta", [None, "", "   \n", "```java\n```"])
def test_call_llm_resposta_vazia_levanta_value_error(monkeypatch, resposta):
    monkeypatch.setattr(gen, "OpenAI", _OpenAIFalso(resposta=resposta))
    with pytest.raises(ValueError):
        gen._call_llm("p")


# ---------------------------------------------------------------------------
# _to_class_name / _build_filename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome, esperado", [
    ("calcularDesconto", "CalcularDesconto"),
    ("calcular_desconto", "CalcularDesconto"),
    ("CalcularDesconto", "CalcularDesconto"),
    ("x", "X"),
    ("", ""),
    ("calcular_total_do_pedido", "CalcularTotalDoPedido"),
])
def test_nome_da_classe_em_pascal_case(nome, esperado):
    assert gen._to_class_name(nome) == esperado


@pytest.mark.parametrize("framework, ext, esperado", [
    ("junit5", "js", "CalcTotalTest.java"),
    ("junit4", "js", "CalcTotalTest.java"),
    ("apex-test", "js", "CalcTotalTest.cls"),
    ("jest-rtl", "js", "calcTotal.test.tsx"),
    ("jest", "ts", "calcTotal.test.ts"),
    ("jest", "js", "calcTotal.test.js"),
])
def test_nome_do_arquivo_por_framework(framework, ext, esperado):
    assert gen._build_filename("calcTotal", "CalcTotal", framework, ext) == esperado
