"""
Senhas gravadas como ******** (CLAUDE.md): nunca vão para o código. Cada campo mascarado ganha
um id estável (annotate_masked), recebe um secret (apply_secrets) e, na linha de comando, o
usuário escolhe/cadastra o secret (resolve_secrets_in_terminal).
"""

import builtins
import getpass

import pytest

import json_processor as jp
import secrets_store

URL = "https://erp.exemplo.local/app/login.jsp"


def fill(alvo, valor="x", url=URL, **extra):
    a = {"type": "fill", "xpath": f'id("{alvo.lower()}")', "css_selector": "", "target_text": alvo,
         "value": valor, "url": url, "timestamp": 1}
    a.update(extra)
    return a


def senha(alvo="Senha", url=URL):
    return fill(alvo, "********", url=url, masked=True)


def click(alvo="Entrar"):
    return {"type": "click", "xpath": 'id("ok")', "css_selector": "", "target_text": alvo,
            "element_text": "", "url": URL, "timestamp": 2}


def fluxo():
    return [
        {"tool_name": "fazer_login_login", "actions": [fill("Usuário"), senha(), click()]},
        {"tool_name": "preencher_form", "actions": [fill("Pedido")]},
        {"tool_name": "fazer_login_outro", "actions": [senha("Senha do banco", "https://banco.exemplo.local/x"), click()]},
    ]


# ---------------------------------------------------------------------------
# annotate_masked
# ---------------------------------------------------------------------------

def test_annotate_masked_lista_os_campos_de_senha_com_id_por_posicao():
    passos = fluxo()

    campos = jp.annotate_masked(passos)

    assert campos == [
        {"id": "g0a1", "tool_name": "fazer_login_login", "target_text": "Senha",
         "suggested_name": "ERP_EXEMPLO_LOCAL_SENHA"},
        {"id": "g2a0", "tool_name": "fazer_login_outro", "target_text": "Senha do banco",
         "suggested_name": "BANCO_EXEMPLO_LOCAL_SENHA_DO_BANCO"},
    ]
    assert passos[0]["actions"][1]["masked_id"] == "g0a1"
    assert passos[2]["actions"][0]["masked_id"] == "g2a0"
    assert "masked_id" not in passos[0]["actions"][0]


def test_annotate_masked_e_estavel_entre_chamadas():
    passos = fluxo()

    assert jp.annotate_masked(passos) == jp.annotate_masked(fluxo())


def test_campo_sem_rotulo_sugere_senha():
    passos = [{"tool_name": "t", "actions": [senha("")]}]

    assert jp.annotate_masked(passos)[0]["suggested_name"] == "ERP_EXEMPLO_LOCAL_SENHA"


def test_campo_sem_url_sugere_app():
    passos = [{"tool_name": "t", "actions": [senha("Senha", url="")]}]

    assert jp.annotate_masked(passos)[0]["suggested_name"] == "APP_SENHA"


def test_campo_que_ja_tem_secret_nao_e_listado():
    passos = [{"tool_name": "t", "actions": [dict(senha(), secret="ERP_SENHA")]}]

    assert jp.annotate_masked(passos) == []


def test_fluxo_sem_senha_nao_tem_campos_e_aceita_passo_sem_acoes():
    assert jp.annotate_masked([{"tool_name": "t", "actions": [fill("Pedido")]}, {"tool_name": "u"}]) == []


def test_nome_sugerido_e_um_nome_de_secret_valido():
    passos = [{"tool_name": "t", "actions": [senha("Senha (ERP) nº 1"), senha("Senha", "https://erp-2.exemplo.local/")]}]

    assert all(secrets_store.is_valid_name(c["suggested_name"]) for c in jp.annotate_masked(passos))


def test_nome_sugerido_para_erp_acessado_por_ip_e_valido():
    passos = [{"tool_name": "t", "actions": [senha("Senha", "http://192.168.0.10:8080/systextil/login.jsp")]}]

    sugerido = jp.annotate_masked(passos)[0]["suggested_name"]

    assert secrets_store.is_valid_name(sugerido)
    assert sugerido == "HOST_192_168_0_10_SENHA"


# ---------------------------------------------------------------------------
# apply_secrets
# ---------------------------------------------------------------------------

def test_apply_secrets_troca_o_valor_mascarado_pelo_secret():
    passos = fluxo()

    jp.apply_secrets(passos, {"g0a1": "ERP_SENHA", "g2a0": "BANCO_SENHA"})

    assert passos[0]["actions"][1]["secret"] == "ERP_SENHA"
    assert "value" not in passos[0]["actions"][1]
    assert passos[2]["actions"][0]["secret"] == "BANCO_SENHA"
    assert passos[0]["actions"][0]["value"] == "x"  # campo comum não muda


def test_apply_secrets_sem_secret_para_algum_campo_levanta_erro_com_o_campo():
    passos = fluxo()

    with pytest.raises(ValueError, match="Senha do banco"):
        jp.apply_secrets(passos, {"g0a1": "ERP_SENHA"})


def test_apply_secrets_com_nome_vazio_conta_como_faltando():
    with pytest.raises(ValueError, match="Senha"):
        jp.apply_secrets(fluxo(), {"g0a1": "", "g2a0": "BANCO_SENHA"})


def test_campo_sem_rotulo_faltando_aparece_pelo_id():
    passos = [{"tool_name": "t", "actions": [senha("")]}]

    with pytest.raises(ValueError, match="g0a0"):
        jp.apply_secrets(passos, {})


def test_apply_secrets_sem_campos_mascarados_nao_faz_nada():
    passos = [{"tool_name": "t", "actions": [fill("Pedido")]}]

    jp.apply_secrets(passos, {})

    assert passos == [{"tool_name": "t", "actions": [fill("Pedido")]}]


def test_apply_secrets_mantem_secret_ja_definido():
    passos = [{"tool_name": "t", "actions": [dict(senha(), secret="JA_DEFINIDO")]}]

    jp.apply_secrets(passos, {"g0a0": "OUTRO"})

    assert passos[0]["actions"][0]["secret"] == "JA_DEFINIDO"


# ---------------------------------------------------------------------------
# resolve_secrets_in_terminal (input/getpass falsos)
# ---------------------------------------------------------------------------

@pytest.fixture
def terminal(monkeypatch):
    """Simula o usuário: `respostas` para input(), `senhas` para getpass()."""
    estado = {"respostas": [], "senhas": [], "perguntas": [], "pedidas": []}

    def fake_input(prompt=""):
        estado["perguntas"].append(prompt)
        return estado["respostas"].pop(0)

    def fake_getpass(prompt=""):
        estado["pedidas"].append(prompt)
        return estado["senhas"].pop(0)

    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    return estado


def test_terminal_sem_campo_de_senha_nao_pergunta_nada(terminal):
    passos = [{"tool_name": "t", "actions": [fill("Pedido")]}]

    jp.resolve_secrets_in_terminal(passos)

    assert terminal["perguntas"] == []


def test_terminal_enter_aceita_o_nome_sugerido_e_cadastra_o_valor(terminal):
    terminal["respostas"] = [""]
    terminal["senhas"] = ["valor-sintetico"]
    passos = [{"tool_name": "t", "actions": [senha()]}]

    jp.resolve_secrets_in_terminal(passos)

    assert passos[0]["actions"][0]["secret"] == "ERP_EXEMPLO_LOCAL_SENHA"
    assert secrets_store.get_secret("ERP_EXEMPLO_LOCAL_SENHA") == "valor-sintetico"


def test_terminal_secret_existente_nao_pede_o_valor_de_novo(terminal):
    secrets_store.set_secret("ERP_SENHA", "ja-cadastrado")
    terminal["respostas"] = ["ERP_SENHA", "ERP_SENHA"]
    passos = fluxo()

    jp.resolve_secrets_in_terminal(passos)

    assert terminal["pedidas"] == []
    assert passos[0]["actions"][1]["secret"] == "ERP_SENHA"
    assert passos[2]["actions"][0]["secret"] == "ERP_SENHA"
    assert secrets_store.get_secret("ERP_SENHA") == "ja-cadastrado"


def test_terminal_secret_novo_usado_duas_vezes_pede_o_valor_uma_vez(terminal):
    terminal["respostas"] = ["NOVO_SECRET", "NOVO_SECRET"]
    terminal["senhas"] = ["v1"]

    jp.resolve_secrets_in_terminal(fluxo())

    assert len(terminal["pedidas"]) == 1
    assert secrets_store.get_secret("NOVO_SECRET") == "v1"


def test_terminal_nome_invalido_pergunta_de_novo(terminal, capsys):
    terminal["respostas"] = ["minusculo", "1COMECA_COM_DIGITO", "VALIDO"]
    terminal["senhas"] = ["v"]
    passos = [{"tool_name": "t", "actions": [senha()]}]

    jp.resolve_secrets_in_terminal(passos)

    assert len(terminal["perguntas"]) == 3
    assert passos[0]["actions"][0]["secret"] == "VALIDO"
    assert "Nome inválido" in capsys.readouterr().out
