# =============================================================================
# Odoo 18 Community, built from the official Git source at a PINNED revision.
#
# Why this shape (see docs/decisions/ADR-011-odoo-source-pinning.md):
#   * the official `odoo:18.0` image supplies the exact OS + Python dependency
#     set and wkhtmltopdf build that Odoo 18 expects;
#   * the application code we actually run is a pinned checkout of
#     https://github.com/odoo/odoo at ODOO_REVISION, placed in /opt/odoo/source;
#   * odoo-bin puts its own directory first on sys.path, so the pinned source
#     shadows the distro package completely.
#
# Odoo core is NEVER modified. Everything custom lives in /mnt/extra-addons.
# =============================================================================
FROM odoo:18.0

USER root

ARG ODOO_REVISION
ENV ODOO_REVISION=${ODOO_REVISION}
ENV ODOO_SOURCE=/opt/odoo/source
ENV PYTHONPATH=/opt/odoo/source
ENV PYTHONUNBUFFERED=1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# Fetch exactly one commit. Reproducible and far smaller than a full clone.
RUN set -eux; \
    test -n "${ODOO_REVISION}" || (echo "ODOO_REVISION build arg is required" && exit 1); \
    mkdir -p "${ODOO_SOURCE}"; \
    cd "${ODOO_SOURCE}"; \
    git init -q .; \
    git remote add origin https://github.com/odoo/odoo.git; \
    git fetch -q --depth 1 origin "${ODOO_REVISION}"; \
    git checkout -q FETCH_HEAD; \
    git rev-parse HEAD > /opt/odoo/REVISION; \
    rm -rf "${ODOO_SOURCE}/.git"; \
    chown -R odoo:odoo /opt/odoo

COPY entrypoint.sh /usr/local/bin/mati-entrypoint.sh
RUN chmod +x /usr/local/bin/mati-entrypoint.sh

RUN mkdir -p /mnt/extra-addons /var/lib/odoo && chown -R odoo:odoo /mnt/extra-addons /var/lib/odoo

USER odoo
EXPOSE 8069 8072
ENTRYPOINT ["/usr/local/bin/mati-entrypoint.sh"]
CMD ["odoo"]
