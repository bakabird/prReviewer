from __future__ import annotations

import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from pr_reviewer.llm import LLMError, OpenAICompatibleProvider, ProviderConfigError, llm_log_context

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "verdict": {"type": "string"},
    },
    "required": ["summary", "verdict"],
    "additionalProperties": False,
}


def _make_success_response(content: dict) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}],
    }
    return resp


def _make_error_response(status_code: int, message: str = "error") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = message
    resp.headers = {}
    resp.json.return_value = {"error": {"message": message}}
    return resp


def test_json_schema_mode_included_in_payload() -> None:
    """When json_schema is provided, payload should use response_format type=json_schema."""
    success_resp = _make_success_response({"summary": "ok", "verdict": "looks good"})

    with patch("pr_reviewer.llm.requests.post", return_value=success_resp) as mock_post:
        provider = OpenAICompatibleProvider(api_key="test-key")
        provider.complete_json(
            model="gpt-4",
            system_prompt="You are a reviewer.",
            user_prompt="Review this.",
            json_schema=SIMPLE_SCHEMA,
        )

    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload["response_format"]["type"] == "json_schema"
    assert sent_payload["response_format"]["json_schema"]["name"] == "review_result"
    assert sent_payload["response_format"]["json_schema"]["strict"] is True
    assert sent_payload["response_format"]["json_schema"]["schema"] == SIMPLE_SCHEMA
    assert sent_payload["stream"] is False


def test_json_object_fallback_when_no_schema() -> None:
    """When json_schema is not provided, payload should use response_format type=json_object."""
    success_resp = _make_success_response({"summary": "ok", "verdict": "looks good"})

    with patch("pr_reviewer.llm.requests.post", return_value=success_resp) as mock_post:
        provider = OpenAICompatibleProvider(api_key="test-key")
        provider.complete_json(
            model="gpt-4",
            system_prompt="You are a reviewer.",
            user_prompt="Review this.",
        )

    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload["response_format"] == {"type": "json_object"}
    assert sent_payload["stream"] is False


def test_max_tokens_is_included_when_configured() -> None:
    success_resp = _make_success_response({"summary": "ok", "verdict": "looks good"})

    with patch("pr_reviewer.llm.requests.post", return_value=success_resp) as mock_post:
        provider = OpenAICompatibleProvider(api_key="test-key", max_tokens=321)
        provider.complete_json(
            model="gpt-4",
            system_prompt="You are a reviewer.",
            user_prompt="Review this.",
        )

    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload["max_tokens"] == 321


def test_request_and_successful_response_are_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    success_resp = _make_success_response({"summary": "ok", "verdict": "looks good"})
    system_prompt = "You are a reviewer."
    user_prompt = "Review this."

    with (
        caplog.at_level(logging.INFO, logger="pr_reviewer.llm"),
        patch("pr_reviewer.llm.requests.post", return_value=success_resp),
    ):
        provider = OpenAICompatibleProvider(api_key="test-key")
        with llm_log_context(pass_name="correctness", chunk_index=2, chunk_count=3):
            provider.complete_json(
                model="gpt-4",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

    expected_prompt_chars = len(system_prompt) + len(user_prompt)
    assert any(
        "LLM request attempt 1/10" in record.message
        and "model=gpt-4" in record.message
        and "pass=correctness" in record.message
        and "chunk=2/3" in record.message
        and f"prompt_chars={expected_prompt_chars}" in record.message
        for record in caplog.records
    )
    assert any(
        "LLM response 200 in" in record.message
        and "model=gpt-4" in record.message
        and "pass=correctness" in record.message
        and "chunk=2/3" in record.message
        for record in caplog.records
    )


def test_graceful_fallback_on_400() -> None:
    """If json_schema mode returns 400, retry with json_object and succeed."""
    import copy

    error_resp = _make_error_response(400, "json_schema not supported")
    success_resp = _make_success_response({"summary": "ok", "verdict": "looks good"})

    captured_payloads: list[dict] = []

    def _capture_post(*args, **kwargs):
        captured_payloads.append(copy.deepcopy(kwargs.get("json", {})))
        if len(captured_payloads) == 1:
            return error_resp
        return success_resp

    with patch("pr_reviewer.llm.requests.post", side_effect=_capture_post):
        provider = OpenAICompatibleProvider(api_key="test-key", max_retries=3)
        result = provider.complete_json(
            model="gpt-4",
            system_prompt="You are a reviewer.",
            user_prompt="Review this.",
            json_schema=SIMPLE_SCHEMA,
        )

    assert result == json.dumps({"summary": "ok", "verdict": "looks good"})
    assert len(captured_payloads) == 2

    # First call should have used json_schema
    assert captured_payloads[0]["response_format"]["type"] == "json_schema"
    assert captured_payloads[0]["stream"] is False

    # Second call should have fallen back to json_object
    assert captured_payloads[1]["response_format"] == {"type": "json_object"}
    assert captured_payloads[1]["stream"] is False


def test_graceful_fallback_on_final_allowed_attempt() -> None:
    error_resp = _make_error_response(400, "json_schema not supported")
    success_resp = _make_success_response({"summary": "ok", "verdict": "looks good"})

    with patch("pr_reviewer.llm.requests.post", side_effect=[error_resp, success_resp]) as mock_post:
        provider = OpenAICompatibleProvider(api_key="test-key", max_retries=1)
        result = provider.complete_json(
            model="gpt-4",
            system_prompt="You are a reviewer.",
            user_prompt="Review this.",
            json_schema=SIMPLE_SCHEMA,
        )

    assert result == json.dumps({"summary": "ok", "verdict": "looks good"})
    assert mock_post.call_count == 2


def test_default_request_errors_retry_ten_times() -> None:
    with (
        patch("pr_reviewer.llm.requests.post", side_effect=requests.Timeout("timeout")) as mock_post,
        patch("pr_reviewer.llm.random.uniform", return_value=0),
        patch("pr_reviewer.llm.time.sleep") as mock_sleep,
    ):
        provider = OpenAICompatibleProvider(api_key="test-key")

        with pytest.raises(LLMError, match="after 10 attempts"):
            provider.complete_json(
                model="gpt-4",
                system_prompt="You are a reviewer.",
                user_prompt="Review this.",
            )

    assert mock_post.call_count == 10
    assert mock_sleep.call_count == 9


def test_provider_reads_timeout_and_retry_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("PR_REVIEWER_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("PR_REVIEWER_MAX_RETRIES", "3")
    monkeypatch.setenv("PR_REVIEWER_MAX_TOKENS", "600")

    provider = OpenAICompatibleProvider(api_key="test-key")

    assert provider.timeout_seconds == 45
    assert provider.max_retries == 3
    assert provider.max_tokens == 600


def test_provider_rejects_invalid_timeout_env(monkeypatch) -> None:
    monkeypatch.setenv("PR_REVIEWER_TIMEOUT_SECONDS", "abc")

    with pytest.raises(ProviderConfigError, match="PR_REVIEWER_TIMEOUT_SECONDS must be an integer"):
        OpenAICompatibleProvider(api_key="test-key")


def test_provider_rejects_invalid_max_tokens_env(monkeypatch) -> None:
    monkeypatch.setenv("PR_REVIEWER_MAX_TOKENS", "abc")

    with pytest.raises(ProviderConfigError, match="PR_REVIEWER_MAX_TOKENS must be an integer"):
        OpenAICompatibleProvider(api_key="test-key")


def test_live_openai_compatible_provider_non_streaming_json_response() -> None:
    """Opt-in smoke test for a local OpenAI-compatible provider that defaults to SSE without stream=false."""
    if os.getenv("PR_REVIEWER_LIVE_LLM") != "1":
        pytest.skip("Set PR_REVIEWER_LIVE_LLM=1 to run the live OpenAI-compatible provider smoke test.")

    api_key = os.getenv("PR_REVIEWER_LIVE_API_KEY") or os.getenv("PR_REVIEWER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("Set PR_REVIEWER_LIVE_API_KEY to run the live OpenAI-compatible provider smoke test.")

    provider = OpenAICompatibleProvider(
        api_key=api_key,
        base_url=os.getenv("PR_REVIEWER_LIVE_BASE_URL", "http://localhost:20128/v1"),
        timeout_seconds=60,
        max_retries=1,
    )

    content = provider.complete_json(
        model=os.getenv("PR_REVIEWER_LIVE_MODEL", "cc-haiku"),
        system_prompt="Return only a compact JSON object.",
        user_prompt='Return exactly {"ok": true}.',
    )

    assert json.loads(content) == {"ok": True}
