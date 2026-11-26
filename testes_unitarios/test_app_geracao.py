"""
Geração de tools com revisão interativa: /api/generate-tools (SSE) e /api/review-response.

O LLM é sempre um falso (tool_generator._call_llm trocado via monkeypatch) — nenhum teste
chama a OpenAI. O resto (prompt, fingerprint, gravação da tool em tools/) é o código real,
com tools/ apontando para a pasta temporária do teste (conftest).
"""

import json
import queue
import threading
import time

import pytest

import app as app_mod
import db
import gravacoes_store
import secrets_store
import tool_generator
from db import DBError
from json_processor import _actions_fingerprint, group_steps, name_steps, resolve_tool_names
from testes_unitarios.gravacao_exemplo import gravacao

URL_A = "https://erp.exemplo.local/app/form"
URL_B = "https://erp.exemplo.local/app/lista"
_CABECALHO = (
    "from playwright.async_api import Page\n"
    "from rpa_runtime import confirmar_login, localizar, login_necessario, secret\n\n"
)


# ---------------------------------------------------------------------------
# Fixtures e auxiliares
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def geracoes_limpas():
    app_mod._generations.clear()
    app_mod._reserved_tool_names.clear()
    yield
    # Libera geração que algum teste tenha deixado esperando revisão
    for estado in list(app_mod._generations.values()):
        estado["event"].set()
    app_mod._generations.clear()
    app_mod._reserved_tool_names.clear()


class LLMFalso:
    """Devolve as respostas da fila (str, ou Exception para simular falha) e guarda os prompts."""

    def __init__(self):
        self.prompts: list[str] = []
        self.respostas: list = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        r = self.respostas.pop(0) if self.respostas else f"async def gerada(page) -> bool:\n    return True  # {len(self.prompts)}\n"
        if isinstance(r, Exception):
            raise r
        return r() if callable(r) else r


@pytest.fixture
def llm(monkeypatch):
    falso = LLMFalso()
    monkeypatch.setattr(tool_generator, "_call_llm", falso)
    return falso


@pytest.fixture
def revisao_expira_logo(monkeypatch):
    """Sem ninguém respondendo, cada revisão expira em 50 ms (o stream termina sozinho)."""
    monkeypatch.setattr(app_mod, "REVIEW_TIMEOUT", 0.05)
    monkeypatch.setattr(app_mod, "SSE_HEARTBEAT", 0.01)


class LeitorSSE:
    """Lê o stream numa thread, para o teste responder a revisão enquanto a geração espera."""

    def __init__(self, resp):
        self.resp = resp
        self.fila: queue.Queue = queue.Queue()
        self.vistos: list[str] = []
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        try:
            for chunk in self.resp.response:
                texto = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                for parte in texto.split("\n\n"):
                    if parte:
                        self.fila.put(parte)
        finally:
            self.fila.put(None)

    def esperar(self, prefixo: str, timeout: float = 10) -> str:
        limite = time.time() + timeout
        while True:
            try:
                item = self.fila.get(timeout=max(0.01, limite - time.time()))
            except queue.Empty:
                raise AssertionError(f"evento {prefixo!r} não chegou; vistos: {self.vistos}")
            if item is None:
                raise AssertionError(f"stream terminou sem {prefixo!r}; vistos: {self.vistos}")
            self.vistos.append(item)
            if item.startswith("data: " + prefixo):
                return item[len("data: " + prefixo):]

    def ate_o_fim(self, timeout: float = 10) -> list[str]:
        """Dados de todos os eventos vistos (sem pings), do começo ao fim do stream."""
        limite = time.time() + timeout
        while True:
            try:
                item = self.fila.get(timeout=max(0.01, limite - time.time()))
            except queue.Empty:
                raise AssertionError(f"stream não terminou; vistos: {self.vistos}")
            if item is None:
                return [v[len("data: "):] for v in self.vistos if v.startswith("data: ")]
            self.vistos.append(item)


def _eventos(texto: str) -> list[str]:
    return [p[len("data: "):] for p in texto.split("\n\n") if p.startswith("data: ")]


def _acoes(xpath: str = 'id("ok")', url: str = URL_A, valor: str | None = None) -> list[dict]:
    acoes = [{"type": "click", "xpath": xpath, "url": url, "target_text": "Confirmar"}]
    if valor is not None:
        acoes.insert(0, {"type": "input", "xpath": 'id("campo")', "url": url, "value": valor})
    return acoes


def _step(nome: str, **kw) -> dict:
    return {"tool_name": nome, "actions": _acoes(**kw)}


def _iniciar(client, named_steps, **extra) -> tuple[LeitorSSE, str]:
    resp = client.post("/api/generate-tools", json={"named_steps": named_steps, **extra}, buffered=False)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    leitor = LeitorSSE(resp)
    return leitor, leitor.esperar("__GEN_ID__")


def _responder(client, gen_id: str, action: str, feedback: str | None = None, timeout: float = 5):
    """
    Responde a revisão. Logo depois do __REVIEW__ a geração ainda pode não ter entrado na
    espera (o evento chega antes): até lá o endpoint responde 409 e o teste tenta de novo.
    """
    corpo = {"gen_id": gen_id, "action": action}
    if feedback is not None:
        corpo["feedback"] = feedback
    limite = time.time() + timeout
    while True:
        r = client.post("/api/review-response", json=corpo)
        if r.status_code != 409 or time.time() > limite:
            return r
        time.sleep(0.01)


def _revisao(leitor: LeitorSSE) -> dict:
    return json.loads(leitor.esperar("__REVIEW__"))


# ---------------------------------------------------------------------------
# Fluxo de aprovação
# ---------------------------------------------------------------------------

def test_tool_aprovada_e_salva_com_fingerprint_e_cabecalho(client, tmp_path, llm):
    llm.respostas = ["async def preencher_form(page) -> bool:\n    return True"]
    leitor, gen_id = _iniciar(client, [_step("preencher_form", valor="ABC")])

    revisao = _revisao(leitor)
    antes_de_aprovar = (tmp_path / "tools" / "preencher_form.py").exists()
    r = _responder(client, gen_id, "approve")
    eventos = leitor.ate_o_fim()

    assert len(gen_id) == 12
    assert revisao == {"name": "preencher_form", "code": "async def preencher_form(page) -> bool:\n    return True"}
    assert antes_de_aprovar is False
    assert r.json == {"ok": True}
    assert eventos == ["__GEN_ID__" + gen_id, "__GENERATING__preencher_form",
                       "__REVIEW__" + json.dumps(revisao, ensure_ascii=False),
                       "__APPROVED__preencher_form", "__GENERATE_DONE__"]
    fp = _actions_fingerprint(_acoes(valor="ABC"))
    assert (tmp_path / "tools" / "preencher_form.py").read_text(encoding="utf-8") == (
        f"# actions_fp: {fp}\n" + _CABECALHO + "async def preencher_form(page) -> bool:\n    return True")
    assert app_mod._generations == {}
    assert app_mod._reserved_tool_names == set()


def test_prompt_leva_o_nome_e_as_acoes_da_tool(client, llm, revisao_expira_logo):
    client.post("/api/generate-tools", json={"named_steps": [_step("clicar_ok", xpath='id("botao_x")')]}).get_data()

    assert "clicar_ok" in llm.prompts[0]
    assert 'id(\\"botao_x\\")' in llm.prompts[0]


def test_prompt_avisa_quando_a_etapa_deve_terminar_em_outra_tela(client, monkeypatch, llm, revisao_expira_logo):
    pedidos = []
    original = tool_generator._build_prompt

    def gravar(nome, acoes, expect_url_change=False):
        pedidos.append((nome, expect_url_change))
        return original(nome, acoes, expect_url_change=expect_url_change)
    monkeypatch.setattr(tool_generator, "_build_prompt", gravar)

    client.post("/api/generate-tools", json={"named_steps": [
        _step("etapa_form", url=URL_A), _step("etapa_lista", url=URL_B)]}).get_data()

    assert pedidos == [("etapa_form", True), ("etapa_lista", False)]


def test_campo_de_senha_vai_para_o_llm_como_secret_e_nunca_como_valor(client, llm, revisao_expira_logo):
    secrets_store.set_secret("ERP_SENHA", "valor-sintetico")
    step = {"tool_name": "fazer_login", "actions": [
        {"type": "input", "xpath": 'id("senha")', "url": URL_A, "value": "********", "masked": True,
         "target_text": "SENHA"},
        {"type": "click", "xpath": 'id("entrar")', "url": URL_A},
    ]}

    r = client.post("/api/generate-tools", json={"named_steps": [step], "secrets_map": {"g0a0": "ERP_SENHA"}})
    r.get_data()

    assert r.status_code == 200
    assert "ERP_SENHA" in llm.prompts[0]
    assert "********" not in llm.prompts[0]
    assert "valor-sintetico" not in llm.prompts[0]


# ---------------------------------------------------------------------------
# Rejeição, revisão e timeout
# ---------------------------------------------------------------------------

def test_tool_rejeitada_nao_fica_no_disco_e_a_geracao_segue_para_a_proxima(client, tmp_path, llm):
    leitor, gen_id = _iniciar(client, [_step("primeira", xpath="a"), _step("segunda", xpath="b")])

    assert _revisao(leitor)["name"] == "primeira"
    _responder(client, gen_id, "reject")
    assert leitor.esperar("__REJECTED__") == "primeira"
    assert _revisao(leitor)["name"] == "segunda"
    _responder(client, gen_id, "approve")
    eventos = leitor.ate_o_fim()

    assert eventos[-2:] == ["__APPROVED__segunda", "__GENERATE_DONE__"]
    assert not (tmp_path / "tools" / "primeira.py").exists()
    assert (tmp_path / "tools" / "segunda.py").exists()


def test_revisoes_acumulam_o_feedback_e_salva_o_ultimo_codigo(client, tmp_path, llm):
    llm.respostas = ["codigo = 1", "codigo = 2", "codigo = 3", "codigo = 4"]
    leitor, gen_id = _iniciar(client, [_step("revisada")])

    assert _revisao(leitor)["code"] == "codigo = 1"
    _responder(client, gen_id, "revise", "  usar Enter no campo  ")
    assert leitor.esperar("__REVISING__") == "revisada"
    assert _revisao(leitor)["code"] == "codigo = 2"
    _responder(client, gen_id, "revise", "   ")  # feedback vazio não entra na lista
    assert _revisao(leitor)["code"] == "codigo = 3"
    _responder(client, gen_id, "revise", "esperar mais")
    assert _revisao(leitor)["code"] == "codigo = 4"
    _responder(client, gen_id, "approve")
    leitor.ate_o_fim()

    base = llm.prompts[0]
    nota = "\n\nO usuário pediu as seguintes revisões (aplique TODAS):\n"
    assert llm.prompts[1] == base + nota + "- usar Enter no campo"
    assert llm.prompts[2] == base + nota + "- usar Enter no campo"
    assert llm.prompts[3] == base + nota + "- usar Enter no campo\n- esperar mais"
    assert (tmp_path / "tools" / "revisada.py").read_text(encoding="utf-8").endswith("codigo = 4")


def test_revisao_sem_resposta_expira_nao_salva_e_a_geracao_termina(client, monkeypatch, tmp_path, llm):
    monkeypatch.setattr(app_mod, "REVIEW_TIMEOUT", 0.3)
    monkeypatch.setattr(app_mod, "SSE_HEARTBEAT", 0.05)

    inicio = time.time()
    corpo = client.post("/api/generate-tools", json={"named_steps": [_step("esquecida")]}).get_data(as_text=True)

    assert time.time() - inicio < 5
    assert ": ping\n\n" in corpo
    assert _eventos(corpo)[-2:] == ["__REVIEW_TIMEOUT__esquecida", "__GENERATE_DONE__"]
    assert not (tmp_path / "tools" / "esquecida.py").exists()
    assert app_mod._generations == {}
    assert app_mod._reserved_tool_names == set()


def test_resposta_atrasada_enquanto_a_proxima_tool_e_gerada_e_recusada(client, monkeypatch, llm):
    monkeypatch.setattr(app_mod, "REVIEW_TIMEOUT", 0.2)
    monkeypatch.setattr(app_mod, "SSE_HEARTBEAT", 0.05)
    gerando, liberar = threading.Event(), threading.Event()

    def llm_lento():
        gerando.set()
        liberar.wait(5)
        return "codigo = 2"
    llm.respostas = ["codigo = 1", llm_lento]
    leitor, gen_id = _iniciar(client, [_step("expira", xpath="a"), _step("proxima", xpath="b")])
    assert leitor.esperar("__REVIEW_TIMEOUT__") == "expira"
    assert gerando.wait(5)

    atrasada = client.post("/api/review-response", json={"gen_id": gen_id, "action": "approve"})
    liberar.set()
    leitor.ate_o_fim()

    # A resposta chegou depois do prazo da "expira" e antes da revisão da "proxima": não vale para nenhuma
    assert atrasada.status_code == 409
    assert leitor.vistos[-2] == "data: __REVIEW_TIMEOUT__proxima"


def test_aprovacao_da_tool_anterior_nao_vale_para_a_proxima(client, monkeypatch, tmp_path, llm):
    monkeypatch.setattr(app_mod, "REVIEW_TIMEOUT", 1.0)
    monkeypatch.setattr(app_mod, "SSE_HEARTBEAT", 0.05)
    leitor, gen_id = _iniciar(client, [_step("aprovada", xpath="a"), _step("sem_resposta", xpath="b")])

    _revisao(leitor)
    _responder(client, gen_id, "approve")
    assert leitor.esperar("__APPROVED__") == "aprovada"
    eventos = leitor.ate_o_fim()

    assert eventos[-2:] == ["__REVIEW_TIMEOUT__sem_resposta", "__GENERATE_DONE__"]
    assert not (tmp_path / "tools" / "sem_resposta.py").exists()


def test_resposta_depois_do_timeout_e_recusada(client, llm, revisao_expira_logo):
    resp = client.post("/api/generate-tools", json={"named_steps": [_step("tarde")]})
    gen_id = _eventos(resp.get_data(as_text=True))[0][len("__GEN_ID__"):]

    r = client.post("/api/review-response", json={"gen_id": gen_id, "action": "approve"})

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Erros do LLM e do banco
# ---------------------------------------------------------------------------

def test_llm_que_falha_descarta_so_aquela_tool(client, llm, revisao_expira_logo):
    llm.respostas = [RuntimeError("LLM fora do ar")]

    corpo = client.post("/api/generate-tools", json={"named_steps": [
        _step("falha_llm", xpath="a"), _step("depois", xpath="b")]}).get_data(as_text=True)
    eventos = _eventos(corpo)

    assert "__GEN_ERROR__" + json.dumps({"name": "falha_llm", "error": "LLM fora do ar"}) in eventos
    assert "__GENERATING__depois" in eventos
    assert eventos[-1] == "__GENERATE_DONE__"


def test_llm_que_falha_na_revisao_descarta_a_tool(client, tmp_path, llm):
    llm.respostas = ["codigo = 1", RuntimeError("cota excedida")]
    leitor, gen_id = _iniciar(client, [_step("revisao_falha")])

    _revisao(leitor)
    _responder(client, gen_id, "revise", "mudar algo")
    eventos = leitor.ate_o_fim()

    assert eventos[-3:] == ["__REVISING__revisao_falha",
                            "__GEN_ERROR__" + json.dumps({"name": "revisao_falha", "error": "cota excedida"}),
                            "__GENERATE_DONE__"]
    assert not (tmp_path / "tools" / "revisao_falha.py").exists()


def test_banco_que_falha_ao_aprovar_nao_grava_no_disco(client, monkeypatch, tmp_path, llm):
    def banco_fora(*a, **k):
        raise DBError("banco caiu")
    monkeypatch.setattr(db, "upsert_tool", banco_fora)
    leitor, gen_id = _iniciar(client, [_step("sem_banco")])

    _revisao(leitor)
    _responder(client, gen_id, "approve")
    eventos = leitor.ate_o_fim()

    assert eventos[-2:] == ["__GEN_ERROR__" + json.dumps({"name": "sem_banco", "error": "banco caiu"}),
                            "__GENERATE_DONE__"]
    assert not (tmp_path / "tools" / "sem_banco.py").exists()


# ---------------------------------------------------------------------------
# Versionamento e reserva de nomes
# ---------------------------------------------------------------------------

def test_tool_com_fingerprint_identico_e_reaproveitada_sem_chamar_o_llm(client, tmp_path, llm):
    acoes = _acoes()
    original = f"# actions_fp: {_actions_fingerprint(acoes)}\n# conteúdo original\n"
    (tmp_path / "tools" / "ja_existe.py").write_text(original, encoding="utf-8")

    corpo = client.post("/api/generate-tools", json={"named_steps": [{"tool_name": "ja_existe", "actions": acoes}]})
    eventos = _eventos(corpo.get_data(as_text=True))

    assert eventos[1:] == ["__SKIP__ja_existe", "__GENERATE_DONE__"]
    assert llm.prompts == []
    assert (tmp_path / "tools" / "ja_existe.py").read_text(encoding="utf-8") == original


def test_tool_existente_com_acoes_diferentes_gera_v2_sem_tocar_a_original(client, tmp_path, llm):
    original = "# actions_fp: outrofp00000\n# conteúdo original\n"
    (tmp_path / "tools" / "mesma.py").write_text(original, encoding="utf-8")
    leitor, gen_id = _iniciar(client, [_step("mesma")])

    assert leitor.esperar("__GENERATING__") == "mesma_v2"
    _responder(client, gen_id, "approve")
    leitor.ate_o_fim()

    assert (tmp_path / "tools" / "mesma_v2.py").exists()
    assert (tmp_path / "tools" / "mesma.py").read_text(encoding="utf-8") == original


def test_geracoes_simultaneas_nao_escolhem_o_mesmo_nome(client, tmp_path, llm):
    a, gen_a = _iniciar(client, [_step("nova_tool", xpath="a")])
    assert a.esperar("__GENERATING__") == "nova_tool"
    reservados_durante = set(app_mod._reserved_tool_names)

    b, gen_b = _iniciar(client, [_step("nova_tool", xpath="b")])
    nome_b = b.esperar("__GENERATING__")

    _revisao(a)
    _revisao(b)
    _responder(client, gen_a, "approve")
    _responder(client, gen_b, "approve")
    a.ate_o_fim()
    b.ate_o_fim()

    assert reservados_durante == {"nova_tool"}
    assert nome_b == "nova_tool_v2"
    assert (tmp_path / "tools" / "nova_tool.py").exists()
    assert (tmp_path / "tools" / "nova_tool_v2.py").exists()
    assert app_mod._reserved_tool_names == set()
    assert app_mod._generations == {}


def test_cada_geracao_tem_a_sua_revisao(client, tmp_path, llm):
    llm.respostas = ["codigo_a = 1", "codigo_b = 1"]
    a, gen_a = _iniciar(client, [_step("da_aba_a", xpath="a")])
    _revisao(a)
    b, gen_b = _iniciar(client, [_step("da_aba_b", xpath="b")])
    _revisao(b)

    _responder(client, gen_b, "reject")
    _responder(client, gen_a, "approve")

    assert a.ate_o_fim()[-2] == "__APPROVED__da_aba_a"
    assert b.ate_o_fim()[-2] == "__REJECTED__da_aba_b"
    assert gen_a != gen_b


def test_aba_fechada_no_meio_da_revisao_libera_a_geracao_e_o_nome(client, tmp_path, llm):
    resp = client.post("/api/generate-tools", json={"named_steps": [_step("abandonada")]}, buffered=False)
    for chunk in resp.response:
        if b"__REVIEW__" in chunk:
            break
    durante = (dict(app_mod._generations), set(app_mod._reserved_tool_names))

    resp.close()

    assert len(durante[0]) == 1 and durante[1] == {"abandonada"}
    assert app_mod._generations == {}
    assert app_mod._reserved_tool_names == set()
    assert not (tmp_path / "tools" / "abandonada.py").exists()


# ---------------------------------------------------------------------------
# Entrada: filename ou named_steps, secrets e validação
# ---------------------------------------------------------------------------

def test_gera_a_partir_de_uma_gravacao_salva(client, pasta_json, llm, revisao_expira_logo):
    (pasta_json / "pedido.json").write_text(json.dumps(gravacao()), encoding="utf-8")
    esperado = name_steps(group_steps(gravacao()["steps"]))
    resolve_tool_names(esperado, app_mod.TOOLS_DIR, set())

    eventos = _eventos(client.post("/api/generate-tools", json={"filename": "pedido.json"}).get_data(as_text=True))

    gerados = [e[len("__GENERATING__"):] for e in eventos if e.startswith("__GENERATING__")]
    assert gerados == [s["tool_name"] for s in esperado]
    assert len(llm.prompts) == len(esperado)


@pytest.mark.parametrize("corpo, status, mensagem", [
    ({}, 400, "Nenhum step para processar"),
    ({"named_steps": []}, 400, "Nenhum step para processar"),
    ({"named_steps": {"tool_name": "x"}}, 400, "Nenhum step para processar"),
    ({"filename": "../segredo.json"}, 400, "Nome de arquivo inválido"),
    ({"filename": "pedido.txt"}, 400, "Nome de arquivo inválido"),
    ({"filename": "nao_existe.json"}, 404, "Arquivo não encontrado"),
    ({"named_steps": [{"tool_name": "1-nome-ruim", "actions": []}]}, 400, "Nome de tool inválido"),
    ({"named_steps": [{"tool_name": None, "actions": []}]}, 400, "Nome de tool inválido"),
    ({"named_steps": [_step("ok")], "secrets_map": {"g0a0": "NAO_CADASTRADO"}}, 400, "Secret não cadastrado: NAO_CADASTRADO"),
    ({"named_steps": [{"tool_name": "login", "actions": [
        {"type": "input", "xpath": "x", "url": URL_A, "value": "********", "masked": True, "target_text": "SENHA"}]}]},
     400, "Campos de senha sem secret definido: SENHA"),
])
def test_pedido_invalido_e_recusado_sem_chamar_o_llm_nem_reservar_nome(client, llm, corpo, status, mensagem):
    r = client.post("/api/generate-tools", json=corpo)

    assert r.status_code == status
    assert mensagem in r.json["error"]
    assert llm.prompts == []
    assert app_mod._generations == {}
    assert app_mod._reserved_tool_names == set()


def test_gravacao_com_json_corrompido_e_recusada(client, pasta_json, llm):
    (pasta_json / "quebrado.json").write_text("{nao e json", encoding="utf-8")

    r = client.post("/api/generate-tools", json={"filename": "quebrado.json"})

    assert r.status_code == 400
    assert "JSON inválido" in r.json["error"]


def test_gravacao_fora_do_formato_e_recusada(client, pasta_json, llm):
    (pasta_json / "lista.json").write_text("[1, 2, 3]", encoding="utf-8")

    r = client.post("/api/generate-tools", json={"filename": "lista.json"})

    assert r.status_code == 400
    assert "steps" in r.json["error"]


def test_banco_fora_do_ar_ao_carregar_a_gravacao_responde_500(client, monkeypatch, llm):
    def banco_fora(nome):
        raise DBError("Erro no banco de dados (get_gravacao)")
    monkeypatch.setattr(gravacoes_store, "carregar", banco_fora)

    r = client.post("/api/generate-tools", json={"filename": "pedido.json"})

    assert r.status_code == 500
    assert "banco" in r.json["error"]


@pytest.mark.xfail(strict=True, reason="BUG: named_steps sem 'tool_name' ou sem 'actions' derruba com 500 "
                                        "(KeyError em resolve_tool_names) em vez de 400")
@pytest.mark.parametrize("step", [{"actions": []}, {"tool_name": "sem_acoes"}])
def test_step_sem_nome_ou_sem_acoes_e_recusado_com_400(client, llm, step):
    r = client.post("/api/generate-tools", json={"named_steps": [step]})

    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/review-response
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("acao", [None, "", "aprovar", "APPROVE"])
def test_resposta_com_acao_invalida_e_recusada(client, acao):
    r = client.post("/api/review-response", json={"gen_id": "qualquer", "action": acao})

    assert r.status_code == 400
    assert r.json["error"] == "Ação inválida"


@pytest.mark.parametrize("gen_id", [None, "", "naoexiste000"])
def test_resposta_para_geracao_inexistente_da_404(client, gen_id):
    r = client.post("/api/review-response", json={"gen_id": gen_id, "action": "approve"})

    assert r.status_code == 404


def test_resposta_para_geracao_que_nao_esta_esperando_da_409_e_nao_e_guardada(client):
    estado = {"event": threading.Event(), "response": None, "awaiting": False}
    app_mod._generations["gen000000001"] = estado

    r = client.post("/api/review-response", json={"gen_id": "gen000000001", "action": "approve"})

    assert r.status_code == 409
    assert estado["response"] is None
    assert not estado["event"].is_set()


def test_resposta_valida_acorda_a_geracao_com_a_acao_e_o_feedback(client):
    estado = {"event": threading.Event(), "response": None, "awaiting": True}
    sem_feedback = {"event": threading.Event(), "response": None, "awaiting": True}
    app_mod._generations.update({"gen000000002": estado, "gen000000003": sem_feedback})

    r = client.post("/api/review-response", json={"gen_id": "gen000000002", "action": "revise",
                                                   "feedback": "usar Tab"})
    client.post("/api/review-response", json={"gen_id": "gen000000003", "action": "reject"})

    assert r.json == {"ok": True}
    assert estado["response"] == {"action": "revise", "feedback": "usar Tab"}
    assert estado["awaiting"] is False
    assert estado["event"].is_set()
    assert sem_feedback["response"] == {"action": "reject", "feedback": ""}


def test_segunda_resposta_para_a_mesma_revisao_e_recusada(client):
    app_mod._generations["gen000000004"] = {"event": threading.Event(), "response": None, "awaiting": True}

    primeira = client.post("/api/review-response", json={"gen_id": "gen000000004", "action": "approve"})
    segunda = client.post("/api/review-response", json={"gen_id": "gen000000004", "action": "reject"})

    assert primeira.status_code == 200
    assert segunda.status_code == 409
    assert app_mod._generations["gen000000004"]["response"]["action"] == "approve"


# ---------------------------------------------------------------------------
# Banco de teste: tool aprovada vai para a tabela tools, rejeitada não
# ---------------------------------------------------------------------------

def test_tool_aprovada_vai_para_o_banco_e_rejeitada_nao(pg, client, llm):
    leitor, gen_id = _iniciar(client, [_step("vai_banco", xpath="a"), _step("nao_vai", xpath="b")])
    _revisao(leitor)
    _responder(client, gen_id, "approve")
    _revisao(leitor)
    _responder(client, gen_id, "reject")
    leitor.ate_o_fim()

    tools = {t["name"]: t for t in db.get_all_tools()}
    assert set(tools) == {"vai_banco"}
    assert db.get_tool_fingerprint("vai_banco") == _actions_fingerprint(_acoes(xpath="a"))


def test_banco_fora_do_ar_ao_conferir_os_secrets_responde_500_e_nao_secret_nao_cadastrado(
        client, monkeypatch, llm):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:s@127.0.0.1:1/rpa_teste")

    r = client.post("/api/generate-tools",
                    json={"named_steps": [_step("ok")], "secrets_map": {"g0a0": "ERP_SENHA"}})

    assert r.status_code == 500
    assert "banco" in r.json["error"].lower()
    assert "não cadastrado" not in r.json["error"]
    assert llm.prompts == []
    assert app_mod._reserved_tool_names == set()
