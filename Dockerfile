# LCFTP server image: web application, ingest worker and migrations.
#
#   docker run lcftp           # web application on port 8080
#   docker run lcftp worker    # broker ingest worker
#   docker run lcftp migrate   # database schema migration

FROM python:3.14-slim-trixie AS base-image
RUN apt-get update \
    && apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

FROM base-image AS install-image
# setuptools-scm runs git to determine package versions.
RUN apt-get update \
    && apt-get -y install --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
COPY uv.lock pyproject.toml ./
COPY models/pyproject.toml models/pyproject.toml
COPY auth/pyproject.toml auth/pyproject.toml
COPY client/pyproject.toml client/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-workspace --package lcftp \
        -o /tmp/requirements.txt \
    && uv pip install -r /tmp/requirements.txt
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps ./models ./auth .

FROM base-image AS runtime-image
RUN useradd --create-home lcftp
COPY --from=install-image /opt/venv /opt/venv
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
ENV PATH=/opt/venv/bin:$PATH LCFTP_ALEMBIC_CONFIG=/app/alembic.ini
WORKDIR /app
USER lcftp
EXPOSE 8080
ENTRYPOINT ["lcftp"]
CMD ["web"]
