"""Gateway error taxonomy.

The distinction that matters operationally is transient vs permanent:
a transient provider error is retried (same idempotency key, so at most one
registration); a permanent one is not.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for every error raised inside the gateway."""

    status_code = 500
    code = "gateway_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ValidationError(GatewayError):
    status_code = 422
    code = "validation_error"


class ResourceNotFound(GatewayError):
    status_code = 404
    code = "not_found"


class IdempotencyConflict(GatewayError):
    """Same idempotency key, different request body."""

    status_code = 409
    code = "idempotency_conflict"


class OperationInProgress(GatewayError):
    """A request with this idempotency key is currently being processed."""

    status_code = 409
    code = "operation_in_progress"


class InvalidStateTransition(GatewayError):
    status_code = 409
    code = "invalid_state_transition"


class AuthenticationError(GatewayError):
    status_code = 401
    code = "authentication_error"


# --- provider-facing errors -------------------------------------------------
class ProviderError(GatewayError):
    """Base class for anything a provider adapter raises."""

    status_code = 502
    code = "provider_error"

    def __init__(self, message: str, *, provider: str = "unknown", detail: dict | None = None) -> None:
        super().__init__(message, detail=detail)
        self.provider = provider


class ProviderTransientError(ProviderError):
    """Retryable: timeout, connection reset, 5xx, provider rate limit."""

    status_code = 503
    code = "provider_unavailable"
    retryable = True


class ProviderPermanentError(ProviderError):
    """Not retryable without changing the request: rejected/invalid document."""

    status_code = 422
    code = "provider_rejected"
    retryable = False


class ProviderNotConfigured(ProviderError):
    """A real provider was selected but its credentials/implementation are absent."""

    status_code = 501
    code = "provider_not_configured"
    retryable = False
