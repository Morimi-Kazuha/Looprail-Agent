"""Deterministic repository-style fixture used by the Phase 6 evidence tests.

This fixture is intentionally a tiny local Python repository. It exercises the
existing PicoBench Task/Trial/Verifier/Pair path without a model, a network
provider, or candidate-code generation. The fixture is evidence machinery, not
an Evolver entry point.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from benchmarks.picobench.canonical import to_primitive
from benchmarks.picobench.isolation import TrialIsolation
from benchmarks.picobench.protocol import TrialContext, TrialExecution
from benchmarks.picobench.reproducibility import capture_reproducibility_manifest
from benchmarks.picobench.records import (
    DeliveryOutcome,
    TrialStatus,
    TurnTerminalState,
    VerificationState,
    VerifierResult,
)
from benchmarks.picobench.schema import PackDefinition, PairSpec, TaskSpec, VariantSpec
from benchmarks.picobench.verifier import VerifierSeal, RepositoryTaskVerifier, run_sealed_verifier


@dataclass(frozen=True)
class Phase6RepositoryFixture:
    fixture_id: str = "phase6-calculator-v1"
    source_path: str = "calculator.py"
    test_path: str = "test_calculator.py"

    def materialize(self, workspace: Path) -> None:
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / self.source_path).write_text(
            "def add(left, right):\n    return left - right\n",
            encoding="utf-8",
        )
        (workspace / self.test_path).write_text(
            "from calculator import add\n\n\ndef test_add_is_correct():\n    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )

    def apply_strategy(self, workspace: Path, strategy_id: str) -> str:
        """Apply one deterministic strategy and return its self-report.

        The report is deliberately ignored by the verifier. It exists only so
        the acceptance test can prove that a claimed PASS cannot override an
        independently observed FAIL.
        """
        if strategy_id == "candidate":
            (Path(workspace) / self.source_path).write_text(
                "def add(left, right):\n    return left + right\n",
                encoding="utf-8",
            )
            return "fixed"
        return "fixed"  # an intentionally false baseline self-report

    def verifier(self, workspace: Path) -> RepositoryTaskVerifier:
        return RepositoryTaskVerifier.capture(
            workspace,
            required_changed_paths=(self.source_path,),
            forbidden_paths=(self.test_path,),
        )


class Phase6RepositoryPack:
    """A small Pack adapter that reuses the ordinary PicoBench harness."""

    fixture = Phase6RepositoryFixture()

    def definition(self) -> PackDefinition:
        return PackDefinition(
            pack_id="phase6-repository",
            tasks=(
                TaskSpec(
                    task_id="calculator-add",
                    payload={
                        "fixture_id": self.fixture.fixture_id,
                        "success": "pytest_passes_and_required_source_changes",
                        "required_changed_paths": [self.fixture.source_path],
                        "forbidden_paths": [self.fixture.test_path],
                        "verifier": "repository_task_v1",
                    },
                ),
            ),
            variants=(
                VariantSpec(variant_id="baseline", settings={"strategy": "baseline"}),
                VariantSpec(variant_id="candidate", settings={"strategy": "candidate"}),
            ),
            pairs=(
                PairSpec(
                    treatment_axis="strategy",
                    control_variant_id="baseline",
                    treatment_variant_id="candidate",
                ),
            ),
            identity={
                "fixture_id": self.fixture.fixture_id,
                "verifier_id": "repository_task_v1",
                "minimum_valid_pairs_per_task": 1,
            },
        )

    async def run_trial(self, context: TrialContext) -> TrialExecution:
        attempt_id = (
            f"{context.key.task_id}-{context.key.variant_id}-"
            f"r{context.key.repetition}-b{context.block_attempt}"
        )
        isolation = TrialIsolation.create(
            context.experiment.output_root / context.experiment_id / ".phase6-repository",
            attempt_id,
        )
        isolation.prepare()
        self.fixture.materialize(isolation.workspace)
        verifier = self.fixture.verifier(isolation.workspace)
        seal = VerifierSeal.capture_many((isolation.workspace / self.fixture.test_path,))
        claimed = self.fixture.apply_strategy(
            isolation.workspace,
            str(context.variant.settings["strategy"]),
        )
        verification = await run_sealed_verifier(
            verifier,
            workspace=isolation.workspace,
            seal=seal,
        )
        manifest = capture_reproducibility_manifest(
            Path(__file__).resolve().parents[3],
            task_id=context.key.task_id,
            fixture_id=self.fixture.fixture_id,
            strategy_id=str(context.variant.settings["strategy"]),
            verifier=verifier,
            config={
                "test_args": ["-q"],
                "timeout_seconds": context.experiment.execution.timeout_seconds,
            },
        )
        manifest_path = isolation.root / "reproducibility.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verifier_path = isolation.root / "verifier-result.json"
        verifier_path.write_text(
            json.dumps(to_primitive(verification.result), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        relative_root = isolation.root.relative_to(
            context.experiment.output_root / context.experiment_id,
        )
        verifier_ref = (relative_root / verifier_path.name).as_posix()
        reproducibility_ref = (relative_root / manifest_path.name).as_posix()
        if verification.infrastructure_error is not None:
            status = TrialStatus.INFRASTRUCTURE_FAILURE
            findings = (verification.infrastructure_error,)
            measurement_valid = False
        elif verification.result.state is VerificationState.PASSED:
            status = TrialStatus.PASSED
            findings = ()
            measurement_valid = True
        else:
            status = TrialStatus.TASK_FAILED
            findings = tuple(verification.result.findings)
            measurement_valid = True
        return TrialExecution(
            status=status,
            runtime_state=TurnTerminalState.COMPLETED,
            delivery_state=DeliveryOutcome.DELIVERED,
            verification=verification.result,
            observed_variant_settings=dict(context.variant.settings),
            metrics={
                "fixture_id": self.fixture.fixture_id,
                "agent_self_report": claimed,
                "task_result": status.value,
                "verifier_observed_pass": verification.result.state is VerificationState.PASSED,
            },
            findings=findings,
            artifact_refs=(relative_root.as_posix(), verifier_ref, reproducibility_ref),
            measurement_valid=measurement_valid,
            verifier_artifact_ref=verifier_ref,
            reproducibility_ref=reproducibility_ref,
        )


__all__ = ["Phase6RepositoryFixture", "Phase6RepositoryPack"]
