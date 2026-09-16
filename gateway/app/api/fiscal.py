"""Fiscal rail HTTP API.

Contract note: a provider failure is NOT an HTTP error. The gateway accepted
and durably recorded the request, so it answers 200 with
``status: "failed"`` and a ``last_error``. The ERP reads the domain status,
keeps its own record retryable, and tries again later with the same
idempotency key. This is what makes the offline story work.

HTTP errors are reserved for the request itself being wrong: bad auth (401),
invalid body (422), reused key with a different body (409).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Header, Path

from app.api.deps import AuthDep, FiscalServiceDep
from app.domain.contracts import FiscalCancelRequest, FiscalDocumentRequest, FiscalDocumentResponse
from app.domain.errors import ValidationError

router = APIRouter(prefix="/fiscal", tags=["fiscal"], dependencies=[AuthDep])


@router.post(
    "/documents",
    response_model=FiscalDocumentResponse,
    summary="Register a fiscal document",
)
async def register_document(
    service: FiscalServiceDep,
    payload: Annotated[FiscalDocumentRequest, Body()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> FiscalDocumentResponse:
    """Register one fiscal document.

    ``Idempotency-Key`` may be sent as a header as well as in the body; if both
    are present they must agree. Repeating an identical request returns the
    original registration (``replayed: true``) and never creates a second IRN.
    """
    if idempotency_key and idempotency_key != payload.idempotency_key:
        raise ValidationError(
            "Idempotency-Key header does not match idempotency_key in the body",
            detail={"header": idempotency_key, "body": payload.idempotency_key},
        )
    return await service.register(payload)


@router.get("/documents/{document_id}", response_model=FiscalDocumentResponse)
async def get_document(
    service: FiscalServiceDep,
    document_id: Annotated[str, Path()],
) -> FiscalDocumentResponse:
    return await service.get(document_id)


@router.get("/documents/by-key/{idempotency_key}", response_model=FiscalDocumentResponse)
async def get_document_by_key(
    service: FiscalServiceDep,
    idempotency_key: Annotated[str, Path()],
) -> FiscalDocumentResponse:
    """Recovery path: the ERP lost the gateway id but still knows its own key."""
    return await service.get_by_idempotency_key(idempotency_key)


@router.post("/documents/{document_id}/retry", response_model=FiscalDocumentResponse)
async def retry_document(
    service: FiscalServiceDep,
    document_id: Annotated[str, Path()],
) -> FiscalDocumentResponse:
    """Re-submit a failed document under its original identity."""
    return await service.retry(document_id)


@router.post("/documents/{document_id}/cancel", response_model=FiscalDocumentResponse)
async def cancel_document(
    service: FiscalServiceDep,
    document_id: Annotated[str, Path()],
    payload: Annotated[FiscalCancelRequest, Body()],
) -> FiscalDocumentResponse:
    return await service.cancel(document_id, payload.reason)
