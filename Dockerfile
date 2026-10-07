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
# The service's own code, compiled like its dependencies: every container of this image
# (the migrate job, the server) would otherwise compile it again at start.
RUN /opt/venv/bin/python -m compileall -q -x '/(tests|\.venv)/' /workspace

# ---- runtime: slim base + prebuilt venv; psycopg[binary] bundles libpq ----
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    ARKITEKT_SERVICE=lok_server.contract
WORKDIR /workspace
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /workspace /workspace
# Where this code came from, said by the build (`--build-arg`; the release workflow passes the
# repository and the commit): `describe` reports it as `source`. Last, so that a new commit
# invalidates no layer above. Empty for a local build, which then names no source.
ARG ARKITEKT_SOURCE_REPOSITORY=""
ARG ARKITEKT_SOURCE_REVISION=""
ENV ARKITEKT_SOURCE_REPOSITORY=${ARKITEKT_SOURCE_REPOSITORY} \
    ARKITEKT_SOURCE_REVISION=${ARKITEKT_SOURCE_REVISION}
# With no command, a container of this image says what it is and stops: that is how an
# installer asks, knowing nothing of what is inside. How it serves, how it is prepared and
# what else can be run in it are in the answer (`serve`, `debug`, `jobs`).
CMD ["arkitekt-service", "describe"]
