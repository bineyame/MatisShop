# =============================================================================
# Odoo 18 Community, built from the official source at a PINNED revision.
#
# Why this shape (see docs/decisions/ADR-011-odoo-source-pinning.md):
#   * the official `odoo:18.0` image supplies the exact OS + Python dependency
#     set and wkhtmltopdf build that Odoo 18 expects;
#   * the application code we actually run is the official source tree at
#     ODOO_REVISION, unpacked into /opt/odoo/source;
#   * odoo-bin puts its own directory first on sys.path, so the pinned source
#     shadows the distro package completely.
#
# The source arrives as a tarball of the exact commit rather than a git clone.
# It is the same bytes GitHub would give `git archive <sha>`, but it is a
# single plain HTTPS stream: a shallow clone of Odoo is a ~400 MB pack that a
# slow or flaky link drops mid-transfer, and we delete `.git` afterwards
# anyway, so the git machinery bought us nothing.
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
    apt-get install -y --no-install-recommends curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    test -n "${ODOO_REVISION}" || (echo "ODOO_REVISION build arg is required" && exit 1); \
    mkdir -p "${ODOO_SOURCE}"; \
    curl -fL --retry 8 --retry-delay 5 --retry-all-errors --connect-timeout 30 \
         -o /tmp/odoo-source.tar.gz \
         "https://codeload.github.com/odoo/odoo/tar.gz/${ODOO_REVISION}"; \
    tar -xzf /tmp/odoo-source.tar.gz -C "${ODOO_SOURCE}" --strip-components=1; \
    rm -f /tmp/odoo-source.tar.gz; \
    test -f "${ODOO_SOURCE}/odoo-bin" || (echo "unpacked tree has no odoo-bin" && exit 1); \
    echo "${ODOO_REVISION}" > /opt/odoo/REVISION; \
    chown -R odoo:odoo /opt/odoo

COPY entrypoint.sh /usr/local/bin/mati-entrypoint.sh
RUN chmod +x /usr/local/bin/mati-entrypoint.sh

RUN mkdir -p /mnt/extra-addons /var/lib/odoo && chown -R odoo:odoo /mnt/extra-addons /var/lib/odoo

USER odoo
EXPOSE 8069 8072
ENTRYPOINT ["/usr/local/bin/mati-entrypoint.sh"]
CMD ["odoo"]
