"""
restore_from_db: no startup o banco reconstitui tools/ e tests/ no disco.

Regras conferidas: escreve o que falta, sobrescreve só arquivo com conteúdo diferente,
não reescreve arquivo idêntico e nunca apaga arquivo que não está no banco.
"""

import os
from pathlib import Path

import pytest

import db

BANCO_FORA_DO_AR = "postgresql://usuario:senha@127.0.0.1:1/rpa_teste"
_MTIME_ANTIGO = 1_000_000_000  # 2001 — qualquer reescrita muda o mtime para "agora"


@pytest.fixture
def pastas(tmp_path):
    return tmp_path / "restore" / "tools", tmp_path / "restore" / "tests"


def _arquivo_antigo(path: Path, conteudo: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(conteudo, encoding="utf-8")
    os.utime(path, (_MTIME_ANTIGO, _MTIME_ANTIGO))
    return path


def test_restore_cria_as_pastas_e_escreve_tools_e_workflows_do_banco(pg, pastas):
    tools_dir, tests_dir = pastas
    db.upsert_tool("fazer_login", "# codigo da tool\n", "fp1")
    db.upsert_workflow("pedido_venda", "# codigo do script\n", ["fazer_login"])

    db.restore_from_db(tools_dir, tests_dir)

    assert (tools_dir / "fazer_login.py").read_text(encoding="utf-8") == "# codigo da tool\n"
    assert (tests_dir / "pedido_venda.py").read_text(encoding="utf-8") == "# codigo do script\n"
    assert sorted(p.name for p in tools_dir.iterdir()) == ["fazer_login.py"]
    assert sorted(p.name for p in tests_dir.iterdir()) == ["pedido_venda.py"]


def test_restore_preserva_acentos_do_codigo(pg, pastas):
    tools_dir, tests_dir = pastas
    db.upsert_tool("preencher_endereco", "# São Paulo — ação\n")

    db.restore_from_db(tools_dir, tests_dir)

    assert (tools_dir / "preencher_endereco.py").read_text(encoding="utf-8") == "# São Paulo — ação\n"


def test_restore_nao_reescreve_arquivo_identico(pg, pastas):
    tools_dir, tests_dir = pastas
    db.upsert_tool("fazer_login", "igual")
    db.upsert_workflow("pedido_venda", "igual wf", [])
    tool = _arquivo_antigo(tools_dir / "fazer_login.py", "igual")
    wf = _arquivo_antigo(tests_dir / "pedido_venda.py", "igual wf")

    db.restore_from_db(tools_dir, tests_dir)

    assert tool.stat().st_mtime == _MTIME_ANTIGO
    assert wf.stat().st_mtime == _MTIME_ANTIGO


def test_restore_sobrescreve_arquivo_diferente_com_a_versao_do_banco(pg, pastas):
    """
    O banco é a fonte de verdade: edição feita direto no disco (fora da interface) é
    descartada no próximo startup.
    """
    tools_dir, tests_dir = pastas
    db.upsert_tool("fazer_login", "versao do banco")
    db.upsert_workflow("pedido_venda", "wf do banco", [])
    _arquivo_antigo(tools_dir / "fazer_login.py", "editado a mao no disco")
    _arquivo_antigo(tests_dir / "pedido_venda.py", "wf editado no disco")

    db.restore_from_db(tools_dir, tests_dir)

    assert (tools_dir / "fazer_login.py").read_text(encoding="utf-8") == "versao do banco"
    assert (tests_dir / "pedido_venda.py").read_text(encoding="utf-8") == "wf do banco"


def test_restore_nao_apaga_arquivos_que_nao_estao_no_banco(pg, pastas):
    tools_dir, tests_dir = pastas
    db.upsert_tool("no_banco", "x")
    so_disco_tool = _arquivo_antigo(tools_dir / "so_no_disco.py", "tool local")
    so_disco_wf = _arquivo_antigo(tests_dir / "script_local.py", "wf local")

    db.restore_from_db(tools_dir, tests_dir)

    assert so_disco_tool.read_text(encoding="utf-8") == "tool local"
    assert so_disco_wf.read_text(encoding="utf-8") == "wf local"
    assert (tools_dir / "no_banco.py").exists()


def test_restore_grava_workflow_em_tests_e_tool_em_tools(pg, pastas):
    tools_dir, tests_dir = pastas
    db.upsert_tool("mesmo_nome", "codigo da tool")
    db.upsert_workflow("mesmo_nome", "codigo do workflow", [])

    db.restore_from_db(tools_dir, tests_dir)

    assert (tools_dir / "mesmo_nome.py").read_text(encoding="utf-8") == "codigo da tool"
    assert (tests_dir / "mesmo_nome.py").read_text(encoding="utf-8") == "codigo do workflow"


def test_restore_no_modo_local_so_cria_as_pastas(pastas):
    tools_dir, tests_dir = pastas
    _arquivo_antigo(tools_dir / "local.py", "local")

    db.restore_from_db(tools_dir, tests_dir)

    assert tests_dir.is_dir()
    assert [p.name for p in tools_dir.iterdir()] == ["local.py"]
    assert (tools_dir / "local.py").read_text(encoding="utf-8") == "local"


def test_restore_com_banco_fora_do_ar_nao_levanta_nem_mexe_no_disco(monkeypatch, pastas):
    tools_dir, tests_dir = pastas
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA_DO_AR)
    tool = _arquivo_antigo(tools_dir / "fazer_login.py", "local")

    db.restore_from_db(tools_dir, tests_dir)

    assert [p.name for p in tools_dir.iterdir()] == ["fazer_login.py"]
    assert tool.read_text(encoding="utf-8") == "local"
    assert tool.stat().st_mtime == _MTIME_ANTIGO
