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


def _result() -> ReviewResult:
    return ReviewResult(
        summary="one finding",
        verdict=Verdict.needs_attention,
        findings=[
            ReviewFinding(
                severity=Severity.medium,
                category=Category.bug,
                title="Changed return value",
                explanation="Behavior changed from 40 to 42.",
                file="app/a.py",
                line=2,
                confidence=0.91,
            )
        ],
        model="fake",
        diff=DiffStats(files=["app/a.py"], files_changed=1, additions=2, deletions=2, line_count=8),
    )


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
