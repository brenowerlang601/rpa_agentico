"""
Secrets (secrets_store.py): arquivo local secrets.json (modo local) ou tabela secrets (banco).

O conftest troca SECRETS_FILE por um arquivo na pasta temporária do teste.
"""

import json
import os
import stat

import pytest

import db
import secrets_store
from db import DBError

BANCO_FORA_DO_AR = "postgresql://usuario:senha@127.0.0.1:1/rpa_teste"


def _modo(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def arquivo():
    return secrets_store.SECRETS_FILE


@pytest.fixture
def umask_aberto():
    """umask 022 (o padrão de quase todo sistema): arquivo novo nasce 644."""
    anterior = os.umask(0o022)
    yield
    os.umask(anterior)


# ---------------------------------------------------------------------------
# is_valid_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome", ["ERP_SENHA", "A", "_INTERNO", "SENHA2", "A_1_B"])
def test_is_valid_name_aceita_maiusculas_numeros_e_sublinhado(nome):
    assert secrets_store.is_valid_name(nome) is True


@pytest.mark.parametrize("nome", [
    "erp_senha", "Erp_Senha", "2SENHA", "ERP-SENHA", "ERP SENHA", "SENHA_É", "", None, 123, ["A"],
])
def test_is_valid_name_recusa_minusculas_inicio_numerico_e_outros_tipos(nome):
    assert secrets_store.is_valid_name(nome) is False


@pytest.mark.xfail(strict=True, reason=(
    "BUG: NAME_RE usa '$', que casa antes de um '\\n' final — 'ERP_SENHA\\n' é aceito como "
    "nome válido (deveria usar fullmatch ou '\\Z')"))
def test_is_valid_name_recusa_quebra_de_linha_no_fim():
    assert secrets_store.is_valid_name("ERP_SENHA\n") is False


# ---------------------------------------------------------------------------
# Modo local (secrets.json)
# ---------------------------------------------------------------------------

def test_set_secret_cria_arquivo_com_permissao_600(arquivo):
    secrets_store.set_secret("ERP_SENHA", "s3nh@")

    assert _modo(arquivo) == 0o600


def test_set_secret_corrige_permissao_de_arquivo_existente_aberto(arquivo):
    arquivo.write_text("{}", encoding="utf-8")
    os.chmod(arquivo, 0o644)

    secrets_store.set_secret("ERP_SENHA", "s3nh@")

    assert _modo(arquivo) == 0o600


@pytest.mark.xfail(strict=True, reason=(
    "BUG (baixo risco): _write_file escreve primeiro e só depois faz chmod 600 — na criação, "
    "o secrets.json com a senha em texto existe por um instante com a permissão do umask (644)"))
def test_secrets_json_nunca_existe_legivel_por_outros_usuarios(arquivo, monkeypatch, umask_aberto):
    chmod_real = os.chmod
    modos_vistos = []

    def chmod_espiao(path, mode, *a, **kw):
        modos_vistos.append(_modo(arquivo))
        return chmod_real(path, mode, *a, **kw)

    monkeypatch.setattr(secrets_store.os, "chmod", chmod_espiao)

    secrets_store.set_secret("ERP_SENHA", "s3nh@")

    assert modos_vistos and all(m & 0o077 == 0 for m in modos_vistos)


def test_set_e_get_secret_no_arquivo_local(arquivo):
    secrets_store.set_secret("ERP_SENHA", "s3nh@ çã")

    assert secrets_store.get_secret("ERP_SENHA") == "s3nh@ çã"
    salvo = json.loads(arquivo.read_text(encoding="utf-8"))
    assert salvo["ERP_SENHA"]["value"] == "s3nh@ çã"
    assert salvo["ERP_SENHA"]["updated_at"]


def test_get_secret_inexistente_devolve_none():
    secrets_store.set_secret("A", "1")

    assert secrets_store.get_secret("B") is None


def test_get_secret_sem_arquivo_devolve_none(arquivo):
    assert not arquivo.exists()
    assert secrets_store.get_secret("A") is None


def test_set_secret_mesmo_nome_atualiza_valor_e_data_sem_duplicar(arquivo, monkeypatch):
    secrets_store.set_secret("ERP_SENHA", "antiga")
    antes = json.loads(arquivo.read_text(encoding="utf-8"))["ERP_SENHA"]["updated_at"]

    secrets_store.set_secret("ERP_SENHA", "nova")

    salvo = json.loads(arquivo.read_text(encoding="utf-8"))
    assert list(salvo) == ["ERP_SENHA"]
    assert salvo["ERP_SENHA"]["value"] == "nova"
    assert salvo["ERP_SENHA"]["updated_at"] > antes


def test_set_secret_preserva_os_outros_secrets(arquivo):
    secrets_store.set_secret("A", "1")
    secrets_store.set_secret("B", "2")

    assert secrets_store.all_values() == {"A": "1", "B": "2"}


@pytest.mark.parametrize("nome", ["erp_senha", "../X", "", None])
def test_set_secret_nome_invalido_levanta_valueerror_e_nao_cria_arquivo(arquivo, nome):
    with pytest.raises(ValueError, match="Nome inválido"):
        secrets_store.set_secret(nome, "valor")

    assert not arquivo.exists()


def test_list_secrets_local_devolve_nomes_ordenados_e_nunca_valores():
    secrets_store.set_secret("ZETA", "valor-z")
    secrets_store.set_secret("ALFA", "valor-a")

    itens = secrets_store.list_secrets()

    assert [i["name"] for i in itens] == ["ALFA", "ZETA"]
    assert all(set(i) == {"name", "updated_at"} for i in itens)
    assert "valor-" not in json.dumps(itens)


def test_list_secrets_sem_arquivo_devolve_lista_vazia():
    assert secrets_store.list_secrets() == []


def test_all_values_local_devolve_nome_e_valor():
    secrets_store.set_secret("A", "1")
    secrets_store.set_secret("B", "2")

    assert secrets_store.all_values() == {"A": "1", "B": "2"}


def test_all_values_sem_arquivo_devolve_dicionario_vazio():
    assert secrets_store.all_values() == {}


def test_delete_secret_local_remove_e_devolve_true(arquivo):
    secrets_store.set_secret("A", "1")
    secrets_store.set_secret("B", "2")

    assert secrets_store.delete_secret("A") is True

    assert json.loads(arquivo.read_text(encoding="utf-8")).keys() == {"B"}
    assert _modo(arquivo) == 0o600


def test_delete_secret_local_inexistente_devolve_false_sem_reescrever(arquivo):
    secrets_store.set_secret("A", "1")
    os.utime(arquivo, (1_000_000_000, 1_000_000_000))

    assert secrets_store.delete_secret("NAO_EXISTE") is False
    assert arquivo.stat().st_mtime == 1_000_000_000


def test_delete_secret_sem_arquivo_devolve_false(arquivo):
    assert secrets_store.delete_secret("A") is False
    assert not arquivo.exists()


def test_arquivo_corrompido_e_lido_como_vazio(arquivo):
    arquivo.write_text("{ isto não é json", encoding="utf-8")

    assert secrets_store.list_secrets() == []
    assert secrets_store.get_secret("A") is None
    assert secrets_store.all_values() == {}


def test_arquivo_com_bytes_invalidos_e_lido_como_vazio(arquivo):
    arquivo.write_bytes(b"\xff\xfe\x00")

    assert secrets_store.list_secrets() == []


@pytest.mark.xfail(strict=True, reason=(
    "BUG: com secrets.json corrompido, _read_file devolve {} e set_secret regrava o arquivo só "
    "com o secret novo — os demais (recuperáveis à mão) são apagados sem aviso"))
def test_set_secret_com_arquivo_corrompido_nao_apaga_o_conteudo(arquivo):
    corrompido = '{"ERP_SENHA": {"value": "s3nh@", "updated_at": "2026-01-01"},'
    arquivo.write_text(corrompido, encoding="utf-8")

    try:
        secrets_store.set_secret("OUTRO", "x")
    except ValueError:
        pass  # recusar a escrita também é aceitável

    assert "s3nh@" in arquivo.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Modo banco
# ---------------------------------------------------------------------------

def test_modo_banco_set_get_list_e_all_values_usam_a_tabela(pg, arquivo):
    secrets_store.set_secret("ZETA", "valor-z")
    secrets_store.set_secret("ALFA", "valor-a")

    assert secrets_store.get_secret("ALFA") == "valor-a"
    assert secrets_store.get_secret("NAO_EXISTE") is None
    assert [i["name"] for i in secrets_store.list_secrets()] == ["ALFA", "ZETA"]
    assert all(set(i) == {"name", "updated_at"} for i in secrets_store.list_secrets())
    assert secrets_store.all_values() == {"ALFA": "valor-a", "ZETA": "valor-z"}
    assert db.all_secret_values() == {"ALFA": "valor-a", "ZETA": "valor-z"}
    assert not arquivo.exists()


def test_modo_banco_ignora_secrets_json_local(pg, arquivo):
    arquivo.write_text(json.dumps({"LOCAL": {"value": "do-arquivo", "updated_at": "x"}}), encoding="utf-8")

    assert secrets_store.get_secret("LOCAL") is None
    assert secrets_store.list_secrets() == []
    assert secrets_store.all_values() == {}


def test_modo_banco_set_secret_atualiza_sem_duplicar(pg):
    secrets_store.set_secret("ERP_SENHA", "antiga")
    secrets_store.set_secret("ERP_SENHA", "nova")

    assert secrets_store.all_values() == {"ERP_SENHA": "nova"}


def test_modo_banco_delete_secret(pg):
    secrets_store.set_secret("A", "1")

    assert secrets_store.delete_secret("A") is True
    assert secrets_store.delete_secret("A") is False
    assert secrets_store.all_values() == {}


def test_modo_banco_nome_invalido_nao_chega_ao_banco(pg, monkeypatch):
    chamadas = []
    monkeypatch.setattr(db, "upsert_secret", lambda *a: chamadas.append(a))

    with pytest.raises(ValueError):
        secrets_store.set_secret("minusculo", "x")

    assert chamadas == []


def test_banco_fora_do_ar_set_e_delete_levantam_dberror_sem_criar_arquivo(monkeypatch, arquivo):
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA_DO_AR)

    with pytest.raises(DBError):
        secrets_store.set_secret("ERP_SENHA", "x")
    with pytest.raises(DBError):
        secrets_store.delete_secret("ERP_SENHA")

    assert not arquivo.exists()


def test_banco_fora_do_ar_leituras_nao_caem_para_o_arquivo_local(monkeypatch, arquivo):
    """Comportamento atual: leitura devolve vazio (ver xfail de list_secrets em test_db.py)."""
    arquivo.write_text(json.dumps({"A": {"value": "local", "updated_at": "x"}}), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", BANCO_FORA_DO_AR)

    assert secrets_store.get_secret("A") is None
    assert secrets_store.all_values() == {}
    assert secrets_store.list_secrets() == []
