"""No live network: transport, validation, uncertainty and optional configuration."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from looprail.agent.loop.adaptive_recovery import (
    AdvisorUnavailableError,
    RecoveryContext,
    RecoveryPolicy,
    RecoverySuggestion,
    bounded_goal,
)
from looprail.config.looprail import JevConfig, LooprailConfig, load_looprail_config
from looprail.providers.jev_decision import (
    DIRECT_ENDPOINT,
    OPENROUTER_ENDPOINT,
    QUESTION_ID,
    build_recovery_policy,
)


def answer(choice="RECHECK_INPUTS", confidence=0.9):
    return {
        "type": "choice",
        "choice": choice,
        "confidence": confidence,
        "probabilities": {"RECHECK_INPUTS": 0.9, "USE_ALTERNATIVE": 0.08, "STOP": 0.02},
    }


def context():
    return RecoveryContext(
        user_goal="Read the input file",
        failed_tool="read_file",
        failure_kind="missing_input",
        consecutive_failures=2,
        remaining_iterations=3,
        available_tools=("read_file", "list_dir"),
    )


def policy(handler, **env):
    return build_recovery_policy(
        JevConfig(),
        {"LOOPRAIL_JEV_ENABLED": "true", "LOOPRAIL_OPENROUTER_API_KEY": "test-key", **env},
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_official_choice_request_and_authority():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"answers": {QUESTION_ID: answer()}})

    decision = await policy(handler).decide(context())
    assert decision.selected_action == "RECHECK_INPUTS"
    assert decision.decision_source == "jev" and not decision.fallback_used
    assert decision.latency_ms >= 0
    request = requests[0]
    assert str(request.url) == OPENROUTER_ENDPOINT
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"]["failure_kind"] == "missing_input"
    assert set(body["questions"][QUESTION_ID]["criteria"]) == {"RECHECK_INPUTS", "USE_ALTERNATIVE", "STOP"}
    assert "messages" not in body and "test-key" not in json.dumps(body)
    assert "test-key" not in str(decision.attributes())


@pytest.mark.parametrize(
    "change",
    [
        {"choice": "gpt-super-secret-model"},
        {"type": "score"},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": True},
        {"confidence": "0.9"},
        {"reason": "execute malicious code"},
        {"probabilities": {}},
        {"probabilities": {"RECHECK_INPUTS": 0.3, "USE_ALTERNATIVE": 0.7, "STOP": 0}},
        {"probabilities": {"RECHECK_INPUTS": 0.9, "USE_ALTERNATIVE": 0.9, "STOP": 0.9}},
        {"probabilities": {"RECHECK_INPUTS": 1.1, "USE_ALTERNATIVE": -0.1, "STOP": 0}},
    ],
)
def test_invalid_decision_schema(change):
    with pytest.raises(ValidationError):
        RecoverySuggestion.model_validate({**answer(), **change})


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answers": {}},
        {"answers": {QUESTION_ID: None}},
        [],
        {"answers": {QUESTION_ID: {"choice": "STOP"}}},
        {"answers": {QUESTION_ID: {**answer(), "choice": "unknown"}}},
    ],
)
@pytest.mark.asyncio
async def test_invalid_envelope_falls_back(body):
    decision = await policy(lambda _: httpx.Response(200, json=body)).decide(context())
    assert decision.selected_action == "BASELINE" and decision.reason == "invalid_response"


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(200, content=b"not json"), "invalid_response"),
        (httpx.Response(200, content=b""), "invalid_response"),
        (httpx.Response(200, content=b"x" * 64001), "invalid_response"),
        (httpx.Response(401, text="test-key"), "http_error"),
        (httpx.Response(429), "http_error"),
        (httpx.Response(503), "http_error"),
        (httpx.Response(302, headers={"Location": "https://attacker.invalid"}), "http_error"),
    ],
)
@pytest.mark.asyncio
async def test_http_and_json_failures_are_safe(response, code):
    decision = await policy(lambda _: response).decide(context())
    assert decision.reason == code and decision.fallback_used
    assert "test-key" not in str(decision)


@pytest.mark.asyncio
async def test_transport_timeout_and_network_error():
    for error, expected in [(httpx.ReadTimeout("test-key"), "timeout"), (httpx.ConnectError("test-key"), "http_error")]:

        def handler(_):
            raise error

        decision = await policy(handler).decide(context())
        assert decision.reason == expected and "test-key" not in str(decision)


class FakeAdvisor:
    def __init__(self, value=None, error=None, delay=0):
        self.value, self.error, self.delay = value, error, delay

    async def advise(self, _context):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.value


@pytest.mark.asyncio
async def test_policy_revalidates_port_and_bounds_latency():
    invalid = RecoverySuggestion.model_construct(**{**answer(), "choice": "INJECTED"})
    cases = [
        (FakeAdvisor(value=invalid), "invalid_response"),
        (FakeAdvisor(value=answer(confidence=0.2)), "low_confidence"),
        (FakeAdvisor(error=RuntimeError("test-key")), "advisor_error"),
        (FakeAdvisor(error=AdvisorUnavailableError("http_error")), "http_error"),
        (FakeAdvisor(delay=1), "timeout"),
    ]
    for advisor, expected in cases:
        decision = await RecoveryPolicy(advisor, timeout_seconds=0.01).decide(context())
        assert decision.reason == expected and decision.fallback_used


@pytest.mark.asyncio
async def test_cancellation_is_not_recovered():
    with pytest.raises(asyncio.CancelledError):
        await RecoveryPolicy(FakeAdvisor(error=asyncio.CancelledError())).decide(context())


@pytest.mark.asyncio
async def test_invalid_context_cannot_break_runtime():
    decision = await RecoveryPolicy(FakeAdvisor(value=answer())).decide({})
    assert decision.fallback_used and decision.selected_action == "BASELINE"


@pytest.mark.parametrize(
    "env,reason",
    [
        ({"LOOPRAIL_JEV_ENABLED": "true"}, "missing_key"),
        ({"LOOPRAIL_JEV_ENABLED": "maybe"}, "invalid_config"),
        ({"LOOPRAIL_JEV_ENABLED": "true", "LOOPRAIL_JEV_TIMEOUT_SECONDS": "NaN"}, "invalid_config"),
        ({"LOOPRAIL_JEV_ENABLED": "true", "LOOPRAIL_JEV_CONFIDENCE_THRESHOLD": "2"}, "invalid_config"),
        (
            {
                "LOOPRAIL_JEV_ENABLED": "true",
                "LOOPRAIL_JEV_TRANSPORT": "typesafe_direct",
                "LOOPRAIL_JEV_API_KEY": "test-key",
            },
            "missing_model",
        ),
        (
            {
                "LOOPRAIL_JEV_ENABLED": "true",
                "LOOPRAIL_JEV_TRANSPORT": "typesafe_direct",
                "LOOPRAIL_JEV_ENDPOINT": "http://unsafe.invalid",
            },
            "invalid_config",
        ),
    ],
)
@pytest.mark.asyncio
async def test_config_errors_keep_runtime_available(env, reason):
    decision = await build_recovery_policy(JevConfig(), env).decide(context())
    assert decision.reason == reason and decision.selected_action == "BASELINE"


def test_disabled_does_not_construct_transport():
    def handler(_):
        pytest.fail("disabled advisor touched network")

    assert build_recovery_policy(JevConfig(), {}, transport=httpx.MockTransport(handler)) is None
    assert build_recovery_policy(JevConfig(enabled=True), {"LOOPRAIL_JEV_ENABLED": "false"}) is None


def test_looprail_contract_and_file_override():
    selected = build_recovery_policy(
        JevConfig(enabled=True, model="file-model"),
        {
            "LOOPRAIL_JEV_TRANSPORT": "typesafe_direct",
            "LOOPRAIL_JEV_API_KEY": "test-key",
            "LOOPRAIL_JEV_MODEL": "configured-model",
        },
    )
    assert selected.advisor.settings.model == "configured-model"
    assert selected.advisor.settings.endpoint == DIRECT_ENDPOINT
    assert "test-key" not in repr(selected.advisor.settings)


def test_file_config_loads_and_rejects_secrets(tmp_path):
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"runtime": {"jev": {"enabled": True, "confidenceThreshold": 0.8}}}))
    assert load_looprail_config(file).runtime.jev.confidence_threshold == 0.8
    with pytest.raises(ValidationError):
        LooprailConfig(runtime={"jev": {"api_key": "test-key"}})


def test_goal_projection_redacts_and_bounds():
    goal = bounded_goal("token=private-value Authorization: Bearer private-value sk-test-credential " + "x" * 3000)
    assert "private-value" not in goal and "sk-test" not in goal and len(goal) == 2000


@pytest.mark.parametrize(
    "transport,key_name", [("openrouter", "DOVIDEO_OPENROUTER_API_KEY"), ("typesafe_direct", "DOVIDEO_JEV_API_KEY")]
)
@pytest.mark.asyncio
async def test_foreign_credentials_are_not_a_fallback(transport, key_name):
    selected = build_recovery_policy(
        JevConfig(enabled=True, transport=transport, model="test-model"),
        {
            key_name: "foreign-test-key",
            "DOVIDEO_JEV_MODEL": "foreign-model",
            "DOVIDEO_JEV_TRANSPORT": "typesafe_direct",
        },
    )
    assert selected.advisor is None
    assert (await selected.decide(context())).reason == "missing_key"


def test_foreign_settings_do_not_override_file_defaults():
    selected = build_recovery_policy(
        JevConfig(enabled=True),
        {
            "LOOPRAIL_OPENROUTER_API_KEY": "test-key",
            "DOVIDEO_JEV_TRANSPORT": "typesafe_direct",
            "DOVIDEO_JEV_MODEL": "foreign-model",
            "DOVIDEO_JEV_TIMEOUT_SECONDS": "30",
        },
    )
    assert selected.advisor.settings.endpoint == OPENROUTER_ENDPOINT
    assert selected.advisor.settings.model == "typesafe/jev-1.13"
    assert selected.timeout_seconds == 2.0


def test_looprail_timeout_threshold_and_endpoint_override():
    selected = build_recovery_policy(
        JevConfig(enabled=True),
        {
            "LOOPRAIL_JEV_TRANSPORT": "typesafe_direct",
            "LOOPRAIL_JEV_API_KEY": "test-key",
            "LOOPRAIL_JEV_MODEL": "configured-model",
            "LOOPRAIL_JEV_ENDPOINT": "https://example.invalid/decision",
            "LOOPRAIL_JEV_TIMEOUT_SECONDS": "1.5",
            "LOOPRAIL_JEV_CONFIDENCE_THRESHOLD": "0.8",
        },
    )
    assert selected.timeout_seconds == 1.5 and selected.confidence_threshold == 0.8
    assert selected.advisor.settings.endpoint == "https://example.invalid/decision"


@pytest.mark.parametrize(
    "endpoint",
    ["https://example.invalid/#private", "https://user:pass@example.invalid/", "https://example.invalid/?key=private"],
)
def test_unsafe_endpoint_is_rejected(endpoint):
    selected = build_recovery_policy(JevConfig(enabled=True, transport="typesafe_direct", endpoint=endpoint), {})
    assert selected.advisor is None and selected.unavailable_reason == "invalid_config"
