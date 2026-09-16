"""Context budget, limit resolution, and bounded decision evidence.

The Context Engine owns the *supported* input budget.  Provider/model metadata
can lower the configured window, but an unknown provider limit never causes the
runtime to invent a larger one.  The small value objects in this module are
also used as audit evidence; they are not a second Memory or Session store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pico.call_efficiency.pricing import resolve_context_window

# This is used only when a caller supplies a non-positive configured value.  A
# normal AgentLoop always supplies the configured AgentDefaults value.  Keeping
# the fallback conservative and explicit is safer than treating zero as an
# unlimited window.
DEFAULT_CONTEXT_LIMIT = 65_536


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def provider_context_limit(provider: Any, model: str | None) -> int | None:
    """Find an advertised provider/model input limit without network I/O.

    A few provider integrations expose a direct limit; the existing pricing
    catalog is the final metadata source.  The lookup is deliberately duck
    typed so it remains compatible with test providers and third-party
    wrappers.  A missing/invalid value returns ``None`` and is recorded by the
    caller as a configured-limit fallback.
    """

    owners: list[Any] = []
    pending = [provider]
    seen: set[int] = set()
    while pending:
        owner = pending.pop(0)
        if owner is None or id(owner) in seen:
            continue
        seen.add(id(owner))
        owners.append(owner)
        for attr in ("delegate", "_provider", "_delegate"):
            nested = getattr(owner, attr, None)
            if nested is not None:
                pending.append(nested)

    for owner in owners:
        for method_name in (
            "get_context_window",
            "get_context_window_tokens",
            "context_window_for_model",
        ):
            method = getattr(owner, method_name, None)
            if not callable(method):
                continue
            for args in ((model,), ()) if model else ((),):
                try:
                    value = _positive_int(method(*args))
                except (TypeError, ValueError, AttributeError, NotImplementedError):
                    continue
                if value is not None:
                    return value

        for attr_name in (
            "context_window_tokens",
            "context_window",
            "context_length",
            "max_input_tokens",
        ):
            value = _positive_int(getattr(owner, attr_name, None))
            if value is not None:
                return value

    if not model:
        return None
    try:
        # Do not import a lazy provider or refresh OpenRouter here.  The
        # configured window remains the safe fallback when catalog metadata is
        # not already available.
        return resolve_context_window(
            model,
            allow_network=False,
            allow_litellm_import=False,
        )
    except Exception:  # noqa: BLE001 — metadata must never block a Turn.
        return None


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """Resolved limits for one provider request."""

    configured_context_limit: int
    provider_context_limit: int | None
    effective_context_limit: int
    reserved_output: int
    runtime_margin: int
    input_context_budget: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured_context_limit": self.configured_context_limit,
            "provider_context_limit": self.provider_context_limit,
            "effective_context_limit": self.effective_context_limit,
            "reserved_output": self.reserved_output,
            "runtime_margin": self.runtime_margin,
            "input_context_budget": self.input_context_budget,
            "source": self.source,
        }


def resolve_context_limits(
    provider: Any,
    model: str | None,
    configured_context_limit: int,
    reserved_output: int,
    runtime_margin: int,
) -> ContextLimits:
    """Resolve a conservative input budget for one turn.

    ``effective_context_limit`` is the lower of a valid configured limit and a
    known provider/model limit.  If the latter is unavailable, the configured
    limit is explicitly used as the conservative fallback.  The input budget
    is allowed to become zero; later assembly then raises a normalized error
    if even protected content cannot fit.
    """

    configured = _positive_int(configured_context_limit) or DEFAULT_CONTEXT_LIMIT
    provider_limit = provider_context_limit(provider, model)
    if provider_limit is None:
        effective = configured
        source = "configured_fallback"
    else:
        effective = min(configured, provider_limit)
        source = "provider_and_configured" if configured != provider_limit else "provider"

    output = max(0, int(reserved_output or 0))
    margin = max(0, int(runtime_margin or 0))
    input_budget = max(0, effective - output - margin)
    return ContextLimits(
        configured_context_limit=configured,
        provider_context_limit=provider_limit,
        effective_context_limit=effective,
        reserved_output=output,
        runtime_margin=margin,
        input_context_budget=input_budget,
        source=source,
    )


@dataclass(frozen=True, slots=True)
class ContextDecision:
    """Bounded, provider-side evidence for one retained/dropped group."""

    source: str
    layer: str
    estimated_tokens: int
    priority: float
    decision: str
    reason: str
    protected: bool = False
    message_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "layer": self.layer,
            "estimated_tokens": self.estimated_tokens,
            "priority": self.priority,
            "decision": self.decision,
            "reason": self.reason,
            "protected": self.protected,
            "message_ids": list(self.message_ids),
        }


class ContextBudgetError(RuntimeError):
    """Raised when supported input budget cannot hold protected context."""

    def __init__(
        self,
        *,
        estimated_tokens: int,
        input_context_budget: int,
        context_limit: int,
        reserved_output: int,
        runtime_margin: int,
        reason: str,
        protected_tokens: int | None = None,
    ) -> None:
        self.estimated_tokens = int(estimated_tokens)
        self.input_context_budget = int(input_context_budget)
        self.context_limit = int(context_limit)
        self.reserved_output = int(reserved_output)
        self.runtime_margin = int(runtime_margin)
        self.reason = str(reason)
        self.protected_tokens = None if protected_tokens is None else int(protected_tokens)
        super().__init__(
            "context budget exceeded: "
            f"estimated={self.estimated_tokens}, input_budget={self.input_context_budget}, "
            f"context_limit={self.context_limit}, reserved_output={self.reserved_output}, "
            f"runtime_margin={self.runtime_margin}, reason={self.reason}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "context_budget_exceeded",
            "estimated_tokens": self.estimated_tokens,
            "input_context_budget": self.input_context_budget,
            "context_limit": self.context_limit,
            "reserved_output": self.reserved_output,
            "runtime_margin": self.runtime_margin,
            "reason": self.reason,
            "protected_tokens": self.protected_tokens,
        }


__all__ = [
    "ContextBudgetError",
    "ContextDecision",
    "ContextLimits",
    "DEFAULT_CONTEXT_LIMIT",
    "provider_context_limit",
    "resolve_context_limits",
]
