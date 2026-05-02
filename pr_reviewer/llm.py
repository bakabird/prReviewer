from __future__ import annotations

import contextlib
import logging
import os
import random
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

import requests

logger = logging.getLogger(__name__)
_log_context: ContextVar[dict[str, object]] = ContextVar("llm_log_context", default={})


class ProviderConfigError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    def complete_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict | None = None,
    ) -> str:
        ...


@contextlib.contextmanager
def llm_log_context(**context: object) -> Iterator[None]:
    token = _log_context.set({key: value for key, value in context.items() if value is not None})
    try:
        yield
    finally:
        _log_context.reset(token)


@dataclass
class OpenAICompatibleProvider:
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("PR_REVIEWER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ProviderConfigError(
                "Missing API key. Set PR_REVIEWER_API_KEY (or OPENAI_API_KEY)."
            )

        default_base_url = "https://api.openai.com/v1"
        self.base_url = (
            self.base_url
            or os.getenv("PR_REVIEWER_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or default_base_url
        ).rstrip("/")
        self.timeout_seconds = _resolve_positive_int(
            value=self.timeout_seconds,
            env_name="PR_REVIEWER_TIMEOUT_SECONDS",
            default=120,
        )
        self.max_retries = _resolve_positive_int(
            value=self.max_retries,
            env_name="PR_REVIEWER_MAX_RETRIES",
            default=10,
        )
        self.max_tokens = _resolve_optional_positive_int(
            value=self.max_tokens,
            env_name="PR_REVIEWER_MAX_TOKENS",
        )

    def complete_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict | None = None,
    ) -> str:
        url = f"{self.base_url}/chat/completions"

        if json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "review_result",
                    "strict": True,
                    "schema": json_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}

        payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": response_format,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response: requests.Response | None = None
        last_exception: requests.RequestException | None = None
        attempts = max(1, int(self.max_retries))
        base_delay = 0.35
        max_delay = 8.0

        attempt = 1
        while attempt <= attempts:
            logger.info(
                "LLM request attempt %d/%d%s prompt_chars=%d",
                attempt,
                attempts,
                _format_log_context(model=model),
                len(system_prompt) + len(user_prompt),
            )
            t0 = time.monotonic()
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                elapsed = time.monotonic() - t0
                logger.debug("LLM request failed after %.2fs: %s", elapsed, exc)
                last_exception = exc
                if attempt < attempts:
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
                    logger.warning("LLM request error (attempt %d/%d): %s — retrying in %.1fs", attempt, attempts, exc, delay)
                    time.sleep(delay)
                    attempt += 1
                    continue

                raise LLMError(
                    "Failed to reach LLM provider after "
                    f"{attempts} attempts: {exc}. "
                    "Check network connectivity, DNS, VPN/proxy settings, and PR_REVIEWER_BASE_URL."
                ) from exc

            elapsed = time.monotonic() - t0
            logger.info(
                "LLM response %d in %.2fs%s",
                response.status_code,
                elapsed,
                _format_log_context(model=model),
            )

            # Graceful fallback: if json_schema mode gets 400, retry with json_object
            if response.status_code == 400 and json_schema and payload["response_format"]["type"] == "json_schema":
                logger.warning(
                    "Provider returned 400 with json_schema mode; "
                    "falling back to json_object response_format."
                )
                payload["response_format"] = {"type": "json_object"}
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(max_delay, float(retry_after)) + random.uniform(0, 1)
                    except ValueError:
                        delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
                else:
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
                logger.warning(
                    "LLM returned %d (attempt %d/%d) — retrying in %.1fs",
                    response.status_code,
                    attempt,
                    attempts,
                    delay,
                )
                time.sleep(delay)
                attempt += 1
                continue
            break

        if response is None and last_exception is not None:
            raise LLMError(
                "Failed to reach LLM provider. Last error: "
                f"{last_exception}. Check network connectivity, DNS, and proxy settings."
            )

        if response.status_code >= 400:
            detail = response.text.strip()
            with contextlib.suppress(ValueError):
                detail = response.json().get("error", {}).get("message", detail)
            raise LLMError(f"LLM provider error ({response.status_code}): {detail}")

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("LLM provider returned non-JSON HTTP response.") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response format from LLM provider.") from exc

        if isinstance(content, list):
            text_chunks = []
            for chunk in content:
                if isinstance(chunk, dict) and chunk.get("type") == "text":
                    text_chunks.append(chunk.get("text", ""))
            content = "".join(text_chunks).strip()

        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM response did not contain text content.")

        return content


def _resolve_positive_int(*, value: int | None, env_name: str, default: int) -> int:
    if value is not None:
        resolved = value
    else:
        raw = os.getenv(env_name)
        if raw is None or not raw.strip():
            resolved = default
        else:
            try:
                resolved = int(raw)
            except ValueError as exc:
                raise ProviderConfigError(f"{env_name} must be an integer, got {raw!r}.") from exc

    if resolved <= 0:
        raise ProviderConfigError(f"{env_name} must be greater than 0, got {resolved}.")
    return resolved


def _format_log_context(*, model: str) -> str:
    context = _log_context.get()
    parts = [f"model={model}"]

    pass_name = context.get("pass_name")
    if pass_name:
        parts.append(f"pass={pass_name}")

    chunk_index = context.get("chunk_index")
    chunk_count = context.get("chunk_count")
    if chunk_index is not None and chunk_count is not None:
        parts.append(f"chunk={chunk_index}/{chunk_count}")

    return " (" + " ".join(parts) + ")"


def _resolve_optional_positive_int(*, value: int | None, env_name: str) -> int | None:
    if value is not None:
        resolved = value
    else:
        raw = os.getenv(env_name)
        if raw is None or not raw.strip():
            return None
        try:
            resolved = int(raw)
        except ValueError as exc:
            raise ProviderConfigError(f"{env_name} must be an integer, got {raw!r}.") from exc

    if resolved <= 0:
        raise ProviderConfigError(f"{env_name} must be greater than 0, got {resolved}.")
    return resolved
