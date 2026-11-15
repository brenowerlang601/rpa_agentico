FROM python:3.13-slim

# Dependências de sistema: Playwright + psycopg2 + git (o gerador de testes QA clona repositórios)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# uv para instalar dependências
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Instala dependências Python (aproveita cache do Docker)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Instala browsers do Playwright
RUN uv run playwright install chromium --with-deps

# Copia o código da aplicação
COPY app.py db.py json_processor.py tool_generator.py qa_code_analyzer.py qa_test_generator.py ./
COPY templates/ templates/

# Cria pastas necessárias (serão populadas pelo restore_from_db no startup)
RUN mkdir -p tools tests json_rpa

EXPOSE 5000

# Modo "com browser": dentro do container, localhost é o próprio container.
# O Chrome roda no host, então o padrão aponta para host.docker.internal
# (no Linux, rode o container com --add-host=host.docker.internal:host-gateway).
# O script resolve o nome para IP, porque o Chrome recusa conexões CDP com Host que não seja IP/localhost.
ENV CDP_URL=http://host.docker.internal:9222

# 1 worker + threads para suportar execuções paralelas (SSE + estado em memória exige processo único)
CMD ["uv", "run", "gunicorn", "-w", "1", "--threads", "20", \
     "-b", "0.0.0.0:5000", "--timeout", "300", "--keep-alive", "5", \
     "--log-level", "info", "app:app"]
