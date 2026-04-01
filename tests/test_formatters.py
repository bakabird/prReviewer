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


def _empty_result() -> ReviewResult:
    return ReviewResult(
        summary="No issues found.",
        verdict=Verdict.looks_good,
        findings=[],
        model="gpt-4.1-mini",
        diff=DiffStats(
            files=["api/user.py"],
            files_changed=1,
            additions=2,
            deletions=1,
            line_count=10,
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


def test_text_compact_mode_one_line_per_finding() -> None:
    output = format_review(_sample_result(), output_format="text", compact=True)

    # Compact mode should not include "Why it matters:" detail
    assert "Why it matters:" not in output
    # But should include the finding title and category
    assert "Potential None access" in output
    assert "bug" in output


def test_text_empty_findings_shows_no_issues() -> None:
    output = format_review(_empty_result(), output_format="text")

    assert "No material issues found" in output


def test_markdown_empty_findings_shows_no_issues() -> None:
    output = format_review(_empty_result(), output_format="markdown")

    assert "No material issues found" in output


def test_text_color_disabled_has_no_ansi_codes() -> None:
    output = format_review(_sample_result(), output_format="text", color=False)

    assert "\033[" not in output


def test_text_color_enabled_has_ansi_codes() -> None:
    output = format_review(_sample_result(), output_format="text", color=True)

    assert "\033[" in output


def test_markdown_compact_mode() -> None:
    output = format_review(_sample_result(), output_format="markdown", compact=True)

    # Compact markdown should use bullet points, not full sections
    assert "### [HIGH]" not in output
    assert "**[HIGH] Potential None access**" in output


def test_all_verdicts_in_text_format() -> None:
    for verdict in Verdict:
        result = ReviewResult(
            summary="test",
            verdict=verdict,
            findings=[],
            model="fake",
            diff=DiffStats(files=[], files_changed=0, additions=0, deletions=0, line_count=0),
        )
        output = format_review(result, output_format="text")
        assert verdict.value.upper() in output


def test_all_verdicts_in_markdown_format() -> None:
    for verdict in Verdict:
        result = ReviewResult(
            summary="test",
            verdict=verdict,
            findings=[],
            model="fake",
            diff=DiffStats(files=[], files_changed=0, additions=0, deletions=0, line_count=0),
        )
        output = format_review(result, output_format="markdown")
        assert f"`{verdict.value}`" in output
