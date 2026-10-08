"""Run the deterministic coding scenario used by R2 acceptance tests.

Run from the repository root: python -m scripts.demo_context_compression_r2
Uses fake providers/tools; the real AgentLoop, Session and Curator execute.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from tests.test_context_compression_r2 import run_coding_scenario


async def run(output: Path, state: Path) -> None:
    scenario = await run_coding_scenario(state)
    metadata = scenario["metadata"]
    payload = {
        "provider": "deterministic fake; actual AgentLoop and fake tool execution",
        "executed_tools": scenario["executions"],
        "original_history": scenario["original_history"],
        "tokens": metadata["context_estimate"],
        "stages": metadata["compression_stages"],
        "summary_calls": scenario["summary_calls"],
        "provider_context": scenario["provider_context"],
        "final_validation": metadata["provider_context_validation"],
        "continuation": scenario["continuation"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "before": payload["tokens"]["before"],
                "after": payload["tokens"]["after"],
                "stages": [event["stage"] for event in payload["stages"]],
                "summary_calls": payload["summary_calls"],
                "continuation": payload["continuation"],
                "output": str(output.resolve()),
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/context-compression-r2-demo.json"))
    parser.add_argument("--state", type=Path, required=True, help="Isolated disposable scenario state directory")
    args = parser.parse_args()
    asyncio.run(run(args.output, args.state))


if __name__ == "__main__":
    main()
