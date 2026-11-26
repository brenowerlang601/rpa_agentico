"""
Primeira passagem do json_processor: ações brutas da extensão → ações normalizadas.

Regras (CLAUDE.md): click+input no mesmo xpath vira fill; key_press repetido da mesma tecla no
mesmo elemento agrupa com count; scroll isolado some; Ctrl+C/Ctrl+V ignorados; senha gravada
como ******** fica marcada como mascarada; timeout_ms = 2× intervalo gravado + 5 s, entre 10 s
e 180 s.
"""

import pytest

import json_processor as jp

URL = "https://erp.exemplo.local/app/form"


# ---------------------------------------------------------------------------
# Construtores de steps no formato da extensão (dados sintéticos)
# ---------------------------------------------------------------------------

def nav(ts, url=URL):
    return {"type": "navigation", "timestamp": ts, "url": url}


def click(xpath, ts, url=URL, target="", texto="", css=None):
    s = {"type": "click", "timestamp": ts, "url": url, "xpath": xpath,
         "elementTag": "INPUT", "targetText": target, "elementText": texto}
    if css is not None:
        s["cssSelector"] = css
    return s


def inp(xpath, valor, ts, url=URL, target="", css=None):
    s = {"type": "input", "timestamp": ts, "url": url, "xpath": xpath,
         "elementTag": "INPUT", "targetText": target, "value": valor}
    if css is not None:
        s["cssSelector"] = css
    return s


def tecla(key, ts, xpath="", css="", url=URL):
    return {"type": "key_press", "timestamp": ts, "url": url, "key": key,
            "xpath": xpath, "cssSelector": css}


def scroll(ts, url=URL):
    return {"type": "scroll", "timestamp": ts, "url": url, "scrollX": 0, "scrollY": 300}


# ---------------------------------------------------------------------------
# _normalize_key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bruta, esperada", [
    ("CmdOrCtrl+Z", "Control+z"),
    ("CmdOrCtrl+F", "Control+f"),
    ("Return", "Enter"),
    ("Esc", "Escape"),
    ("Del", "Delete"),
    ("Ins", "Insert"),
    ("  Tab  ", "Tab"),
    ("ArrowRight", "ArrowRight"),
    ("Enter", "Enter"),
])
def test_normalize_key_converte_para_nome_do_playwright(bruta, esperada):
    assert jp._normalize_key(bruta) == esperada


# ---------------------------------------------------------------------------
# _normalize_actions — tipos de ação
# ---------------------------------------------------------------------------

def test_click_seguido_de_input_no_mesmo_xpath_vira_um_fill():
    acoes = jp._normalize_actions([
        click("id(\"nome\")", 1000, target="NOME", css="#nome"),
        inp("id(\"nome\")", "ABC", 1500),
    ])

    assert len(acoes) == 1
    fill = acoes[0]
    assert fill["type"] == "fill"
    assert fill["xpath"] == "id(\"nome\")"
    assert fill["value"] == "ABC"
    assert fill["target_text"] == "NOME"
    assert fill["css_selector"] == "#nome"
    assert fill["url"] == URL
    assert fill["timestamp"] == 1000


def test_fill_usa_css_e_texto_do_input_quando_o_click_nao_tem():
    acoes = jp._normalize_actions([
        click("id(\"nome\")", 1000),
        inp("id(\"nome\")", "ABC", 1500, target=" NOME ", css="#do_input"),
    ])

    assert acoes[0]["css_selector"] == "#do_input"
    assert acoes[0]["target_text"] == "NOME"


def test_scroll_entre_click_e_input_nao_impede_o_fill():
    acoes = jp._normalize_actions([
        click("id(\"nome\")", 1000),
        scroll(1100),
        scroll(1200),
        inp("id(\"nome\")", "ABC", 1500),
    ])

    assert [a["type"] for a in acoes] == ["fill"]


def test_click_seguido_de_input_em_outro_xpath_fica_click_e_fill_separados():
    acoes = jp._normalize_actions([
        click("id(\"botao\")", 1000, texto=" Novo "),
        inp("id(\"nome\")", "ABC", 1500, target="NOME"),
    ])

    assert [a["type"] for a in acoes] == ["click", "fill"]
    assert acoes[0]["element_text"] == "Novo"
    assert acoes[1]["xpath"] == "id(\"nome\")"
    assert acoes[1]["value"] == "ABC"
    assert acoes[1]["target_text"] == "NOME"


def test_click_isolado_guarda_seletores_e_textos():
    acoes = jp._normalize_actions([click("id(\"ok\")", 1000, target=" OK ", texto="Confirmar", css="#ok")])

    assert acoes == [{
        "type": "click", "xpath": "id(\"ok\")", "css_selector": "#ok", "target_text": "OK",
        "element_text": "Confirmar", "url": URL, "timestamp": 1000, "timeout_ms": 10000,
    }]


def test_click_com_element_text_nulo_vira_texto_vazio():
    passo = click("id(\"ok\")", 1000)
    passo["elementText"] = None

    assert jp._normalize_actions([passo])[0]["element_text"] == ""


def test_navigation_e_preservada_com_url():
    acoes = jp._normalize_actions([nav(1000, "https://erp.exemplo.local/inicio")])

    assert acoes[0]["type"] == "navigation"
    assert acoes[0]["url"] == "https://erp.exemplo.local/inicio"
    assert acoes[0]["timestamp"] == 1000


def test_scroll_isolado_some():
    acoes = jp._normalize_actions([scroll(1000), click("id(\"ok\")", 2000), scroll(3000)])

    assert [a["type"] for a in acoes] == ["click"]


def test_tipo_desconhecido_e_ignorado():
    acoes = jp._normalize_actions([{"type": "hover", "timestamp": 1000, "url": URL},
                                   click("id(\"ok\")", 2000)])

    assert [a["type"] for a in acoes] == ["click"]


def test_lista_vazia_devolve_lista_vazia():
    assert jp._normalize_actions([]) == []


# ---------------------------------------------------------------------------
# _normalize_actions — teclas
# ---------------------------------------------------------------------------

def test_key_press_repetido_da_mesma_tecla_no_mesmo_elemento_agrupa_com_count():
    acoes = jp._normalize_actions([
        tecla("Backspace", 1000, xpath="id(\"q\")"),
        tecla("Backspace", 1100, xpath="id(\"q\")"),
        tecla("Backspace", 1200, xpath="id(\"q\")"),
    ])

    assert len(acoes) == 1
    assert acoes[0]["type"] == "key_press"
    assert acoes[0]["key"] == "Backspace"
    assert acoes[0]["count"] == 3
    assert acoes[0]["xpath"] == "id(\"q\")"


def test_key_press_isolado_tem_count_1_e_tecla_normalizada():
    acoes = jp._normalize_actions([tecla("Return", 1000, xpath="id(\"q\")", css="#q")])

    assert acoes[0]["key"] == "Enter"
    assert acoes[0]["count"] == 1
    assert acoes[0]["css_selector"] == "#q"


def test_mesma_tecla_em_elementos_diferentes_nao_agrupa():
    """Tab em campos diferentes é navegação entre campos: cada uma é uma ação."""
    acoes = jp._normalize_actions([
        tecla("Tab", 1000, xpath="id(\"a\")"),
        tecla("Tab", 1100, xpath="id(\"b\")"),
    ])

    assert [a["count"] for a in acoes] == [1, 1]
    assert [a["xpath"] for a in acoes] == ["id(\"a\")", "id(\"b\")"]


def test_mesma_tecla_com_css_diferente_nao_agrupa():
    acoes = jp._normalize_actions([
        tecla("Tab", 1000, css="#a"),
        tecla("Tab", 1100, css="#b"),
    ])

    assert [a["count"] for a in acoes] == [1, 1]


def test_teclas_diferentes_no_mesmo_elemento_nao_agrupam():
    acoes = jp._normalize_actions([
        tecla("Tab", 1000, xpath="id(\"a\")"),
        tecla("Enter", 1100, xpath="id(\"a\")"),
    ])

    assert [(a["key"], a["count"]) for a in acoes] == [("Tab", 1), ("Enter", 1)]


def test_outra_acao_entre_teclas_iguais_interrompe_o_agrupamento():
    acoes = jp._normalize_actions([
        tecla("Tab", 1000, xpath="id(\"a\")"),
        click("id(\"x\")", 1100),
        tecla("Tab", 1200, xpath="id(\"a\")"),
    ])

    assert [a["type"] for a in acoes] == ["key_press", "click", "key_press"]
    assert acoes[0]["count"] == 1 and acoes[2]["count"] == 1


def test_aliases_da_mesma_tecla_agrupam():
    acoes = jp._normalize_actions([
        tecla("Return", 1000, xpath="id(\"a\")"),
        tecla("Enter", 1100, xpath="id(\"a\")"),
    ])

    assert len(acoes) == 1
    assert acoes[0]["count"] == 2


@pytest.mark.parametrize("atalho", ["CmdOrCtrl+C", "CmdOrCtrl+V"])
def test_copiar_e_colar_sao_ignorados(atalho):
    acoes = jp._normalize_actions([
        tecla(atalho, 1000, xpath="id(\"a\")"),
        tecla("Tab", 1100, xpath="id(\"a\")"),
    ])

    assert [a["key"] for a in acoes] == ["Tab"]


def test_outros_atalhos_com_ctrl_sao_mantidos():
    acoes = jp._normalize_actions([tecla("CmdOrCtrl+Z", 1000, xpath="id(\"a\")")])

    assert [a["key"] for a in acoes] == ["Control+z"]


# ---------------------------------------------------------------------------
# _normalize_actions — senhas mascaradas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", ["***", "********", "**************"])
def test_fill_com_valor_mascarado_fica_marcado(valor):
    acoes = jp._normalize_actions([click("id(\"senha\")", 1000), inp("id(\"senha\")", valor, 1100)])

    assert acoes[0]["masked"] is True


@pytest.mark.parametrize("valor", ["**", "abc", "*a*", "", "1234****"])
def test_fill_com_valor_comum_nao_fica_marcado(valor):
    acoes = jp._normalize_actions([inp("id(\"campo\")", valor, 1000)])

    assert "masked" not in acoes[0]


# ---------------------------------------------------------------------------
# _gap_ms e _add_wait_timeouts
# ---------------------------------------------------------------------------

def test_gap_ms_e_a_diferenca_entre_timestamps():
    assert jp._gap_ms({"timestamp": 1000}, {"timestamp": 4500}) == 3500


def test_gap_ms_aceita_timestamp_em_texto():
    assert jp._gap_ms({"timestamp": "1000"}, {"timestamp": "2500"}) == 1500


def test_gap_ms_nunca_e_negativo():
    assert jp._gap_ms({"timestamp": 5000}, {"timestamp": 1000}) == 0


@pytest.mark.parametrize("anterior, atual", [
    (None, {"timestamp": 1000}),
    ({"timestamp": 1000}, None),
    ({}, {"timestamp": 1000}),
    ({"timestamp": 1000}, {"timestamp": None}),
])
def test_gap_ms_sem_timestamp_devolve_zero(anterior, atual):
    assert jp._gap_ms(anterior, atual) == 0


def _timeout_com_intervalo(gap_ms: int) -> int:
    acoes = [{"type": "click", "timestamp": 1_000_000}, {"type": "click", "timestamp": 1_000_000 + gap_ms}]
    jp._add_wait_timeouts(acoes)
    return acoes[1]["timeout_ms"]


@pytest.mark.parametrize("gap, esperado", [
    (0, 10000),          # mínimo
    (2499, 10000),       # 2×2499+5000 = 9998 → sobe para o mínimo
    (2500, 10000),       # borda exata do mínimo
    (2501, 10002),       # primeiro valor acima do mínimo
    (12000, 29000),      # 2×12 s + 5 s
    (87499, 179998),     # logo abaixo do máximo
    (87500, 180000),     # borda exata do máximo
    (87501, 180000),     # acima → limitado
    (600000, 180000),
])
def test_timeout_e_dobro_do_intervalo_mais_5s_entre_10s_e_180s(gap, esperado):
    assert _timeout_com_intervalo(gap) == esperado


def test_primeira_acao_usa_o_timeout_minimo():
    acoes = [{"type": "navigation", "timestamp": 1000}]
    jp._add_wait_timeouts(acoes)

    assert acoes[0]["timeout_ms"] == jp.DEFAULT_TIMEOUT_MS


def test_timeout_usa_o_intervalo_desde_a_acao_anterior_e_nao_desde_a_primeira():
    acoes = [{"timestamp": 1000}, {"timestamp": 51000}, {"timestamp": 52000}]
    jp._add_wait_timeouts(acoes)

    assert [a["timeout_ms"] for a in acoes] == [10000, 105000, 10000]


def test_normalize_actions_preenche_timeout_a_partir_da_gravacao():
    acoes = jp._normalize_actions([
        nav(1000),
        click("id(\"ok\")", 25000),  # 24 s depois (ex.: resposta demorada)
    ])

    assert [a["timeout_ms"] for a in acoes] == [10000, 53000]
