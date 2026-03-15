from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

import requests


class ProviderConfigError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    def complete_json(self, *, model: str, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass
class OpenAICompatibleProvider:
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: int = 60
    max_retries: int = 3

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

    def complete_json(self, *, model: str, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response: requests.Response | None = None
        last_exception: requests.RequestException | None = None
        attempts = max(1, int(self.max_retries))
        for attempt in range(1, attempts + 1):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_exception = exc
                if attempt < attempts:
                    time.sleep(min(4.0, 0.35 * (2 ** (attempt - 1))))
                    continue

                raise LLMError(
                    "Failed to reach LLM provider after "
                    f"{attempts} attempts: {exc}. "
                    "Check network connectivity, DNS, VPN/proxy settings, and PR_REVIEWER_BASE_URL."
                ) from exc

            if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
                time.sleep(min(4.0, 0.35 * (2 ** (attempt - 1))))
                continue
            break

        if response is None and last_exception is not None:
            raise LLMError(
                "Failed to reach LLM provider. Last error: "
                f"{last_exception}. Check network connectivity, DNS, and proxy settings."
            )

        if response.status_code >= 400:
            detail = response.text.strip()
            try:
                detail = response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
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
