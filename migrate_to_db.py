"""
Script de migração única: lê tools, workflows e gravações (json_rpa/) do disco e persiste no banco.
Rodar uma vez manualmente antes de subir o container:

    python migrate_to_db.py
"""

import json
from pathlib import Path

from db import init_db, upsert_gravacao, upsert_tool, upsert_workflow
from gravacoes_store import JSON_RPA_DIR, nome_valido
from json_processor import TESTS_DIR, TOOLS_DIR, _read_tool_fingerprint

init_db()

print("=== Migrando tools ===")
for p in sorted(TOOLS_DIR.glob("*.py")):
    if p.stem == "__init__":
        continue
    code = p.read_text(encoding="utf-8")
    fp = _read_tool_fingerprint(p) or ""
    upsert_tool(p.stem, code, fp)
    print(f"  [OK] tool: {p.stem}")

print("\n=== Migrando workflows ===")
for p in sorted(TESTS_DIR.glob("*.py")):
    if p.stem == "__init__" or p.stem.startswith("rpa_run_"):
        continue
    code = p.read_text(encoding="utf-8")
    steps = []
    for line in code.splitlines():
        if line.startswith("        results["):
            steps.append(line.strip().split("'")[1])
    upsert_workflow(p.stem, code, steps)
    print(f"  [OK] workflow: {p.stem}")

print("\n=== Migrando gravações (json_rpa/) ===")
for p in sorted(JSON_RPA_DIR.glob("*.json")):
    if not nome_valido(p.name):
        print(f"  [PULADO] nome inválido: {p.name}")
        continue
    try:
        conteudo = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        print(f"  [PULADO] JSON inválido: {p.name} ({e})")
        continue
    upsert_gravacao(p.name, conteudo)
    print(f"  [OK] gravação: {p.name}")

print("\nMigração concluída.")
