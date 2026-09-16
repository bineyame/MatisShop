"""Explicit state machines for every rail resource.

Statuses are not free-form strings that anybody may overwrite. A transition
that is not declared here raises, which is what keeps "registered" from
silently regressing to "pending" and losing an IRN.
"""

from __future__ import annotations

from app.domain.errors import InvalidStateTransition

# ---------------------------------------------------------------------------
# Fiscal
#
#   pending ──► submitting ──► registered ──► cancelled
#                   │
#                   └────────► failed ──► submitting (retry, same identity)
# ---------------------------------------------------------------------------
FISCAL_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"submitting", "cancelled"},
    "submitting": {"registered", "failed"},
    "failed": {"submitting", "cancelled"},
    "registered": {"cancelled"},
    "cancelled": set(),
}
FISCAL_TERMINAL = {"cancelled"}

PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"authorized", "succeeded", "failed", "cancelled"},
    "authorized": {"succeeded", "failed", "cancelled"},
    "succeeded": {"refunded", "partially_refunded"},
    "partially_refunded": {"partially_refunded", "refunded"},
    "failed": {"pending"},
    "cancelled": set(),
    "refunded": set(),
}

DELIVERY_TRANSITIONS: dict[str, set[str]] = {
    "created": {"assigned", "cancelled", "failed"},
    "assigned": {"picked_up", "cancelled", "failed"},
    "picked_up": {"in_transit", "delivered", "failed"},
    "in_transit": {"delivered", "failed"},
    "delivered": set(),
    "failed": {"cancelled"},
    "cancelled": set(),
}

MACHINES = {
    "fiscal": FISCAL_TRANSITIONS,
    "payment": PAYMENT_TRANSITIONS,
    "delivery": DELIVERY_TRANSITIONS,
}


def can_transition(machine: str, current: str, target: str) -> bool:
    transitions = MACHINES[machine]
    if current not in transitions:
        return False
    return target in transitions[current]


def assert_transition(machine: str, current: str, target: str) -> None:
    """Raise unless ``current -> target`` is a declared transition."""
    if not can_transition(machine, current, target):
        raise InvalidStateTransition(
            f"{machine}: cannot move from '{current}' to '{target}'",
            detail={"machine": machine, "from": current, "to": target},
        )
