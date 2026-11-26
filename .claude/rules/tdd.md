# TDD — teste que falha antes, código depois

Toda função nova, correção de bug ou mudança de comportamento no código do sistema
(`app.py`, `db.py`, `json_processor.py`, `tool_generator.py`, `rpa_runtime.py`,
`secrets_store.py`, `gravacoes_store.py` e módulos novos) segue o ciclo:

1. **RED** — escrever o teste em `testes_unitarios/test_<assunto>.py` descrevendo o
   comportamento esperado.
2. **Ver falhar** — rodar só esse teste e conferir que falha **pelo motivo certo**
   (a função não existe / o comportamento não existe), não por erro de digitação ou import.
   Se passar de primeira, o teste não prova nada: refazer.
3. **GREEN** — escrever o mínimo de código para o teste passar.
4. **Ver passar** — rodar a suíte inteira (`uv run pytest`), não só o arquivo novo.
5. **Refatorar** com a suíte verde.

Use a skill `superpowers:test-driven-development` (e `superpowers:writing-plans`, que já escreve
cada tarefa como "teste que falha → implementação → teste passa").

Bug reportado → primeiro um teste que reproduz o bug e falha; só depois a correção.

## Comandos

```bash
uv run pytest                                   # suíte (só testes_unitarios/)
uv run pytest testes_unitarios/test_x.py -k nome  # um teste
# Com Postgres de teste (sem a variável, os testes de banco são pulados):
docker run -d --rm --name rpa-pg-teste -e POSTGRES_PASSWORD=teste \
    -e POSTGRES_DB=rpa_teste -p 55499:5432 postgres:16-alpine
RPA_PG_TEST_DSN=postgresql://postgres:teste@127.0.0.1:55499/rpa_teste uv run pytest
```

Ao terminar, informe o resultado da suíte (antes → depois) e o que **não** foi testado e por quê.

## Onde ficam e onde NÃO ficam os testes

- Testes unitários: **só** em `testes_unitarios/` (o `pyproject.toml` restringe o pytest a ela).
- `tests/` são scripts RPA do ERP — **nunca** coletar com pytest nem criar teste unitário lá.
- `tools/` é código gerado pelo LLM e aprovado pelo usuário — não exige teste antes
  (exceção prevista no TDD: código gerado). Mudanças no **gerador** (`tool_generator.py`,
  `json_processor.py`) exigem.

## Regras dos testes

- Nada de banco de produção, OpenAI ou ERP: o `conftest.py` zera `DATABASE_URL` e
  `OPENAI_API_KEY` antes dos imports e isola `json_rpa/` e `secrets.json` em pasta temporária.
- Banco de verdade só pelo fixture `pg` (Postgres de teste; recusa banco sem "teste" no nome).
- Dados sintéticos (`testes_unitarios/gravacao_exemplo.py`) — nunca copiar gravação real do ERP.
- Nome do teste descreve o comportamento em português (`test_upload_de_json_quebrado_e_recusado...`).
- Testar comportamento real (endpoint pelo `client`, função pública); mock só quando inevitável.
