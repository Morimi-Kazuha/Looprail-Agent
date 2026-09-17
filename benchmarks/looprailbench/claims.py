from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .schema import ClaimRule


@dataclass(frozen=True)
class ClaimRuleResult:
    rule_id: str
    metric: str
    passed: bool
    observed: int | float | None
    threshold: int | float
    reason: str


class ClaimState(StrEnum):
    SUPPORTED = "SUPPORTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"
    INVALID_MEASUREMENT = "INVALID_MEASUREMENT"


@dataclass(frozen=True)
class LayeredClaimEvaluation:
    """Auditable hard-gate result kept separate from task/trial status."""

    state: ClaimState
    reason: str
    gates: dict[str, bool]
    efficiency_observed: float | None = None

    @property
    def positive_claim_eligible(self) -> bool:
        return self.state is ClaimState.SUPPORTED


@dataclass(frozen=True)
class ClaimEvaluation:
    ship_complete: bool
    measurement_valid: bool
    positive_claim_eligible: bool
    rules: tuple[ClaimRuleResult, ...]

    @property
    def state(self) -> ClaimState:
        if not self.measurement_valid:
            return ClaimState.INVALID_MEASUREMENT
        if self.positive_claim_eligible:
            return ClaimState.SUPPORTED
        if any(rule.reason in {"metric_missing", "prerequisite_not_met"} for rule in self.rules):
            return ClaimState.INCONCLUSIVE
        return ClaimState.REJECTED


def evaluate_claim_rules(
    rules: tuple[ClaimRule, ...],
    *,
    metrics: dict[str, Any],
    ship_complete: bool,
    measurement_valid: bool,
) -> ClaimEvaluation:
    results = tuple(_evaluate_rule(rule, metrics) for rule in rules)
    eligible = ship_complete and measurement_valid and bool(results) and all(result.passed for result in results)
    return ClaimEvaluation(
        ship_complete=ship_complete,
        measurement_valid=measurement_valid,
        positive_claim_eligible=eligible,
        rules=results,
    )


def evaluate_layered_claim(
    *,
    measurement_valid: bool,
    correctness_ok: bool,
    regression_ok: bool,
    evidence_sufficient: bool,
    integrity_ok: bool = True,
    efficiency_observed: float | None = None,
) -> LayeredClaimEvaluation:
    """Apply non-compensating Claim Gates in a fixed, explainable order.

    Efficiency is reported as context only. It cannot turn a correctness or
    regression failure into a supported claim, and weak evidence returns
    ``INCONCLUSIVE`` instead of manufacturing a winner.
    """
    gates = {
        "measurement_valid": bool(measurement_valid),
        "correctness": bool(correctness_ok),
        "integrity": bool(integrity_ok),
        "regression_non_inferiority": bool(regression_ok),
        "evidence_sufficiency": bool(evidence_sufficient),
    }
    if not gates["measurement_valid"]:
        return LayeredClaimEvaluation(ClaimState.INVALID_MEASUREMENT, "measurement_invalid", gates, efficiency_observed)
    if not gates["correctness"]:
        return LayeredClaimEvaluation(ClaimState.REJECTED, "correctness_gate_failed", gates, efficiency_observed)
    if not gates["integrity"]:
        return LayeredClaimEvaluation(ClaimState.REJECTED, "integrity_gate_failed", gates, efficiency_observed)
    if not gates["regression_non_inferiority"]:
        return LayeredClaimEvaluation(ClaimState.REJECTED, "regression_gate_failed", gates, efficiency_observed)
    if not gates["evidence_sufficiency"]:
        return LayeredClaimEvaluation(ClaimState.INCONCLUSIVE, "evidence_insufficient", gates, efficiency_observed)
    return LayeredClaimEvaluation(ClaimState.SUPPORTED, "all_hard_gates_passed", gates, efficiency_observed)


def evaluate_paired_claim(
    *,
    baseline_passes: int,
    candidate_passes: int,
    valid_pairs: int,
    minimum_valid_pairs: int,
    measurement_valid: bool = True,
    integrity_ok: bool = True,
    efficiency_observed: float | None = None,
    candidate_correctness_ok: bool | None = None,
) -> LayeredClaimEvaluation:
    """Evaluate a deterministic baseline/candidate paired comparison."""
    if (
        baseline_passes < 0
        or candidate_passes < 0
        or valid_pairs < 0
        or baseline_passes > valid_pairs
        or candidate_passes > valid_pairs
        or minimum_valid_pairs < 1
    ):
        raise ValueError("paired claim counts must be non-negative and minimum_valid_pairs must be positive")
    correctness_ok = (
        candidate_correctness_ok
        if candidate_correctness_ok is not None
        else candidate_passes == valid_pairs
    )
    regression_ok = candidate_passes >= baseline_passes
    return evaluate_layered_claim(
        measurement_valid=measurement_valid,
        correctness_ok=correctness_ok,
        regression_ok=regression_ok,
        evidence_sufficient=valid_pairs >= minimum_valid_pairs,
        integrity_ok=integrity_ok,
        efficiency_observed=efficiency_observed,
    )


# Stable descriptive alias for callers that name the operation after the
# policy rather than the implementation module.
evaluate_claim_gate = evaluate_layered_claim


def _evaluate_rule(
    rule: ClaimRule,
    metrics: dict[str, Any],
) -> ClaimRuleResult:
    if any(metrics.get(prerequisite) is not True for prerequisite in rule.prerequisites):
        return ClaimRuleResult(
            rule_id=rule.rule_id,
            metric=rule.metric,
            passed=False,
            observed=_number_or_none(metrics.get(rule.metric)),
            threshold=rule.threshold,
            reason="prerequisite_not_met",
        )
    observed = _number_or_none(metrics.get(rule.metric))
    if observed is None:
        return ClaimRuleResult(
            rule_id=rule.rule_id,
            metric=rule.metric,
            passed=False,
            observed=None,
            threshold=rule.threshold,
            reason="metric_missing",
        )
    passed = {
        "eq": observed == rule.threshold,
        "ge": observed >= rule.threshold,
        "gt": observed > rule.threshold,
        "le": observed <= rule.threshold,
        "lt": observed < rule.threshold,
    }[rule.operator]
    return ClaimRuleResult(
        rule_id=rule.rule_id,
        metric=rule.metric,
        passed=passed,
        observed=observed,
        threshold=rule.threshold,
        reason="passed" if passed else "threshold_not_met",
    )


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value
