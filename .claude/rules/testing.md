# Regras para a pasta tests/

A pasta `tests/` contém scripts de automação RPA para o ERP Systextil.

## O que são esses arquivos

Scripts Playwright Python completos que automatizam fluxos no ERP da empresa.
Cada script importa tools de `tools/` e as executa em sequência.

## Como executar

```bash
python tests/nome_do_script.py
```

O browser deve estar aberto com CDP em `http://localhost:9222`.

## O que NÃO fazer

- NÃO rodar `pytest`, `unittest` ou qualquer test runner nesses arquivos
- NÃO tratar erros de automação como falhas de teste de software
- NÃO criar fixtures, mocks ou helpers de teste — os scripts são diretos ao ponto
- Arquivos com prefixo `rpa_run_` são temporários gerados pelo servidor — ignorar

Testes unitários do código do sistema ficam em `testes_unitarios/` (ver [tdd.md](tdd.md)).
