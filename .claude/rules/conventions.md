# Convenções do projeto

## Nomenclatura de pastas (CRÍTICO)

- `tools/` — componentes/etapas Playwright reutilizáveis, NÃO ferramentas de IA
- `tests/` — scripts de automação para o ERP, NÃO testes unitários
- `json_rpa/` — JSONs gravados pela extensão de browser (input)

## Nunca fazer

- Rodar `pytest` na pasta `tests/` — os scripts são executados diretamente com `python tests/nome.py`
- Salvar tools automaticamente sem aprovação do usuário — o fluxo é sempre: gerar → revisar → aprovar/rejeitar
- Criar browser dentro de uma tool — `page` sempre vem como parâmetro via CDP

## Padrão de tool

Toda tool em `tools/` deve seguir:
1. Primeira linha: `# actions_fp: <hash_md5>` (fingerprint das ações)
2. Segunda linha: `from playwright.async_api import Page`
3. Terceira linha: `from rpa_runtime import confirmar_login, localizar, login_necessario, secret`
4. Função: `async def nome_da_tool(page) -> bool:`
5. Retorno: `True` (sem erros) ou `False` (com erros)
6. Nomes em snake_case português
7. Elementos via `localizar(...)`; senhas via `secret("NOME")` (nunca o valor no código)
8. Tools de login pulam o login quando a sessão já está aberta (`login_necessario`)

## Versionamento de tools

- Mesma tool com ações diferentes → sufixo `_v2`, `_v3`, etc.
- Fingerprint idêntico → reutiliza sem regerar

## Linguagem

Respostas e comentários no código em português brasileiro.
