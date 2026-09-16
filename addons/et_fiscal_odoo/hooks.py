"""Install-time configuration.

The gateway URL and API key are read from the environment at install time so a
`docker compose up` deployment is wired up without anyone opening Settings.
They remain editable in the UI afterwards.

Secrets live in the environment, never in this source tree.
"""

import logging
import os

_logger = logging.getLogger(__name__)

DEFAULTS = {
    "et_fiscal.gateway_base_url": ("GATEWAY_BASE_URL", "http://gateway:8000"),
    "et_fiscal.gateway_api_key": ("GATEWAY_API_KEY", ""),
    "et_fiscal.gateway_timeout": ("GATEWAY_TIMEOUT", "20"),
    "et_fiscal.auto_submit": ("ET_FISCAL_AUTO_SUBMIT", "1"),
    "et_fiscal.enabled": ("ET_FISCAL_ENABLED", "1"),
    "et_fiscal.max_auto_retries": ("ET_FISCAL_MAX_AUTO_RETRIES", "10"),
}


def post_init_hook(env):
    params = env["ir.config_parameter"].sudo()
    for key, (env_var, fallback) in DEFAULTS.items():
        if params.get_param(key):
            continue
        params.set_param(key, os.environ.get(env_var, fallback))

    if not params.get_param("et_fiscal.gateway_api_key"):
        _logger.warning(
            "et_fiscal_odoo: no gateway API key configured. Set GATEWAY_API_KEY in the "
            "environment or fill it in under Settings > Ethiopian Fiscalization."
        )
    _logger.info(
        "et_fiscal_odoo: gateway base url set to %s",
        params.get_param("et_fiscal.gateway_base_url"),
    )
