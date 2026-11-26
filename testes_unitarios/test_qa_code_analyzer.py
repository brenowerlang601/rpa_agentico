"""
Busca de função/método num repositório de código (qa_code_analyzer).

Nada de rede: cada teste monta um repositório git LOCAL em tmp_path com arquivos sintéticos
e o clona por caminho / file://. O cache de clones (_REPOS_CACHE) também vai para tmp_path.
"""

import subprocess
import threading

import pytest

import qa_code_analyzer as qa


# ---------------------------------------------------------------------------
# Fixtures e auxiliares
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def git_isolado(monkeypatch):
    """Ignora a configuração git do usuário/sistema (insteadOf, hooks etc.) e fixa o autor."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Teste")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "teste@exemplo.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Teste")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "teste@exemplo.invalid")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("LC_ALL", "C")  # mensagens do git em inglês, para conferir o stderr
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """Cache de clones isolado por teste (o padrão é /tmp/rpa_repos)."""
    pasta = tmp_path / "cache"
    pasta.mkdir()
    monkeypatch.setattr(qa, "_REPOS_CACHE", pasta)
    return pasta


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _escrever(repo, arquivos):
    for rel, conteudo in arquivos.items():
        destino = repo / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo, encoding="utf-8")


def criar_repo(base, arquivos, nome="origem"):
    """git init + um commit com os arquivos informados. Devolve o caminho do repositório."""
    repo = base / nome
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _escrever(repo, arquivos)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "inicial")
    return repo


def novo_commit(repo, arquivos):
    _escrever(repo, arquivos)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mudança")


JAVA_SERVICO = """\
package com.exemplo.vendas;

import java.util.List;
import java.math.BigDecimal;

public class CalculadoraPedido {

    public BigDecimal calcularTotal(List<BigDecimal> itens) {
        BigDecimal total = BigDecimal.ZERO;
        for (BigDecimal item : itens) {
            total = total.add(item);
        }
        return total;
    }

    private int calcular(int a, int b) {
        String s = "chave } solta";
        return a + b;
    }

    public static class Interna {
        protected String formatarCodigo(String codigo) throws IllegalArgumentException {
            if (codigo == null) {
                throw new IllegalArgumentException("vazio");
            }
            return codigo.trim();
        }
    }
}
"""

JS_UTIL = """\
import { arredondar } from './numeros';
const path = require('path');
require('dotenv');

export async function buscarCliente(id) {
  const resp = await fetch(`/clientes/${id}`);
  return resp.json();
}

export const somar = (a, b) => {
  return a + b;
};

class Carrinho {
  adicionarItem(item) {
    this.itens.push(item);
    return this.itens.length;
  }
}
"""

TS_COMPONENTE = """\
import React from 'react';

export class ServicoFrete {
  private calcularFrete(peso: number, destino: string): number {
    if (peso <= 0) {
      return 0;
    }
    return peso * 2.5;
  }
}
"""

APEX_CLASSE = """\
public class ContaService {
    public static Decimal calcularSaldo(Id contaId) {
        Decimal saldo = 0;
        return saldo;
    }
}
"""


# ---------------------------------------------------------------------------
# fetch_function_from_repo (ponta a ponta, repositório git local)
# ---------------------------------------------------------------------------

def test_busca_metodo_java_no_repositorio_clonado(cache, tmp_path):
    origem = criar_repo(tmp_path, {"src/main/java/CalculadoraPedido.java": JAVA_SERVICO})

    r = qa.fetch_function_from_repo(str(origem), "calcularTotal")

    assert r["language"] == "java"
    assert r["framework"] == "junit5"
    assert r["file_path"] == "src/main/java/CalculadoraPedido.java"
    assert r["function_name"] == "calcularTotal"
    assert r["function_code"].startswith("public BigDecimal calcularTotal(List<BigDecimal> itens) {")
    assert r["function_code"].endswith("return total;\n    }")
    assert "package com.exemplo.vendas;" in r["file_context"]
    assert "public class CalculadoraPedido {" in r["file_context"]


def test_busca_funcao_javascript_via_url_file(cache, tmp_path):
    origem = criar_repo(tmp_path, {"src/util.js": JS_UTIL, "package.json": '{"name": "x"}'})

    r = qa.fetch_function_from_repo(origem.as_uri(), "buscarCliente")

    assert r["language"] == "javascript"
    assert r["framework"] == "jest"
    assert r["file_path"] == "src/util.js"
    assert r["function_code"].startswith("export async function buscarCliente(id) {")
    assert r["function_code"].endswith("return resp.json();\n}")


def test_busca_metodo_typescript_em_projeto_react_usa_jest_rtl(cache, tmp_path):
    origem = criar_repo(tmp_path, {
        "src/frete.ts": TS_COMPONENTE,
        "package.json": '{"dependencies": {"react": "^18.0.0"}}',
    })

    r = qa.fetch_function_from_repo(origem.as_uri(), "calcularFrete")

    assert r["language"] == "typescript"
    assert r["framework"] == "jest-rtl"
    assert r["function_code"].startswith("private calcularFrete(peso: number, destino: string): number {")
    assert r["function_code"].endswith("return peso * 2.5;\n  }")


def test_busca_metodo_apex(cache, tmp_path):
    origem = criar_repo(tmp_path, {"force-app/classes/ContaService.cls": APEX_CLASSE})

    r = qa.fetch_function_from_repo(origem.as_uri(), "calcularSaldo")

    assert r["language"] == "apex"
    assert r["framework"] == "apex-test"
    assert r["file_path"] == "force-app/classes/ContaService.cls"
    assert r["function_code"].startswith("public static Decimal calcularSaldo(Id contaId) {")


def test_busca_metodo_java_de_classe_interna(cache, tmp_path):
    origem = criar_repo(tmp_path, {"Calc.java": JAVA_SERVICO})

    r = qa.fetch_function_from_repo(str(origem), "formatarCodigo")

    assert r["function_code"].startswith(
        "protected String formatarCodigo(String codigo) throws IllegalArgumentException {")
    assert r["function_code"].endswith("return codigo.trim();\n        }")


def test_funcao_inexistente_levanta_erro_claro(cache, tmp_path):
    origem = criar_repo(tmp_path, {"Calc.java": JAVA_SERVICO, "util.js": JS_UTIL})

    with pytest.raises(ValueError, match="Função 'naoExiste' não encontrada"):
        qa.fetch_function_from_repo(str(origem), "naoExiste")


def test_funcao_que_so_existe_em_python_nao_e_encontrada(cache, tmp_path):
    """Python não é linguagem suportada: arquivos .py não são varridos."""
    # O .py até contém um trecho JS numa string — mesmo assim não pode ser considerado
    origem = criar_repo(tmp_path, {"servico.py": (
        'def calcular_imposto(valor):\n'
        '    return """function calcularImposto(v) {\n  return v;\n}"""\n')})

    with pytest.raises(ValueError, match="não encontrada"):
        qa.fetch_function_from_repo(str(origem), "calcular_imposto")
    with pytest.raises(ValueError, match="não encontrada"):
        qa.fetch_function_from_repo(str(origem), "calcularImposto")


def test_arquivo_indicado_pelo_usuario_e_usado_na_busca_ponta_a_ponta(cache, tmp_path):
    origem = criar_repo(tmp_path, {
        "Calc.java": "class C {\n    int calcular() {\n        return 1;\n    }\n}\n",
        "web/calc.js": "export function calcular() {\n  return 2;\n}\n",
    })

    sem_hint = qa.fetch_function_from_repo(str(origem), "calcular")
    com_hint = qa.fetch_function_from_repo(str(origem), "calcular", hint_file_path="web/calc.js")

    assert sem_hint["file_path"] == "Calc.java" and sem_hint["framework"] == "junit5"
    assert com_hint["file_path"] == "web/calc.js"
    assert com_hint["language"] == "javascript" and com_hint["framework"] == "jest"


def test_url_invalida_levanta_value_error_com_a_mensagem_do_git(cache, tmp_path):
    inexistente = tmp_path / "nao_existe"

    with pytest.raises(ValueError, match=r"Falha ao clonar .*nao_existe: fatal: .*does not exist"):
        qa.fetch_function_from_repo(str(inexistente), "qualquer")


# ---------------------------------------------------------------------------
# _repo_cache_dir / _repo_lock
# ---------------------------------------------------------------------------

def test_pasta_de_cache_tem_prefixo_legivel_sem_git_nem_barra_final(cache):
    a = qa._repo_cache_dir("https://exemplo.invalid/org/meu-repo.git")
    b = qa._repo_cache_dir("https://exemplo.invalid/org/meu-repo/")

    assert a.parent == cache
    assert a.name.startswith("meu-repo_") and len(a.name) == len("meu-repo_") + 12
    assert b.name.startswith("meu-repo_")


def test_pasta_de_cache_e_estavel_e_diferente_por_url(cache):
    url1 = "https://exemplo.invalid/a/repo.git"
    url2 = "https://exemplo.invalid/b/repo.git"

    assert qa._repo_cache_dir(url1) == qa._repo_cache_dir(url1)
    assert qa._repo_cache_dir(url1) != qa._repo_cache_dir(url2)


def test_lock_e_o_mesmo_para_o_mesmo_repositorio_e_outro_para_repositorio_diferente(tmp_path):
    a1 = qa._repo_lock(tmp_path / "a")
    a2 = qa._repo_lock(tmp_path / "a")
    b = qa._repo_lock(tmp_path / "b")

    assert a1 is a2
    assert a1 is not b


# ---------------------------------------------------------------------------
# _is_valid_clone
# ---------------------------------------------------------------------------

def test_pasta_sem_git_nao_e_clone_valido(tmp_path):
    (tmp_path / "parcial").mkdir()
    assert qa._is_valid_clone(tmp_path / "parcial") is False


def test_repositorio_sem_commit_nao_e_clone_valido(tmp_path):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    _git(vazio, "init", "-q")

    assert qa._is_valid_clone(vazio) is False


def test_repositorio_com_commit_e_clone_valido(tmp_path):
    repo = criar_repo(tmp_path, {"A.java": "class A {}\n"})
    assert qa._is_valid_clone(repo) is True


# ---------------------------------------------------------------------------
# _clone_or_update
# ---------------------------------------------------------------------------

def test_clone_vai_para_o_cache_e_nao_deixa_pasta_temporaria(cache, tmp_path):
    origem = criar_repo(tmp_path, {"A.java": JAVA_SERVICO})

    destino = qa._clone_or_update(str(origem))

    assert destino == qa._repo_cache_dir(str(origem))
    assert (destino / "A.java").read_text() == JAVA_SERVICO
    assert [p.name for p in cache.iterdir()] == [destino.name]


def test_clone_que_falha_nao_deixa_nada_no_cache(cache, tmp_path):
    with pytest.raises(ValueError):
        qa._clone_or_update(str(tmp_path / "nao_existe"))

    assert list(cache.iterdir()) == []


def test_clone_com_tempo_esgotado_levanta_value_error_e_limpa_a_pasta(cache, tmp_path, monkeypatch):
    def run_lento(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(qa.subprocess, "run", run_lento)

    with pytest.raises(ValueError, match="Tempo esgotado ao clonar"):
        qa._clone_or_update("https://exemplo.invalid/org/lento.git")
    assert list(cache.iterdir()) == []


def test_segunda_chamada_atualiza_o_cache_com_o_commit_novo(cache, tmp_path):
    origem = criar_repo(tmp_path, {"A.java": "class A { void v1() {} }\n"})
    qa._clone_or_update(origem.as_uri())

    novo_commit(origem, {"A.java": "class A { void v2() {} }\n"})
    destino = qa._clone_or_update(origem.as_uri())

    assert "v2" in (destino / "A.java").read_text()


def test_sem_acesso_a_origem_usa_a_copia_em_cache(cache, tmp_path, caplog):
    origem = criar_repo(tmp_path, {"A.java": "class A { void v1() {} }\n"})
    url = origem.as_uri()
    qa._clone_or_update(url)
    origem.rename(tmp_path / "sumiu")

    with caplog.at_level("WARNING", logger="qa_code_analyzer"):
        destino = qa._clone_or_update(url)

    assert "v1" in (destino / "A.java").read_text()
    assert "usando cópia em cache" in caplog.text


def test_clone_interrompido_no_cache_e_descartado_e_clonado_de_novo(cache, tmp_path):
    origem = criar_repo(tmp_path, {"A.java": JAVA_SERVICO})
    parcial = qa._repo_cache_dir(str(origem))
    parcial.mkdir()
    (parcial / "lixo.txt").write_text("clone interrompido")

    destino = qa._clone_or_update(str(origem))

    assert not (destino / "lixo.txt").exists()
    assert (destino / "A.java").exists()
    assert qa._is_valid_clone(destino)


def test_clones_simultaneos_do_mesmo_repo_nao_se_atrapalham(cache, tmp_path):
    origem = criar_repo(tmp_path, {"A.java": JAVA_SERVICO})
    resultados, erros = [], []

    def clonar():
        try:
            resultados.append(qa._clone_or_update(str(origem)))
        except Exception as e:  # noqa: BLE001 — o teste quer ver qualquer erro
            erros.append(e)

    threads = [threading.Thread(target=clonar) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert erros == []
    assert len(set(resultados)) == 1
    assert [p.name for p in cache.iterdir()] == [resultados[0].name]


@pytest.mark.xfail(strict=True, reason=(
    "BUG: repo_url vindo do usuário vai para `git clone` como argumento sem `--`; uma URL "
    "começando com '-' é interpretada como opção do git (injeção de argumento)"))
def test_url_que_parece_opcao_do_git_nao_chega_ao_git_como_opcao(cache, monkeypatch):
    chamadas = []
    run_real = subprocess.run

    def run_espiao(cmd, **kw):
        chamadas.append(list(cmd))
        return run_real(cmd, **kw)

    monkeypatch.setattr(qa.subprocess, "run", run_espiao)
    url = "--upload-pack=touch /dev/null/pwned"

    with pytest.raises(ValueError):
        qa._clone_or_update(url)

    for cmd in chamadas:
        if url in cmd:
            # Aceitável: a URL só aparece depois de um "--" (fim das opções)
            assert "--" in cmd and cmd.index("--") < cmd.index(url)


# ---------------------------------------------------------------------------
# _find_function
# ---------------------------------------------------------------------------

def test_arquivo_indicado_tem_prioridade_sobre_a_varredura(tmp_path):
    # Sem indicação, .java é varrido antes de .js — a indicação tem de inverter isso
    repo = tmp_path / "repo"
    _escrever(repo, {
        "a/Primeiro.java": "class P {\n    int calcular() {\n        return 1;\n    }\n}\n",
        "z/segundo.js": "function calcular() {\n  return 2;\n}\n",
    })

    assert qa._find_function(repo, "calcular")["file_path"] == "a/Primeiro.java"
    r = qa._find_function(repo, "calcular", hint_file_path="z/segundo.js")

    assert r["file_path"] == "z/segundo.js"
    assert "return 2;" in r["function_code"]


def test_arquivo_indicado_com_extensao_nao_suportada_e_ignorado(tmp_path):
    repo = tmp_path / "repo"
    _escrever(repo, {
        "A.java": "class A {\n    int calcular() {\n        return 1;\n    }\n}\n",
        "notas.txt": "function calcular() {\n  return 2;\n}\n",
    })

    assert qa._find_function(repo, "calcular", hint_file_path="notas.txt")["file_path"] == "A.java"


def test_arquivo_indicado_inexistente_cai_na_varredura(tmp_path):
    repo = tmp_path / "repo"
    _escrever(repo, {"A.java": "class A {\n    int calcular() {\n        return 1;\n    }\n}\n"})

    r = qa._find_function(repo, "calcular", hint_file_path="nao/existe.java")

    assert r["file_path"] == "A.java"


@pytest.mark.parametrize("hint", ["../fora/Segredo.java", "/etc/passwd", "sub/../../fora/Segredo.java"])
def test_arquivo_indicado_fora_do_repositorio_e_recusado(tmp_path, hint):
    repo = tmp_path / "repo"
    _escrever(repo, {"A.java": "class A {}\n"})
    _escrever(tmp_path / "fora", {"Segredo.java": "class S {\n    int calcular() {\n        return 9;\n    }\n}\n"})

    with pytest.raises(ValueError, match="dentro do repositório"):
        qa._find_function(repo, "calcular", hint_file_path=hint)


@pytest.mark.parametrize("pasta", ["node_modules", "build", "target", "dist", ".git", "__pycache__"])
def test_pastas_de_dependencias_e_build_sao_ignoradas(tmp_path, pasta):
    repo = tmp_path / "repo"
    _escrever(repo, {f"{pasta}/lib/Gerado.java": "class G {\n    int calcular() {\n        return 1;\n    }\n}\n"})

    with pytest.raises(ValueError, match="não encontrada"):
        qa._find_function(repo, "calcular")


@pytest.mark.xfail(strict=True, reason=(
    "BUG: o filtro de node_modules/build/target/dist olha o caminho ABSOLUTO do arquivo; "
    "se o próprio cache/repositório estiver sob uma pasta 'build' (ex.: TMPDIR=/srv/build/tmp), "
    "nenhum arquivo é varrido e toda busca falha"))
def test_repositorio_dentro_de_pasta_chamada_build_ainda_e_varrido(tmp_path):
    repo = tmp_path / "build" / "repo"
    _escrever(repo, {"A.java": "class A {\n    int calcular() {\n        return 1;\n    }\n}\n"})

    r = qa._find_function(repo, "calcular")

    assert r["file_path"] == "A.java"


def test_arquivo_grande_com_milhares_de_funcoes_encontra_a_ultima(tmp_path):
    repo = tmp_path / "repo"
    corpo = "".join(f"export function funcao{i}(x) {{\n  return x + {i};\n}}\n\n" for i in range(5000))
    corpo += "export function alvoFinal(x) {\n  return 'fim';\n}\n"
    _escrever(repo, {"grande.js": corpo})

    r = qa._find_function(repo, "alvoFinal")

    assert r["function_code"] == "export function alvoFinal(x) {\n  return 'fim';\n}"


def test_resultado_traz_contexto_e_caminho_relativo(tmp_path):
    repo = tmp_path / "repo"
    _escrever(repo, {"pkg/Calc.java": JAVA_SERVICO})

    r = qa._find_function(repo, "calcular")

    assert r == {
        "language": "java",
        "file_path": "pkg/Calc.java",
        "function_code": r["function_code"],
        "file_context": qa._extract_file_context(JAVA_SERVICO, "java"),
        "function_name": "calcular",
    }
    assert r["function_code"].startswith("private int calcular(int a, int b) {")


# ---------------------------------------------------------------------------
# _detect_language / _extract_function
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arquivo, lang", [
    ("A.java", "java"), ("a.js", "javascript"), ("a.jsx", "javascript"),
    ("a.ts", "typescript"), ("a.tsx", "typescript"), ("A.cls", "apex"),
    ("A.trigger", "apex"), ("A.JAVA", "java"), ("a.py", "unknown"), ("Makefile", "unknown"),
])
def test_linguagem_detectada_pela_extensao(tmp_path, arquivo, lang):
    assert qa._detect_language(tmp_path / arquivo) == lang


def test_extract_function_despacha_por_linguagem():
    java = "class A {\n    int calcular() {\n        return 1;\n    }\n}\n"
    js = "function calcular() {\n  return 1;\n}\n"

    assert qa._extract_function(java, "calcular", "java").startswith("int calcular()")
    assert qa._extract_function(java, "calcular", "apex").startswith("int calcular()")
    assert qa._extract_function(js, "calcular", "javascript") == js.strip()
    assert qa._extract_function(js, "calcular", "typescript") == js.strip()
    assert qa._extract_function(js, "calcular", "unknown") is None


# ---------------------------------------------------------------------------
# _extract_java_style
# ---------------------------------------------------------------------------

def test_java_nome_com_prefixo_ou_sufixo_parecido_nao_confunde():
    fonte = """\
class A {
    public int calcularTotal(int a) {
        return 1;
    }
    public int recalcular(int a) {
        return 2;
    }
    public int calcular(int a) {
        return 3;
    }
}
"""
    codigo = qa._extract_java_style(fonte, "calcular")

    assert codigo.startswith("public int calcular(int a) {")
    assert "return 3;" in codigo and "return 1;" not in codigo and "return 2;" not in codigo


def test_java_so_prefixo_ou_so_sufixo_nao_casa():
    fonte = "class A {\n    public int calcularTotal(int a) {\n        return 1;\n    }\n}\n"
    assert qa._extract_java_style(fonte, "calcular") is None
    assert qa._extract_java_style(fonte, "Total") is None


def test_java_chamada_do_metodo_nao_e_confundida_com_a_definicao():
    fonte = "class A {\n    void usar() {\n        int x = calcular(1, 2);\n    }\n}\n"
    assert qa._extract_java_style(fonte, "calcular") is None


def test_java_metodo_com_generics_e_throws():
    fonte = """\
class Repo {
    public static <T> Map<String, List<T>> agrupar(List<T> itens, int n) throws IOException, SQLException {
        Map<String, List<T>> m = new HashMap<>();
        return m;
    }
}
"""
    codigo = qa._extract_java_style(fonte, "agrupar")
    assert codigo.endswith("return m;\n    }")
    assert "agrupar(List<T> itens, int n) throws IOException, SQLException {" in codigo


def test_java_metodo_global_with_sharing_apex():
    fonte = "global with sharing class C {\n    global static void processar() {\n        update x;\n    }\n}\n"
    codigo = qa._extract_java_style(fonte, "processar")
    assert codigo == "global static void processar() {\n        update x;\n    }"


def test_java_chave_dentro_de_string_nao_fecha_o_metodo():
    codigo = qa._extract_java_style(JAVA_SERVICO, "calcular")
    assert codigo.endswith("return a + b;\n    }")


@pytest.mark.xfail(strict=True, reason=(
    "BUG: o padrão do tipo de retorno ([\\w<>\\[\\],\\s]+?) atravessa linhas e o trecho extraído "
    "começa com o texto da linha anterior sem o '@'/'//' (ex.: 'Override\\n    public int ...')"))
@pytest.mark.parametrize("linha_anterior", ["@Override", "// soma os dois valores"])
def test_java_codigo_extraido_nao_leva_lixo_da_linha_anterior(linha_anterior):
    fonte = f"class A {{\n    {linha_anterior}\n    public int calcular(int a) {{\n        return a;\n    }}\n}}\n"
    codigo = qa._extract_java_style(fonte, "calcular")
    assert codigo.startswith(("public int calcular", linha_anterior))


# ---------------------------------------------------------------------------
# _extract_js_style
# ---------------------------------------------------------------------------

def test_js_function_declarada_async_e_exportada():
    codigo = qa._extract_js_style(JS_UTIL, "buscarCliente")
    assert codigo == (
        "export async function buscarCliente(id) {\n"
        "  const resp = await fetch(`/clientes/${id}`);\n"
        "  return resp.json();\n}"
    )


def test_js_arrow_function_com_bloco():
    assert qa._extract_js_style(JS_UTIL, "somar") == "export const somar = (a, b) => {\n  return a + b;\n}"


def test_js_arrow_function_de_linha_unica_no_fim_do_arquivo():
    assert qa._extract_js_style("const dobro = x => x * 2", "dobro") == "const dobro = x => x * 2"
    assert qa._extract_js_style("const dobro = x => x * 2\nlet y = 1;", "dobro") == "const dobro = x => x * 2"


def test_js_metodo_de_classe():
    codigo = qa._extract_js_style(JS_UTIL, "adicionarItem")
    assert codigo == "adicionarItem(item) {\n    this.itens.push(item);\n    return this.itens.length;\n  }"


def test_ts_metodo_com_modificador_tipo_de_retorno_e_generics():
    fonte = "class S {\n  public async carregar<T>(id: string, f: (x: T) => void): Promise<T> {\n    return f;\n  }\n}\n"
    codigo = qa._extract_js_style(fonte, "carregar")
    assert codigo == "public async carregar<T>(id: string, f: (x: T) => void): Promise<T> {\n    return f;\n  }"


def test_js_chamada_da_funcao_nao_e_confundida_com_definicao_de_metodo():
    fonte = "const total = calcular(1);\nif (x) {\n  y();\n}\n"
    assert qa._extract_js_style(fonte, "calcular") is None


def test_js_nome_com_prefixo_ou_sufixo_parecido_nao_confunde():
    fonte = (
        "function somarTudo(a) {\n  return 1;\n}\n"
        "function resomar(a) {\n  return 2;\n}\n"
        "const somarX = () => 3;\n"
    )
    assert qa._extract_js_style(fonte, "somar") is None


def test_js_funcao_aninhada_e_encontrada():
    fonte = "function externa() {\n  function interna(v) {\n    return v * 2;\n  }\n  return interna(1);\n}\n"
    assert qa._extract_js_style(fonte, "interna") == "function interna(v) {\n    return v * 2;\n  }"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: arrow de linha única seguida de outra função — o código procura o próximo '{' em "
    "qualquer ponto do arquivo e devolve também a função seguinte"))
def test_js_arrow_de_linha_unica_nao_engole_a_funcao_seguinte():
    fonte = "export const dobro = (n) => n * 2;\n\nexport function outra() {\n  return 1;\n}\n"
    assert qa._extract_js_style(fonte, "dobro") == "export const dobro = (n) => n * 2;"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: function com parâmetro desestruturado/valor padrão com '{' (ex.: componente React "
    "`function Botao({ texto })`) — o primeiro '{' achado é o dos parâmetros e o corpo é cortado"))
@pytest.mark.parametrize("assinatura", [
    "export function Botao({ texto, onClick })",
    "function configurar(opcoes = {})",
])
def test_js_function_com_chaves_nos_parametros_devolve_o_corpo_inteiro(assinatura):
    nome = assinatura.split("function ")[1].split("(")[0]
    fonte = f"{assinatura} {{\n  return 1;\n}}\n"
    assert qa._extract_js_style(fonte, nome) == fonte.strip()


@pytest.mark.xfail(strict=True, reason=(
    "BUG: arrow function TypeScript com tipo de retorno (`(a: number): number =>`) não é "
    "reconhecida — o padrão exige '=>' logo depois do ')'"))
def test_ts_arrow_function_com_tipo_de_retorno_e_encontrada():
    fonte = "export const soma = (a: number, b: number): number => {\n  return a + b;\n};\n"
    assert qa._extract_js_style(fonte, "soma") == "export const soma = (a: number, b: number): number => {\n  return a + b;\n}"


# ---------------------------------------------------------------------------
# _find_closing_brace
# ---------------------------------------------------------------------------

def _fecha(fonte):
    return qa._find_closing_brace(fonte, fonte.index("{"))


def test_chave_de_fechamento_com_blocos_aninhados():
    fonte = "f() { if (a) { b(); } else { c(); } } depois"
    assert _fecha(fonte) == fonte.index(" depois") - 1


@pytest.mark.parametrize("miolo", [
    "x = '}';", 'x = "}";', "x = `}${a}`;", 'x = "aspas \\" }";', "x = '\\'}';",
    "// comentário com }\n", "/* bloco } */",
])
def test_chave_dentro_de_string_ou_comentario_e_ignorada(miolo):
    fonte = "{ " + miolo + " }FIM"
    assert _fecha(fonte) == fonte.index("FIM") - 1


def test_url_dentro_de_string_nao_vira_comentario():
    fonte = 'f() { u = "http://exemplo.invalid"; }FIM'
    assert _fecha(fonte) == fonte.index("FIM") - 1


def test_chaves_desbalanceadas_devolvem_o_fim_do_texto():
    fonte = "f() { if (a) { b();"
    assert _fecha(fonte) == len(fonte) - 1


def test_comentario_nao_fechado_ate_o_fim_do_texto():
    assert _fecha("{ // sem quebra de linha }") == len("{ // sem quebra de linha }") - 1
    assert _fecha("{ /* sem fim }") == len("{ /* sem fim }") - 1


# ---------------------------------------------------------------------------
# _extract_file_context
# ---------------------------------------------------------------------------

def test_contexto_java_tem_package_imports_e_classe():
    ctx = qa._extract_file_context(JAVA_SERVICO, "java")
    assert ctx.splitlines() == [
        "package com.exemplo.vendas;",
        "import java.util.List;",
        "import java.math.BigDecimal;",
        "public class CalculadoraPedido {",
    ]


def test_contexto_js_tem_imports_e_require():
    ctx = qa._extract_file_context(JS_UTIL, "javascript")
    assert ctx.splitlines() == [
        "import { arredondar } from './numeros';",
        "require('dotenv');",
        "class Carrinho {",
    ]


def test_contexto_apex_inclui_import_e_classe_mas_nao_package():
    fonte = "package x;\nimport y;\npublic class ContaService {\n}\n"
    assert qa._extract_file_context(fonte, "apex").splitlines() == ["import y;", "public class ContaService {"]


def test_contexto_de_linguagem_sem_regra_so_traz_classes():
    fonte = "import x;\nclass A {\n"
    assert qa._extract_file_context(fonte, "unknown") == "class A {"


def test_contexto_e_limitado_a_50_linhas():
    fonte = "".join(f"import pacote.Classe{i};\n" for i in range(80))
    linhas = qa._extract_file_context(fonte, "java").splitlines()
    assert len(linhas) == 50
    assert linhas[-1] == "import pacote.Classe49;"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: declaração de classe com modificadores além de public/private/global "
    "('public with sharing class' do Apex, 'export class' do TS, 'const x = require(...)') "
    "fica fora do contexto enviado ao LLM"))
@pytest.mark.parametrize("linha, lang", [
    ("public with sharing class ContaService {", "apex"),
    ("export class ServicoFrete {", "typescript"),
    ("public final class Util {", "java"),
    ("const fs = require('fs');", "javascript"),
])
def test_contexto_inclui_declaracoes_comuns_de_classe_e_require(linha, lang):
    assert linha in qa._extract_file_context(linha + "\n}\n", lang)


# ---------------------------------------------------------------------------
# _infer_framework
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arquivo, conteudo, esperado", [
    ("pom.xml", "<artifactId>junit-jupiter</artifactId>", "junit5"),
    ("pom.xml", "<artifactId>junit-platform-launcher</artifactId>", "junit5"),
    ("build.gradle", "testImplementation 'org.JUnit5:algo'", "junit5"),
    ("build.gradle", "testImplementation 'junit:junit:4.13.2'", "junit4"),
    ("pom.xml", "<!-- usa JUnit4 -->", "junit4"),
    ("pom.xml", "<artifactId>outra-coisa</artifactId>", "junit5"),
])
def test_framework_java_vem_do_arquivo_de_build(tmp_path, arquivo, conteudo, esperado):
    (tmp_path / arquivo).write_text(conteudo)
    assert qa._infer_framework("java", tmp_path) == esperado


def test_pom_tem_prioridade_sobre_build_gradle(tmp_path):
    (tmp_path / "pom.xml").write_text("junit-jupiter")
    (tmp_path / "build.gradle").write_text("junit:junit:4.12")
    assert qa._infer_framework("java", tmp_path) == "junit5"


def test_java_sem_arquivo_de_build_usa_junit5(tmp_path):
    assert qa._infer_framework("java", tmp_path) == "junit5"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: pom.xml Maven de JUnit 4 (groupId/artifactId junit + version 4.x) não contém "
    "'junit:junit:4' nem 'junit4' e é tratado como JUnit 5 — testes gerados não compilam"))
def test_pom_maven_com_junit4_usa_junit4(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<dependency>\n  <groupId>junit</groupId>\n  <artifactId>junit</artifactId>\n"
        "  <version>4.13.2</version>\n</dependency>\n")
    assert qa._infer_framework("java", tmp_path) == "junit4"


def test_apex_usa_apex_test(tmp_path):
    assert qa._infer_framework("apex", tmp_path) == "apex-test"


@pytest.mark.parametrize("lang", ["javascript", "typescript"])
def test_projeto_react_usa_jest_rtl(tmp_path, lang):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "18"}}')
    assert qa._infer_framework(lang, tmp_path) == "jest-rtl"


@pytest.mark.parametrize("lang", ["javascript", "typescript"])
def test_projeto_js_sem_react_usa_jest(tmp_path, lang):
    (tmp_path / "package.json").write_text('{"dependencies": {"react-router": "6"}}')
    assert qa._infer_framework(lang, tmp_path) == "jest"
    (tmp_path / "package.json").unlink()
    assert qa._infer_framework(lang, tmp_path) == "jest"


def test_linguagem_desconhecida_usa_jest(tmp_path):
    (tmp_path / "pom.xml").write_text("junit:junit:4")
    assert qa._infer_framework("unknown", tmp_path) == "jest"
