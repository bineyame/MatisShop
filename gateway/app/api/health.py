"""Health endpoints.

Unauthenticated on purpose so Docker Compose, a load balancer and a developer
with curl can all use them. They expose no business data.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep, SettingsDep
from app.domain.contracts import HealthResponse
from app.providers.registry import describe_providers

router = APIRouter(tags=["health"])


async def _health(session, settings) -> HealthResponse:  # noqa: ANN001
    try:
        await session.execute(text("SELECT 1"))
        database = "up"
        status = "pass"
    except Exception as exc:  # pragma: no cover - only on a broken database
        database = f"down: {type(exc).__name__}"
        status = "fail"
    return HealthResponse(
        status=status,
        service=settings.service_name,
        version=__version__,
        environment=settings.gateway_env,
        database=database,
        providers=describe_providers(settings),
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness/readiness probe")
async def health(session: SessionDep, settings: SettingsDep) -> HealthResponse:
    return await _health(session, settings)


@router.get("/api/v1/health", response_model=HealthResponse, include_in_schema=False)
async def health_v1(session: SessionDep, settings: SettingsDep) -> HealthResponse:
    return await _health(session, settings)
