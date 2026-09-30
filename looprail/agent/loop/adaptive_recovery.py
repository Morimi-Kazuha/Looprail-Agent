"""Bounded recovery advice; the Loop retains execution and budget authority."""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from typing import Literal, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecoveryStrategy = Literal["RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP"]
STRATEGIES = ("RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP")
FailureCode = Literal[
    "disabled",
    "missing_key",
    "missing_model",
    "invalid_config",
    "timeout",
    "http_error",
    "invalid_response",
    "advisor_error",
    "low_confidence",
]

STRATEGY_HINTS: dict[RecoveryStrategy, str] = {
    "RECHECK_INPUTS": (
        "[recovery:RECHECK_INPUTS] Inspect the exact path, schema, or precondition using available "
        "read-only tools before another attempt. Do not repeat the failed call unchanged. "
        "A failed write may have partial effects: inspect current state first."
    ),
    "USE_ALTERNATIVE": (
        "[recovery:USE_ALTERNATIVE] Change method or use available local evidence. "
        "Do not repeat the failed call unchanged, bypass permission boundaries, or invent results. "
        "Report any remaining blocker."
    ),
    "STOP": (
        "[recovery:STOP] Repeated tool failures prevent safe progress. No more tools will run in this turn. "
        "Summarize verified progress and the blocker; clearly state what remains incomplete."
    ),
}


class RecoveryContext(BaseModel):
    """A small projection, without tool arguments, results, history, or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    user_goal: str = Field(max_length=2000)
    failed_tool: str = Field(min_length=1, max_length=128)
    failure_kind: Literal["missing_input", "permission_denied", "invalid_arguments", "tool_failure"]
    consecutive_failures: int = Field(ge=2)
    remaining_iterations: int = Field(ge=1)
    available_tools: tuple[str, ...]


class RecoverySuggestion(BaseModel):
    """The documented Choice answer, validated again at the policy boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    type: Literal["choice"]
    choice: RecoveryStrategy
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    probabilities: dict[RecoveryStrategy, float]

    @model_validator(mode="after")
    def valid_distribution(self) -> RecoverySuggestion:
        values = self.probabilities
        if set(values) != set(STRATEGIES):
            raise ValueError("incomplete recovery distribution")
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in values.values()):
            raise ValueError("invalid recovery probability")
        if not math.isclose(sum(values.values()), 1.0, abs_tol=0.001):
            raise ValueError("recovery probabilities must sum to one")
        if values[self.choice] < max(values.values()):
            raise ValueError("choice must have maximal probability")
        return self


class DecisionAdvisorPort(Protocol):
    async def advise(self, context: RecoveryContext) -> RecoverySuggestion: ...


class AdvisorUnavailableError(Exception):
    """Only a closed error code crosses the infrastructure boundary."""

    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RecoveryDecision:
    selected_action: RecoveryStrategy | Literal["BASELINE"]
    decision_source: Literal["jev", "baseline"]
    confidence: float | None
    reason: str
    fallback_used: bool
    latency_ms: float
    suggested_action: RecoveryStrategy | None = None

    def attributes(self) -> dict:
        return {"decision_type": "TOOL_RECOVERY", **self.__dict__}


class RecoveryPolicy:
    """One call per turn is enforced by the caller; all failures keep its old nudge."""

    def __init__(
        self,
        advisor: DecisionAdvisorPort | None,
        *,
        timeout_seconds: float = 2.0,
        confidence_threshold: float = 0.65,
        unavailable_reason: FailureCode = "missing_key",
    ):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError("invalid recovery timeout")
        if not math.isfinite(confidence_threshold) or not 0 <= confidence_threshold <= 1:
            raise ValueError("invalid recovery threshold")
        self.advisor = advisor
        self.timeout_seconds = timeout_seconds
        self.confidence_threshold = confidence_threshold
        self.unavailable_reason = unavailable_reason

    async def decide(self, context: RecoveryContext | dict) -> RecoveryDecision:
        started = time.perf_counter()
        suggestion = None
        failure: FailureCode | None = None
        try:
            validated_context: RecoveryContext = RecoveryContext.model_validate(
                context.model_dump() if isinstance(context, RecoveryContext) else context
            )
            if self.advisor is None:
                raise AdvisorUnavailableError(self.unavailable_reason)
            raw = await asyncio.wait_for(self.advisor.advise(validated_context), self.timeout_seconds)
            # Revalidate even a constructed/mutated Pydantic instance supplied by a custom port.
            suggestion = RecoverySuggestion.model_validate(
                raw.model_dump() if isinstance(raw, RecoverySuggestion) else raw
            )
            if suggestion.confidence < self.confidence_threshold:
                failure = "low_confidence"
        except asyncio.CancelledError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            failure = "timeout"
        except AdvisorUnavailableError as exc:
            failure = exc.code if exc.code in get_args(FailureCode) else "advisor_error"
        except (ValueError, TypeError):
            failure = "invalid_response"
        except Exception:
            failure = "advisor_error"
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        if failure:
            return RecoveryDecision(
                "BASELINE",
                "baseline",
                suggestion.confidence if suggestion else None,
                failure,
                True,
                elapsed,
                suggestion.choice if suggestion else None,
            )
        if suggestion is None:
            return RecoveryDecision("BASELINE", "baseline", None, "invalid_response", True, elapsed)
        return RecoveryDecision(
            suggestion.choice,
            "jev",
            suggestion.confidence,
            f"accepted_{suggestion.choice.lower()}",
            False,
            elapsed,
            suggestion.choice,
        )


def project_failure_kind(
    result: object,
) -> Literal["missing_input", "permission_denied", "invalid_arguments", "tool_failure"]:
    """Only controlled facts from the failure enter the advisor; raw output stays local."""
    text = str(result).lower()
    if any(marker in text for marker in ("permission denied", "access denied", "forbidden")):
        return "permission_denied"
    if any(marker in text for marker in ("not found", "does not exist", "no such file")):
        return "missing_input"
    if any(marker in text for marker in ("invalid argument", "validation", "required parameter")):
        return "invalid_arguments"
    return "tool_failure"


def bounded_goal(value: str) -> str:
    """Best-effort removal of credential-shaped text; no raw goal is logged here."""
    value = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(
        r"(?i)\b([\w-]*(?:api[_-]?key|secret|password|token))\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        value,
    )
    return value[:2000]
