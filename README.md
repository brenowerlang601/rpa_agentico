# RPA Agêntico

Ferramenta para gravar fluxos de automação em um ERP (Systextil) via extensão de browser e transformá-los em scripts Playwright Python reutilizáveis, com geração de código via LLM.

## Como funciona

```
Extensão de browser → JSON gravado → Processamento → Geração via LLM → Revisão humana → Script pronto → Execução
```

1. O usuário grava um fluxo no ERP com a extensão de browser, que salva as ações em JSON
2. O JSON é processado, as ações são agrupadas em micro-processos e nomeadas automaticamente
3. Um LLM (GPT-4o) gera o código Playwright para cada etapa (tool)
4. O usuário revisa, aprova ou rejeita cada tool gerada
5. As tools aprovadas são combinadas em um script final e executadas no browser via CDP

## Estrutura

```
app.py              → Servidor Flask — endpoints REST + SSE
json_processor.py   → Processa JSON bruto: normaliza ações, agrupa em micro-processos
tool_generator.py   → Gera código Playwright via LLM (GPT-4o), salva em tools/
db.py               → Persistência PostgreSQL (fonte de verdade)
orchestrator.py     → Orquestração de execuções
qa_code_analyzer.py → Análise de qualidade do código gerado
qa_test_generator.py → Geração de testes de qualidade

json_rpa/           → JSONs gravados pela extensão (input do sistema)
tools/              → Funções Playwright async reutilizáveis (etapas do fluxo)
tests/              → Scripts de automação completos gerados para o ERP
templates/          → Frontend HTML (interface visual Flask)
```

## Pré-requisitos

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)
- PostgreSQL
- Browser com CDP habilitado em `http://localhost:9222`
- Chave de API da OpenAI

## Instalação

```bash
# Instalar dependências
uv sync

# Instalar browser Playwright
uv run playwright install chromium --with-deps

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com DATABASE_URL e OPENAI_API_KEY
```

### Variáveis de ambiente (`.env`)

```
DATABASE_URL=postgresql://user:password@host:5432/rpa_db
OPENAI_API_KEY=sk-...
```

## Uso

### Iniciando o servidor

```bash
python app.py
# Acesse http://localhost:5000
```

### Executando um script diretamente

```bash
python tests/nome_do_script.py
```

> O browser deve estar aberto com CDP em `http://localhost:9222`

## Docker

```bash
docker build -t rpa-agentico .
docker run -p 5000:5000 --env-file .env rpa-agentico
```

## Padrão das Tools

Cada arquivo em `tools/` segue a estrutura:

```python
# actions_fp: <hash_md5_das_acoes>
from playwright.async_api import Page

async def nome_da_tool(page) -> bool:
    errors = []
    try:
        # ação
    except Exception as e:
        errors.append(("ação", str(e)))
    
    if errors:
        print(f"[FAIL] nome_da_tool: {len(errors)} erro(s)")
        return False
    print("[OK] nome_da_tool")
    return True
```

- `page` sempre vem como parâmetro — nunca cria browser
- Retorna `True` (sucesso) ou `False` (falhou)
- Nomes em snake_case português

## Versionamento de tools

- Mesma tool com ações diferentes → sufixo `_v2`, `_v3`, etc.
- Fingerprint idêntico → reutiliza sem regerar

## Fluxo de aprovação de tools

Tools geradas via LLM não são salvas automaticamente. O fluxo é:

```
__GENERATING__ → __REVIEW__ → usuário aprova/rejeita/pede revisão → __APPROVED__ ou __REJECTED__
```

Tool rejeitada é deletada do disco imediatamente.

## Tecnologias

- **Flask** — servidor web e SSE
- **Playwright** — automação de browser
- **GPT-4o (OpenAI)** — geração de código
- **PostgreSQL** — persistência de tools e workflows
- **uv** — gerenciamento de dependências
- **Docker + Gunicorn** — deploy em container
