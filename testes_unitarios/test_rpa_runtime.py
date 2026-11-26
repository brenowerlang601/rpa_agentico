"""
Funções de apoio das tools (rpa_runtime.py).

localizar / login_necessario / confirmar_login rodam contra um Chromium REAL em headless com
HTML local (page.set_content) — nada de CDP, ERP ou rede. Os timeouts são curtos.
"""

import asyncio
import time

import pytest
from playwright.async_api import async_playwright

import rpa_runtime
import secrets_store


def _com_pagina(html, corpo):
    """Abre um Chromium headless com `html` e roda `await corpo(page)`; devolve o resultado."""

    async def rodar():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(html)
                return await corpo(page)
            finally:
                await browser.close()

    return asyncio.run(rodar())


async def _cronometrar(coro):
    inicio = time.monotonic()
    resultado = await coro
    return resultado, time.monotonic() - inicio


# ---------------------------------------------------------------------------
# _como_lista
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entrada, esperado", [
    (None, []),
    ("", []),
    ([], []),
    ("Confirmar", ["Confirmar"]),
    (["OK", "", "Salvar", "OK", None], ["OK", "Salvar"]),
    (("A", "B"), ["A", "B"]),
])
def test_texto_vira_lista_sem_vazios_e_sem_repetidos(entrada, esperado):
    assert rpa_runtime._como_lista(entrada) == esperado


# ---------------------------------------------------------------------------
# _candidatos
# ---------------------------------------------------------------------------

def test_candidatos_seguem_a_ordem_xpath_css_e_textos():
    async def corpo(page):
        return [n for n, _ in rpa_runtime._candidatos(page, 'id("a")', "button.ok", [" Salvar "], True)]

    assert _com_pagina("<p></p>", corpo) == [
        'xpath=id("a")', "css=button.ok",
        "label=Salvar", "placeholder=Salvar", "botão=Salvar", "link=Salvar", "texto=Salvar",
    ]


def test_candidatos_sem_textos_ate_o_plano_b():
    async def corpo(page):
        return [n for n, _ in rpa_runtime._candidatos(page, None, "button.ok", ["Salvar"], False)]

    assert _com_pagina("<p></p>", corpo) == ["css=button.ok"]


# ---------------------------------------------------------------------------
# _visiveis
# ---------------------------------------------------------------------------

def test_visiveis_ignora_elementos_escondidos():
    html = "<button>A</button><button style='display:none'>B</button><button>C</button>"

    async def corpo(page):
        return await rpa_runtime._visiveis(page.locator("button"))

    assert _com_pagina(html, corpo) == [0, 2]


def test_visiveis_respeita_o_limite():
    html = "<button>x</button>" * 5

    async def corpo(page):
        return await rpa_runtime._visiveis(page.locator("button"), limite=3)

    assert _com_pagina(html, corpo) == [0, 1, 2]


def test_visiveis_de_seletor_invalido_e_lista_vazia():
    async def corpo(page):
        return await rpa_runtime._visiveis(page.locator("xpath=//*[[["))

    assert _com_pagina("<button>x</button>", corpo) == []


# ---------------------------------------------------------------------------
# localizar
# ---------------------------------------------------------------------------

def test_localizar_usa_o_xpath_quando_ele_e_unico():
    html = "<button class='ghost' id='ok'>OK</button><button class='ghost'>Cancelar</button>"

    async def corpo(page):
        loc = await rpa_runtime.localizar(page, xpath='id("ok")', css="button.ghost", timeout=2000)
        return await loc.inner_text()

    assert _com_pagina(html, corpo) == "OK"


def test_localizar_troca_xpath_ambiguo_pelo_css_unico():
    html = "<div><button>A</button><button class='alvo'>B</button></div>"

    async def corpo(page):
        loc = await rpa_runtime.localizar(page, xpath="//div/button", css="button.alvo", timeout=2000)
        return await loc.inner_text()

    assert _com_pagina(html, corpo) == "B"


def test_localizar_ignora_copia_escondida_do_elemento():
    html = "<button class='ok' style='display:none'>Velho</button><button class='ok'>Novo</button>"

    async def corpo(page):
        loc, dt = await _cronometrar(rpa_runtime.localizar(page, css="button.ok", timeout=2000))
        return await loc.inner_text(), dt

    texto, dt = _com_pagina(html, corpo)
    assert texto == "Novo"
    assert dt < 1.0  # único visível: não espera o timeout


def test_localizar_so_com_seletores_ambiguos_usa_o_primeiro_depois_do_timeout(capsys):
    html = "<button class='ghost'>Primeiro</button><button class='ghost'>Segundo</button>"

    async def corpo(page):
        loc, dt = await _cronometrar(rpa_runtime.localizar(page, css="button.ghost", timeout=800))
        return await loc.inner_text(), dt

    texto, dt = _com_pagina(html, corpo)
    assert texto == "Primeiro"
    assert 0.75 <= dt < 1.45  # espera o tempo todo (e só ele) antes de aceitar o ambíguo
    assert "seletor ambíguo (css=button.ghost casou 2 elementos)" in capsys.readouterr().out


def test_localizar_levanta_timeout_quando_nada_aparece():
    async def corpo(page):
        inicio = time.monotonic()
        with pytest.raises(TimeoutError) as erro:
            await rpa_runtime.localizar(page, xpath='id("nao_existe")', texto="Nada", timeout=700)
        return str(erro.value), time.monotonic() - inicio

    mensagem, dt = _com_pagina("<button>OK</button>", corpo)
    assert "700 ms" in mensagem and "nao_existe" in mensagem and "Nada" in mensagem
    assert 0.65 <= dt < 1.3


def test_localizar_espera_elemento_que_aparece_depois():
    html = """<div id='raiz'></div>
    <script>setTimeout(() => { document.getElementById('raiz').innerHTML =
        "<button id='tarde'>Resposta</button>"; }, 600);</script>"""

    async def corpo(page):
        loc, dt = await _cronometrar(rpa_runtime.localizar(page, xpath='id("tarde")', timeout=3000))
        return await loc.inner_text(), dt

    texto, dt = _com_pagina(html, corpo)
    assert texto == "Resposta"
    assert 0.4 <= dt < 2.0


def test_localizar_so_tenta_o_texto_depois_de_60_por_cento_do_tempo():
    # O texto "Confirmar" já está na tela, mas o xpath gravado não existe: o plano B por
    # texto só entra depois de 60% do timeout (evita clicar num texto igual antigo).
    html = "<button>Confirmar</button>"

    async def corpo(page):
        loc, dt = await _cronometrar(
            rpa_runtime.localizar(page, xpath='id("novo")', texto=["Confirmar"], timeout=1500))
        return await loc.inner_text(), dt

    texto, dt = _com_pagina(html, corpo)
    assert texto == "Confirmar"
    assert 0.85 <= dt < 1.5


def test_localizar_so_com_texto_procura_o_texto_imediatamente():
    html = "<label>Usuário <input id='u'></label>"

    async def corpo(page):
        loc, dt = await _cronometrar(rpa_runtime.localizar(page, texto="Usuário", timeout=3000))
        return await loc.get_attribute("id"), dt

    ident, dt = _com_pagina(html, corpo)
    assert ident == "u"
    assert dt < 1.0


@pytest.mark.parametrize("html, texto, esperado", [
    ("<input id='alvo' placeholder='Pesquisar'>", "Pesquisar", "alvo"),
    ("<button id='alvo'>Gravar</button><span>Gravar pedido</span>", "Gravar", "alvo"),
    ("<a id='alvo' href='#'>Ajuda</a>", "Ajuda", "alvo"),
    ("<span id='alvo'>Pedido 123</span>", "Pedido 123", "alvo"),
])
def test_localizar_por_texto_acha_placeholder_botao_link_e_texto(html, texto, esperado):
    async def corpo(page):
        loc = await rpa_runtime.localizar(page, texto=texto, timeout=2000)
        return await loc.get_attribute("id")

    assert _com_pagina(html, corpo) == esperado


# ---------------------------------------------------------------------------
# login_necessario / confirmar_login
# ---------------------------------------------------------------------------

def test_login_necessario_quando_a_tela_de_login_esta_presente():
    async def corpo(page):
        return await rpa_runtime.login_necessario(page, xpath='id("usuario")', timeout=1000)

    assert _com_pagina("<input id='usuario'>", corpo) is True


def test_login_necessario_espera_a_tela_de_login_carregar():
    html = """<div id='raiz'></div>
    <script>setTimeout(() => { document.getElementById('raiz').innerHTML =
        "<input id='usuario'>"; }, 500);</script>"""

    async def corpo(page):
        return await rpa_runtime.login_necessario(page, xpath='id("usuario")', timeout=2500)

    assert _com_pagina(html, corpo) is True


def test_login_desnecessario_quando_a_sessao_ja_esta_aberta():
    async def corpo(page):
        return await _cronometrar(rpa_runtime.login_necessario(page, xpath='id("usuario")', timeout=500))

    resultado, dt = _com_pagina("<h1>Menu principal</h1>", corpo)
    assert resultado is False
    assert dt < 2.0


def test_confirmar_login_quando_a_tela_de_login_some():
    html = """<input id='usuario'>
    <script>setTimeout(() => document.getElementById('usuario').remove(), 500);</script>"""

    async def corpo(page):
        return await _cronometrar(rpa_runtime.confirmar_login(page, xpath='id("usuario")', timeout=4000))

    resultado, dt = _com_pagina(html, corpo)
    assert resultado is True
    assert dt < 3.5


def test_confirmar_login_falha_quando_a_tela_de_login_continua():
    async def corpo(page):
        return await _cronometrar(rpa_runtime.confirmar_login(page, xpath='id("usuario")', timeout=1200))

    resultado, dt = _com_pagina("<input id='usuario'>", corpo)
    assert resultado is False
    assert dt >= 1.15


# ---------------------------------------------------------------------------
# secret
# ---------------------------------------------------------------------------

def test_secret_le_da_aba_secrets(monkeypatch):
    monkeypatch.delenv("ERP_SENHA", raising=False)
    secrets_store.set_secret("ERP_SENHA", "s3nha")

    assert rpa_runtime.secret("ERP_SENHA") == "s3nha"


def test_secret_injetado_no_ambiente_tem_prioridade(monkeypatch):
    secrets_store.set_secret("ERP_SENHA", "do_arquivo")
    monkeypatch.setenv("ERP_SENHA", "do_ambiente")

    assert rpa_runtime.secret("ERP_SENHA") == "do_ambiente"


def test_secret_vazio_no_ambiente_e_respeitado(monkeypatch):
    secrets_store.set_secret("ERP_SENHA", "do_arquivo")
    monkeypatch.setenv("ERP_SENHA", "")

    assert rpa_runtime.secret("ERP_SENHA") == ""


def test_secret_inexistente_da_erro_claro(monkeypatch):
    monkeypatch.delenv("NAO_CADASTRADO", raising=False)

    with pytest.raises(KeyError, match="NAO_CADASTRADO.*aba Secrets"):
        rpa_runtime.secret("NAO_CADASTRADO")
