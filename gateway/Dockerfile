# Integration Gateway image - small, single purpose, no Odoo dependency at all.
#
# Built from the REPOSITORY ROOT (see compose.yaml) so the cross-boundary
# contract tests in ../tests/contract land in the image alongside the gateway.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv/gateway

WORKDIR /srv/gateway

# Dependencies only, in their own cached layer. We deliberately do NOT
# `pip install .`: the app runs from WORKDIR (see PYTHONPATH), so installing it
# as a site package would shadow the mounted sources during development.
# Reading the deps out of pyproject.toml keeps a single source of truth.
COPY gateway/pyproject.toml ./
RUN pip install --upgrade pip \
    && python -c "import tomllib,sys; d=tomllib.load(open('pyproject.toml','rb')); sys.stdout.write('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir pytest pytest-asyncio

COPY gateway/alembic.ini ./
COPY gateway/migrations ./migrations
COPY gateway/app ./app
COPY gateway/tests ./tests
# Mirrors the repo layout: /srv/gateway + /srv/tests, so `../tests/contract`
# resolves exactly as it does on a developer machine.
COPY tests /srv/tests
COPY docs/contracts /srv/docs/contracts
COPY gateway/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Do not run as root.
RUN useradd --create-home --uid 10001 gateway \
    && chown -R gateway:gateway /srv
USER gateway

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve"]
