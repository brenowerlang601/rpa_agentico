"""
Nomes e versões das tools (CLAUDE.md, "Versionamento de tools"):
- nome semântico por grupo (fazer_login_*, preencher_*, clicar_*, ...), snake_case sem acento;
- mesma tool com ações diferentes → nome_v2, nome_v3; fingerprint idêntico → reutiliza;
- tool gerada pelo LLM só entra se o usuário aprovar (gerador falso aqui, nada de OpenAI).
"""

import pytest

import json_processor as jp
import tool_generator

URL = "https://erp.exemplo.local/app/form"


def fill(ts, alvo="CAMPO", valor="x", url=URL, **extra):
    a = {"type": "fill", "xpath": f'id("{alvo.lower()}")', "css_selector": "", "target_text": alvo,
         "value": valor, "url": url, "timestamp": ts}
    a.update(extra)
    return a


def click(ts, alvo="", url=URL):
    return {"type": "click", "xpath": f'id("btn_{ts}")', "css_selector": "", "target_text": alvo,
            "element_text": "", "url": url, "timestamp": ts}


def nav(ts, url=URL):
    return {"type": "navigation", "url": url, "timestamp": ts}


def tecla(ts, key="Tab", url=URL):
    return {"type": "key_press", "key": key, "count": 1, "url": url, "xpath": "", "css_selector": "",
            "timestamp": ts}


def grupo(acoes, url=URL):
    return {"url": jp._screen_key(url), "actions": acoes}


def nome(acoes, url=URL):
    return jp.name_groups([grupo(acoes, url)])[0]["tool_name"]


# ---------------------------------------------------------------------------
# _slug e is_valid_tool_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("Pedido de Venda", "pedido_de_venda"),
    ("Ação Rápida", "acao_rapida"),
    ("ÁÀÃÂÄ éèêë íìîï óòõôö úùûü ç", "aaaaa_eeee_iiii_ooooo_uuuu_c"),
    ("  --Código #42!!  ", "codigo_42"),
    ("JÁ_EXISTE", "ja_existe"),
    ("!!!", ""),
    ("", ""),
])
def test_slug_vira_snake_case_sem_acento(texto, esperado):
    assert jp._slug(texto) == esperado


@pytest.mark.parametrize("nome_tool", ["fazer_login_forms", "_interna", "Tool2", "a"])
def test_nome_de_tool_valido_e_identificador_python(nome_tool):
    assert jp.is_valid_tool_name(nome_tool)


@pytest.mark.parametrize("nome_tool", [
    "2tool", "com espaco", "com-hifen", "tools.x", "../x", "", "x;import os", "a\nb", None, 42,
])
def test_nome_de_tool_invalido_e_recusado(nome_tool):
    assert not jp.is_valid_tool_name(nome_tool)


@pytest.mark.xfail(strict=True, reason="BUG: regex com $ aceita quebra de linha no fim ('nome\\n'); usar \\Z ou fullmatch")
def test_nome_de_tool_com_quebra_de_linha_no_fim_e_recusado():
    """'nome\\n' passa e vira `from tools.nome\\n import nome\\n` — script que nem compila."""
    assert not jp.is_valid_tool_name("nome\n")


# ---------------------------------------------------------------------------
# name_groups — classificação
# ---------------------------------------------------------------------------

def test_grupo_com_login_vira_fazer_login():
    acoes = [fill(1, "USUARIO"), fill(2, "SENHA"), click(3, "Entrar")]

    assert nome(acoes, "https://erp.exemplo.local/app/inicio.jsp") == "fazer_login_inicio"


def test_pagina_de_login_com_campos_vira_fazer_login_mesmo_sem_envio():
    assert nome([fill(1, "Código")], "https://erp.exemplo.local/app/acesso.jsp") == "fazer_login_acesso"


def test_pagina_de_login_sem_campos_nao_e_login():
    assert nome([click(1, "Esqueci")], "https://erp.exemplo.local/app/login.jsp") == "clicar_esqueci_login"


def test_so_campos_vira_preencher():
    assert nome([fill(1, "A"), fill(2, "B")], "https://erp.exemplo.local/app/pedido.jsp") == "preencher_pedido"


def test_campos_e_cliques_vira_preencher_e_confirmar():
    acoes = [fill(1, "A"), click(2, "Salvar")]

    assert nome(acoes, "https://erp.exemplo.local/app/pedido.jsp") == "preencher_e_confirmar_pedido"


def test_so_cliques_vira_clicar_com_o_texto_do_ultimo_clique():
    acoes = [click(1, "Menu"), click(2, "Relatório Mensal")]

    assert nome(acoes, "https://erp.exemplo.local/app/pedido.jsp") == "clicar_relatorio_mensal_pedido"


def test_clique_sem_texto_usa_acao_no_nome():
    assert nome([click(1, "")], "https://erp.exemplo.local/app/pedido.jsp") == "clicar_acao_pedido"


def test_so_navegacao_vira_navegar():
    assert nome([nav(1)], "https://erp.exemplo.local/app/pedido.jsp") == "navegar_pedido"


def test_navegacao_e_cliques_vira_executar():
    assert nome([nav(1), click(2, "Ok")], "https://erp.exemplo.local/app/pedido.jsp") == "executar_pedido"


def test_so_teclas_vira_executar():
    assert nome([tecla(1)], "https://erp.exemplo.local/app/pedido.jsp") == "executar_pedido"


# ---------------------------------------------------------------------------
# name_groups — nome da página
# ---------------------------------------------------------------------------

def test_fragment_com_significado_entra_no_nome():
    assert nome([nav(1)], "https://erp.exemplo.local/app/nxj#pedi_f130") == "navegar_nxj_pedi_f130"


@pytest.mark.parametrize("fragment", ["0x", "1", "12x"])
def test_fragment_numerico_nao_entra_no_nome(fragment):
    assert nome([nav(1)], f"https://erp.exemplo.local/app/nxj#{fragment}") == "navegar_nxj"


def test_fragment_sem_path_usa_so_o_fragment():
    assert nome([nav(1)], "https://erp.exemplo.local/#tela_pedidos") == "navegar_tela_pedidos"


def test_sem_path_usa_o_host():
    assert nome([nav(1)], "https://erp.exemplo.local/") == "navegar_erp_exemplo_local"


def test_sem_url_usa_pagina():
    assert jp.name_groups([{"url": "", "actions": [nav(1, "")]}])[0]["tool_name"] == "navegar_pagina"


def test_nomes_repetidos_ganham_sufixo_numerico():
    grupos = [grupo([nav(1)]), grupo([click(2, "Ok")]), grupo([nav(3)]), grupo([nav(4)])]

    nomes = [g["tool_name"] for g in jp.name_groups(grupos)]

    assert nomes == ["navegar_form", "clicar_ok_form", "navegar_form_2", "navegar_form_3"]


def test_name_groups_devolve_nome_url_e_acoes():
    acoes = [nav(1)]

    assert jp.name_groups([grupo(acoes)]) == [{"tool_name": "navegar_form", "url": jp._screen_key(URL), "actions": acoes}]


def test_nomes_gerados_sao_nomes_de_tool_validos():
    grupos = [grupo([click(1, "Relatório: 2º Trimestre (%)")], "https://erp.exemplo.local/app/Pedido-Venda.jsp#Tela 1"),
              grupo([nav(2)], "https://192.168.0.10/")]

    assert all(jp.is_valid_tool_name(g["tool_name"]) for g in jp.name_groups(grupos))


def test_name_steps_e_o_mesmo_que_name_groups():
    grupos = [grupo([nav(1)]), grupo([fill(2, "A")])]

    assert jp.name_steps(grupos) == jp.name_groups(grupos)


# ---------------------------------------------------------------------------
# _actions_fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_e_deterministico_com_12_hex():
    fp = jp._actions_fingerprint([fill(1, "A"), click(2)])

    assert fp == jp._actions_fingerprint([fill(1, "A"), click(2)])
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)


@pytest.mark.parametrize("campo, novo", [
    ("type", "click"), ("xpath", 'id("outro")'), ("css_selector", "#outro"),
    ("value", "outro valor"), ("url", "https://erp.exemplo.local/outra"),
])
def test_fingerprint_muda_quando_a_acao_muda(campo, novo):
    base = fill(1, "A")
    alterada = dict(base, **{campo: novo})

    assert jp._actions_fingerprint([alterada]) != jp._actions_fingerprint([base])


def test_fingerprint_muda_com_tecla_e_repeticoes():
    base = tecla(1, "Tab")

    assert jp._actions_fingerprint([dict(base, key="Enter")]) != jp._actions_fingerprint([base])
    assert jp._actions_fingerprint([dict(base, count=3)]) != jp._actions_fingerprint([base])


def test_fingerprint_muda_com_a_ordem_e_a_quantidade_de_acoes():
    a, b = fill(1, "A"), click(2)

    assert jp._actions_fingerprint([a, b]) != jp._actions_fingerprint([b, a])
    assert jp._actions_fingerprint([a, b]) != jp._actions_fingerprint([a])


def test_fingerprint_ignora_tempo_e_textos():
    base = fill(1, "A")
    outra = dict(base, timestamp=99999, timeout_ms=180000, target_text="Outro rótulo")

    assert jp._actions_fingerprint([outra]) == jp._actions_fingerprint([base])


def test_fingerprint_de_senha_nao_depende_do_valor_nem_do_secret():
    mascarada = fill(1, "SENHA", valor="********", masked=True)
    outra_mascara = fill(1, "SENHA", valor="************", masked=True)
    com_secret = fill(1, "SENHA", masked=True, secret="ERP_SENHA")
    com_secret.pop("value")

    fps = {jp._actions_fingerprint([x]) for x in (mascarada, outra_mascara, com_secret)}
    assert len(fps) == 1
    assert fps != {jp._actions_fingerprint([fill(1, "SENHA", valor="********")])}


# ---------------------------------------------------------------------------
# _read_tool_fingerprint
# ---------------------------------------------------------------------------

def _tool(tmp_path, nome_tool, conteudo):
    p = tmp_path / "tools" / f"{nome_tool}.py"
    p.write_text(conteudo, encoding="utf-8")
    return p


def test_le_fingerprint_do_cabecalho(tmp_path):
    p = _tool(tmp_path, "t", "# actions_fp:  abc123def456 \nfrom playwright.async_api import Page\n")

    assert jp._read_tool_fingerprint(p) == "abc123def456"


def test_le_fingerprint_ate_a_terceira_linha(tmp_path):
    p = _tool(tmp_path, "t", "# coding\n\n# actions_fp: abc\n")

    assert jp._read_tool_fingerprint(p) == "abc"


def test_cabecalho_depois_da_terceira_linha_nao_conta(tmp_path):
    p = _tool(tmp_path, "t", "a = 1\nb = 2\nc = 3\n# actions_fp: abc\n")

    assert jp._read_tool_fingerprint(p) is None


def test_tool_sem_cabecalho_e_sem_banco_devolve_none(tmp_path):
    p = _tool(tmp_path, "t", "async def t(page):\n    return True\n")

    assert jp._read_tool_fingerprint(p) is None


def test_tool_inexistente_sem_banco_devolve_none(tmp_path):
    assert jp._read_tool_fingerprint(tmp_path / "tools" / "nao_existe.py") is None


def test_tool_sem_cabecalho_usa_fingerprint_do_banco(pg, tmp_path):
    import db

    db.upsert_tool("editada_a_mao", "async def editada_a_mao(page):\n    return True\n", "fp_do_banco1")
    p = _tool(tmp_path, "editada_a_mao", "async def editada_a_mao(page):\n    return True\n")

    assert jp._read_tool_fingerprint(p) == "fp_do_banco1"


def test_cabecalho_do_arquivo_tem_prioridade_sobre_o_banco(pg, tmp_path):
    import db

    db.upsert_tool("com_cabecalho", "x", "fp_do_banco1")
    p = _tool(tmp_path, "com_cabecalho", "# actions_fp: fp_do_arquivo\n")

    assert jp._read_tool_fingerprint(p) == "fp_do_arquivo"


def test_tool_so_no_banco_usa_fingerprint_do_banco(pg, tmp_path):
    import db

    db.upsert_tool("so_no_banco", "x", "fp_do_banco2")

    assert jp._read_tool_fingerprint(tmp_path / "tools" / "so_no_banco.py") == "fp_do_banco2"


def test_tool_fora_do_banco_devolve_none(pg, tmp_path):
    p = _tool(tmp_path, "nao_cadastrada", "async def nao_cadastrada(page):\n    return True\n")

    assert jp._read_tool_fingerprint(p) is None


def test_erro_ao_consultar_o_banco_devolve_none(monkeypatch, tmp_path):
    import db

    def quebrado(_):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(db, "get_tool_fingerprint", quebrado)

    assert jp._read_tool_fingerprint(tmp_path / "tools" / "x.py") is None


# ---------------------------------------------------------------------------
# resolve_tool_names
# ---------------------------------------------------------------------------

ACOES = [nav(1), fill(2, "A"), click(3, "Salvar")]
OUTRAS = [nav(1), fill(2, "B"), click(3, "Salvar")]


def _existentes(tmp_path, **tools):
    """Cria tools em tools/ com o fingerprint das ações dadas: nome=acoes."""
    for nome_tool, acoes in tools.items():
        _tool(tmp_path, nome_tool, f"# actions_fp: {jp._actions_fingerprint(acoes)}\n")
    return set(tools)


def test_nome_novo_e_mantido(tmp_path):
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", set()) == ["preencher_form"]
    assert passos[0]["tool_name"] == "preencher_form"


def test_tool_existente_com_mesmas_acoes_e_reutilizada(tmp_path):
    existentes = _existentes(tmp_path, preencher_form=ACOES)
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", existentes) == ["preencher_form"]


def test_tool_existente_com_acoes_diferentes_vira_v2(tmp_path):
    existentes = _existentes(tmp_path, preencher_form=OUTRAS)
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", existentes) == ["preencher_form_v2"]
    assert passos[0]["tool_name"] == "preencher_form_v2"


def test_v2_tambem_diferente_vira_v3(tmp_path):
    existentes = _existentes(tmp_path, preencher_form=OUTRAS, preencher_form_v2=[click(9)])
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", existentes) == ["preencher_form_v3"]


def test_v2_com_mesmas_acoes_e_reutilizada(tmp_path):
    existentes = _existentes(tmp_path, preencher_form=OUTRAS, preencher_form_v2=ACOES)
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", existentes) == ["preencher_form_v2"]


def test_nome_reservado_sem_arquivo_conta_como_diferente(tmp_path):
    """Nome reservado por outra geração (ainda sem arquivo) não pode ser reutilizado."""
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", {"preencher_form"}) == ["preencher_form_v2"]


def test_resolve_cada_passo_do_fluxo(tmp_path):
    existentes = _existentes(tmp_path, navegar_form=[nav(1)], preencher_form=OUTRAS)
    passos = [{"tool_name": "navegar_form", "actions": [nav(1)]},
              {"tool_name": "preencher_form", "actions": ACOES},
              {"tool_name": "clicar_ok_form", "actions": [click(5, "Ok")]}]

    assert jp.resolve_tool_names(passos, tmp_path / "tools", existentes) == \
        ["navegar_form", "preencher_form_v2", "clicar_ok_form"]


# ---------------------------------------------------------------------------
# save_tools (gerador falso — nada de OpenAI)
# ---------------------------------------------------------------------------

@pytest.fixture
def gerador(monkeypatch, tmp_path):
    """Troca o LLM por um gerador que grava a tool e registra as chamadas."""
    chamadas = []
    rejeitar = set()

    def gerar(func_name, actions, fingerprint="", expect_url_change=False):
        chamadas.append({"nome": func_name, "acoes": actions, "fp": fingerprint, "url_muda": expect_url_change})
        if func_name in rejeitar:
            return None
        p = tmp_path / "tools" / f"{func_name}.py"
        p.write_text(f"# actions_fp: {fingerprint}\n", encoding="utf-8")
        return p

    monkeypatch.setattr(tool_generator, "generate_tool_from_actions", gerar)
    monkeypatch.setattr(tool_generator, "expects_url_change", lambda steps, i: i == 0)
    gerar.chamadas = chamadas
    gerar.rejeitar = rejeitar
    return gerar


def test_save_tools_gera_tool_nova_com_fingerprint(gerador, tmp_path):
    existentes = set()
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    salvas = jp.save_tools(passos, tmp_path / "tools", existentes)

    assert salvas == ["preencher_form"]
    assert gerador.chamadas == [{"nome": "preencher_form", "acoes": ACOES,
                                 "fp": jp._actions_fingerprint(ACOES), "url_muda": True}]
    assert existentes == {"preencher_form"}


def test_save_tools_reutiliza_tool_identica_sem_chamar_o_llm(gerador, tmp_path):
    existentes = _existentes(tmp_path, preencher_form=ACOES)
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.save_tools(passos, tmp_path / "tools", existentes) == ["preencher_form"]
    assert gerador.chamadas == []


def test_save_tools_gera_v2_quando_as_acoes_mudaram(gerador, tmp_path):
    existentes = _existentes(tmp_path, preencher_form=OUTRAS)
    passos = [{"tool_name": "preencher_form", "actions": ACOES}]

    assert jp.save_tools(passos, tmp_path / "tools", existentes) == ["preencher_form_v2"]
    assert [c["nome"] for c in gerador.chamadas] == ["preencher_form_v2"]
    assert passos[0]["tool_name"] == "preencher_form_v2"
    assert (tmp_path / "tools" / "preencher_form_v2.py").exists()


def test_save_tools_tool_rejeitada_fica_de_fora(gerador, tmp_path):
    gerador.rejeitar.add("navegar_form")
    existentes = set()
    passos = [{"tool_name": "navegar_form", "actions": [nav(1)]},
              {"tool_name": "preencher_form", "actions": ACOES}]

    salvas = jp.save_tools(passos, tmp_path / "tools", existentes)

    assert salvas == ["preencher_form"]
    assert passos[0]["rejected"] is True
    assert "rejected" not in passos[1]
    assert existentes == {"preencher_form"}
    assert [c["url_muda"] for c in gerador.chamadas] == [True, False]


def test_save_tools_passa_o_secret_e_nao_a_mascara_para_o_gerador(gerador, tmp_path, monkeypatch):
    import builtins
    import getpass

    import secrets_store

    respostas = iter(["ERP_SENHA"])
    monkeypatch.setattr(builtins, "input", lambda _="": next(respostas))
    monkeypatch.setattr(getpass, "getpass", lambda _="": "valor-sintetico")
    passos = [{"tool_name": "fazer_login_form",
               "actions": [fill(1, "USUARIO"), fill(2, "SENHA", valor="********", masked=True), click(3, "Entrar")]}]

    jp.save_tools(passos, tmp_path / "tools", set())

    senha = gerador.chamadas[0]["acoes"][1]
    assert senha["secret"] == "ERP_SENHA"
    assert "value" not in senha
    assert secrets_store.get_secret("ERP_SENHA") == "valor-sintetico"
