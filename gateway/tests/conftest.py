"""Test harness.

Tests run against a throwaway SQLite database so the suite needs no
PostgreSQL. The schema is created from the same SQLAlchemy metadata the
migrations target, and test_migrations.py separately proves the Alembic
migration produces that same schema.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("GATEWAY_ENV", "test")
os.environ.setdefault("GATEWAY_API_KEY", "test-api-key")
os.environ.setdefault("GATEWAY_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("GATEWAY_RETRY_BASE_DELAY_MS", "0")
os.environ.setdefault("GATEWAY_MAX_ATTEMPTS", "3")

import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.persistence import database  # noqa: E402
from app.persistence.database import Base  # noqa: E402
from app.persistence import models  # noqa: F401,E402  (registers tables)

API_KEY = "test-api-key"
WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def engine(tmp_path: Path):
    db_path = tmp_path / "gateway-test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["GATEWAY_DATABASE_URL"] = url
    get_settings.cache_clear()

    engine = create_async_engine(url, future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    database.set_sessionmaker(factory, engine)
    yield engine
    await engine.dispose()
    database._engine = None
    database._sessionmaker = None
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    factory = database.get_sessionmaker()
    async with factory() as session:
        yield session
        await session.commit()


@pytest_asyncio.fixture
async def client(engine) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client with the app's lifespan skipped.

    The lifespan would build a second engine from the environment; the test
    engine fixture has already bound the session factory.
    """
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://gateway.test",
        headers={"X-API-Key": API_KEY},
        timeout=30.0,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def anon_client(engine) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.test", timeout=30.0
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Payload builders - shaped exactly like what et_fiscal_odoo sends.
# ---------------------------------------------------------------------------
def fiscal_payload(
    *,
    idempotency_key: str | None = None,
    grand_total: str = "6900.00",
    subtotal: str = "6000.00",
    tax_total: str = "900.00",
    seller_tin: str | None = "0012345678",
    record_id: str = "42",
    document_number: str = "Shop 1 Retail/0001",
) -> dict:
    key = idempotency_key or uuid.uuid4().hex
    return {
        "idempotency_key": key,
        "source": {
            "system": "odoo",
            "model": "pos.order",
            "record_id": record_id,
            "reference": document_number,
        },
        "document": {
            "type": "receipt",
            "number": document_number,
            "issued_at": datetime.now(tz=UTC).isoformat(),
        },
        "seller": {
            "name": "Mati's Shoes PLC",
            "tin": seller_tin,
            "address": "Bole, Addis Ababa",
            "phone": "+251911000000",
        },
        "buyer": {"name": "Walk-in Customer"},
        "lines": [
            {
                "line_id": "1",
                "sku": "SAM-BLK-42",
                "barcode": "2000004200008",  # the real seeded EAN-13 for SAM-BLK-42
                "description": "Adidas Samba / Black / 42",
                "quantity": "1",
                "unit_price": subtotal,
                "tax_code": "VAT15",
                "tax_rate": "15",
                "tax_amount": tax_total,
                "line_total": subtotal,
            }
        ],
        "taxes": [
            {"code": "VAT15", "name": "VAT 15%", "rate": "15", "base": subtotal, "amount": tax_total}
        ],
        "totals": {
            "currency": "ETB",
            "subtotal": subtotal,
            "tax_total": tax_total,
            "grand_total": grand_total,
        },
    }


def payment_payload(*, idempotency_key: str | None = None, amount: str = "6900.00") -> dict:
    return {
        "idempotency_key": idempotency_key or uuid.uuid4().hex,
        "reference": f"ODOO-TX-{uuid.uuid4().hex[:8]}",
        "source": {"system": "odoo", "model": "payment.transaction", "record_id": "7"},
        "amount": amount,
        "currency": "ETB",
        "customer": {"name": "Abebe Kebede", "phone": "+251911223344"},
        "description": "Online order payment",
        "return_url": "http://localhost:8069/payment/status",
    }


def delivery_payload(*, idempotency_key: str | None = None) -> dict:
    return {
        "idempotency_key": idempotency_key or uuid.uuid4().hex,
        "reference": f"WH/OUT/{uuid.uuid4().hex[:5]}",
        "source": {"system": "odoo", "model": "stock.picking", "record_id": "11"},
        "pickup": {"name": "Mati Main Warehouse", "city": "Addis Ababa", "country_code": "ET"},
        "dropoff": {"name": "Abebe Kebede", "city": "Addis Ababa", "country_code": "ET"},
        "packages": [{"description": "Adidas Samba / Black / 42", "quantity": "1", "sku": "SAM-BLK-42"}],
        "cash_on_delivery": "0.00",
        "currency": "ETB",
    }


def money(value: str | Decimal) -> Decimal:
    return Decimal(str(value))
