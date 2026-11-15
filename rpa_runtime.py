"""
Funções de apoio usadas pelas tools geradas em tools/.

Concentram aqui o que o LLM errava ao gerar o código diretamente:
- localizar(): escolhe, entre os seletores gravados, o que aponta para UM elemento visível.
  Os cssSelector da extensão costumam ser genéricos (ex.: "button.ghost") e casam vários
  elementos; o xpath é preciso mas pode mudar em SPAs. Também espera o tempo certo para
  elementos que dependem de processamento lento (ex.: resposta de IA).
- login_necessario() / confirmar_login(): o login só é feito se a tela de login estiver
  presente, e depois é confirmado.
- secret(): lê senhas e outros valores sensíveis da aba Secrets (nunca ficam no código).
"""

import asyncio
import os
import time

# Fração do tempo de espera em que só xpath/css são aceitos. Os seletores por texto
# entram depois, como plano B — evita clicar num texto igual que já estava na tela
# antes de o elemento gravado aparecer.
_FRACAO_SO_SELETORES = 0.6


def _como_lista(texto) -> list[str]:
    if not texto:
        return []
    if isinstance(texto, str):
        return [texto]
    return list(dict.fromkeys(t for t in texto if t))


def _candidatos(page, xpath, css, textos, incluir_texto: bool):
    cands = []
    if xpath:
        cands.append((f"xpath={xpath}", page.locator(f"xpath={xpath}")))
    if css:
        cands.append((f"css={css}", page.locator(css)))
    if incluir_texto:
        for t in textos:
            t = t.strip()
            cands += [
                (f"label={t}", page.get_by_label(t, exact=True)),
                (f"placeholder={t}", page.get_by_placeholder(t, exact=True)),
                (f"botão={t}", page.get_by_role("button", name=t, exact=True)),
                (f"link={t}", page.get_by_role("link", name=t, exact=True)),
                (f"texto={t}", page.get_by_text(t, exact=True)),
            ]
    return cands


async def _visiveis(loc, limite: int = 20) -> list[int]:
    try:
        n = await loc.count()
    except Exception:
        return []
    idx = []
    for i in range(min(n, limite)):
        try:
            if await loc.nth(i).is_visible():
                idx.append(i)
        except Exception:
            pass
    return idx


async def localizar(page, *, xpath: str | None = None, css: str | None = None,
                    texto=None, timeout: int = 10000):
    """
    Retorna o Locator de UM elemento visível, testando os seletores gravados em ordem:
    xpath → css → textos (label, placeholder, botão, link, texto).
    Espera até `timeout` ms. Se só encontrar seletores ambíguos, usa o primeiro elemento
    visível do mais específico e avisa. Levanta TimeoutError se nada aparecer.
    """
    textos = _como_lista(texto)
    inicio = time.monotonic()
    limite = inicio + timeout / 1000
    so_seletores_ate = inicio + (timeout / 1000) * _FRACAO_SO_SELETORES
    if not (xpath or css):
        so_seletores_ate = inicio

    while True:
        agora = time.monotonic()
        ambiguo = None
        for nome, loc in _candidatos(page, xpath, css, textos, incluir_texto=agora >= so_seletores_ate):
            vis = await _visiveis(loc)
            if len(vis) == 1:
                return loc.nth(vis[0])
            if len(vis) > 1 and ambiguo is None:
                ambiguo = (nome, loc, vis)
        if agora >= limite:
            if ambiguo:
                nome, loc, vis = ambiguo
                print(f"  [aviso] seletor ambíguo ({nome} casou {len(vis)} elementos) — usando o primeiro", flush=True)
                return loc.nth(vis[0])
            raise TimeoutError(
                f"Elemento não encontrado em {timeout} ms (xpath={xpath!r}, css={css!r}, texto={textos!r})"
            )
        await asyncio.sleep(0.25)


async def login_necessario(page, *, xpath: str | None = None, css: str | None = None,
                           texto=None, timeout: int = 5000) -> bool:
    """True se o campo de login aparecer em até `timeout` ms (ou seja, a sessão não está aberta)."""
    try:
        await localizar(page, xpath=xpath, css=css, texto=texto, timeout=timeout)
        return True
    except TimeoutError:
        return False


async def confirmar_login(page, *, xpath: str | None = None, css: str | None = None,
                          texto=None, timeout: int = 15000) -> bool:
    """True se o campo de login sumir em até `timeout` ms depois de enviar o login."""
    limite = time.monotonic() + timeout / 1000
    while time.monotonic() < limite:
        if not await login_necessario(page, xpath=xpath, css=css, texto=texto, timeout=500):
            return True
        await asyncio.sleep(0.5)
    return False


def secret(nome: str) -> str:
    """
    Valor de um secret da aba Secrets. Na execução pela interface o servidor já injeta os
    secrets como variáveis de ambiente; ao rodar `python tests/x.py` direto, busca no banco
    (ou no secrets.json local).
    """
    valor = os.environ.get(nome)
    if valor is not None:
        return valor
    from secrets_store import get_secret
    valor = get_secret(nome)
    if valor is None:
        raise KeyError(f"Secret '{nome}' não cadastrado — crie na aba Secrets da interface")
    return valor
