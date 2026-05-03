from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ReviewFinding, ReviewResult

_RESET = "\033[0m"
_BOLD = "\033[1m"
_SEVERITY_COLOR = {
    "low": "\033[36m",
    "medium": "\033[33m",
    "high": "\033[31m",
}
_VERDICT_COLOR = {
    "looks good": "\033[32m",
    "needs attention": "\033[33m",
    "high risk": "\033[31m",
}


def format_review(
    result: ReviewResult,
    *,
    output_format: str = "text",
    compact: bool = False,
    color: bool = False,
) -> str:
    if output_format == "text":
        return _format_text(result, compact=compact, color=color)
    if output_format == "markdown":
        return _format_markdown(result, compact=compact)
    if output_format == "json":
        return json.dumps(result.model_dump(mode="json", exclude_none=True), indent=2)

    raise ValueError(f"Unsupported format: {output_format}")


def _format_text(result: ReviewResult, *, compact: bool, color: bool) -> str:
    severity_counts = _severity_counts(result.findings)
    verdict = _style(result.verdict.value.upper(), _VERDICT_COLOR[result.verdict.value], color)
    generated_at = result.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        _style("PR Review", _BOLD, color),
        "=========",
        f"Verdict       : {verdict}",
        f"Review mode   : {result.review_mode}",
        f"Files changed : {result.diff.files_changed}",
        f"Diff churn    : +{result.diff.additions} / -{result.diff.deletions}",
        f"Findings      : {len(result.findings)} "
        f"(high: {severity_counts['high']}, medium: {severity_counts['medium']}, low: {severity_counts['low']})",
        f"Model         : {result.model}",
        f"Generated     : {generated_at}",
    ]

    if result.passes_run:
        lines.append(f"Passes        : {', '.join(result.passes_run)}")

    if result.diff.files:
        lines.append(f"Changed files: {', '.join(result.diff.files)}")

    if result.warnings:
        lines.append("")
        lines.append("Warnings")
        lines.append("--------")
        for warning in result.warnings:
            lines.append(f"- {warning}")

    lines.extend([
        "",
        "Summary",
        "-------",
        result.summary,
        "",
        "Findings",
        "--------",
    ])

    if not result.findings:
        lines.append("No material issues found in the visible diff.")
        return "\n".join(lines)

    if compact:
        for finding in result.findings:
            sev = _severity_badge(finding.severity.value, color)
            location_parts = []
            if finding.file:
                location_parts.append(finding.file)
            if finding.line:
                location_parts.append(f"L{finding.line}")
            location = f" ({':'.join(location_parts)})" if location_parts else ""
            lines.append(
                f"{sev} [{finding.category.value}] {finding.title}{location} "
                f"(confidence: {finding.confidence:.2f}, evidence: {finding.evidence.value}"
                f"{_post_level_suffix(finding)})"
            )
        return "\n".join(lines)

    grouped = _group_by_file(result.findings)
    for file_name, findings in grouped.items():
        lines.append(f"File: {file_name}")
        lines.append("-" * (len(file_name) + 6))
        for idx, finding in enumerate(findings, start=1):
            sev = _severity_badge(finding.severity.value, color)
            lines.append(f"{idx}. {sev} [{finding.category.value}] {finding.title}")
            lines.append(f"Category: {finding.category.value}")
            if finding.line:
                lines.append(f"Line: {finding.line}")
            lines.append(f"Confidence: {finding.confidence:.2f}")
            lines.append(f"Evidence: {finding.evidence.value}")
            if finding.post_level:
                lines.append(f"Posting intent: {finding.post_level.value}")
            if finding.impact:
                lines.append(f"Impact: {finding.impact}")
            lines.append(f"Why it matters: {finding.explanation}")
            if finding.suggested_fix:
                lines.append(f"Suggested fix: {finding.suggested_fix}")
            if finding.code_frame:
                lines.append("Code frame:")
                for frame_line in finding.code_frame.splitlines():
                    lines.append(f"  {frame_line}")
            lines.append("")

    return "\n".join(lines).rstrip()


def _format_markdown(result: ReviewResult, *, compact: bool) -> str:
    lines: list[str] = [
        "# PR Review",
        "",
        f"- **Review mode:** `{result.review_mode}`",
        f"- **Files changed:** {result.diff.files_changed}",
        f"- **Additions:** {result.diff.additions}",
        f"- **Deletions:** {result.diff.deletions}",
        f"- **Verdict:** `{result.verdict.value}`",
    ]

    if result.passes_run:
        lines.append(f"- **Passes:** {', '.join(result.passes_run)}")

    if result.diff.files:
        lines.append(f"- **Changed files:** {', '.join(result.diff.files)}")

    if result.warnings:
        lines.append(f"- **Warnings:** {' | '.join(result.warnings)}")

    lines.extend(["", "## Summary", "", result.summary, "", "## Findings", ""])

    if not result.findings:
        lines.append("No material issues found in the visible diff.")
        return "\n".join(lines)

    if compact:
        for finding in result.findings:
            location = f" ({finding.file})" if finding.file else ""
            lines.append(
                f"- **[{finding.severity.value.upper()}] {finding.title}** "
                f"`{finding.category.value}`{location} "
                f"(confidence: {finding.confidence:.2f}, evidence: `{finding.evidence.value}`"
                f"{_post_level_suffix(finding)})"
            )
        return "\n".join(lines)

    for finding in result.findings:
        lines.append(f"### [{finding.severity.value.upper()}] {finding.title}")
        lines.append("")
        lines.append(f"- **Category:** {finding.category.value}")
        if finding.file:
            lines.append(f"- **File:** `{finding.file}`")
        if finding.line:
            lines.append(f"- **Line:** {finding.line}")
        lines.append(f"- **Confidence:** {finding.confidence:.2f}")
        lines.append(f"- **Evidence:** `{finding.evidence.value}`")
        if finding.post_level:
            lines.append(f"- **Posting intent:** `{finding.post_level.value}`")
        if finding.impact:
            lines.append(f"- **Impact:** {finding.impact}")
        lines.append(f"- **Why it matters:** {finding.explanation}")
        if finding.suggested_fix:
            lines.append(f"- **Suggested fix:** {finding.suggested_fix}")
        if finding.code_frame:
            lines.extend(["", "```text", finding.code_frame, "```"])
        lines.append("")

    return "\n".join(lines).rstrip()


def _group_by_file(findings: list[ReviewFinding]) -> dict[str, list[ReviewFinding]]:
    grouped: dict[str, list[ReviewFinding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.file or "(general)"].append(finding)
    return dict(grouped)


def _severity_counts(findings: list[ReviewFinding]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        counts[finding.severity.value] += 1
    return counts


def _post_level_suffix(finding: ReviewFinding) -> str:
    if not finding.post_level:
        return ""
    return f", post: {finding.post_level.value}"


def _severity_badge(severity: str, color: bool) -> str:
    label = severity.upper()
    return f"[{_style(label, _SEVERITY_COLOR[severity], color)}]"


def _style(text: str, color_code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color_code}{text}{_RESET}"
