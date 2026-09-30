"""Small recovery regression scaffold. Fake by default; live explicitly incurs API cost."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from looprail.agent.loop.adaptive_recovery import (
    RecoveryContext,
    RecoveryPolicy,
    RecoveryStrategy,
    RecoverySuggestion,
    bounded_goal,
)
from looprail.config.looprail import JevConfig
from looprail.providers.jev_decision import build_recovery_policy

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "benchmarks" / "recovery" / "cases.json"


class RecoveryCase(BaseModel):
    """Human-reviewed acceptable sets; descriptive notes never reach the advisor."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    goal: str = Field(min_length=1, max_length=2000)
    failure_type: Literal["missing_input", "permission_denied", "invalid_arguments", "tool_failure"]
    tool_name: str = Field(min_length=1, max_length=128)
    failure_count: int = Field(ge=2)
    available_tools: tuple[str, ...] = Field(max_length=64)
    remaining_budget: int = Field(ge=1)
    acceptable_actions: tuple[RecoveryStrategy, ...] = Field(min_length=1, max_length=3)
    notes: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_actions_and_tools(self) -> RecoveryCase:
        if len(set(self.acceptable_actions)) != len(self.acceptable_actions):
            raise ValueError("duplicate acceptable action")
        if len(set(self.available_tools)) != len(self.available_tools):
            raise ValueError("duplicate tool")
        if any(not tool or len(tool) > 128 for tool in self.available_tools):
            raise ValueError("invalid tool name")
        return self

    def context(self) -> RecoveryContext:
        return RecoveryContext(
            user_goal=bounded_goal(self.goal),
            failed_tool=self.tool_name,
            failure_kind=self.failure_type,
            consecutive_failures=self.failure_count,
            available_tools=self.available_tools,
            remaining_iterations=self.remaining_budget,
        )


def load_cases(path: Path) -> list[RecoveryCase]:
    cases = TypeAdapter(list[RecoveryCase]).validate_json(path.read_bytes())
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("case corpus must be nonempty with unique ids")
    return cases


class FakeRecoveryAdvisor:
    """A deliberately simple rule-based double; receives no expected labels."""

    async def advise(self, context: RecoveryContext) -> RecoverySuggestion:
        if (
            context.remaining_iterations <= 1
            or not context.available_tools
            or context.failure_kind == "permission_denied"
        ):
            action: RecoveryStrategy = "STOP"
        elif context.failure_kind in {"missing_input", "invalid_arguments"}:
            action = "RECHECK_INPUTS"
        elif any(tool != context.failed_tool for tool in context.available_tools):
            action = "USE_ALTERNATIVE"
        else:
            action = "STOP"
        return RecoverySuggestion(
            type="choice",
            choice=action,
            confidence=0.8,
            probabilities={
                key: 0.9 if key == action else 0.05 for key in ("RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP")
            },
        )


async def evaluate(cases: list[RecoveryCase], policy: RecoveryPolicy, *, advisor_mode: str) -> dict:
    rows = []
    for case in cases:
        decision = await policy.decide(case.context())
        rows.append(
            {
                "case": case.id,
                **decision.attributes(),
                "acceptable_actions": list(case.acceptable_actions),
                "accepted": not decision.fallback_used and decision.selected_action in case.acceptable_actions,
            }
        )
    return {
        "advisor_mode": advisor_mode,
        "threshold": policy.confidence_threshold,
        "cases": len(rows),
        "accepted_decisions": sum(row["accepted"] for row in rows),
        "fallbacks": sum(row["fallback_used"] for row in rows),
        "invalid_responses": sum(row["reason"] == "invalid_response" for row in rows),
        "mean_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 3) if rows else 0,
        "decision_distribution": dict(Counter(row["selected_action"] for row in rows)),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--advisor", choices=("fake", "live"), default="fake")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--threshold", type=float, help="Override confidence threshold; default 0.65")
    parser.add_argument("--json", action="store_true", help="Print bounded decision rows for later comparisons")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        config = JevConfig(enabled=True)
        if args.threshold is not None:
            config = JevConfig(enabled=True, confidence_threshold=args.threshold)
        policy: RecoveryPolicy | None
        if args.advisor == "fake":
            policy = RecoveryPolicy(FakeRecoveryAdvisor(), confidence_threshold=config.confidence_threshold)
        else:
            policy = build_recovery_policy(config)
            if policy is None or policy.advisor is None:
                parser.error("live advisor requires enabled, valid LOOPRAIL settings and the transport-specific key")
            if args.threshold is not None:
                policy = RecoveryPolicy(
                    policy.advisor,
                    timeout_seconds=policy.timeout_seconds,
                    confidence_threshold=args.threshold,
                )
    except (ValueError, OSError):
        parser.error("invalid case corpus or threshold; check the documented schema and bounds")
    report = asyncio.run(evaluate(cases, policy, advisor_mode=args.advisor))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Recovery Evaluation\nAdvisor: {args.advisor} (fake results are simulated, not Jev quality)\n")
        print(f"Cases: {report['cases']}  threshold: {report['threshold']}\n")
        print(f"{'case':36} {'decision':18} accepted")
        print("-" * 65)
        for row in report["rows"]:
            print(f"{row['case']:36} {row['selected_action']:18} {'yes' if row['accepted'] else 'no'}")
        print(f"\naccepted decisions: {report['accepted_decisions']} / {report['cases']}")
        print(f"fallbacks: {report['fallbacks']}\ninvalid responses: {report['invalid_responses']}")
        print(f"mean latency: {report['mean_latency_ms']} ms")
        print("decision distribution:", json.dumps(report["decision_distribution"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
