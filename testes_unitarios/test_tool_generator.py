"""
Gerador de tools (tool_generator.py).

A OpenAI nunca é chamada: o cliente `OpenAI` (ou `_call_llm`) é trocado por um falso na
fronteira, e o `input()` do terminal é simulado para aprovar / rejeitar / pedir revisão.
"""

import asyncio
import importlib.util
import json
from types import SimpleNamespace

import pytest

import db
import tool_generator

URL_A = "https://erp.exemplo.local/app/form#cadastro"
URL_B = "https://erp.exemplo.local/app/form#lista"

CORPO_TOOL = '''async def preencher_cadastro(page) -> bool:
    errors: list[tuple[str, str]] = []
    try:
        campo = await localizar(page, xpath='id("nome")', timeout=10000)  # campo nome
        await campo.fill("ABC")
    except Exception as e:
        errors.append(("fill nome", str(e)))
    return not errors
'''


def _acao(tipo="click", url=URL_A, **extra):
    a = {"type": tipo, "url": url, "xpath": 'id("ok")', "css_selector": "", "timestamp": "1000",
         "timeout_ms": 10000}
    a.update(extra)
    return a


# ---------------------------------------------------------------------------
# expects_url_change
# ---------------------------------------------------------------------------

def test_ultima_etapa_nao_precisa_mudar_de_url():
    etapas = [{"actions": [_acao(url=URL_A)]}, {"actions": [_acao(url=URL_B)]}]

    assert tool_generator.expects_url_change(etapas, 1) is False


def test_etapa_seguida_de_outra_tela_precisa_mudar_de_url():
    etapas = [{"actions": [_acao(url=URL_A)]}, {"actions": [_acao(url=URL_B)]}]

    assert tool_generator.expects_url_change(etapas, 0) is True


def test_etapa_seguida_de_navegacao_explicita_nao_precisa_mudar_de_url():
    etapas = [{"actions": [_acao(url=URL_A)]}, {"actions": [_acao("navigation", url=URL_B)]}]

    assert tool_generator.expects_url_change(etapas, 0) is False


def test_etapa_seguida_da_mesma_tela_nao_precisa_mudar_de_url():
    # Só a query string muda (managerId de SPA): continua sendo a mesma tela
    etapas = [{"actions": [_acao(url=URL_A.replace("#", "?m=1#"))]},
              {"actions": [_acao(url=URL_A.replace("#", "?m=2#"))]}]

    assert tool_generator.expects_url_change(etapas, 0) is False


@pytest.mark.parametrize("etapas", [
    [{"actions": []}, {"actions": [_acao(url=URL_B)]}],
    [{"actions": [_acao(url=URL_A)]}, {"actions": None}],
    [{}, {"actions": [_acao(url=URL_B)]}],
])
def test_etapa_sem_acoes_nao_precisa_mudar_de_url(etapas):
    assert tool_generator.expects_url_change(etapas, 0) is False


# ---------------------------------------------------------------------------
# _prompt_actions
# ---------------------------------------------------------------------------

def test_acoes_do_prompt_perdem_campos_internos_e_vazios():
    acoes = [{"type": "fill", "xpath": 'id("x")', "css_selector": "", "value": "ABC", "url": URL_A,
              "timestamp": "123", "masked": False, "masked_id": "g0a0", "target_text": None,
              "timeout_ms": 10000}]

    assert tool_generator._prompt_actions(acoes) == [
        {"type": "fill", "xpath": 'id("x")', "value": "ABC", "url": URL_A, "timeout_ms": 10000}
    ]


def test_acoes_do_prompt_com_secret_nunca_levam_o_valor_gravado():
    acoes = [{"type": "fill", "xpath": 'id("senha")', "value": "********", "secret": "ERP_SENHA",
              "masked": True}]

    saida = tool_generator._prompt_actions(acoes)

    assert saida == [{"type": "fill", "xpath": 'id("senha")', "secret": "ERP_SENHA"}]
    assert acoes[0]["value"] == "********"  # não altera a lista original


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

def test_prompt_traz_nome_assinatura_quantidade_e_todas_as_acoes():
    acoes = [_acao("navigation", url=URL_A), _acao("click", element_text="Confirmar"),
             _acao("key_press", key="Tab", count=3)]

    prompt = tool_generator._build_prompt("confirmar_cadastro", acoes)

    assert "async def confirmar_cadastro(page) -> bool:" in prompt
    assert "exatamente 3 blocos try/except" in prompt
    enviado = json.loads(prompt.split("nenhum pode ser omitido):\n", 1)[1].split("\n\nLembre:")[0])
    assert enviado == tool_generator._prompt_actions(acoes)
    assert "timestamp" not in prompt.split("nenhum pode ser omitido):", 1)[1]


def test_prompt_sem_mudanca_de_url_proibe_comparar_urls():
    prompt = tool_generator._build_prompt("t", [_acao()], expect_url_change=False)

    assert tool_generator._URL_RULE_NONE in prompt
    assert "url_unchanged" not in prompt


def test_prompt_com_mudanca_de_url_exige_comparar_urls():
    prompt = tool_generator._build_prompt("t", [_acao()], expect_url_change=True)

    assert tool_generator._URL_RULE_EXPECT in prompt
    assert tool_generator._URL_RULE_NONE not in prompt


def test_prompt_de_login_verifica_sessao_com_o_primeiro_campo_de_login():
    acoes = [
        _acao("navigation", url=URL_A),
        _acao("fill", xpath='id("usuario")', target_text="Usuário", value="joao"),
        _acao("fill", xpath='id("senha")', target_text="Senha", masked=True, secret="ERP_SENHA"),
        _acao("click", xpath='id("entrar")', element_text="Entrar"),
    ]

    prompt = tool_generator._build_prompt("fazer_login_form", acoes)

    esperado = "login_necessario(page, xpath='id(\"usuario\")', css=None, texto=['Usuário'], timeout=5000)"
    assert esperado in prompt
    assert "confirmar_login(page, xpath='id(\"usuario\")', css=None, texto=['Usuário'], timeout=15000)" in prompt
    assert "[OK] fazer_login_form: sessão já autenticada" in prompt


def test_prompt_sem_login_nao_traz_regra_de_login():
    prompt = tool_generator._build_prompt("t", [_acao("fill", value="ABC"), _acao("click")])

    assert "Validação de login" not in prompt


def test_prompt_recusa_campo_de_senha_sem_secret():
    acoes = [_acao("fill", xpath='id("senha")', value="********", masked=True)]

    with pytest.raises(ValueError, match="sem secret"):
        tool_generator._build_prompt("fazer_login", acoes)


# ---------------------------------------------------------------------------
# _call_llm (cliente OpenAI falso)
# ---------------------------------------------------------------------------

class _OpenAIFalso:
    """Imita OpenAI(api_key=...).chat.completions.create(...) e guarda o que foi enviado."""

    chamadas: list = []
    resposta = ""

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        _OpenAIFalso.chamadas.append({"api_key": self.api_key, **kwargs})
        msg = SimpleNamespace(content=_OpenAIFalso.resposta)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def openai_falso(monkeypatch):
    _OpenAIFalso.chamadas = []
    _OpenAIFalso.resposta = ""
    monkeypatch.setattr(tool_generator, "OpenAI", _OpenAIFalso)
    return _OpenAIFalso


def test_llm_recebe_o_prompt_no_modelo_gpt4o(openai_falso, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "chave-falsa")
    openai_falso.resposta = "  async def t(page) -> bool:\n    return True  \n"

    codigo = tool_generator._call_llm("PROMPT X")

    assert codigo == "async def t(page) -> bool:\n    return True"
    (chamada,) = openai_falso.chamadas
    assert chamada["model"] == "gpt-4o"
    assert chamada["messages"] == [{"role": "user", "content": "PROMPT X"}]
    assert chamada["api_key"] == "chave-falsa"
    assert chamada["max_tokens"] == 8192


@pytest.mark.parametrize("resposta", [
    "```python\nasync def t(page) -> bool:\n    return True\n```",
    "```\nasync def t(page) -> bool:\n    return True\n```",
    "```python\nasync def t(page) -> bool:\n    return True\n```\nEssa função faz o login.",
    "```python\nasync def t(page) -> bool:\n    return True",
])
def test_llm_tira_as_cercas_markdown_do_codigo(openai_falso, resposta):
    openai_falso.resposta = resposta

    assert tool_generator._call_llm("p") == "async def t(page) -> bool:\n    return True"


def test_llm_tira_texto_explicativo_antes_da_cerca(openai_falso):
    openai_falso.resposta = "Aqui está a função:\n```python\nasync def t(page) -> bool:\n    return True\n```"

    assert tool_generator._call_llm("p") == "async def t(page) -> bool:\n    return True"


# ---------------------------------------------------------------------------
# _save_tool
# ---------------------------------------------------------------------------

def test_tool_salva_tem_cabecalho_da_convencao_e_e_importavel(tmp_path):
    caminho = tool_generator._save_tool("preencher_cadastro", CORPO_TOOL, fingerprint="abc123def456")

    assert caminho == tool_generator.TOOLS_DIR / "preencher_cadastro.py"
    linhas = (tmp_path / "tools" / "preencher_cadastro.py").read_text(encoding="utf-8").splitlines()
    assert linhas[0] == "# actions_fp: abc123def456"
    assert linhas[1] == "from playwright.async_api import Page"
    assert linhas[2] == "from rpa_runtime import confirmar_login, localizar, login_necessario, secret"
    assert "async def preencher_cadastro(page) -> bool:" in linhas

    # O cabeçalho importa de verdade os nomes que as tools usam
    spec = importlib.util.spec_from_file_location("preencher_cadastro", tmp_path / "tools" / "preencher_cadastro.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    for nome in ("Page", "localizar", "login_necessario", "confirmar_login", "secret"):
        assert hasattr(modulo, nome)


def test_tool_salva_vai_ao_banco_com_o_mesmo_codigo_do_disco(monkeypatch, tmp_path):
    gravados = []
    monkeypatch.setattr(db, "upsert_tool", lambda nome, codigo, fp="": gravados.append((nome, codigo, fp)))

    tool_generator._save_tool("t", CORPO_TOOL, fingerprint="fp1")

    disco = (tmp_path / "tools" / "t.py").read_text(encoding="utf-8")
    assert gravados == [("t", disco, "fp1")]


def test_tool_nao_vai_ao_disco_quando_o_banco_falha(monkeypatch, tmp_path):
    def falha(*_a, **_k):
        raise db.DBError("banco fora")

    monkeypatch.setattr(db, "upsert_tool", falha)

    with pytest.raises(db.DBError):
        tool_generator._save_tool("t", CORPO_TOOL, fingerprint="fp1")
    assert not (tmp_path / "tools" / "t.py").exists()


def test_tool_sem_fingerprint_comeca_pelos_imports(tmp_path):
    tool_generator._save_tool("t", CORPO_TOOL)

    linhas = (tmp_path / "tools" / "t.py").read_text(encoding="utf-8").splitlines()
    assert linhas[0] == "from playwright.async_api import Page"


# ---------------------------------------------------------------------------
# review_in_terminal (aprovação pelo terminal)
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_falso(monkeypatch):
    """Troca _call_llm: devolve 'codigo_1', 'codigo_2', ... e guarda os prompts."""
    prompts = []

    def falso(prompt):
        prompts.append(prompt)
        return f"codigo_{len(prompts)}"

    monkeypatch.setattr(tool_generator, "_call_llm", falso)
    return prompts


def _respostas(monkeypatch, *respostas):
    fila = list(respostas)
    perguntas = []

    def falso(pergunta=""):
        perguntas.append(pergunta)
        return fila.pop(0)

    monkeypatch.setattr("builtins.input", falso)
    return perguntas


def test_revisao_aprovada_devolve_o_codigo_gerado(llm_falso, monkeypatch):
    _respostas(monkeypatch, " A ")

    assert tool_generator.review_in_terminal("t", "PROMPT") == "codigo_1"
    assert llm_falso == ["PROMPT"]


def test_revisao_rejeitada_devolve_none(llm_falso, monkeypatch, capsys):
    _respostas(monkeypatch, "r")

    assert tool_generator.review_in_terminal("t", "PROMPT") is None
    assert "Rejeitada: t" in capsys.readouterr().out


def test_pedido_de_revisao_regera_com_todo_o_feedback_acumulado(llm_falso, monkeypatch):
    _respostas(monkeypatch, "v", "use o botão OK", "v", "", "v", "espere a tela", "a")

    assert tool_generator.review_in_terminal("t", "PROMPT") == "codigo_4"
    assert len(llm_falso) == 4
    assert llm_falso[1].startswith("PROMPT\n\nO usuário pediu")
    assert llm_falso[1].endswith("- use o botão OK")
    assert llm_falso[2].endswith("- use o botão OK")  # feedback vazio não entra
    assert llm_falso[3].endswith("- use o botão OK\n- espere a tela")


def test_opcao_desconhecida_pergunta_de_novo(llm_falso, monkeypatch):
    perguntas = _respostas(monkeypatch, "x", "", "a")

    assert tool_generator.review_in_terminal("t", "PROMPT") == "codigo_1"
    assert len(perguntas) == 3
    assert len(llm_falso) == 1


# ---------------------------------------------------------------------------
# generate_tool_from_actions / generate_tool_from_agent_result
# ---------------------------------------------------------------------------

def test_tool_aprovada_a_partir_das_acoes_e_salva_com_fingerprint(llm_falso, monkeypatch, tmp_path):
    _respostas(monkeypatch, "a")

    caminho = tool_generator.generate_tool_from_actions("t", [_acao()], fingerprint="fp9",
                                                        expect_url_change=True)

    assert caminho == tool_generator.TOOLS_DIR / "t.py"
    texto = (tmp_path / "tools" / "t.py").read_text(encoding="utf-8")
    assert texto.startswith("# actions_fp: fp9\n")
    assert texto.endswith("codigo_1")
    assert tool_generator._URL_RULE_EXPECT in llm_falso[0]


def test_tool_rejeitada_a_partir_das_acoes_nao_e_salva(llm_falso, monkeypatch, tmp_path):
    _respostas(monkeypatch, "r")

    assert tool_generator.generate_tool_from_actions("t", [_acao()], fingerprint="fp9") is None
    assert list((tmp_path / "tools").iterdir()) == []


def test_tool_com_senha_sem_secret_nao_chega_ao_llm(llm_falso, monkeypatch):
    _respostas(monkeypatch)

    with pytest.raises(ValueError):
        tool_generator.generate_tool_from_actions("t", [_acao("fill", value="****", masked=True)])
    assert llm_falso == []


class _ResultadoAgente:
    def model_actions(self):
        return [{"click_element": {"index": 3}}, {"input_text": {"index": 4, "text": "ABC"}}]


def test_tool_do_agente_aprovada_e_salva_com_todas_as_acoes_no_prompt(llm_falso, monkeypatch, tmp_path):
    _respostas(monkeypatch, "a")

    caminho = asyncio.run(tool_generator.generate_tool_from_agent_result("login_erp", _ResultadoAgente()))

    assert caminho == tool_generator.TOOLS_DIR / "login_erp.py"
    assert (tmp_path / "tools" / "login_erp.py").read_text(encoding="utf-8").endswith("codigo_1")
    prompt = llm_falso[0]
    assert "async def login_erp(page) -> bool:" in prompt
    assert "exatamente 2 blocos" in prompt
    assert '"type": "agent_action"' in prompt
    assert '\\"input_text\\": {\\"index\\": 4, \\"text\\": \\"ABC\\"}' in prompt


def test_tool_do_agente_rejeitada_nao_e_salva(llm_falso, monkeypatch, tmp_path):
    _respostas(monkeypatch, "r")

    assert asyncio.run(tool_generator.generate_tool_from_agent_result("login_erp", _ResultadoAgente())) is None
    assert list((tmp_path / "tools").iterdir()) == []


def test_tool_do_agente_tem_fingerprint_no_cabecalho(llm_falso, monkeypatch, tmp_path):
    _respostas(monkeypatch, "a")

    asyncio.run(tool_generator.generate_tool_from_agent_result("login_erp", _ResultadoAgente()))

    primeira = (tmp_path / "tools" / "login_erp.py").read_text(encoding="utf-8").splitlines()[0]
    assert primeira.startswith("# actions_fp: ") and primeira.split(":", 1)[1].strip()



class _OutroResultadoAgente:
    def model_actions(self):
        return [{"click_element": {"index": 7}}, {"input_text": {"index": 8, "text": "XYZ"}}]


def test_tool_do_agente_com_acoes_diferentes_tem_fingerprint_diferente(llm_falso, monkeypatch, tmp_path):
    """O fingerprint decide se a tool é reaproveitada: ações diferentes não podem colidir."""
    _respostas(monkeypatch, "a", "a")

    asyncio.run(tool_generator.generate_tool_from_agent_result("tarefa_a", _ResultadoAgente()))
    asyncio.run(tool_generator.generate_tool_from_agent_result("tarefa_b", _OutroResultadoAgente()))

    fp_a = (tmp_path / "tools" / "tarefa_a.py").read_text(encoding="utf-8").splitlines()[0]
    fp_b = (tmp_path / "tools" / "tarefa_b.py").read_text(encoding="utf-8").splitlines()[0]
    assert fp_a.startswith("# actions_fp: ") and fp_b.startswith("# actions_fp: ")
    assert fp_a != fp_b