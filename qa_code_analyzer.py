"""
Analisa repositórios de código-fonte e extrai funções/métodos específicos
para serem usados como entrada do gerador de testes.

Suporta: Java, JavaScript, TypeScript, APEX (Salesforce).
"""

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

# Diretório temporário onde repos clonados ficam em cache
_REPOS_CACHE = Path(tempfile.gettempdir()) / "rpa_repos"
_REPOS_CACHE.mkdir(exist_ok=True)

# Extensões por linguagem
_LANG_EXTENSIONS = {
    "java": [".java"],
    "javascript": [".js", ".jsx"],
    "typescript": [".ts", ".tsx"],
    "apex": [".cls", ".trigger"],
}

_ALL_EXTENSIONS = {ext: lang for lang, exts in _LANG_EXTENSIONS.items() for ext in exts}


def fetch_function_from_repo(repo_url: str, function_name: str, hint_file_path: str = "") -> dict:
    """
    Clona o repositório (shallow) e extrai o código da função/método informado.

    Se hint_file_path for fornecido, busca primeiro nesse arquivo antes de varrer o repo.

    Retorna:
        {
            "language": "java" | "javascript" | "typescript" | "apex",
            "framework": "junit5" | "jest" | "react-testing-library" | "apex-test",
            "file_path": "caminho/relativo/no/repo.java",
            "function_code": "código da função",
            "file_context": "imports + classe envolvente",
            "function_name": nome normalizado,
        }

    Lança ValueError em caso de falha.
    """
    repo_dir = _clone_or_update(repo_url)
    result = _find_function(repo_dir, function_name, hint_file_path=hint_file_path)
    result["framework"] = _infer_framework(result["language"], repo_dir)
    return result


def _repo_cache_dir(repo_url: str) -> Path:
    slug = hashlib.md5(repo_url.encode()).hexdigest()[:12]
    # Usa o último segmento da URL como prefixo legível
    name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    return _REPOS_CACHE / f"{name}_{slug}"


def _clone_or_update(repo_url: str) -> Path:
    """Clona o repo com --depth=1. Se já clonado, atualiza via fetch."""
    repo_dir = _repo_cache_dir(repo_url)

    if repo_dir.exists():
        # Tenta atualizar silenciosamente; ignora erro (sem rede, repo privado, etc.)
        subprocess.run(
            ["git", "-C", str(repo_dir), "fetch", "--depth=1", "origin"],
            capture_output=True, timeout=60,
        )
        return repo_dir

    subprocess.run(
        ["git", "clone", "--depth=1", repo_url, str(repo_dir)],
        capture_output=True, timeout=120, check=True,
    )
    return repo_dir


def _find_function(repo_dir: Path, function_name: str, hint_file_path: str = "") -> dict:
    """
    Busca arquivos de código-fonte e extrai a função/método pelo nome.
    Se hint_file_path for informado, tenta esse arquivo primeiro.
    Retorna o primeiro match encontrado.
    """
    candidates = []

    if hint_file_path:
        hint = repo_dir / hint_file_path
        if hint.exists() and hint.suffix.lower() in _ALL_EXTENSIONS:
            candidates.append(hint)

    for ext, lang in _ALL_EXTENSIONS.items():
        candidates.extend(repo_dir.rglob(f"*{ext}"))

    # Remove node_modules, .git, build, target, dist
    candidates = [
        p for p in candidates
        if not any(part in {".git", "node_modules", "build", "target", "dist", "__pycache__"}
                   for part in p.parts)
    ]

    # Deduplica preservando ordem (hint primeiro)
    seen = set()
    unique = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    candidates = unique

    for file_path in candidates:
        lang = _detect_language(file_path)
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        func_code = _extract_function(source, function_name, lang)
        if func_code:
            rel_path = str(file_path.relative_to(repo_dir))
            context = _extract_file_context(source, lang)
            return {
                "language": lang,
                "file_path": rel_path,
                "function_code": func_code,
                "file_context": context,
                "function_name": function_name,
            }

    raise ValueError(
        f"Função '{function_name}' não encontrada em nenhum arquivo "
        f"suportado (.java, .js, .jsx, .ts, .tsx, .cls, .trigger) no repositório."
    )


def _detect_language(file_path: Path) -> str:
    return _ALL_EXTENSIONS.get(file_path.suffix.lower(), "unknown")


def _extract_function(source: str, function_name: str, lang: str) -> str | None:
    """Extrai o corpo completo de uma função/método pelo nome."""
    if lang == "java" or lang == "apex":
        return _extract_java_style(source, function_name)
    elif lang in ("javascript", "typescript"):
        return _extract_js_style(source, function_name)
    return None


def _extract_java_style(source: str, name: str) -> str | None:
    """
    Extrai método Java/APEX pelo nome.
    Lida com modificadores (public/private/static/etc.) e tipos de retorno variados.
    """
    # Padrão: (modificadores opcionais) (tipo) nome(params) {
    pattern = re.compile(
        r'((?:(?:public|private|protected|static|final|synchronized|override|abstract|'
        r'virtual|global|with\s+sharing|without\s+sharing)\s+)*'
        r'[\w<>\[\],\s]+?\s+)' +
        re.escape(name) +
        r'\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        return None

    start = m.start()
    brace_pos = source.index("{", m.start())
    end = _find_closing_brace(source, brace_pos)
    return source[start:end + 1].strip()


def _extract_js_style(source: str, name: str) -> str | None:
    """Extrai função JavaScript/TypeScript pelo nome (function/arrow/method)."""
    patterns = [
        # function nomeFuncao(
        re.compile(r'(?:export\s+)?(?:async\s+)?function\s+' + re.escape(name) + r'\s*\('),
        # const nomeFuncao = (... => ou function
        re.compile(r'(?:export\s+)?(?:const|let|var)\s+' + re.escape(name) + r'\s*=\s*(?:async\s+)?(?:\([^)]*\)|[\w]+)\s*=>'),
        # nomeFuncao( dentro de classe (método)
        re.compile(r'(?:(?:public|private|protected|static|async|override)\s+)*' + re.escape(name) + r'\s*\('),
    ]

    for pattern in patterns:
        m = pattern.search(source)
        if m:
            start = m.start()
            # Encontra o { de abertura (pode ter parâmetros com tipos TS entre os parênteses)
            brace_idx = source.find("{", m.end())
            if brace_idx == -1:
                # Arrow function de linha única: const f = () => expr
                end_line = source.find("\n", m.end())
                return source[start: end_line if end_line != -1 else len(source)].strip()
            end = _find_closing_brace(source, brace_idx)
            return source[start:end + 1].strip()

    return None


def _find_closing_brace(source: str, open_pos: int) -> int:
    """Encontra o índice do } que fecha o { em open_pos."""
    depth = 0
    in_string_single = False
    in_string_double = False
    in_template = False
    i = open_pos
    while i < len(source):
        ch = source[i]
        if ch == "'" and not in_string_double and not in_template:
            in_string_single = not in_string_single
        elif ch == '"' and not in_string_single and not in_template:
            in_string_double = not in_string_double
        elif ch == '`' and not in_string_single and not in_string_double:
            in_template = not in_template
        elif not in_string_single and not in_string_double and not in_template:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return len(source) - 1


def _extract_file_context(source: str, lang: str) -> str:
    """Extrai imports/pacotes e declaração da classe do arquivo."""
    lines = source.splitlines()
    context_lines = []
    for line in lines:
        stripped = line.strip()
        if lang == "java" and (stripped.startswith("import ") or stripped.startswith("package ")):
            context_lines.append(line)
        elif lang == "apex" and stripped.startswith("import "):
            context_lines.append(line)
        elif lang in ("javascript", "typescript") and (
            stripped.startswith("import ") or stripped.startswith("require(")
        ):
            context_lines.append(line)
        # Inclui declaração de classe
        elif re.match(r'^\s*(public|private|global)?\s*(class|interface|enum)\s+\w+', line):
            context_lines.append(line)

    return "\n".join(context_lines[:50])  # Limita para não explodir o prompt


def _infer_framework(language: str, repo_dir: Path) -> str:
    """Detecta o framework de teste mais adequado baseado na linguagem e arquivos do repo."""
    if language == "java":
        # Verifica se usa JUnit 5 ou 4 via pom.xml/build.gradle
        pom = repo_dir / "pom.xml"
        gradle = repo_dir / "build.gradle"
        for build_file in [pom, gradle]:
            if build_file.exists():
                content = build_file.read_text(errors="replace")
                if "junit-jupiter" in content or "junit5" in content.lower() or "junit-platform" in content:
                    return "junit5"
                if "junit:junit:4" in content or "junit4" in content.lower():
                    return "junit4"
        return "junit5"  # default moderno

    elif language == "apex":
        return "apex-test"

    elif language == "typescript":
        # Verifica se é React
        pkg = repo_dir / "package.json"
        if pkg.exists():
            content = pkg.read_text(errors="replace")
            if '"react"' in content:
                return "jest-rtl"  # Jest + React Testing Library
        return "jest"

    elif language == "javascript":
        pkg = repo_dir / "package.json"
        if pkg.exists():
            content = pkg.read_text(errors="replace")
            if '"react"' in content:
                return "jest-rtl"
        return "jest"

    return "jest"
