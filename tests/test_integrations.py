import pytest

from pr_reviewer.integrations import IntegrationError, post_findings
from pr_reviewer.models import (
    Category,
    DiffStats,
    ReviewFinding,
    ReviewResult,
    Severity,
    Verdict,
)


SAMPLE_DIFF = """diff --git a/app/a.py b/app/a.py
index 1111111..2222222 100644
--- a/app/a.py
+++ b/app/a.py
@@ -1,2 +1,2 @@
-def answer():
-    return 40
+def answer():
+    return 42
"""

DELETE_DIFF = """diff --git a/app/obsolete.py b/app/obsolete.py
index 1111111..0000000 100644
--- a/app/obsolete.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def answer():
-    return 40
"""

CONTEXT_DIFF = """diff --git a/app/context.py b/app/context.py
index 1111111..2222222 100644
--- a/app/context.py
+++ b/app/context.py
@@ -1,3 +1,4 @@
 def answer():
+    audit()
     return 42
"""

RENAME_DIFF = """diff --git a/app/old_name.py b/app/new_name.py
index 1111111..2222222 100644
--- a/app/old_name.py
+++ b/app/new_name.py
@@ -1,2 +1,2 @@
-def answer():
+def answer():
     return 42
"""


def _result() -> ReviewResult:
    return _result_with_findings(
        ReviewFinding(
            severity=Severity.medium,
            category=Category.bug,
            title="Changed return value",
            explanation="Behavior changed from 40 to 42.",
            file="app/a.py",
            line=2,
            confidence=0.91,
        )
    )


def _result_with_findings(*findings: ReviewFinding) -> ReviewResult:
    return ReviewResult(
        summary="one finding",
        verdict=Verdict.needs_attention,
        findings=list(findings),
        model="fake",
        diff=DiffStats(files=["app/a.py"], files_changed=1, additions=2, deletions=2, line_count=8),
    )


class _FakeResponse:
    def __init__(self, status_code: int, json_payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text

    def json(self):
        if self._json_payload is None:
            raise ValueError("No JSON payload configured")
        return self._json_payload


class _FakeSession:
    def __init__(self, *, get_responses: list[_FakeResponse], post_responses: list[_FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self._get_responses = get_responses
        self._post_responses = post_responses
        self.get_calls: list[tuple[str, int]] = []
        self.post_calls: list[tuple[str, dict, int]] = []

    def get(self, url: str, timeout: int):
        self.get_calls.append((url, timeout))
        return self._get_responses.pop(0)

    def post(self, url: str, json: dict, timeout: int):
        self.post_calls.append((url, json, timeout))
        return self._post_responses.pop(0)


def test_github_dry_run_posting_requires_no_token_or_network() -> None:
    report = post_findings(
        platform="github",
        result=_result(),
        diff_text=SAMPLE_DIFF,
        repo="owner/repo",
        pr_number=1,
        mr_iid=None,
        token=None,
        base_url=None,
        dry_run=True,
    )

    assert report.platform == "github"
    assert report.attempted == 1
    assert report.posted == 1


def test_dry_run_counts_unpostable_findings_as_skipped() -> None:
    result = ReviewResult(
        summary="two findings",
        verdict=Verdict.needs_attention,
        findings=[
            _result().findings[0],
            ReviewFinding(
                severity=Severity.low,
                category=Category.maintainability,
                title="General note",
                explanation="This finding is not attached to a changed line.",
                file="app/a.py",
                line=99,
                confidence=0.60,
            ),
        ],
        model="fake",
        diff=DiffStats(files=["app/a.py"], files_changed=1, additions=2, deletions=2, line_count=8),
    )

    report = post_findings(
        platform="github",
        result=result,
        diff_text=SAMPLE_DIFF,
        repo="owner/repo",
        pr_number=1,
        mr_iid=None,
        token=None,
        base_url=None,
        dry_run=True,
    )

    assert report.attempted == 1
    assert report.posted == 1
    assert report.skipped == 1


def test_gitlab_dry_run_posting_requires_no_token_or_network() -> None:
    report = post_findings(
        platform="gitlab",
        result=_result(),
        diff_text=SAMPLE_DIFF,
        repo="group/project",
        pr_number=None,
        mr_iid=5,
        token=None,
        base_url=None,
        dry_run=True,
    )

    assert report.platform == "gitlab"
    assert report.attempted == 1
    assert report.posted == 1


def test_github_posting_requires_token_outside_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(IntegrationError, match="Missing GitHub token"):
        post_findings(
            platform="github",
            result=_result(),
            diff_text=SAMPLE_DIFF,
            repo="owner/repo",
            pr_number=1,
            mr_iid=None,
            token=None,
            base_url=None,
            dry_run=False,
        )


def test_github_posts_deleted_lines_on_left_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        get_responses=[_FakeResponse(200, {"head": {"sha": "abc123"}})],
        post_responses=[_FakeResponse(201, {})],
    )
    monkeypatch.setattr("pr_reviewer.integrations.requests.Session", lambda: session)

    report = post_findings(
        platform="github",
        result=_result_with_findings(
            ReviewFinding(
                severity=Severity.high,
                category=Category.bug,
                title="Deleted implementation",
                explanation="The function body was removed entirely.",
                file="app/obsolete.py",
                line=2,
                confidence=0.98,
            )
        ),
        diff_text=DELETE_DIFF,
        repo="owner/repo",
        pr_number=7,
        mr_iid=None,
        token="github-token",
        base_url="https://api.github.com",
        dry_run=False,
    )

    assert report.posted == 1
    assert report.skipped == 0
    _, payload, _ = session.post_calls[0]
    assert payload["path"] == "app/obsolete.py"
    assert payload["line"] == 2
    assert payload["side"] == "LEFT"


def test_gitlab_posts_context_lines_with_old_and_new_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        get_responses=[
            _FakeResponse(
                200,
                [
                    {
                        "base_commit_sha": "base-sha",
                        "start_commit_sha": "start-sha",
                        "head_commit_sha": "head-sha",
                    }
                ],
            )
        ],
        post_responses=[_FakeResponse(201, {})],
    )
    monkeypatch.setattr("pr_reviewer.integrations.requests.Session", lambda: session)

    report = post_findings(
        platform="gitlab",
        result=_result_with_findings(
            ReviewFinding(
                severity=Severity.low,
                category=Category.maintainability,
                title="Entry point lacks docs",
                explanation="The unchanged function signature still needs a docstring.",
                file="app/context.py",
                line=1,
                confidence=0.65,
            )
        ),
        diff_text=CONTEXT_DIFF,
        repo="group/project",
        pr_number=None,
        mr_iid=12,
        token="gitlab-token",
        base_url="https://gitlab.example.com/api/v4",
        dry_run=False,
    )

    assert report.posted == 1
    _, payload, _ = session.post_calls[0]
    position = payload["position"]
    assert position["old_path"] == "app/context.py"
    assert position["new_path"] == "app/context.py"
    assert position["old_line"] == 1
    assert position["new_line"] == 1


def test_github_uses_new_path_when_finding_references_old_renamed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(
        get_responses=[_FakeResponse(200, {"head": {"sha": "abc123"}})],
        post_responses=[_FakeResponse(201, {})],
    )
    monkeypatch.setattr("pr_reviewer.integrations.requests.Session", lambda: session)

    report = post_findings(
        platform="github",
        result=_result_with_findings(
            ReviewFinding(
                severity=Severity.medium,
                category=Category.maintainability,
                title="Renamed file still lacks docs",
                explanation="The function definition changed in the renamed file.",
                file="app/old_name.py",
                line=1,
                confidence=0.74,
            )
        ),
        diff_text=RENAME_DIFF,
        repo="owner/repo",
        pr_number=7,
        mr_iid=None,
        token="github-token",
        base_url="https://api.github.com",
        dry_run=False,
    )

    assert report.posted == 1
    _, payload, _ = session.post_calls[0]
    assert payload["path"] == "app/new_name.py"
    assert payload["side"] == "RIGHT"
