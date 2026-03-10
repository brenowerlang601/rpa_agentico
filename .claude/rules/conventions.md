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
3. Função: `async def nome_da_tool(page) -> bool:`
4. Retorno: `True` (sem erros) ou `False` (com erros)
5. Nomes em snake_case português

## Versionamento de tools

- Mesma tool com ações diferentes → sufixo `_v2`, `_v3`, etc.
- Fingerprint idêntico → reutiliza sem regerar

## Linguagem

Respostas e comentários no código em português brasileiro.
