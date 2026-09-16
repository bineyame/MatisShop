# Integration Gateway image - small, single purpose, no Odoo dependency at all.
#
# Built from the REPOSITORY ROOT (see compose.yaml) so the cross-boundary
# contract tests in ../tests/contract land in the image alongside the gateway.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PIP_NO_CACHE_DIR=1

WORKDIR /srv/gateway

COPY gateway/pyproject.toml ./
RUN pip install --upgrade pip && pip install --no-cache-dir "."     && pip install --no-cache-dir pytest pytest-asyncio

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
RUN useradd --create-home --uid 10001 gateway     && chown -R gateway:gateway /srv
USER gateway

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve"]
