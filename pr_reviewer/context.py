from __future__ import annotations

import base64
import logging
import os

import requests

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 30
_REQUEST_TIMEOUT = (_CONNECT_TIMEOUT, _READ_TIMEOUT)
_MAX_FILE_LINES = 300


def fetch_github_file_context(
    *,
    repo: str,
    file_paths: list[str],
    ref: str,
    token: str | None,
    base_url: str = "https://api.github.com",
) -> dict[str, str]:
    """Fetch full file content from GitHub API. Returns {path: content}. Silently skips failures."""
    token = token or os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    context: dict[str, str] = {}
    session = requests.Session()
    session.headers.update(headers)

    for path in file_paths:
        url = f"{base_url.rstrip('/')}/repos/{repo}/contents/{path}"
        try:
            resp = session.get(url, params={"ref": ref}, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.debug("Failed to fetch context for %s: %s", path, exc)
            continue

        if resp.status_code == 404:
            # File may be deleted in this PR — skip silently
            continue

        if resp.status_code >= 400:
            logger.debug("GitHub returned %d for %s", resp.status_code, path)
            continue

        try:
            data = resp.json()
            content_b64 = data.get("content", "")
            if not content_b64:
                continue
            content = base64.b64decode(content_b64.replace("\n", "")).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to decode context for %s: %s", path, exc)
            continue

        lines = content.splitlines()
        if len(lines) > _MAX_FILE_LINES:
            content = "\n".join(lines[:_MAX_FILE_LINES]) + f"\n# ... truncated at {_MAX_FILE_LINES} lines ..."

        context[path] = content

    logger.debug("Fetched file context for %d/%d files", len(context), len(file_paths))
    return context
