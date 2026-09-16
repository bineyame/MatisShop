"""Gateway configuration.

All configuration - including every provider credential - enters the process
through the environment. Nothing is hardcoded and nothing is read from Odoo.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -------------------------------------------------------------
    gateway_env: Literal["development", "test", "staging", "production"] = "development"
    gateway_log_level: str = "INFO"
    service_name: str = "integration-gateway"

    # --- security ------------------------------------------------------------
    # Shared secret presented by ERP clients in the X-API-Key header.
    gateway_api_key: str = "dev-gateway-api-key-change-me"
    # HMAC-SHA256 secret used to verify inbound provider webhooks.
    gateway_webhook_secret: str = "dev-webhook-secret-change-me"

    # --- persistence ---------------------------------------------------------
    # The gateway owns this database exclusively. It is NEVER the Odoo database.
    gateway_database_url: str = "postgresql+asyncpg://odoo:odoo@postgres:5432/integration_gateway"
    database_echo: bool = False

    # --- provider selection --------------------------------------------------
    fiscal_provider: str = "mock"
    payment_provider: str = "mock"
    delivery_provider: str = "mock"

    # --- resilience ----------------------------------------------------------
    gateway_max_attempts: int = Field(default=3, ge=1, le=10)
    gateway_retry_base_delay_ms: int = Field(default=100, ge=0, le=60_000)
    gateway_provider_timeout_seconds: float = Field(default=10.0, gt=0)

    # --- mock provider behaviour (demo only) ---------------------------------
    mock_fiscal_failure_mode: bool = False
    mock_fiscal_irn_prefix: str = "ET-DEMO"
    mock_payment_mode: Literal["auto_success", "manual", "always_fail"] = "auto_success"
    mock_delivery_mode: Literal["auto_advance", "manual", "always_fail"] = "auto_advance"

    # --- optional outbound ERP notification ----------------------------------
    # When set, provider webhooks are forwarded to this ERP endpoint. Optional:
    # the reference flow is Odoo polling the gateway, which needs no inbound
    # network path into Odoo.
    erp_webhook_url: str | None = None
    erp_webhook_timeout_seconds: float = 5.0

    @field_validator("gateway_database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if "+asyncpg" not in value and "+aiosqlite" not in value:
            raise ValueError(
                "gateway_database_url must use an async driver "
                "(postgresql+asyncpg://... or sqlite+aiosqlite://...)"
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.gateway_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
