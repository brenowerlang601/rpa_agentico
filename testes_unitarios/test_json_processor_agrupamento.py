"""
Agrupamento das ações normalizadas em tools (CLAUDE.md, "Agrupamento das ações em tools"):
- mudança de URL abre uma nova tool;
- na mesma URL, sequências com mais de 6 ações são quebradas em pontos naturais: fim do login,
  clique seguido de espera longa (≥ 3 s) e clique que envia campos preenchidos;
- o login sempre vira uma tool própria.
"""

import pytest

import json_processor as jp
from testes_unitarios.gravacao_exemplo import URL as URL_EXEMPLO
from testes_unitarios.gravacao_exemplo import gravacao

URL = "https://erp.exemplo.local/app/form"
URL_B = "https://erp.exemplo.local/app/lista"


# ---------------------------------------------------------------------------
# Construtores de ações já normalizadas (dados sintéticos)
# ---------------------------------------------------------------------------

def fill(ts, alvo="CAMPO", xpath=None, valor="x", url=URL, **extra):
    a = {"type": "fill", "xpath": xpath or f'id("{alvo.lower()}")', "css_selector": "",
         "target_text": alvo, "value": valor, "url": url, "timestamp": ts}
    a.update(extra)
    return a


def click(ts, alvo="", url=URL, xpath=None):
    return {"type": "click", "xpath": xpath or f'id("btn_{ts}")', "css_selector": "",
            "target_text": alvo, "element_text": "", "url": url, "timestamp": ts}


def tecla(ts, key="Enter", url=URL):
    return {"type": "key_press", "key": key, "count": 1, "url": url, "xpath": "",
            "css_selector": "", "timestamp": ts}


def nav(ts, url=URL):
    return {"type": "navigation", "url": url, "timestamp": ts}


def login(ts0=1000, url=URL):
    """usuário + senha + Entrar (rápido)."""
    return [fill(ts0, "USUARIO", url=url), fill(ts0 + 100, "SENHA", url=url, masked=True),
            click(ts0 + 200, "Entrar", url=url)]


def cliques(n, ts0=10000, passo=100, url=URL):
    return [click(ts0 + i * passo, f"C{i}", url=url) for i in range(n)]


def ids(partes):
    """Representa cada parte pelos timestamps (identificam as ações nos testes)."""
    return [[a["timestamp"] for a in p] for p in partes]


# ---------------------------------------------------------------------------
# _screen_key
# ---------------------------------------------------------------------------

def test_screen_key_ignora_a_query_string():
    assert jp._screen_key("https://erp.exemplo.local/app?managerId=1") == \
        jp._screen_key("https://erp.exemplo.local/app?managerId=2")


def test_screen_key_inclui_o_fragment_sem_a_query_interna():
    assert jp._screen_key("https://erp.exemplo.local/app?x=1#tela_a?y=2") == "https://erp.exemplo.local/app#tela_a"


def test_screen_key_diferencia_fragments():
    assert jp._screen_key("https://erp.exemplo.local/app#0x") != jp._screen_key("https://erp.exemplo.local/app#1")


def test_screen_key_diferencia_host_e_path():
    assert jp._screen_key("https://erp.exemplo.local/a") == "https://erp.exemplo.local/a#"
    assert jp._screen_key("https://outro.exemplo.local/a") != jp._screen_key("https://erp.exemplo.local/a")


# ---------------------------------------------------------------------------
# is_login_field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alvo", ["Usuário", "SENHA", "login", "E-mail".replace("-", ""), "Password", "user"])
def test_fill_com_texto_de_login_e_campo_de_login(alvo):
    assert jp.is_login_field(fill(1, alvo, xpath='id("campo1")'))


@pytest.mark.parametrize("xpath, css", [
    ('id("password")', ""),
    ('//input[@name="usuario"]', ""),
    ("", "input[name='senha']"),
])
def test_fill_com_seletor_de_login_e_campo_de_login(xpath, css):
    a = fill(1, "Código", xpath=xpath)
    a["css_selector"] = css
    assert jp.is_login_field(a)


def test_fill_mascarado_ou_com_secret_e_campo_de_login():
    assert jp.is_login_field(fill(1, "Código", xpath='id("x")', masked=True))
    assert jp.is_login_field(fill(1, "Código", xpath='id("x")', secret="ERP_SENHA"))


def test_campo_comum_nao_e_campo_de_login():
    assert not jp.is_login_field(fill(1, "Pedido", xpath='id("pedido")'))


def test_palavra_de_login_dentro_de_outro_nome_nao_conta():
    """'usuario_responsavel' no seletor não é o campo de usuário do login."""
    assert not jp.is_login_field(fill(1, "Usuário responsável", xpath='id("usuario_responsavel")'))


def test_click_nunca_e_campo_de_login():
    assert not jp.is_login_field(click(1, "Senha", xpath='id("senha")'))


# ---------------------------------------------------------------------------
# _login_end_index e login_field
# ---------------------------------------------------------------------------

def test_fim_do_login_e_o_clique_depois_do_ultimo_campo_de_login():
    acoes = [nav(1)] + login(10) + [click(500, "Menu")]

    assert jp._login_end_index(acoes) == 3


def test_fim_do_login_pode_ser_enter():
    acoes = [fill(1, "USUARIO"), fill(2, "SENHA"), tecla(3, "Tab"), tecla(4, "Enter"), click(5)]

    assert jp._login_end_index(acoes) == 3


def test_clique_antes_do_ultimo_campo_de_login_nao_encerra_o_login():
    acoes = [fill(1, "USUARIO"), click(2, "Mostrar"), fill(3, "SENHA"), click(4, "Entrar")]

    assert jp._login_end_index(acoes) == 3


def test_sem_campo_de_login_nao_ha_fim_de_login():
    assert jp._login_end_index([fill(1, "Pedido"), click(2)]) is None


def test_campos_de_login_sem_envio_nao_sao_login():
    assert jp._login_end_index([fill(1, "USUARIO"), fill(2, "SENHA"), tecla(3, "Tab")]) is None


def test_login_field_devolve_o_primeiro_campo_de_login():
    acoes = [nav(1), fill(2, "Empresa")] + login(10)

    assert jp.login_field(acoes)["target_text"] == "USUARIO"


def test_login_field_sem_envio_devolve_none():
    assert jp.login_field([fill(1, "USUARIO"), fill(2, "SENHA")]) is None


def test_login_field_sem_login_devolve_none():
    assert jp.login_field([fill(1, "Pedido"), click(2)]) is None


# ---------------------------------------------------------------------------
# _split_same_url — sequências curtas (≤ 6)
# ---------------------------------------------------------------------------

def test_sequencia_curta_sem_login_fica_numa_parte():
    acoes = [nav(1), fill(2, "A"), click(3), fill(4, "B"), click(5)]

    assert jp._split_same_url(acoes) == [acoes]


def test_sequencia_curta_com_login_e_algo_depois_separa_o_login():
    acoes = login(10) + [click(500, "Menu"), click(600, "Item")]

    partes = jp._split_same_url(acoes)

    assert ids(partes) == [[10, 110, 210], [500, 600]]


def test_sequencia_curta_terminando_no_login_fica_numa_parte():
    acoes = [nav(1)] + login(10)

    assert jp._split_same_url(acoes) == [acoes]


def test_seis_acoes_com_pontos_naturais_nao_quebram():
    """O limite é > 6: exatamente 6 ações ficam juntas mesmo com submit e espera longa."""
    acoes = [fill(1000, "A"), click(1100), fill(1200, "B"), click(1300), click(9000), click(9100)]

    assert jp._split_same_url(acoes) == [acoes]


# ---------------------------------------------------------------------------
# _split_same_url — sequências longas (> 6)
# ---------------------------------------------------------------------------

def test_sete_acoes_quebram_no_clique_que_envia_campos_preenchidos():
    acoes = [nav(1000), fill(1100, "A"), fill(1200, "B"), click(1300, "Salvar"),
             fill(1400, "C"), fill(1500, "D"), click(1600, "Ok")]

    assert ids(jp._split_same_url(acoes)) == [[1000, 1100, 1200, 1300], [1400, 1500, 1600]]


def test_clique_seguido_de_espera_de_3s_quebra():
    acoes = cliques(3) + cliques(4, ts0=10200 + 3000)  # 3000 ms entre C2 e o próximo

    partes = jp._split_same_url(acoes)

    assert ids(partes) == [[10000, 10100, 10200], [13200, 13300, 13400, 13500]]


def test_clique_seguido_de_espera_menor_que_3s_nao_quebra():
    acoes = cliques(3) + cliques(4, ts0=10200 + 2999)

    assert jp._split_same_url(acoes) == [acoes]


def test_enter_seguido_de_espera_longa_quebra():
    acoes = cliques(3) + [tecla(10300, "Enter")] + cliques(3, ts0=20000)

    assert ids(jp._split_same_url(acoes)) == [[10000, 10100, 10200, 10300], [20000, 20100, 20200]]


def test_outra_tecla_seguida_de_espera_longa_nao_quebra():
    acoes = cliques(3) + [tecla(10300, "Tab")] + cliques(3, ts0=20000)

    assert jp._split_same_url(acoes) == [acoes]


def test_enter_rapido_depois_de_campos_preenchidos_nao_quebra():
    """Só o CLIQUE que envia campos preenchidos é ponto natural; Enter só com espera longa."""
    acoes = [fill(1000, "A"), tecla(1100, "Enter"), fill(1200, "B"), tecla(1300, "Enter"),
             fill(1400, "C"), tecla(1500, "Enter"), fill(1600, "D")]

    assert jp._split_same_url(acoes) == [acoes]


def test_login_vira_parte_propria_numa_sequencia_longa():
    acoes = [nav(1)] + login(10) + [click(300, "Menu"), click(400, "Item"), fill(500, "Campo"), click(600, "Salvar")]

    partes = jp._split_same_url(acoes)

    assert ids(partes) == [[1, 10, 110, 210], [300, 400, 500, 600]]
    assert jp._login_end_index(partes[0]) is not None
    assert jp._login_end_index(partes[1]) is None


def test_login_enviado_com_enter_rapido_vira_parte_propria_numa_sequencia_longa():
    """Enter rápido não é ponto natural por si só — mas o fim do login é."""
    acoes = [nav(1), fill(10, "USUARIO"), fill(20, "SENHA", masked=True), tecla(30, "Enter"),
             click(130, "Menu"), click(230, "Item"), fill(330, "Campo"), click(430, "Salvar")]

    assert ids(jp._split_same_url(acoes)) == [[1, 10, 20, 30], [130, 230, 330, 430]]


def test_parte_de_uma_acao_so_e_juntada_a_seguinte():
    acoes = [fill(1000, "A"), click(1100, "Salvar"),           # submit → parte [A, Salvar]
             click(1200, "Aba"),                                # espera longa → parte de 1 ação
             click(9000), click(9100), click(9200), click(9300)]

    assert ids(jp._split_same_url(acoes)) == [[1000, 1100], [1200, 9000, 9100, 9200, 9300]]


def test_sequencia_longa_sem_pontos_naturais_fica_inteira_ate_12_acoes():
    acoes = cliques(12)

    assert jp._split_same_url(acoes) == [acoes]


def test_sequencia_muito_longa_sem_pontos_naturais_e_cortada_em_12():
    acoes = cliques(30)

    partes = jp._split_same_url(acoes)

    assert len(partes) > 1
    assert all(len(p) <= jp.MAX_ACTIONS_PER_TOOL * 2 for p in partes)
    assert [a for p in partes for a in p] == acoes


def test_quebra_preserva_todas_as_acoes_em_ordem():
    acoes = ([nav(1)] + login(10) + [fill(300, "A"), click(400, "Salvar"), click(500)]
             + cliques(5, ts0=9000) + [fill(20000, "B"), click(20100, "Ok")])

    partes = jp._split_same_url(acoes)

    assert [a for p in partes for a in p] == acoes
    assert all(p for p in partes)


# ---------------------------------------------------------------------------
# group_into_micro_processes
# ---------------------------------------------------------------------------

def test_sem_acoes_nao_ha_grupos():
    assert jp.group_into_micro_processes([]) == []


def test_mudanca_de_url_abre_novo_grupo():
    acoes = [nav(1, URL), click(2, url=URL), nav(3, URL_B), click(4, url=URL_B)]

    grupos = jp.group_into_micro_processes(acoes)

    assert [g["url"] for g in grupos] == [jp._screen_key(URL), jp._screen_key(URL_B)]
    assert [ids([g["actions"]])[0] for g in grupos] == [[1, 2], [3, 4]]


def test_mudanca_so_de_query_string_continua_no_mesmo_grupo():
    acoes = [click(1, url=URL + "?managerId=1"), click(2, url=URL + "?managerId=2")]

    assert len(jp.group_into_micro_processes(acoes)) == 1


def test_mudanca_de_fragment_abre_novo_grupo():
    acoes = [click(1, url=URL + "#tela_a"), click(2, url=URL + "#tela_b")]

    assert len(jp.group_into_micro_processes(acoes)) == 2


def test_voltar_para_url_anterior_abre_outro_grupo():
    acoes = [click(1, url=URL), click(2, url=URL_B), click(3, url=URL)]

    assert [g["url"] for g in jp.group_into_micro_processes(acoes)] == \
        [jp._screen_key(URL), jp._screen_key(URL_B), jp._screen_key(URL)]


def test_grupo_longo_na_mesma_url_e_quebrado_e_mantem_a_url():
    acoes = [nav(1)] + login(10) + [click(300, "Menu"), click(400, "Item"), fill(500, "Campo"), click(600, "Salvar")]

    grupos = jp.group_into_micro_processes(acoes)

    assert len(grupos) == 2
    assert all(g["url"] == jp._screen_key(URL) for g in grupos)


def test_login_curto_seguido_de_acoes_na_mesma_url_vira_tool_propria():
    grupos = jp.group_into_micro_processes(login(10) + [click(500, "Menu")] + [click(600, url=URL_B)])

    assert [ids([g["actions"]])[0] for g in grupos] == [[10, 110, 210], [500], [600]]


# ---------------------------------------------------------------------------
# group_steps (ações brutas → grupos)
# ---------------------------------------------------------------------------

def test_group_steps_normaliza_e_agrupa_a_gravacao():
    grupos = jp.group_steps(gravacao()["steps"])

    assert len(grupos) == 1
    assert grupos[0]["url"] == jp._screen_key(URL_EXEMPLO)
    assert [a["type"] for a in grupos[0]["actions"]] == ["navigation", "fill", "click"]
    assert grupos[0]["actions"][1]["value"] == "ABC"


def test_group_steps_separa_paginas_diferentes():
    passos = gravacao()["steps"] + [
        {"type": "navigation", "timestamp": "5000", "url": URL_B},
        {"type": "click", "timestamp": "6000", "url": URL_B, "xpath": 'id("x")', "targetText": ""},
    ]

    assert len(jp.group_steps(passos)) == 2
