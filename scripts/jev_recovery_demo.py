"""Safe recovery demonstration: real Looprail runtime/tools, scripted text provider.

Run with ``python -m scripts.jev_recovery_demo --case recover --advisor fake``.
Only ``--advisor live`` makes a paid Jev request. No real text-provider request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from loguru import logger

from looprail.agent.loop.adaptive_recovery import RecoveryPolicy, RecoverySuggestion
from looprail.cli._repl_spine import build_repl
from looprail.cli._runtime_assembly import assemble_runtime
from looprail.config.looprail import JevConfig, LooprailConfig
from looprail.config.paths import RuntimePaths
from looprail.config.schema import Config
from looprail.providers.base import LLMResponse, ToolCallRequest
from looprail.providers.jev_decision import build_recovery_policy
from looprail.spine import ChatType, Origin, Source, TurnRequest


class FakeDecisionAdvisor:
    """A labeled test double, never described as a live Jev result."""

    def __init__(self, action="RECHECK_INPUTS"):
        self.action = action
        self.calls = 0

    async def advise(self, context):
        self.calls += 1
        return RecoverySuggestion.model_validate(
            {
                "type": "choice",
                "choice": self.action,
                "confidence": 0.95,
                "probabilities": {
                    action: 0.98 if action == self.action else 0.01
                    for action in ("RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP")
                },
            }
        )


class ScriptedRecoveryProvider:
    """A reproducible text-model simulation that responds to runtime recovery hints."""

    def __init__(self, case="recover"):
        self.case = case
        self.calls = 0
        self.tool_calls = []
        self.hints_seen = []

    def get_default_model(self):
        return "demo/scripted"

    async def chat_with_retry(self, **kwargs):
        self.calls += 1
        messages = kwargs["messages"]
        text = "\n".join(str(m.get("content", "")) for m in messages)
        if kwargs.get("tools") is None:
            return LLMResponse(content="BLOCKED: input unavailable; task remains incomplete.")
        if "RECOVERY_SENTINEL" in text:
            return LLMResponse(content="RECOVERED: read the verified local input.")
        for action in ("RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP"):
            if f"[recovery:{action}]" in text:
                self.hints_seen.append(action)
        if self.case == "normal":
            name, arguments = "read_file", {"path": "input.txt"}
        elif self.calls <= 2 or not self.hints_seen:
            name, arguments = "read_file", {"path": "typo-input.txt"}
        elif self.hints_seen[-1] == "RECHECK_INPUTS" and "list_dir" not in self.tool_calls:
            name, arguments = "list_dir", {"path": "."}
        else:
            name, arguments = "read_file", {"path": "input.txt"}
        self.tool_calls.append(name)
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(str(self.calls), name, arguments)],
            finish_reason="tool_calls",
        )


async def run_demo(
    workspace: Path,
    recovery_policy: RecoveryPolicy | None,
    *,
    case="recover",
    provider: ScriptedRecoveryProvider | None = None,
) -> dict:
    """Same assembly, Scheduler, AgentLoop, effects and Session path as CLI run."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "input.txt").write_text("RECOVERY_SENTINEL", encoding="utf-8")
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.agents.defaults.model = "demo/scripted"
    config.agents.defaults.max_tool_iterations = 6
    config.tools.restrict_to_workspace = True
    features = LooprailConfig(base=config)
    features.memory.backend = None
    features.skill_forge.enabled = False
    features.skill_forge.router.enabled = False
    features.runtime.checkpoint.policy = "never"
    provider = provider or ScriptedRecoveryProvider(case)
    runtime = assemble_runtime(
        config,
        features,
        provider=provider,
        cron_service=None,
        interactive=False,
        paths=RuntimePaths(workspace=workspace, state=workspace / ".state"),
    )
    runtime.agent_loop.recovery_policy = recovery_policy
    rendered: list[str] = []
    errors: list[str] = []
    scheduler, hub, teardown = build_repl(runtime.agent_loop, "cli", rendered.append, render_error=errors.append)
    runtime.agent_loop.subagents.set_submit(scheduler.submit)
    request = TurnRequest(
        origin=Origin.USER,
        source=Source(channel="cli", chat_id="jev-demo", sender_id="user", chat_type=ChatType.DM),
        text="Read the local input. If its filename is wrong, inspect the directory and locate the existing input.",
        conversation="cli:jev-demo",
        turn_id="jev-demo-turn",
    )
    try:
        await runtime.start_memory_backend()
        outcome = await scheduler.submit(request).result()
        await hub.wait_idle("cli")
        records = runtime.agent_loop.effect_journal.load()
        from looprail.session.manager import SessionManager

        # Read from disk with a fresh manager, rather than checking the runtime cache.
        persisted = SessionManager(workspace / ".state").get_or_create(request.conversation)
        marker = runtime.agent_loop.recovery_projector.state_store.load(request.conversation)
        return {
            "case": case,
            "text_provider": "scripted (no live text-model call)",
            "result": rendered,
            "errors": errors,
            "tools_executed": provider.tool_calls,
            "tool_calls": outcome.tool_calls,
            "tool_failures": outcome.tool_failures,
            "persisted_turn_status": marker.last_turn_status if marker else None,
            "recovery_hints_consumed": provider.hints_seen,
            "effect_statuses": [record.status.value for record in records],
            "session_persisted": bool(persisted.messages),
            "persisted_summary": persisted.messages[-1]["content"] if persisted.messages else None,
        }
    finally:
        await teardown()
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("normal", "recover"), default="recover")
    parser.add_argument("--advisor", choices=("fake", "live", "disabled"), default="fake")
    parser.add_argument("--action", choices=("RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP"), default="RECHECK_INPUTS")
    parser.add_argument("--env-file", type=Path, help="Explicit local env source; never saved or printed")
    parser.add_argument("--trace-dir", type=Path, default=Path(".looprail/evidence/jev-demo"))
    args = parser.parse_args()
    values = dict(os.environ)
    if args.env_file:
        from dotenv import dotenv_values

        # Read only Looprail's explicit recovery contract.
        for key, value in dotenv_values(args.env_file).items():
            if key.startswith("LOOPRAIL_JEV_") or key == "LOOPRAIL_OPENROUTER_API_KEY":
                if value and key not in values:
                    values[key] = value
    os.environ["LOOPRAIL_TRACING"] = "1"
    os.environ["LOOPRAIL_TRACING_DIR"] = str(args.trace_dir.resolve())
    log = args.trace_dir / "logs" / "audit-spans.log"
    previous_lines = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
    logger.disable("looprail")
    selected: RecoveryPolicy | None
    if args.advisor == "fake":
        selected = RecoveryPolicy(FakeDecisionAdvisor(args.action))
    elif args.advisor == "live":
        selected = build_recovery_policy(JevConfig(enabled=True), values)
    else:
        selected = None
    with tempfile.TemporaryDirectory(prefix="looprail-jev-demo-") as directory:
        result = asyncio.run(run_demo(Path(directory), selected, case=args.case))
    print(json.dumps({"advisor_mode": args.advisor, **result}, ensure_ascii=False, indent=2))
    if log.exists():
        spans = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()[previous_lines:]]
        decisions = [s for s in spans if s.get("name") == "agent.recovery.decision"]
        if args.case == "recover" and decisions:
            attrs = decisions[-1]["attributes"]
            keys = ("decision_source", "selected_action", "confidence", "reason", "fallback_used", "latency_ms")
            print(json.dumps({"decision": {key: attrs.get(key) for key in keys}}, indent=2))
    print("Trace:", args.trace_dir.resolve())


if __name__ == "__main__":
    main()
