"""Integration Gateway application entry point.

Boundary statement, because it is the whole reason this service exists:
the gateway owns provider communication, credentials, retries, idempotency
and the integration audit trail. It has no Odoo dependency, no Odoo database
connection and no knowledge of Odoo models beyond the provenance strings an
ERP puts in `source`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.api import admin, delivery, fiscal, health, payments, webhooks
from app.config import get_settings
from app.domain.contracts import ErrorResponse
from app.domain.errors import GatewayError
from app.observability import configure_logging, get_logger
from app.persistence.database import init_engine, shutdown_engine

logger = get_logger(__name__)

DESCRIPTION = """
Normalized integration boundary between an ERP (Odoo 18 Community here) and
external fiscal, payment and delivery providers.

**Every bundled provider is a mock.** No registration produced by this service
is a legally valid Ethiopian fiscal registration.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.gateway_log_level, settings.service_name)
    init_engine(settings)
    logger.info(
        "gateway.startup",
        extra={
            "version": __version__,
            "environment": settings.gateway_env,
            "fiscal_provider": settings.fiscal_provider,
            "payment_provider": settings.payment_provider,
            "delivery_provider": settings.delivery_provider,
        },
    )
    yield
    await shutdown_engine()
    logger.info("gateway.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Mati Retail Integration Gateway",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        correlation = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex
        request.state.correlation_id = correlation
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        correlation = getattr(request.state, "correlation_id", None)
        logger.warning(
            "gateway.error",
            extra={
                "code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
                "correlation_id": correlation,
                "error": exc.message,
            },
        )
        body = ErrorResponse(
            code=exc.code, message=exc.message, detail=exc.detail, correlation_id=correlation
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        correlation = getattr(request.state, "correlation_id", None)
        body = ErrorResponse(
            code="validation_error",
            message="request body failed validation",
            detail={"errors": _clean_validation_errors(exc.errors())},
            correlation_id=correlation,
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    app.include_router(health.router)
    app.include_router(fiscal.router, prefix="/api/v1")
    app.include_router(payments.router, prefix="/api/v1")
    app.include_router(delivery.router, prefix="/api/v1")
    app.include_router(webhooks.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.service_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
        }

    return app


def _clean_validation_errors(errors: list[dict]) -> list[dict]:
    """Pydantic puts the offending input in `input`; that can carry PII."""
    cleaned = []
    for error in errors:
        cleaned.append(
            {
                "loc": [str(part) for part in error.get("loc", [])],
                "msg": error.get("msg"),
                "type": error.get("type"),
            }
        )
    return cleaned


app = create_app()
