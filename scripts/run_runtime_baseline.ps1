<#
.SYNOPSIS
    Run Looprail's explicit deterministic runtime baseline.

.DESCRIPTION
    Executes the frozen, checkout-local pytest manifest used to validate the
    supported runtime path. The manifest is intentionally explicit so that a
    normal repository test run cannot silently change the accepted baseline.

.PARAMETER Python
    Optional Python executable. If omitted, the repository virtualenv and the
    sibling baseline virtualenv are tried before falling back to PATH.

.PARAMETER Help
    Show this help text and exit without running tests.
#>
param(
    [string]$Python = "",
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path

if ($Help) {
    Get-Help -Name $MyInvocation.MyCommand.Path -Full
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Python)) {
    $CandidatePythons = @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path (Split-Path $RepoRoot -Parent) ".looprail-baseline-venv\Scripts\python.exe")
    )
    foreach ($CandidatePython in $CandidatePythons) {
        if (Test-Path -LiteralPath $CandidatePython) {
            $Python = $CandidatePython
            break
        }
    }
    if ([string]::IsNullOrWhiteSpace($Python)) {
        $Python = "python"
    }
}

$BaselineTests = @(
    "tests/test_runtime_baseline.py",
    "tests/test_cli_smoke.py",
    "tests/test_cli_repl_spine.py",
    "tests/test_cli_runtime_assembly.py",
    "tests/test_spine_turn.py",
    "tests/test_spine_scheduler.py",
    "tests/test_spine_scheduler_lane.py",
    "tests/test_spine_scheduler_pools.py",
    "tests/test_spine_runner.py",
    "tests/test_agent_loop_context_overflow.py",
    "tests/test_agent_loop_empty_recovery.py",
    "tests/test_agent_loop_max_iter_synthesis.py",
    "tests/test_agent_loop_run_emit.py",
    "tests/test_agent_loop_session_stamps.py",
    "tests/test_agent_loop_stream.py",
    "tests/test_agent_loop_tool_loop_break.py",
    "tests/test_agent_loop_tool_search.py",
    "tests/test_lazy_provider.py",
    "tests/test_litellm_provider_attribution.py",
    "tests/test_litellm_provider_response.py",
    "tests/test_tool_runtime_contract.py",
    "tests/test_durable_recovery_contract.py",
    "tests/test_tool_registry_execution.py",
    "tests/test_tool_registry_timeout.py",
    "tests/test_tool_registry_effects.py",
    "tests/test_effects_journal.py",
    "tests/test_search_tools.py",
    "tests/test_file_search_traversal_guard.py",
    "tests/test_security_untrusted_context.py",
    "tests/test_default_context_engine.py",
    "tests/test_context_invariants.py",
    "tests/test_history_trimmer.py",
    "tests/test_context_budget_contract.py",
    "tests/test_session_manager.py",
    "tests/test_runtime_checkpoint_bug2.py",
    "tests/test_memory_backend_contract.py",
    "tests/test_memory_backend_protocol.py",
    "tests/test_memory_store_lt_additions.py",
    "tests/test_memory_lifecycle_contract.py",
    "tests/test_evidence_chain_contract.py",
    "tests/test_cli_run_contract.py",
    "tests/test_evolution_loop_contract.py",
    "tests/test_tracing_api.py",
    "tests/test_no_otel_tracing.py"
)

Push-Location $RepoRoot
try {
    & $Python -m pytest -q @BaselineTests
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
