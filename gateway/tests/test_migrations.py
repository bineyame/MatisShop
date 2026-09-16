"""The Alembic migration must produce the schema the ORM expects.

Production and Compose apply migrations; tests create tables from metadata.
This test stops those two from drifting apart.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import get_settings
from app.persistence.database import Base

GATEWAY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_sqlite(tmp_path: Path):
    db_path = tmp_path / "migrated.db"
    previous = os.environ.get("GATEWAY_DATABASE_URL")
    os.environ["GATEWAY_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    get_settings.cache_clear()
    yield db_path
    if previous is None:
        os.environ.pop("GATEWAY_DATABASE_URL", None)
    else:
        os.environ["GATEWAY_DATABASE_URL"] = previous
    get_settings.cache_clear()


def test_migration_creates_every_orm_table(alembic_sqlite):
    config = Config(str(GATEWAY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(GATEWAY_ROOT / "migrations"))
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{alembic_sqlite}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected = set(Base.metadata.tables) | {"alembic_version"}
    missing = expected - tables
    assert not missing, f"migration is missing tables: {sorted(missing)}"


def test_migration_creates_the_idempotency_unique_constraints(alembic_sqlite):
    config = Config(str(GATEWAY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(GATEWAY_ROOT / "migrations"))
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{alembic_sqlite}")
    try:
        inspector = inspect(engine)
        fiscal = {c["name"] for c in inspector.get_unique_constraints("fiscal_documents")}
        idem = {c["name"] for c in inspector.get_unique_constraints("idempotency_records")}
        mock = {c["name"] for c in inspector.get_unique_constraints("mock_fiscal_registrations")}
    finally:
        engine.dispose()

    # These constraints ARE the idempotency guarantee - not application logic.
    assert "uq_fiscal_idempotency_key" in fiscal
    assert "uq_idempotency_scope_key" in idem
    assert "uq_mock_fiscal_idempotency" in mock
