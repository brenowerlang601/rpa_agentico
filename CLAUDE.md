# RPA Agêntico — Guia para Claude

## O que é esse projeto

Ferramenta para gravar fluxos de automação em um ERP (Systextil) via extensão de browser, e transformá-los em scripts Playwright Python reutilizáveis. O fluxo é: gravar → JSON → gerar tools via LLM → montar script → executar.

## Arquitetura

```
json_rpa/       → JSONs gravados pela extensão (input do sistema)
tools/          → Componentes/etapas reutilizáveis do script RPA (ex: fazer_login_forms.py)
                  ATENÇÃO: NÃO são ferramentas de IA — são funções Playwright async
tests/          → Scripts de automação gerados para o ERP (ex: pedido_venda.py)
                  ATENÇÃO: NÃO são testes unitários — são os scripts finais que rodam no ERP
templates/      → HTML do frontend (index.html — interface visual Flask)
```

### Arquivos principais

- [app.py](app.py) — Servidor Flask, todos os endpoints REST + SSE
- [json_processor.py](json_processor.py) — Processa JSON bruto: normaliza ações, agrupa em micro-processos, nomeia tools, resolve versões por fingerprint
- [tool_generator.py](tool_generator.py) — Gera código Playwright via LLM (GPT-4o via OpenAI), salva em `tools/`
- [rpa_runtime.py](rpa_runtime.py) — Funções usadas pelas tools: `localizar` (escolhe o seletor único e espera o tempo gravado), `login_necessario`/`confirmar_login`, `secret`
- [secrets_store.py](secrets_store.py) — Secrets (senhas) usados pelas tools: tabela `secrets` no banco ou `secrets.json` local
- [gravacoes_store.py](gravacoes_store.py) — JSONs gravados pela extensão: tabela `gravacoes` no banco ou pasta `json_rpa/` local
- [templates/index.html](templates/index.html) — Frontend completo: canvas drag-and-drop, editor inline, preview do script, terminal de execução

## Convenções críticas

### Tools (`tools/`)
- Cada arquivo é uma função `async def nome_da_tool(page) -> bool:`
- Cabeçalho obrigatório: `# actions_fp: <hash>` (fingerprint MD5 das ações) + `from playwright.async_api import Page` + `from rpa_runtime import ...`
- Elementos são localizados com `await localizar(page, xpath=..., css=..., texto=[...], timeout=...)` — nunca `page.locator` direto
- Senhas nunca ficam no código: `secret("NOME")` lê da aba Secrets (a extensão grava senhas como `********`)
- Tools de login verificam se a tela de login está presente (`login_necessario`) e pulam o login se a sessão já estiver aberta
- Retorna `True` (sucesso) ou `False` (falhou)
- Conecta via CDP já estabelecida — `page` é recebido como parâmetro, nunca cria browser
- Nomes em snake_case português, ex: `fazer_login_forms`, `preencher_e_confirmar_pagina`

### Scripts de teste (`tests/`)
- São scripts Playwright completos que importam e executam tools em sequência
- Gerados por `save_test_script()` ou pelo botão "Salvar" no frontend
- NÃO rodar pytest nesses arquivos — executar diretamente: `python tests/nome.py`
- Scripts temporários de execução têm prefixo `rpa_run_` e são deletados automaticamente

### Agrupamento das ações em tools
- Mudança de URL abre uma nova tool
- Na mesma URL (SPAs, ERPs que trocam de tela sem mudar a URL), sequências com mais de 6 ações são quebradas em pontos naturais: fim do login, clique seguido de espera longa (≥ 3 s na gravação) e clique que envia campos preenchidos
- O login sempre vira uma tool própria
- O tempo de espera de cada ação vem da gravação (`timeout_ms`: 2× o intervalo gravado + 5 s, entre 10 s e 180 s)

### Versionamento de tools
- Se já existe tool com mesmo nome mas ações diferentes → cria `nome_v2`, `nome_v3`, etc.
- Se fingerprint idêntico → reutiliza sem regerar (mesmo comportamento)

### Aprovação de tools
- Tools geradas via LLM **não são salvas automaticamente** — o usuário revisa e aprova/rejeita/pede revisão
- Fluxo via SSE: `__GENERATING__` → `__REVIEW__` → aguarda resposta → `__APPROVED__` ou `__REJECTED__`
- Tool rejeitada é deletada do disco imediatamente

## Execução

```bash
# Iniciar servidor
python app.py  # porta 5000

# Executar script diretamente (sem frontend)
python tests/nome_do_script.py
```

O browser deve estar aberto com CDP em `http://localhost:9222`.

## LLM usado

GPT-4o via OpenAI API (`OPENAI_API_KEY` no `.env`). Modelo configurado em [tool_generator.py](tool_generator.py).
