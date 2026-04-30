#!/usr/bin/env python3
"""GitHub Action entrypoint for pr-reviewer."""

from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime


STATE_MARKER_NAME = "pr-reviewer-state"
STATE_MARKER_PREFIX = f"<!-- {STATE_MARKER_NAME}"
STATE_MARKER_SUFFIX = "-->"


@dataclass(frozen=True)
class ReviewCommand:
    scope: str
    count: int = 1
    model: str | None = None
    start_sha: str | None = None
    head_sha: str | None = None
    update_state: bool = False


def main() -> int:
    event = _load_github_event(os.environ.get("GITHUB_EVENT_PATH"))
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    trigger = os.environ.get("INPUT_TRIGGER", "bulk_commit").strip().lower()
    reviewer_bot_name = os.environ.get("INPUT_REVIEWER_BOT_NAME", "reviewer001")
    allowed_author_associations = os.environ.get(
        "INPUT_ALLOWED_AUTHOR_ASSOCIATIONS",
        "OWNER,MEMBER,COLLABORATOR",
    )

    pr_number = _resolve_pull_request_number(event=event)
    repo = os.environ.get("REPO")
    token = os.environ.get("GITHUB_TOKEN")
    default_model = os.environ.get("INPUT_MODEL", "gpt-4.1-mini")
    models_input = os.environ.get("INPUT_MODELS", "")
    mode = os.environ.get("INPUT_MODE", "multi")
    max_lines = os.environ.get("INPUT_MAX_LINES", "1200")
    exclude = os.environ.get("INPUT_EXCLUDE", "")
    post_comments = os.environ.get("INPUT_POST_COMMENTS", "true").lower() == "true"
    fail_on_error = os.environ.get("INPUT_FAIL_ON_ERROR", "false").lower() == "true"

    review_request = _resolve_review_request(
        trigger=trigger,
        event_name=event_name,
        event=event,
        pr_number=pr_number,
        reviewer_bot_name=reviewer_bot_name,
        allowed_author_associations=allowed_author_associations,
    )
    if review_request is None:
        return 0

    pr_number, command = review_request

    if not repo:
        print("::error::REPO is not set.", file=sys.stderr)
        return 1

    if not os.environ.get("PR_REVIEWER_API_KEY"):
        print("::error::Missing api_key input. Set it to your OpenAI (or compatible) API key.", file=sys.stderr)
        return 1

    print(f"Fetching diff for PR #{pr_number} in {repo}...")
    diff_text = _fetch_review_diff(repo, pr_number, token, command)
    if not diff_text or not diff_text.strip():
        print("PR diff is empty — nothing to review.")
        return 0

    # Apply exclude patterns
    if exclude:
        patterns = [p.strip() for p in exclude.split(",") if p.strip()]
        diff_text = _filter_diff(diff_text, patterns)
        if not diff_text.strip():
            print("All files matched exclude patterns — nothing to review.")
            return 0

    # Write diff to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(diff_text)
        diff_path = f.name

    effective_model = command.model or default_model
    effective_models = _parse_models_input(models_input, fallback_model=effective_model)

    # Build review command
    cmd = [
        sys.executable,
        "-m",
        "pr_reviewer",
        "review",
        diff_path,
        "--mode",
        mode,
        "--max-lines",
        max_lines,
        "--format",
        "text",
        "--color",
        "never",
    ]
    if len(effective_models) == 1:
        cmd.extend(["--model", effective_models[0]])
    else:
        cmd.extend(["--models", ",".join(effective_models)])

    if post_comments:
        cmd.extend([
            "--post",
            "github",
            "--repo",
            repo,
            "--pr",
            str(pr_number),
        ])

    model_label = ",".join(effective_models)
    print(f"Running review (mode={mode}, model={model_label})...")
    result = subprocess.run(cmd, capture_output=False, text=True)

    # Clean up
    with contextlib.suppress(OSError):
        os.unlink(diff_path)

    if result.returncode != 0:
        print(f"::warning::Review exited with code {result.returncode}")
        return result.returncode if fail_on_error else 0

    if post_comments and command.update_state and command.head_sha:
        try:
            _write_review_state(repo, pr_number, token, command.head_sha)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            print("::error::Review completed but failed to persist last reviewed SHA.", file=sys.stderr)
            return code or 1

    return 0


def _load_github_event(event_path: str | None) -> dict:
    if not event_path:
        return {}

    try:
        with open(event_path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::warning::Could not read GitHub event payload: {exc}")
        return {}

    return payload if isinstance(payload, dict) else {}


def _resolve_pull_request_number(*, event: dict) -> str | None:
    env_pr_number = os.environ.get("PR_NUMBER")
    if env_pr_number:
        return env_pr_number

    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict) and pull_request.get("number"):
        return str(pull_request["number"])

    issue = event.get("issue")
    if isinstance(issue, dict) and issue.get("pull_request") and issue.get("number"):
        return str(issue["number"])

    return None


def _resolve_review_request(
    *,
    trigger: str,
    event_name: str,
    event: dict,
    pr_number: str | None,
    reviewer_bot_name: str,
    allowed_author_associations: str,
) -> tuple[str, ReviewCommand] | None:
    if trigger not in {"bulk_commit", "comment"}:
        print(f"::error::Unsupported trigger input: {trigger}", file=sys.stderr)
        raise SystemExit(1)

    if event_name == "issue_comment":
        if trigger == "bulk_commit":
            print("Not a pull_request review trigger. Skipping.")
            return None
        return _resolve_comment_review_request(
            event=event,
            pr_number=pr_number,
            reviewer_bot_name=reviewer_bot_name,
            allowed_author_associations=allowed_author_associations,
        )

    if trigger == "comment":
        print("Not an issue_comment event. Skipping.")
        return None

    if event_name != "pull_request":
        print("Not a pull_request event. Skipping.")
        return None

    action = str(event.get("action") or "")
    if action not in {"opened", "synchronize"}:
        print(f"Unsupported pull_request action for bulk_commit: {action or 'UNKNOWN'}. Skipping.")
        return None

    if not pr_number:
        print("Not a pull request. Skipping.")
        return None

    pull_request = event.get("pull_request") if isinstance(event.get("pull_request"), dict) else {}
    head_sha = str(pull_request.get("head", {}).get("sha") or "")
    if action == "opened":
        return pr_number, ReviewCommand(scope="full", count=0, head_sha=head_sha, update_state=True)

    return pr_number, ReviewCommand(scope="bulk_range", count=0, head_sha=head_sha, update_state=True)


def _resolve_comment_review_request(
    *,
    event: dict,
    pr_number: str | None,
    reviewer_bot_name: str,
    allowed_author_associations: str,
) -> tuple[str, ReviewCommand] | None:
    issue = event.get("issue")
    if not isinstance(issue, dict) or not issue.get("pull_request"):
        print("Not a PR comment. Skipping.")
        return None

    if not pr_number:
        print("Could not resolve PR number from issue_comment event. Skipping.")
        return None

    comment = event.get("comment")
    if not isinstance(comment, dict):
        print("No issue comment payload found. Skipping.")
        return None

    author_association = str(comment.get("author_association") or "").upper()
    if not _is_authorized_author_association(author_association, allowed_author_associations):
        print(f"Ignoring comment from unauthorized association: {author_association or 'UNKNOWN'}")
        return None

    command = _parse_review_command(str(comment.get("body") or ""), reviewer_bot_name)
    if command is None:
        print("Ignoring non-review command.")
        return None

    return pr_number, command


def _parse_review_command(body: str, reviewer_bot_name: str) -> ReviewCommand | None:
    command_text = body.replace("\r", "").strip()
    escaped_bot_name = re.escape(reviewer_bot_name)

    match = re.fullmatch(
        rf"@{escaped_bot_name}[ \t]+(full|last)(?:[ \t]+(\S+))?(?:[ \t]+(\S+))?",
        command_text,
    )
    if not match:
        return None

    scope, arg1, arg2 = match.groups()

    if scope == "full":
        if arg2 is not None:
            return None
        return ReviewCommand(scope="full", count=0, model=arg1)

    if arg1 is None:
        return ReviewCommand(scope="last", count=1)

    if _is_positive_integer_token(arg1):
        if arg2 is not None and _is_integer_like_token(arg2):
            return None
        return ReviewCommand(scope="last", count=int(arg1), model=arg2)

    if _is_integer_like_token(arg1):
        return None

    if arg2 is not None:
        return None
    return ReviewCommand(scope="last", count=1, model=arg1)


def _is_positive_integer_token(token: str) -> bool:
    return bool(re.fullmatch(r"[1-9][0-9]*", token))


def _is_integer_like_token(token: str) -> bool:
    return bool(re.fullmatch(r"[+-]?[0-9]+", token))


def _is_authorized_author_association(author_association: str, allowed_author_associations: str) -> bool:
    allowed = {
        association.strip().upper()
        for association in allowed_author_associations.split(",")
        if association.strip()
    }
    return author_association.upper() in allowed


def _fetch_review_diff(repo: str, pr_number: str, token: str | None, command: ReviewCommand) -> str:
    if command.scope == "full":
        return _fetch_pr_diff(repo, pr_number, token)

    if command.scope == "bulk_range":
        pr_payload = _fetch_pr_metadata(repo, pr_number, token)
        head_sha = command.head_sha or str(pr_payload.get("head", {}).get("sha") or "")
        compare_repo = _head_repo_full_name(pr_payload) or repo
        state = _read_review_state(repo, pr_number, token)
        start_sha = state.last_reviewed_sha if state else None
        if start_sha and head_sha:
            try:
                print(f"Reviewing new commits: {start_sha}...{head_sha}")
                return _fetch_compare_diff(compare_repo, start_sha, head_sha, token)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                print(
                    "Stored review state could not be compared "
                    f"(GitHub compare exited with {code}); falling back to full PR diff."
                )
        return _fetch_pr_diff(repo, pr_number, token)

    commits = _fetch_pr_commits(repo, pr_number, token)
    if not commits:
        return ""

    count = min(command.count, len(commits))
    first_target_commit = commits[len(commits) - count]
    start_sha = _first_parent_sha(first_target_commit)
    start_sha_from_pr_commit_parent = bool(start_sha)
    pr_payload = _fetch_pr_metadata(repo, pr_number, token)
    if not start_sha:
        start_sha = str(pr_payload.get("base", {}).get("sha") or "")

    head_sha = str(pr_payload.get("head", {}).get("sha") or _commit_sha(commits[-1]) or "")
    compare_repo = _head_repo_full_name(pr_payload) if start_sha_from_pr_commit_parent else None
    compare_repo = compare_repo or repo
    if not start_sha or not head_sha:
        print("::error::Could not resolve commit range for comment-triggered review.", file=sys.stderr)
        raise SystemExit(1)

    print(f"Reviewing latest {count} commit(s): {start_sha}...{head_sha}")
    return _fetch_compare_diff(compare_repo, start_sha, head_sha, token)


def _fetch_pr_diff(repo: str, pr_number: str, token: str | None) -> str:
    """Fetch the PR diff using curl (avoids adding requests as a dependency for the action)."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    return _fetch_github_diff(url, token)


def _fetch_compare_diff(repo: str, start_sha: str, head_sha: str, token: str | None) -> str:
    url = f"https://api.github.com/repos/{repo}/compare/{start_sha}...{head_sha}"
    return _fetch_github_diff(url, token)


def _fetch_pr_metadata(repo: str, pr_number: str, token: str | None) -> dict:
    payload = _fetch_github_json(f"https://api.github.com/repos/{repo}/pulls/{pr_number}", token)
    return payload if isinstance(payload, dict) else {}


def _fetch_pr_commits(repo: str, pr_number: str, token: str | None) -> list[dict]:
    commits: list[dict] = []
    page = 1

    while True:
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/commits?per_page=100&page={page}"
        payload = _fetch_github_json(url, token)
        if not isinstance(payload, list):
            print("::error::Unexpected GitHub commits API response.", file=sys.stderr)
            raise SystemExit(1)

        commits.extend(commit for commit in payload if isinstance(commit, dict))
        if len(payload) < 100:
            break
        page += 1

    return commits


@dataclass(frozen=True)
class ReviewState:
    comment_id: int | None
    last_reviewed_sha: str
    updated_at: str
    version: int = 1


def _parse_models_input(models_input: str, *, fallback_model: str) -> list[str]:
    models = [model.strip() for model in models_input.split(",") if model.strip()]
    return models or [fallback_model]


def _read_review_state(repo: str, pr_number: str, token: str | None) -> ReviewState | None:
    comment = _find_state_comment(repo, pr_number, token)
    if comment is None:
        return None
    state = _extract_state_from_comment(str(comment.get("body") or ""))
    if state is None:
        return None
    return ReviewState(
        comment_id=int(comment["id"]) if comment.get("id") else None,
        last_reviewed_sha=state["last_reviewed_sha"],
        updated_at=state["updated_at"],
        version=int(state.get("version", 1)),
    )


def _write_review_state(repo: str, pr_number: str, token: str | None, head_sha: str) -> None:
    payload = {
        "version": 1,
        "last_reviewed_sha": head_sha,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    body = _format_state_comment(payload)
    comment = _find_state_comment(repo, pr_number, token)
    if comment and comment.get("id"):
        _patch_github_json(
            f"https://api.github.com/repos/{repo}/issues/comments/{comment['id']}",
            token,
            {"body": body},
        )
        return
    _post_github_json(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
        token,
        {"body": body},
    )


def _find_state_comment(repo: str, pr_number: str, token: str | None) -> dict | None:
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100&page={page}"
        payload = _fetch_github_json(url, token)
        if not isinstance(payload, list):
            return None
        for comment in payload:
            if isinstance(comment, dict) and STATE_MARKER_PREFIX in str(comment.get("body") or ""):
                return comment
        if len(payload) < 100:
            return None
        page += 1


def _extract_state_from_comment(body: str) -> dict | None:
    start = body.find(STATE_MARKER_PREFIX)
    if start < 0:
        return None
    json_start = start + len(STATE_MARKER_PREFIX)
    end = body.find(STATE_MARKER_SUFFIX, json_start)
    if end < 0:
        return None
    try:
        payload = json.loads(body[json_start:end].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != 1 or not payload.get("last_reviewed_sha"):
        return None
    return payload


def _format_state_comment(payload: dict) -> str:
    state_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return f"{STATE_MARKER_PREFIX}\n{state_json}\n{STATE_MARKER_SUFFIX}"


def _fetch_github_json(url: str, token: str | None):
    response = _curl_github(url, token, accept="application/vnd.github+json")
    try:
        return json.loads(response)
    except json.JSONDecodeError as exc:
        print(f"::error::Failed to parse GitHub API response: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _fetch_github_diff(url: str, token: str | None) -> str:
    return _curl_github(url, token, accept="application/vnd.github.v3.diff")


def _curl_github(url: str, token: str | None, *, accept: str) -> str:
    cmd = [
        "curl",
        "-sS",
        "-f",
        "-H",
        f"Accept: {accept}",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
    ]
    if token:
        cmd.extend(["-H", f"Authorization: Bearer {token}"])
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"::error::GitHub API request failed: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return proc.stdout


def _post_github_json(url: str, token: str | None, payload: dict) -> None:
    _send_github_json("POST", url, token, payload)


def _patch_github_json(url: str, token: str | None, payload: dict) -> None:
    _send_github_json("PATCH", url, token, payload)


def _send_github_json(method: str, url: str, token: str | None, payload: dict) -> None:
    cmd = [
        "curl",
        "-sS",
        "-f",
        "-X",
        method,
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        "Content-Type: application/json",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        "-d",
        json.dumps(payload),
    ]
    if token:
        cmd.extend(["-H", f"Authorization: Bearer {token}"])
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"::error::GitHub API request failed: {proc.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)


def _first_parent_sha(commit: dict) -> str | None:
    parents = commit.get("parents")
    if not isinstance(parents, list) or not parents:
        return None

    first_parent = parents[0]
    if not isinstance(first_parent, dict):
        return None

    sha = first_parent.get("sha")
    return str(sha) if sha else None


def _commit_sha(commit: dict) -> str | None:
    sha = commit.get("sha")
    return str(sha) if sha else None


def _head_repo_full_name(pr_payload: dict) -> str | None:
    head = pr_payload.get("head")
    if not isinstance(head, dict):
        return None

    head_repo = head.get("repo")
    if not isinstance(head_repo, dict):
        return None

    full_name = head_repo.get("full_name")
    return str(full_name) if full_name else None


def _filter_diff(diff_text: str, exclude_patterns: list[str]) -> str:
    """Remove diff sections for files matching exclude patterns."""
    sections: list[str] = []
    current_section: list[str] = []
    current_file: str | None = None

    diff_header_re = re.compile(r"^diff --git a/(.+?) b/(.+)$")

    for line in diff_text.splitlines(keepends=True):
        header_match = diff_header_re.match(line)
        if header_match:
            # Flush previous section
            if current_section and current_file is not None and not _matches_any(current_file, exclude_patterns):
                sections.extend(current_section)
            current_section = [line]
            # Use the new path (b/ side)
            current_file = header_match.group(2)
        else:
            current_section.append(line)

    # Flush last section
    if current_section and current_file is not None and not _matches_any(current_file, exclude_patterns):
        sections.extend(current_section)

    return "".join(sections)


def _matches_any(file_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(file_path, pattern) for pattern in patterns)


if __name__ == "__main__":
    raise SystemExit(main())
