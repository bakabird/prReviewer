"""Tests for the GitHub Action entrypoint."""

import json
import textwrap

import pytest

from action import run_review
from action.run_review import (
    ReviewCommand,
    _fetch_review_diff,
    _filter_diff,
    _is_authorized_author_association,
    _matches_any,
    _parse_review_command,
    _resolve_review_request,
)

SAMPLE_DIFF = textwrap.dedent("""\
diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 import os
+import sys

 def main():
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,5 +1,5 @@
 {
-  "version": "1.0.0",
+  "version": "1.0.1",
   "lockfileVersion": 3
 }
diff --git a/docs/README.md b/docs/README.md
--- a/docs/README.md
+++ b/docs/README.md
@@ -1 +1,2 @@
 # Docs
+New content
""")


def test_filter_diff_removes_excluded_files():
    result = _filter_diff(SAMPLE_DIFF, ["*.json"])
    assert "package-lock.json" not in result
    assert "src/main.py" in result
    assert "docs/README.md" in result


def test_filter_diff_supports_directory_glob():
    result = _filter_diff(SAMPLE_DIFF, ["docs/**"])
    assert "docs/README.md" not in result
    assert "src/main.py" in result
    assert "package-lock.json" in result


def test_filter_diff_multiple_patterns():
    result = _filter_diff(SAMPLE_DIFF, ["*.json", "docs/**"])
    assert "package-lock.json" not in result
    assert "docs/README.md" not in result
    assert "src/main.py" in result


def test_filter_diff_no_patterns_keeps_all():
    result = _filter_diff(SAMPLE_DIFF, [])
    assert "src/main.py" in result
    assert "package-lock.json" in result
    assert "docs/README.md" in result


def test_matches_any_basic():
    assert _matches_any("package-lock.json", ["*.json"]) is True
    assert _matches_any("src/main.py", ["*.json"]) is False
    assert _matches_any("docs/guide/setup.md", ["docs/**"]) is True


def test_parse_review_command_supports_full_and_last():
    assert _parse_review_command("@reviewer001 full", "reviewer001") == ReviewCommand(scope="full", count=0)
    assert _parse_review_command("@reviewer001 last", "reviewer001") == ReviewCommand(scope="last", count=1)
    assert _parse_review_command("@reviewer001 last 2", "reviewer001") == ReviewCommand(scope="last", count=2)


@pytest.mark.parametrize(
    "body",
    [
        "hello @reviewer001 full",
        "@reviewer001 full please",
        "@reviewer001 last 0",
        "@reviewer001 last -1",
        "@reviewer001\nfull",
        "@other full",
    ],
)
def test_parse_review_command_rejects_non_matching_comments(body):
    assert _parse_review_command(body, "reviewer001") is None


def test_parse_review_command_escapes_configured_bot_name():
    assert _parse_review_command("@reviewer.001 last 3", "reviewer.001") == ReviewCommand(scope="last", count=3)
    assert _parse_review_command("@reviewerx001 last 3", "reviewer.001") is None


def test_author_association_allowlist_is_configurable():
    assert _is_authorized_author_association("member", "OWNER,MEMBER,COLLABORATOR") is True
    assert _is_authorized_author_association("contributor", "OWNER,MEMBER,COLLABORATOR") is False
    assert _is_authorized_author_association("contributor", "CONTRIBUTOR") is True


def test_comment_review_request_skips_regular_issue_comment(capsys):
    event = {
        "issue": {"number": 12},
        "comment": {"body": "@reviewer001 full", "author_association": "OWNER"},
    }

    result = _resolve_review_request(
        trigger="comment",
        event_name="issue_comment",
        event=event,
        pr_number="12",
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "Not a PR comment" in captured.out


def test_comment_review_request_skips_unauthorized_author(capsys):
    event = {
        "issue": {"number": 12, "pull_request": {"url": "https://api.github.com/pulls/12"}},
        "comment": {"body": "@reviewer001 full", "author_association": "CONTRIBUTOR"},
    }

    result = _resolve_review_request(
        trigger="comment",
        event_name="issue_comment",
        event=event,
        pr_number="12",
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "unauthorized association" in captured.out


def test_comment_review_request_returns_command_for_pr_comment():
    event = {
        "issue": {"number": 12, "pull_request": {"url": "https://api.github.com/pulls/12"}},
        "comment": {"body": "@reviewer001 last 2", "author_association": "MEMBER"},
    }

    result = _resolve_review_request(
        trigger="comment",
        event_name="issue_comment",
        event=event,
        pr_number="12",
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    assert result == ("12", ReviewCommand(scope="last", count=2))


def test_comment_trigger_skips_non_issue_comment_event(capsys):
    event = {"pull_request": {"number": 12}}

    result = _resolve_review_request(
        trigger="comment",
        event_name="pull_request",
        event=event,
        pr_number="12",
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "Not an issue_comment event" in captured.out


def test_main_skips_non_command_comment_before_requiring_secrets(tmp_path, monkeypatch, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({
            "issue": {"number": 12, "pull_request": {"url": "https://api.github.com/pulls/12"}},
            "comment": {"body": "ordinary comment", "author_association": "MEMBER"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("INPUT_TRIGGER", "comment")
    monkeypatch.delenv("PR_REVIEWER_API_KEY", raising=False)
    monkeypatch.delenv("REPO", raising=False)

    exit_code = run_review.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Ignoring non-review command" in captured.out


def test_fetch_review_diff_for_last_commits_uses_first_target_parent(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        run_review,
        "_fetch_pr_commits",
        lambda repo, pr_number, token: [
            {"sha": "c1", "parents": [{"sha": "base"}]},
            {"sha": "c2", "parents": [{"sha": "c1"}]},
            {"sha": "c3", "parents": [{"sha": "c2"}]},
        ],
    )
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "head", "repo": {"full_name": "owner/repo"}}},
    )

    def fake_fetch_compare_diff(repo, start_sha, head_sha, token):
        calls["compare"] = (repo, start_sha, head_sha, token)
        return "diff --git a/app.py b/app.py\n"

    monkeypatch.setattr(run_review, "_fetch_compare_diff", fake_fetch_compare_diff)

    diff = _fetch_review_diff("owner/repo", "12", "token", ReviewCommand(scope="last", count=2))

    assert diff.startswith("diff --git")
    assert calls["compare"] == ("owner/repo", "c1", "head", "token")


def test_fetch_review_diff_for_fork_pr_compares_against_head_repo(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        run_review,
        "_fetch_pr_commits",
        lambda repo, pr_number, token: [
            {"sha": "c1", "parents": [{"sha": "base"}]},
            {"sha": "c2", "parents": [{"sha": "c1"}]},
        ],
    )
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "fork-head", "repo": {"full_name": "fork/repo"}}},
    )

    def fake_fetch_compare_diff(repo, start_sha, head_sha, token):
        calls["compare"] = (repo, start_sha, head_sha, token)
        return "diff --git a/app.py b/app.py\n"

    monkeypatch.setattr(run_review, "_fetch_compare_diff", fake_fetch_compare_diff)

    diff = _fetch_review_diff("owner/repo", "12", "token", ReviewCommand(scope="last", count=1))

    assert diff.startswith("diff --git")
    assert calls["compare"] == ("fork/repo", "c1", "fork-head", "token")


def test_fetch_review_diff_for_fork_pr_base_fallback_compares_against_base_repo(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        run_review,
        "_fetch_pr_commits",
        lambda repo, pr_number, token: [
            {"sha": "root", "parents": []},
        ],
    )
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {
            "base": {"sha": "base-only"},
            "head": {"sha": "fork-head", "repo": {"full_name": "fork/repo"}},
        },
    )

    def fake_fetch_compare_diff(repo, start_sha, head_sha, token):
        calls["compare"] = (repo, start_sha, head_sha, token)
        return "diff --git a/app.py b/app.py\n"

    monkeypatch.setattr(run_review, "_fetch_compare_diff", fake_fetch_compare_diff)

    diff = _fetch_review_diff("owner/repo", "12", "token", ReviewCommand(scope="last", count=1))

    assert diff.startswith("diff --git")
    assert calls["compare"] == ("owner/repo", "base-only", "fork-head", "token")


def test_fetch_review_diff_for_full_uses_pr_diff(monkeypatch):
    monkeypatch.setattr(run_review, "_fetch_pr_diff", lambda repo, pr_number, token: "full diff")

    diff = _fetch_review_diff("owner/repo", "12", "token", ReviewCommand(scope="full", count=0))

    assert diff == "full diff"
