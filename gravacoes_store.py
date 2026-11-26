"""
Armazenamento das gravações (JSONs gravados pela extensão de browser).

Com DATABASE_URL o banco é a fonte (tabela gravacoes) — em produção o disco do container
é descartado a cada deploy. Sem banco, usa a pasta local json_rpa/.

Diferente das tools, a gravação não precisa existir em disco: ela só é lida e processada,
nunca importada pelo Python.
"""

import json
from pathlib import Path

import db

JSON_RPA_DIR = Path(__file__).parent / "json_rpa"


def nome_valido(name) -> bool:
    """
    Nome de arquivo .json simples. Bloqueia ../, barras e caminhos absolutos
    (o nome vem do usuário e, no modo local, vira caminho em disco).
    """
    if not isinstance(name, str) or not name.endswith(".json"):
        return False
    return name == Path(name).name and not name.startswith(".") and "\\" not in name


def listar() -> list[str]:
    """Nomes das gravações disponíveis (ex.: 'pedido_venda.json')."""
    if db.db_enabled():
        return db.list_gravacoes()
    return [p.name for p in sorted(JSON_RPA_DIR.glob("*.json"))]


def carregar(name: str) -> dict | None:
    """
    Conteúdo da gravação, ou None se não existir.
    Levanta ValueError se o JSON em disco estiver corrompido e db.DBError se o banco falhar.
    """
    if not nome_valido(name):
        return None
    if db.db_enabled():
        return db.get_gravacao(name)
    path = JSON_RPA_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def salvar(name: str, conteudo: dict) -> None:
    """
    Cria ou substitui a gravação com esse nome.
    Levanta ValueError para nome inválido e db.DBError se o banco falhar.
    """
    if not nome_valido(name):
        raise ValueError("Nome de arquivo inválido — envie um arquivo .json")
    if db.db_enabled():
        db.upsert_gravacao(name, conteudo)
        return
    JSON_RPA_DIR.mkdir(exist_ok=True)
    (JSON_RPA_DIR / name).write_text(
        json.dumps(conteudo, indent=2, ensure_ascii=False), encoding="utf-8"
    )
