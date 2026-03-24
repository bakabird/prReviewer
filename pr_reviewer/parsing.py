from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import DiffStats

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class HunkLine:
    raw: str
    line_type: str  # add | del | context | meta
    old_line: int | None
    new_line: int | None
    content: str


@dataclass
class DiffHunk:
    file_path: str
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class ParsedDiff:
    stats: DiffStats
    hunks_by_file: dict[str, list[DiffHunk]] = field(default_factory=dict)
    changed_new_lines: dict[str, set[int]] = field(default_factory=dict)


def read_patch_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def normalize_diff_path(path: str) -> str:
    cleaned = path.strip()
    if cleaned.startswith(("a/", "b/")):
        cleaned = cleaned[2:]
    return cleaned


def parse_diff_stats(diff_text: str) -> DiffStats:
    return parse_unified_diff(diff_text).stats


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    lines = diff_text.splitlines()

    files: list[str] = []
    seen_files: set[str] = set()
    additions = 0
    deletions = 0
    patch_like = False

    hunks_by_file: dict[str, list[DiffHunk]] = {}
    changed_new_lines: dict[str, set[int]] = {}

    current_file: str | None = None
    current_hunk: DiffHunk | None = None
    old_cursor = 0
    new_cursor = 0

    for line in lines:
        diff_header = _DIFF_HEADER_RE.match(line)
        if diff_header:
            patch_like = True
            current_file = normalize_diff_path(diff_header.group(2))
            if current_file != "/dev/null":
                _register_file(current_file, files, seen_files)
            current_hunk = None
            continue

        if line.startswith("--- ") or line.startswith("+++ "):
            patch_like = True
            if line.startswith("+++ "):
                candidate = normalize_diff_path(line[4:])
                if candidate and candidate != "/dev/null":
                    current_file = candidate
                    _register_file(candidate, files, seen_files)
            continue

        hunk_header = _HUNK_HEADER_RE.match(line)
        if hunk_header and current_file:
            patch_like = True
            old_start = int(hunk_header.group(1))
            old_count = int(hunk_header.group(2) or 1)
            new_start = int(hunk_header.group(3))
            new_count = int(hunk_header.group(4) or 1)

            old_cursor = old_start
            new_cursor = new_start
            current_hunk = DiffHunk(
                file_path=current_file,
                header=line,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            hunks_by_file.setdefault(current_file, []).append(current_hunk)
            changed_new_lines.setdefault(current_file, set())
            continue

        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

        if current_hunk is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current_hunk.lines.append(
                HunkLine(
                    raw=line,
                    line_type="add",
                    old_line=None,
                    new_line=new_cursor,
                    content=line[1:],
                )
            )
            changed_new_lines[current_file].add(new_cursor)
            new_cursor += 1
            continue

        if line.startswith("-") and not line.startswith("---"):
            current_hunk.lines.append(
                HunkLine(
                    raw=line,
                    line_type="del",
                    old_line=old_cursor,
                    new_line=None,
                    content=line[1:],
                )
            )
            old_cursor += 1
            continue

        if line.startswith(" "):
            current_hunk.lines.append(
                HunkLine(
                    raw=line,
                    line_type="context",
                    old_line=old_cursor,
                    new_line=new_cursor,
                    content=line[1:],
                )
            )
            old_cursor += 1
            new_cursor += 1
            continue

        current_hunk.lines.append(
            HunkLine(
                raw=line,
                line_type="meta",
                old_line=None,
                new_line=None,
                content=line,
            )
        )

    stats = DiffStats(
        files=files,
        files_changed=len(files),
        additions=additions,
        deletions=deletions,
        line_count=len(lines),
        patch_like=patch_like,
    )
    return ParsedDiff(stats=stats, hunks_by_file=hunks_by_file, changed_new_lines=changed_new_lines)


def build_finding_annotation(
    parsed_diff: ParsedDiff,
    *,
    file_path: str,
    line: int,
    context: int = 2,
) -> tuple[str | None, str | None, bool]:
    normalized = normalize_diff_path(file_path)
    hunks = parsed_diff.hunks_by_file.get(normalized)
    if not hunks:
        return None, None, False

    target_hunk: DiffHunk | None = None
    target_index: int | None = None

    for hunk in hunks:
        for idx, hunk_line in enumerate(hunk.lines):
            if hunk_line.new_line == line:
                target_hunk = hunk
                target_index = idx
                break
        if target_hunk is not None:
            break

    # Fallback for findings that point to removed lines.
    if target_hunk is None:
        for hunk in hunks:
            for idx, hunk_line in enumerate(hunk.lines):
                if hunk_line.old_line == line:
                    target_hunk = hunk
                    target_index = idx
                    break
            if target_hunk is not None:
                break

    if target_hunk is None or target_index is None:
        return None, None, False

    start = max(0, target_index - context)
    end = min(len(target_hunk.lines), target_index + context + 1)

    frame_lines: list[str] = [target_hunk.header]
    for hunk_line in target_hunk.lines[start:end]:
        display_line = hunk_line.new_line if hunk_line.new_line is not None else hunk_line.old_line
        number = f"{display_line:>4}" if display_line is not None else "   -"

        if hunk_line.line_type == "add":
            sign = "+"
        elif hunk_line.line_type == "del":
            sign = "-"
        elif hunk_line.line_type == "context":
            sign = " "
        else:
            sign = "\\"

        is_target = hunk_line.new_line == line or (
            hunk_line.new_line is None and hunk_line.old_line == line
        )
        marker = ">" if is_target else " "
        frame_lines.append(f"{marker}{number} {sign} {hunk_line.content}")

    on_changed_line = line in parsed_diff.changed_new_lines.get(normalized, set())
    return target_hunk.header, "\n".join(frame_lines), on_changed_line


def truncate_diff(diff_text: str, max_lines: int) -> tuple[str, bool, int]:
    lines = diff_text.splitlines()
    original_line_count = len(lines)

    if max_lines <= 0:
        return "", bool(lines), original_line_count

    if original_line_count <= max_lines:
        return diff_text, False, original_line_count

    section_starts = [idx for idx, line in enumerate(lines) if _DIFF_HEADER_RE.match(line)]
    if len(section_starts) > 1 and max_lines > 2:
        distributed = _truncate_diff_sections(lines, section_starts, max_lines, original_line_count)
        if distributed is not None:
            return distributed, True, original_line_count

    return _truncate_head_tail(lines, max_lines, original_line_count)


def _truncate_head_tail(
    lines: list[str],
    max_lines: int,
    original_line_count: int,
) -> tuple[str, bool, int]:
    tail_count = min(120, max(20, max_lines // 3))
    head_count = max_lines - tail_count - 1
    if head_count < 1:
        head_count = max(1, max_lines - 1)
        tail_count = 0

    truncated_lines = lines[:head_count]
    truncated_lines.append(
        f"# ... diff truncated: showing {head_count} head lines and {tail_count} tail lines out of {original_line_count} total ..."
    )
    if tail_count > 0:
        truncated_lines.extend(lines[-tail_count:])

    return "\n".join(truncated_lines), True, original_line_count


def _truncate_diff_sections(
    lines: list[str],
    section_starts: list[int],
    max_lines: int,
    original_line_count: int,
) -> str | None:
    sections: list[list[str]] = []
    for idx, start in enumerate(section_starts):
        end = section_starts[idx + 1] if idx + 1 < len(section_starts) else len(lines)
        section = lines[start:end]
        if section:
            sections.append(section)

    if len(sections) < 2:
        return None

    content_budget = max_lines - 1
    if content_budget < len(sections):
        return None

    take_counts = [min(len(section), content_budget // len(sections)) for section in sections]
    used = sum(take_counts)
    remaining = content_budget - used

    while remaining > 0:
        progressed = False
        for idx, section in enumerate(sections):
            if take_counts[idx] >= len(section):
                continue
            take_counts[idx] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break

    distributed_lines: list[str] = []
    for section, take_count in zip(sections, take_counts):
        distributed_lines.extend(section[:take_count])

    if len(distributed_lines) >= original_line_count:
        return None

    distributed_lines.append(
        "# ... diff truncated: showing distributed excerpts across "
        f"{len(sections)} file sections out of {original_line_count} total lines ..."
    )
    return "\n".join(distributed_lines)


def _register_file(file_path: str, files: list[str], seen_files: set[str]) -> None:
    if file_path and file_path not in seen_files:
        files.append(file_path)
        seen_files.add(file_path)
