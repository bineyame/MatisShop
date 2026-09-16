"""Idempotency: an external operation happens at most once.

The guarantee is enforced by a unique constraint, not by application logic:
two concurrent identical requests race on the database and exactly one wins.
The loser reads the winner result.

Four outcomes of a claim:

    new          first time we have seen this key -> do the work
    retry        a previous attempt failed        -> do the work again, same identity
    replay       already completed                -> return the stored response
    in_progress  another request holds the claim  -> tell the caller to wait
    conflict     same key, different body         -> reject (client bug)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability import get_logger
from app.persistence.models import IdempotencyRecord, utcnow

logger = get_logger(__name__)

ClaimState = Literal["new", "retry", "replay", "in_progress", "conflict"]


def compute_request_hash(payload: BaseModel | dict[str, Any]) -> str:
    """Stable hash of the logical request body.

    Canonicalised through sorted-key JSON so that key ordering or whitespace
    differences never look like a different request.
    """
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Claim:
    record: IdempotencyRecord
    state: ClaimState

    @property
    def should_execute(self) -> bool:
        return self.state in ("new", "retry")


class IdempotencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        correlation_id: str | None = None,
    ) -> Claim:
        existing = await self._get(scope, key)
        if existing is None:
            record = IdempotencyRecord(
                scope=scope,
                idempotency_key=key,
                request_hash=request_hash,
                status="in_progress",
                correlation_id=correlation_id,
            )
            try:
                # Savepoint: a unique violation here must not poison the
                # surrounding transaction. The add() has to happen INSIDE the
                # savepoint, otherwise rolling it back leaves the failed object
                # pending and the next autoflush retries the same bad INSERT.
                async with self.session.begin_nested():
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError:
                # Lost the race - somebody else created it microseconds ago.
                existing = await self._get(scope, key)
                if existing is None:  # pragma: no cover - defensive
                    raise
            else:
                logger.info(
                    "idempotency.claimed",
                    extra={"scope": scope, "idempotency_key": key, "claim_state": "new"},
                )
                return Claim(record=record, state="new")

        assert existing is not None
        if existing.request_hash != request_hash:
            logger.warning(
                "idempotency.conflict",
                extra={"scope": scope, "idempotency_key": key},
            )
            return Claim(record=existing, state="conflict")

        if existing.status == "completed":
            return Claim(record=existing, state="replay")
        if existing.status == "failed":
            # Previous attempt failed: same logical identity is retried.
            existing.status = "in_progress"
            existing.updated_at = utcnow()
            if correlation_id:
                existing.correlation_id = correlation_id
            await self.session.flush()
            return Claim(record=existing, state="retry")
        return Claim(record=existing, state="in_progress")

    async def complete(
        self,
        record: IdempotencyRecord,
        *,
        resource_type: str,
        resource_id: str,
        response: BaseModel | dict[str, Any],
    ) -> None:
        record.status = "completed"
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.response_snapshot = (
            response.model_dump(mode="json") if isinstance(response, BaseModel) else response
        )
        record.updated_at = utcnow()
        await self.session.flush()

    async def fail(
        self,
        record: IdempotencyRecord,
        *,
        error: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Mark the claim failed so the same key may be retried later.

        The failure history is not stored here - it lives in integration_requests,
        which is append-only (ADR-007).
        """
        record.status = "failed"
        record.resource_type = resource_type or record.resource_type
        record.resource_id = resource_id or record.resource_id
        record.response_snapshot = {"error": error}
        record.updated_at = utcnow()
        await self.session.flush()

    async def _get(self, scope: str, key: str) -> IdempotencyRecord | None:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
