"""
Armazenamento dos secrets (senhas e valores sensíveis usados pelas tools).

Funcionam como variáveis de ambiente: um NOME_EM_MAIUSCULAS e um valor. As tools nunca
guardam o valor — chamam secret("NOME") (rpa_runtime) e o valor é lido daqui.

Com DATABASE_URL o banco é a fonte (tabela secrets). Sem banco, usa o arquivo local
secrets.json (fora do git, permissão 600).
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import db

SECRETS_FILE = Path(__file__).parent / "secrets.json"
NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def is_valid_name(name) -> bool:
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name))  # fullmatch: "$" aceitaria "NOME\n"


def _read_file() -> dict:
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _read_file_para_escrita() -> dict:
    """
    Como _read_file, mas recusa arquivo corrompido: regravar a partir de {} apagaria os
    outros secrets (que ainda dá para recuperar à mão no arquivo).
    """
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ValueError(f"{SECRETS_FILE.name} está corrompido — corrija ou remova o arquivo "
                         f"antes de salvar secrets ({e})") from e


def _write_file(data: dict) -> None:
    SECRETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(SECRETS_FILE, 0o600)


def list_secrets() -> list[dict]:
    """Nomes e data de atualização — nunca os valores."""
    if db.db_enabled():
        return db.list_secrets()
    return [{"name": k, "updated_at": v.get("updated_at")} for k, v in sorted(_read_file().items())]


def get_secret(name: str) -> str | None:
    if db.db_enabled():
        return db.get_secret_value(name)
    item = _read_file().get(name)
    return item["value"] if item else None


def all_values() -> dict[str, str]:
    """Todos os secrets como {NOME: valor} — usado para injetar no ambiente da execução."""
    if db.db_enabled():
        return db.all_secret_values()
    return {k: v["value"] for k, v in _read_file().items()}


def set_secret(name: str, value: str) -> None:
    """Cria ou atualiza. Levanta ValueError para nome inválido e db.DBError se o banco falhar."""
    if not is_valid_name(name):
        raise ValueError("Nome inválido: use MAIÚSCULAS, números e _ (ex.: ERP_SENHA)")
    if db.db_enabled():
        db.upsert_secret(name, value)
        return
    data = _read_file_para_escrita()
    data[name] = {"value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
    _write_file(data)


def delete_secret(name: str) -> bool:
    if db.db_enabled():
        return db.delete_secret(name)
    data = _read_file()
    if name not in data:
        return False
    del data[name]
    _write_file(data)
    return True
