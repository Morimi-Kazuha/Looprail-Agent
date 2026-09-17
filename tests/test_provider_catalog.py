"""Catalog-shape tests for the LLM provider registry and backend classes.

These pin the *current* shape so that adding / removing a provider spec or a
concrete backend class trips a test instead of silently drifting.
"""

from __future__ import annotations

import pytest

from looprail.providers.base import LLMProvider
from looprail.providers.common_models import common_models_for
from looprail.providers.registry import PROVIDERS, find_by_name

EXPECTED_PROVIDER_NAMES = {
    "custom",
    "azure_openai",
    "openrouter",
    "aihubmix",
    "siliconflow",
    "volcengine",
    "anthropic",
    "openai",
    "openai_codex",
    "github_copilot",
    "deepseek",
    "gemini",
    "zhipu",
    "dashscope",
    "moonshot",
    "minimax",
    "vllm",
    "ollama",
    "groq",
}


def test_registry_has_exactly_19_providers() -> None:
    assert len(PROVIDERS) == 19
    assert len(EXPECTED_PROVIDER_NAMES) == 19


def test_registry_provider_name_set_is_pinned() -> None:
    assert {spec.name for spec in PROVIDERS} == EXPECTED_PROVIDER_NAMES


def test_provider_names_are_unique() -> None:
    names = [spec.name for spec in PROVIDERS]
    assert len(names) == len(set(names))


_SEEDED_DIRECT_PROVIDERS = [
    "deepseek",
    "openai",
    "anthropic",
    "gemini",
    "zhipu",
    "dashscope",
    "groq",
]


@pytest.mark.parametrize("slug", _SEEDED_DIRECT_PROVIDERS)
def test_seeded_provider_default_model_in_shortlist(slug: str) -> None:
    default = find_by_name(slug).default_model
    assert default, f"{slug} has no default_model"
    assert default in common_models_for(slug)


def _concrete_provider_subclasses() -> set[type]:
    """All non-abstract LLMProvider subclasses defined in looprail.providers."""

    import looprail.providers.azure_openai_provider  # noqa: F401
    import looprail.providers.custom_provider  # noqa: F401
    import looprail.providers.lazy  # noqa: F401
    import looprail.providers.litellm_provider  # noqa: F401
    import looprail.providers.openai_codex_provider  # noqa: F401
    import looprail.providers.per_model_provider  # noqa: F401

    seen: set[type] = set()
    stack = list(LLMProvider.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if getattr(cls, "__abstractmethods__", frozenset()):
            continue
        if cls.__module__.startswith("looprail.providers"):
            seen.add(cls)
    return seen


def test_exactly_six_concrete_backend_classes() -> None:

    from looprail.providers.azure_openai_provider import AzureOpenAIProvider
    from looprail.providers.custom_provider import CustomProvider
    from looprail.providers.lazy import LazyProvider
    from looprail.providers.litellm_provider import LiteLLMProvider
    from looprail.providers.openai_codex_provider import OpenAICodexProvider
    from looprail.providers.per_model_provider import PerModelProvider

    expected = {
        LiteLLMProvider,
        AzureOpenAIProvider,
        OpenAICodexProvider,
        CustomProvider,
        LazyProvider,
        PerModelProvider,
    }
    assert _concrete_provider_subclasses() == expected
    for cls in expected:
        assert issubclass(cls, LLMProvider)
