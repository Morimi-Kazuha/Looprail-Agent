"""Validate corpus, policy scoring and the offline-by-default runner."""

import json

import httpx
import pytest
from pydantic import ValidationError

from looprail.agent.loop.adaptive_recovery import RecoveryPolicy, RecoverySuggestion
from scripts.eval_jev_recovery import DEFAULT_CASES, FakeRecoveryAdvisor, evaluate, load_cases, main


def test_case_corpus_and_projection():
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) == 15
    assert {action for case in cases for action in case.acceptable_actions} == {
        "RECHECK_INPUTS",
        "USE_ALTERNATIVE",
        "STOP",
    }
    assert any(len(case.acceptable_actions) > 1 for case in cases)
    for case in cases:
        state = case.context().model_dump()
        assert "acceptable_actions" not in state and "notes" not in state
        assert state["remaining_iterations"] == case.remaining_budget


@pytest.mark.parametrize(
    "change",
    [
        {"id": ""},
        {"failure_type": "unknown"},
        {"failure_count": 1},
        {"remaining_budget": 0},
        {"remaining_budget": True},
        {"acceptable_actions": []},
        {"acceptable_actions": ["EXECUTE"]},
        {"acceptable_actions": ["STOP", "STOP"]},
        {"available_tools": [""]},
        {"available_tools": ["read_file", "read_file"]},
        {"unexpected": "field"},
    ],
)
def test_invalid_case_is_rejected(tmp_path, change):
    row = json.loads(DEFAULT_CASES.read_text())[0]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{**row, **change}]))
    with pytest.raises(ValidationError):
        load_cases(path)


@pytest.mark.parametrize("payload", [[], [{"duplicate": True}]])
def test_empty_or_malformed_corpus_rejected(tmp_path, payload):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_cases(path)


def test_duplicate_ids_rejected(tmp_path):
    row = json.loads(DEFAULT_CASES.read_text())[0]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match="unique ids"):
        load_cases(path)


@pytest.mark.asyncio
async def test_fake_scoring_and_threshold():
    cases = load_cases(DEFAULT_CASES)
    policy = RecoveryPolicy(FakeRecoveryAdvisor())
    report = await evaluate(cases, policy, advisor_mode="fake")
    # The simple fake stops on any permission failure; one labeled local alternative disagrees.
    assert report["cases"] == 15 and report["accepted_decisions"] == 14
    assert report["fallbacks"] == report["invalid_responses"] == 0
    assert sum(report["decision_distribution"].values()) == 15
    assert report["mean_latency_ms"] >= 0
    strict = await evaluate(
        cases, RecoveryPolicy(FakeRecoveryAdvisor(), confidence_threshold=0.85), advisor_mode="fake"
    )
    assert strict["accepted_decisions"] == 0 and strict["fallbacks"] == 15
    assert strict["invalid_responses"] == 0
    assert all(row["suggested_action"] is not None and row["reason"] == "low_confidence" for row in strict["rows"])


@pytest.mark.asyncio
async def test_invalid_response_count_and_fallback_scoring():
    class InvalidAdvisor:
        async def advise(self, context):
            return RecoverySuggestion.model_construct(type="choice", choice="EXECUTE")

    report = await evaluate(load_cases(DEFAULT_CASES)[:1], RecoveryPolicy(InvalidAdvisor()), advisor_mode="test")
    assert report["invalid_responses"] == report["fallbacks"] == 1
    assert report["accepted_decisions"] == 0
    missing = await evaluate(load_cases(DEFAULT_CASES)[:1], RecoveryPolicy(None), advisor_mode="test")
    assert missing["invalid_responses"] == 0 and missing["fallbacks"] == 1


def test_default_runner_cannot_call_network(monkeypatch, capsys):
    async def forbidden(*args, **kwargs):
        pytest.fail("fake evaluation touched network")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    monkeypatch.setenv("LOOPRAIL_JEV_ENABLED", "true")
    monkeypatch.setenv("LOOPRAIL_OPENROUTER_API_KEY", "test-key")
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "Advisor: fake" in output and "Cases: 15" in output
    assert "accepted decisions: 14 / 15" in output


def test_json_runner_and_live_missing_key(monkeypatch, capsys):
    assert main(["--json", "--threshold", "0.85"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["threshold"] == 0.85 and report["fallbacks"] == 15
    monkeypatch.delenv("LOOPRAIL_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LOOPRAIL_JEV_API_KEY", raising=False)
    monkeypatch.setenv("LOOPRAIL_JEV_TRANSPORT", "openrouter")
    with pytest.raises(SystemExit) as error:
        main(["--advisor", "live"])
    assert error.value.code == 2


@pytest.mark.parametrize("threshold", ["NaN", "1.1", "-0.1"])
def test_runner_rejects_invalid_threshold(threshold):
    with pytest.raises(SystemExit) as error:
        main(["--threshold", threshold])
    assert error.value.code == 2
