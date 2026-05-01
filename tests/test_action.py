"""Tests for the GitHub Action entrypoint."""

import json
import textwrap
from types import SimpleNamespace

import pytest

from action import run_review
from action.run_review import (
    ReviewCommand,
    ReviewState,
    _build_gate_state,
    _extract_state_from_comment,
    _fetch_pr_head_sha,
    _fetch_review_diff,
    _filter_diff,
    _format_state_comment,
    _is_authorized_author_association,
    _matches_any,
    _parse_review_command,
    _parse_models_input,
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
    assert _parse_review_command("@reviewer001 full gpt-5.4", "reviewer001") == ReviewCommand(
        scope="full",
        count=0,
        model="gpt-5.4",
    )
    assert _parse_review_command("@reviewer001 last gpt-5.4", "reviewer001") == ReviewCommand(
        scope="last",
        count=1,
        model="gpt-5.4",
    )
    assert _parse_review_command("@reviewer001 last 2 gpt-5.4", "reviewer001") == ReviewCommand(
        scope="last",
        count=2,
        model="gpt-5.4",
    )


@pytest.mark.parametrize(
    "body",
    [
        "hello @reviewer001 full",
        "@reviewer001 full gpt-5.4 extra",
        "@reviewer001 last 0",
        "@reviewer001 last -1",
        "@reviewer001 last 2 gpt-5.4 extra",
        "@reviewer001 last 2 3",
        "@reviewer001\nfull",
        "@other full",
    ],
)
def test_parse_review_command_rejects_non_matching_comments(body):
    assert _parse_review_command(body, "reviewer001") is None


def test_parse_review_command_escapes_configured_bot_name():
    assert _parse_review_command("@reviewer.001 last 3", "reviewer.001") == ReviewCommand(scope="last", count=3)
    assert _parse_review_command("@reviewer.001 full gpt-5.4", "reviewer.001") == ReviewCommand(
        scope="full",
        count=0,
        model="gpt-5.4",
    )
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
        input_pr_number=None,
        expected_head_sha=None,
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
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "unauthorized association" in captured.out


def test_comment_review_request_returns_command_for_pr_comment():
    event = {
        "issue": {"number": 12, "pull_request": {"url": "https://api.github.com/pulls/12"}},
        "comment": {"body": "@reviewer001 last 2 gpt-5.4", "author_association": "MEMBER"},
    }

    result = _resolve_review_request(
        trigger="comment",
        event_name="issue_comment",
        event=event,
        pr_number="12",
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    assert result == ("12", ReviewCommand(scope="last", count=2, model="gpt-5.4"))


def test_comment_trigger_skips_non_issue_comment_event(capsys):
    event = {"pull_request": {"number": 12}}

    result = _resolve_review_request(
        trigger="comment",
        event_name="pull_request",
        event=event,
        pr_number="12",
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "Not an issue_comment event" in captured.out


@pytest.mark.parametrize("trigger", ["auto", "pull_request"])
def test_removed_trigger_modes_are_rejected(trigger):
    with pytest.raises(SystemExit):
        _resolve_review_request(
            trigger=trigger,
            event_name="pull_request",
            event={"action": "opened", "pull_request": {"number": 12}},
            pr_number="12",
            input_pr_number=None,
            expected_head_sha=None,
            reviewer_bot_name="reviewer001",
            allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
        )


def test_bulk_commit_skips_comment_events(capsys):
    result = _resolve_review_request(
        trigger="bulk_commit",
        event_name="issue_comment",
        event={"issue": {"number": 12, "pull_request": {}}, "comment": {"body": "@reviewer001 full"}},
        pr_number="12",
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "Not a pull_request review trigger" in captured.out


def test_bulk_commit_opened_routes_full_review():
    result = _resolve_review_request(
        trigger="bulk_commit",
        event_name="pull_request",
        event={"action": "opened", "pull_request": {"number": 12, "head": {"sha": "head1"}}},
        pr_number="12",
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    assert result == ("12", ReviewCommand(scope="full", count=0, head_sha="head1", update_state=True))


def test_bulk_commit_synchronize_routes_range_review():
    result = _resolve_review_request(
        trigger="bulk_commit",
        event_name="pull_request",
        event={"action": "synchronize", "pull_request": {"number": 12, "head": {"sha": "head2"}}},
        pr_number="12",
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    assert result == ("12", ReviewCommand(scope="bulk_range", count=0, head_sha="head2", update_state=True))


def test_bulk_commit_skips_unsupported_pull_request_actions(capsys):
    result = _resolve_review_request(
        trigger="bulk_commit",
        event_name="pull_request",
        event={"action": "closed", "pull_request": {"number": 12}},
        pr_number="12",
        input_pr_number=None,
        expected_head_sha=None,
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    captured = capsys.readouterr()
    assert result is None
    assert "Unsupported pull_request action" in captured.out


def test_full_pr_trigger_uses_workflow_dispatch_inputs():
    result = _resolve_review_request(
        trigger="full_pr",
        event_name="workflow_dispatch",
        event={},
        pr_number=None,
        input_pr_number="34",
        expected_head_sha="abc123",
        reviewer_bot_name="reviewer001",
        allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
    )

    assert result == (
        "34",
        ReviewCommand(
            scope="full",
            count=0,
            head_sha="abc123",
            expected_head_sha="abc123",
            update_state=True,
            gate_relevant=True,
        ),
    )


def test_full_pr_trigger_requires_pr_number():
    with pytest.raises(SystemExit):
        _resolve_review_request(
            trigger="full_pr",
            event_name="workflow_dispatch",
            event={},
            pr_number=None,
            input_pr_number="",
            expected_head_sha="abc123",
            reviewer_bot_name="reviewer001",
            allowed_author_associations="OWNER,MEMBER,COLLABORATOR",
        )


def test_fetch_pr_head_sha_reads_current_head(monkeypatch):
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "actual-head"}},
    )

    assert _fetch_pr_head_sha("owner/repo", "12", "token") == "actual-head"


def test_gate_state_includes_counts_fallbacks_and_errors():
    state = _build_gate_state(
        {
            "severity_counts": {"high": 1, "medium": 2, "low": 3, "unparseable": 1},
            "fallback_findings": [{"severity": "high", "title": "Fallback"}],
            "posting_errors": ["diagnostic"],
        },
        command=ReviewCommand(
            scope="full",
            count=0,
            expected_head_sha="abc123",
            gate_relevant=True,
        ),
    )

    assert state["status"] == "completed"
    assert state["source"] == "full_pr"
    assert state["expected_head_sha"] == "abc123"
    assert state["severity_counts"]["high"] == 1
    assert state["fallback_findings"] == [{"severity": "high", "title": "Fallback"}]
    assert state["errors"] == ["diagnostic"]


def test_models_input_takes_precedence_over_model():
    assert _parse_models_input("gpt-5.4, gpt-4.1-mini", fallback_model="fallback") == [
        "gpt-5.4",
        "gpt-4.1-mini",
    ]
    assert _parse_models_input("", fallback_model="fallback") == ["fallback"]


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


def test_main_uses_comment_model_override(tmp_path, monkeypatch, capsys):
    captured_run = {}
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({
            "issue": {"number": 12, "pull_request": {"url": "https://api.github.com/pulls/12"}},
            "comment": {"body": "@reviewer001 full gpt-5.4", "author_association": "MEMBER"},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("INPUT_TRIGGER", "comment")
    monkeypatch.setenv("INPUT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("INPUT_MODE", "multi")
    monkeypatch.setenv("INPUT_MAX_LINES", "1200")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "false")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setattr(run_review, "_fetch_review_diff", lambda repo, pr_number, token, command: SAMPLE_DIFF)

    def fake_run(cmd, capture_output, text):
        captured_run["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_review.subprocess, "run", fake_run)

    exit_code = run_review.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "--model" in captured_run["cmd"]
    assert captured_run["cmd"][captured_run["cmd"].index("--model") + 1] == "gpt-5.4"
    assert "model=gpt-5.4" in captured.out


def test_main_uses_default_model_without_comment_override(tmp_path, monkeypatch, capsys):
    captured_run = {}
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"action": "opened", "pull_request": {"number": 12, "head": {"sha": "head"}}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("INPUT_TRIGGER", "bulk_commit")
    monkeypatch.setenv("INPUT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("INPUT_MODE", "multi")
    monkeypatch.setenv("INPUT_MAX_LINES", "1200")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "false")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "12")
    monkeypatch.setattr(run_review, "_fetch_review_diff", lambda repo, pr_number, token, command: SAMPLE_DIFF)

    def fake_run(cmd, capture_output, text):
        captured_run["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_review.subprocess, "run", fake_run)

    exit_code = run_review.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "--model" in captured_run["cmd"]
    assert captured_run["cmd"][captured_run["cmd"].index("--model") + 1] == "gpt-4.1-mini"
    assert "model=gpt-4.1-mini" in captured.out


def test_main_uses_models_input_order(tmp_path, monkeypatch, capsys):
    captured_run = {}
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"action": "opened", "pull_request": {"number": 12, "head": {"sha": "head"}}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("INPUT_TRIGGER", "bulk_commit")
    monkeypatch.setenv("INPUT_MODEL", "fallback")
    monkeypatch.setenv("INPUT_MODELS", "gpt-5.4, gpt-4.1-mini")
    monkeypatch.setenv("INPUT_MODE", "multi")
    monkeypatch.setenv("INPUT_MAX_LINES", "1200")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "false")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "12")
    monkeypatch.setattr(run_review, "_fetch_review_diff", lambda repo, pr_number, token, command: SAMPLE_DIFF)

    def fake_run(cmd, capture_output, text):
        captured_run["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_review.subprocess, "run", fake_run)

    exit_code = run_review.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "--models" in captured_run["cmd"]
    assert captured_run["cmd"][captured_run["cmd"].index("--models") + 1] == "gpt-5.4,gpt-4.1-mini"
    assert "model=gpt-5.4,gpt-4.1-mini" in captured.out


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


def test_state_comment_round_trip():
    body = _format_state_comment(
        {"version": 1, "last_reviewed_sha": "abc123", "updated_at": "2026-04-30T00:00:00+00:00"}
    )

    assert _extract_state_from_comment(body) == {
        "version": 1,
        "last_reviewed_sha": "abc123",
        "updated_at": "2026-04-30T00:00:00+00:00",
    }
    assert _extract_state_from_comment("ordinary comment") is None


def test_bulk_range_uses_stored_last_reviewed_sha(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "head", "repo": {"full_name": "fork/repo"}}},
    )
    monkeypatch.setattr(
        run_review,
        "_read_review_state",
        lambda repo, pr_number, token: ReviewState(
            comment_id=99,
            last_reviewed_sha="base",
            updated_at="2026-04-30T00:00:00+00:00",
        ),
    )

    def fake_fetch_compare_diff(repo, start_sha, head_sha, token):
        calls["compare"] = (repo, start_sha, head_sha, token)
        return "range diff"

    monkeypatch.setattr(run_review, "_fetch_compare_diff", fake_fetch_compare_diff)

    diff = _fetch_review_diff(
        "owner/repo",
        "12",
        "token",
        ReviewCommand(scope="bulk_range", count=0, head_sha="head"),
    )

    assert diff == "range diff"
    assert calls["compare"] == ("fork/repo", "base", "head", "token")


def test_bulk_range_missing_state_falls_back_to_full_pr(monkeypatch):
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "head", "repo": {"full_name": "fork/repo"}}},
    )
    monkeypatch.setattr(run_review, "_read_review_state", lambda repo, pr_number, token: None)
    monkeypatch.setattr(run_review, "_fetch_pr_diff", lambda repo, pr_number, token: "full diff")

    diff = _fetch_review_diff(
        "owner/repo",
        "12",
        "token",
        ReviewCommand(scope="bulk_range", count=0, head_sha="head"),
    )

    assert diff == "full diff"


def test_bulk_range_invalid_state_falls_back_to_full_pr(monkeypatch):
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "head", "repo": {"full_name": "fork/repo"}}},
    )
    monkeypatch.setattr(
        run_review,
        "_read_review_state",
        lambda repo, pr_number, token: ReviewState(
            comment_id=99,
            last_reviewed_sha="missing",
            updated_at="2026-04-30T00:00:00+00:00",
        ),
    )

    def fake_fetch_compare_diff(repo, start_sha, head_sha, token):
        raise SystemExit(1)

    monkeypatch.setattr(run_review, "_fetch_compare_diff", fake_fetch_compare_diff)
    monkeypatch.setattr(run_review, "_fetch_pr_diff", lambda repo, pr_number, token: "full diff")

    diff = _fetch_review_diff(
        "owner/repo",
        "12",
        "token",
        ReviewCommand(scope="bulk_range", count=0, head_sha="head"),
    )

    assert diff == "full diff"


def test_bulk_range_invalid_state_logs_compare_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        run_review,
        "_fetch_pr_metadata",
        lambda repo, pr_number, token: {"head": {"sha": "head", "repo": {"full_name": "fork/repo"}}},
    )
    monkeypatch.setattr(
        run_review,
        "_read_review_state",
        lambda repo, pr_number, token: ReviewState(
            comment_id=99,
            last_reviewed_sha="missing",
            updated_at="2026-04-30T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(run_review, "_fetch_compare_diff", lambda repo, start_sha, head_sha, token: (_ for _ in ()).throw(SystemExit(22)))
    monkeypatch.setattr(run_review, "_fetch_pr_diff", lambda repo, pr_number, token: "full diff")

    diff = _fetch_review_diff(
        "owner/repo",
        "12",
        "token",
        ReviewCommand(scope="bulk_range", count=0, head_sha="head"),
    )

    captured = capsys.readouterr()
    assert diff == "full diff"
    assert "exited with 22" in captured.out


def test_main_advances_state_after_successful_posted_review(tmp_path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"action": "opened", "pull_request": {"number": 12, "head": {"sha": "head-success"}}}),
        encoding="utf-8",
    )
    written = {}

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("INPUT_TRIGGER", "bulk_commit")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "true")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setattr(run_review, "_fetch_review_diff", lambda repo, pr_number, token, command: SAMPLE_DIFF)
    monkeypatch.setattr(
        run_review,
        "_write_review_state",
        lambda repo, pr_number, token, head_sha, **kwargs: written.setdefault("head_sha", head_sha),
    )
    monkeypatch.setattr(
        run_review.subprocess,
        "run",
        lambda cmd, capture_output, text: SimpleNamespace(returncode=0),
    )

    assert run_review.main() == 0
    assert written["head_sha"] == "head-success"


def test_main_fails_and_does_not_advance_state_when_review_fails(tmp_path, monkeypatch, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"action": "opened", "pull_request": {"number": 12, "head": {"sha": "head-fail"}}}),
        encoding="utf-8",
    )
    written = {}

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("INPUT_TRIGGER", "bulk_commit")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "true")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setattr(run_review, "_fetch_review_diff", lambda repo, pr_number, token, command: SAMPLE_DIFF)
    monkeypatch.setattr(
        run_review,
        "_write_review_state",
        lambda repo, pr_number, token, head_sha, **kwargs: written.setdefault("head_sha", head_sha),
    )
    monkeypatch.setattr(
        run_review.subprocess,
        "run",
        lambda cmd, capture_output, text: SimpleNamespace(returncode=1),
    )

    assert run_review.main() == 1
    assert written == {}
    captured = capsys.readouterr()
    assert "Review exited with code 1" in captured.err


def test_main_full_pr_expected_head_mismatch_stops_before_review(tmp_path, monkeypatch, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({}), encoding="utf-8")
    called = {}

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("INPUT_TRIGGER", "full_pr")
    monkeypatch.setenv("INPUT_PR_NUMBER", "12")
    monkeypatch.setenv("INPUT_EXPECTED_HEAD_SHA", "expected")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "true")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setattr(run_review, "_fetch_pr_head_sha", lambda repo, pr_number, token: "actual")
    monkeypatch.setattr(
        run_review,
        "_fetch_review_diff",
        lambda repo, pr_number, token, command: called.setdefault("review", True) or SAMPLE_DIFF,
    )

    assert run_review.main() == 1
    assert called == {}
    captured = capsys.readouterr()
    assert "Expected PR head SHA expected but found actual" in captured.err


def test_main_reports_state_update_failure(tmp_path, monkeypatch, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"action": "opened", "pull_request": {"number": 12, "head": {"sha": "head-fail"}}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("INPUT_TRIGGER", "bulk_commit")
    monkeypatch.setenv("INPUT_POST_COMMENTS", "true")
    monkeypatch.setenv("PR_REVIEWER_API_KEY", "secret")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setattr(run_review, "_fetch_review_diff", lambda repo, pr_number, token, command: SAMPLE_DIFF)
    monkeypatch.setattr(
        run_review,
        "_write_review_state",
        lambda repo, pr_number, token, head_sha, **kwargs: (_ for _ in ()).throw(SystemExit(22)),
    )
    monkeypatch.setattr(
        run_review.subprocess,
        "run",
        lambda cmd, capture_output, text: SimpleNamespace(returncode=0),
    )

    assert run_review.main() == 22
    captured = capsys.readouterr()
    assert "failed to persist last reviewed SHA" in captured.err
