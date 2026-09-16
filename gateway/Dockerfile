# Integration Gateway image - small, single purpose, no Odoo dependency at all.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/gateway

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install --no-cache-dir "." \
    && pip install --no-cache-dir pytest pytest-asyncio

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY tests ./tests
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Do not run as root.
RUN useradd --create-home --uid 10001 gateway && chown -R gateway:gateway /srv/gateway
USER gateway

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve"]
