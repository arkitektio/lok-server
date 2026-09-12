# syntax=docker/dockerfile:1
# ---- builder: compile deps, then discard the toolchain ----
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
# lok talks to ionscale over HTTP (ionscale.service_token); no CLI binary is
# shipped any more. Deployments still on the legacy `admin_key` path must mount
# an `ionscale` binary themselves.
WORKDIR /workspace
# Dependency layer — cached until pyproject.toml / uv.lock change:
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
# django-allauth[saml] pulls xmlsec + lxml, which resolve to self-contained
# manylinux_2_28 wheels on this glibc-2.36 base — so no libxmlsec1/libxml2 apt
# packages are needed. Fail the build loudly if that ever stops being true
# (a source fallback would otherwise produce an image that 500s on first login).
RUN /opt/venv/bin/python -c "import xmlsec, lxml.etree, onelogin.saml2.auth"
# Project layer:
COPY . .
RUN uv sync --frozen --no-dev

# ---- runtime: slim base + prebuilt venv; psycopg[binary] bundles libpq ----
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /workspace
COPY --from=builder /opt/venv /opt/venv
COPY . .
