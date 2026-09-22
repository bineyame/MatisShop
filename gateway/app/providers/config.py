"""Provider configuration checking.

The rule (spec §22): selecting a real provider without its credentials must
**fail clearly**. It must never silently fall back to the mock - a deployment
that thinks it is registering real fiscal documents while quietly using a mock
is the worst possible failure mode.

Mocks are the explicit default in `.env.example`. Choosing a real provider is
therefore always a deliberate act, and this module makes sure that act is
either complete or loudly incomplete.
"""

from __future__ import annotations

from app.config import Settings
from app.domain.errors import ProviderNotConfigured


def missing_settings(settings: Settings, required: tuple[str, ...]) -> list[str]:
    """Names of the required settings that are absent or blank."""
    missing = []
    for name in required:
        value = getattr(settings, name.lower(), None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name.upper())
    return missing


def require_settings(
    settings: Settings,
    provider_name: str,
    rail: str,
    required: tuple[str, ...],
    *,
    implemented: bool = True,
    blockers: tuple[str, ...] = (),
) -> None:
    """Raise ``ProviderNotConfigured`` unless this provider is usable.

    Two distinct reasons a provider may be unusable, reported differently
    because they need different actions:

    * credentials are missing  -> supply them
    * the adapter is a declared seam awaiting an API specification
      -> implement it; the blockers say what is needed first
    """
    missing = missing_settings(settings, required)

    if not implemented:
        raise ProviderNotConfigured(
            f"{rail} provider '{provider_name}' is an adapter seam and is not implemented yet",
            provider=provider_name,
            detail={
                "rail": rail,
                "required_settings": list(required),
                "missing_settings": missing,
                "blockers": list(blockers),
                "documentation": "docs/providers.md",
            },
        )

    if missing:
        raise ProviderNotConfigured(
            f"{rail} provider '{provider_name}' is selected but not configured: "
            f"missing {', '.join(missing)}",
            provider=provider_name,
            detail={
                "rail": rail,
                "required_settings": list(required),
                "missing_settings": missing,
                "documentation": "docs/providers.md",
            },
        )
