import json

from pr_reviewer.formatters import format_review
from pr_reviewer.models import Category, DiffStats, ReviewFinding, ReviewResult, Severity, Verdict


def _sample_result() -> ReviewResult:
    return ReviewResult(
        summary="Useful change, but it introduces a possible None access path.",
        verdict=Verdict.needs_attention,
        findings=[
            ReviewFinding(
                severity=Severity.high,
                category=Category.bug,
                title="Potential None access",
                explanation="The updated path dereferences `user` without guarding after a branch.",
                file="api/user.py",
                line=12,
                suggested_fix="Add a guard clause before dereferencing user fields.",
            )
        ],
        model="gpt-4.1-mini",
        diff=DiffStats(
            files=["api/user.py"],
            files_changed=1,
            additions=10,
            deletions=3,
            line_count=20,
        ),
    )


def test_text_formatter_includes_expected_sections() -> None:
    output = format_review(_sample_result(), output_format="text")

    assert "PR Review" in output
    assert "Summary" in output
    assert "Findings" in output
    assert "Potential None access" in output


def test_markdown_formatter_uses_markdown_headings() -> None:
    output = format_review(_sample_result(), output_format="markdown")

    assert "# PR Review" in output
    assert "## Findings" in output
    assert "### [HIGH] Potential None access" in output


def test_json_formatter_returns_valid_json() -> None:
    output = format_review(_sample_result(), output_format="json")
    parsed = json.loads(output)

    assert parsed["verdict"] == "needs attention"
    assert parsed["findings"][0]["severity"] == "high"
