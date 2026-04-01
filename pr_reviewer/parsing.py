from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import DiffStats

logger = logging.getLogger(__name__)

_WARN_SIZE_BYTES = 1_000_000   # 1 MB
_MAX_SIZE_BYTES = 10_000_000   # 10 MB

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
    changed_old_lines: dict[str, set[int]] = field(default_factory=dict)
    files_by_path: dict[str, DiffFile] = field(default_factory=dict)


@dataclass
class DiffFile:
    old_path: str | None
    new_path: str | None
    display_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    changed_new_lines: set[int] = field(default_factory=set)
    changed_old_lines: set[int] = field(default_factory=set)


@dataclass
class ResolvedDiffLine:
    diff_file: DiffFile
    hunk: DiffHunk
    hunk_line: HunkLine
    index: int


@dataclass
class DiffChunk:
    diff_text: str
    stats: DiffStats


def read_patch_file(path: str | Path) -> str:
    p = Path(path)
    size = p.stat().st_size
    if size > _MAX_SIZE_BYTES:
        raise ValueError(f"Diff file is too large ({size:,} bytes, max {_MAX_SIZE_BYTES:,}). Split the diff or increase the limit.")
    if size > _WARN_SIZE_BYTES:
        logger.warning("Diff file is large (%s bytes); review quality may be reduced.", f"{size:,}")
    return p.read_text(encoding="utf-8", errors="replace")


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
    changed_old_lines: dict[str, set[int]] = {}
    files_by_path: dict[str, DiffFile] = {}

    current_file: str | None = None
    current_hunk: DiffHunk | None = None
    current_diff_file: DiffFile | None = None
    old_cursor = 0
    new_cursor = 0
    pending_old_path: str | None = None

    for line in lines:
        diff_header = _DIFF_HEADER_RE.match(line)
        if diff_header:
            patch_like = True
            old_path = _normalize_optional_diff_path(diff_header.group(1))
            new_path = _normalize_optional_diff_path(diff_header.group(2))
            current_diff_file = _ensure_diff_file(
                old_path=old_path,
                new_path=new_path,
                files=files,
                seen_files=seen_files,
                files_by_path=files_by_path,
            )
            current_file = current_diff_file.display_path
            current_hunk = None
            pending_old_path = old_path
            continue

        if line.startswith("--- ") or line.startswith("+++ "):
            patch_like = True
            if line.startswith("--- "):
                pending_old_path = _normalize_optional_diff_path(line[4:])
                continue

            candidate = _normalize_optional_diff_path(line[4:])
            if (
                current_diff_file is None
                or current_diff_file.old_path != pending_old_path
                or current_diff_file.new_path != candidate
            ):
                current_diff_file = _ensure_diff_file(
                    old_path=pending_old_path,
                    new_path=candidate,
                    files=files,
                    seen_files=seen_files,
                    files_by_path=files_by_path,
                )
                current_hunk = None
            current_file = current_diff_file.display_path
            continue

        hunk_header = _HUNK_HEADER_RE.match(line)
        if hunk_header and current_file and current_diff_file is not None:
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
            current_diff_file.hunks.append(current_hunk)
            changed_new_lines.setdefault(current_file, set())
            changed_old_lines.setdefault(current_file, set())
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
            current_diff_file.changed_new_lines.add(new_cursor)
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
            changed_old_lines[current_file].add(old_cursor)
            current_diff_file.changed_old_lines.add(old_cursor)
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
    return ParsedDiff(
        stats=stats,
        hunks_by_file=hunks_by_file,
        changed_new_lines=changed_new_lines,
        changed_old_lines=changed_old_lines,
        files_by_path=files_by_path,
    )


def build_finding_annotation(
    parsed_diff: ParsedDiff,
    *,
    file_path: str,
    line: int,
    context: int = 2,
) -> tuple[str | None, str | None, bool]:
    resolved = resolve_diff_line(
        parsed_diff,
        file_path=file_path,
        line=line,
    )
    if resolved is None:
        return None, None, False

    start = max(0, resolved.index - context)
    end = min(len(resolved.hunk.lines), resolved.index + context + 1)

    frame_lines: list[str] = [resolved.hunk.header]
    for hunk_line in resolved.hunk.lines[start:end]:
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

        is_target = hunk_line is resolved.hunk_line
        marker = ">" if is_target else " "
        frame_lines.append(f"{marker}{number} {sign} {hunk_line.content}")

    on_changed_line = resolved.hunk_line.line_type in {"add", "del"}
    return resolved.hunk.header, "\n".join(frame_lines), on_changed_line


def resolve_diff_line(
    parsed_diff: ParsedDiff,
    *,
    file_path: str,
    line: int,
) -> ResolvedDiffLine | None:
    normalized = normalize_diff_path(file_path)
    diff_file = parsed_diff.files_by_path.get(normalized)
    if diff_file is None:
        return None

    for hunk in diff_file.hunks:
        for idx, hunk_line in enumerate(hunk.lines):
            if hunk_line.new_line == line:
                return ResolvedDiffLine(
                    diff_file=diff_file,
                    hunk=hunk,
                    hunk_line=hunk_line,
                    index=idx,
                )

    for hunk in diff_file.hunks:
        for idx, hunk_line in enumerate(hunk.lines):
            if hunk_line.old_line == line:
                return ResolvedDiffLine(
                    diff_file=diff_file,
                    hunk=hunk,
                    hunk_line=hunk_line,
                    index=idx,
                )

    return None


def chunk_diff(diff_text: str, max_lines: int) -> tuple[list[DiffChunk], bool, int]:
    lines = diff_text.splitlines()
    original_line_count = len(lines)

    if not lines:
        return [DiffChunk(diff_text="", stats=parse_unified_diff("").stats)], False, 0

    if max_lines <= 0:
        return [DiffChunk(diff_text="", stats=parse_unified_diff("").stats)], True, original_line_count

    if original_line_count <= max_lines:
        return [DiffChunk(diff_text=diff_text, stats=parse_unified_diff(diff_text).stats)], False, original_line_count

    patch_sections = _split_patch_sections(lines)
    if patch_sections:
        chunk_lines = _build_patch_chunks(patch_sections, max_lines)
    else:
        chunk_lines = _split_line_windows(lines, max_lines=max_lines, overlap=_window_overlap(max_lines))

    chunks = [
        DiffChunk(
            diff_text="\n".join(chunk),
            stats=parse_unified_diff("\n".join(chunk)).stats,
        )
        for chunk in chunk_lines
        if chunk
    ]
    if not chunks:
        fallback_text, _, _ = truncate_diff(diff_text, max_lines=max_lines)
        return [DiffChunk(diff_text=fallback_text, stats=parse_unified_diff(fallback_text).stats)], True, original_line_count

    return chunks, len(chunks) > 1, original_line_count


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
    for section, take_count in zip(sections, take_counts, strict=True):
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


def _normalize_optional_diff_path(path: str) -> str | None:
    normalized = normalize_diff_path(path)
    if not normalized or normalized == "/dev/null":
        return None
    return normalized


def _ensure_diff_file(
    *,
    old_path: str | None,
    new_path: str | None,
    files: list[str],
    seen_files: set[str],
    files_by_path: dict[str, DiffFile],
) -> DiffFile:
    aliases = [path for path in [new_path, old_path] if path]
    for alias in aliases:
        existing = files_by_path.get(alias)
        if existing is not None:
            if existing.old_path is None and old_path is not None:
                existing.old_path = old_path
            if existing.new_path is None and new_path is not None:
                existing.new_path = new_path
            _register_diff_file_aliases(existing, files_by_path)
            return existing

    display_path = new_path or old_path or "(unknown)"
    diff_file = DiffFile(old_path=old_path, new_path=new_path, display_path=display_path)
    _register_file(display_path, files, seen_files)
    _register_diff_file_aliases(diff_file, files_by_path)
    return diff_file


def _register_diff_file_aliases(
    diff_file: DiffFile,
    files_by_path: dict[str, DiffFile],
) -> None:
    for alias in [diff_file.display_path, diff_file.old_path, diff_file.new_path]:
        if alias:
            files_by_path[alias] = diff_file


def _split_patch_sections(lines: list[str]) -> list[list[str]]:
    section_starts = [idx for idx, line in enumerate(lines) if _DIFF_HEADER_RE.match(line)]
    if not section_starts:
        return []

    sections: list[list[str]] = []
    for idx, start in enumerate(section_starts):
        end = section_starts[idx + 1] if idx + 1 < len(section_starts) else len(lines)
        section = lines[start:end]
        if section:
            sections.append(section)
    return sections


def _build_patch_chunks(sections: list[list[str]], max_lines: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []

    for section in sections:
        if len(section) > max_lines:
            if current:
                chunks.append(current)
                current = []
            chunks.extend(_split_large_patch_section(section, max_lines))
            continue

        if current and len(current) + len(section) > max_lines:
            chunks.append(current)
            current = []

        current.extend(section)

    if current:
        chunks.append(current)

    return chunks


def _split_large_patch_section(section: list[str], max_lines: int) -> list[list[str]]:
    hunk_starts = [idx for idx, line in enumerate(section) if _HUNK_HEADER_RE.match(line)]
    if not hunk_starts:
        return _split_line_windows(section, max_lines=max_lines, overlap=_window_overlap(max_lines))

    preamble = section[: hunk_starts[0]]
    if len(preamble) >= max_lines:
        return _split_line_windows(section, max_lines=max_lines, overlap=_window_overlap(max_lines))

    hunks: list[list[str]] = []
    for idx, start in enumerate(hunk_starts):
        end = hunk_starts[idx + 1] if idx + 1 < len(hunk_starts) else len(section)
        hunks.append(section[start:end])

    chunks: list[list[str]] = []
    current = preamble.copy()
    current_has_hunk = False

    for hunk in hunks:
        if current_has_hunk and len(current) + len(hunk) > max_lines:
            chunks.append(current)
            current = preamble.copy()
            current_has_hunk = False

        if len(preamble) + len(hunk) > max_lines:
            if current_has_hunk:
                chunks.append(current)
                current = preamble.copy()
                current_has_hunk = False
            chunks.extend(_split_large_hunk(preamble, hunk, max_lines))
            continue

        current.extend(hunk)
        current_has_hunk = True

    if current_has_hunk:
        chunks.append(current)

    return chunks


def _split_large_hunk(preamble: list[str], hunk: list[str], max_lines: int) -> list[list[str]]:
    if not hunk:
        return []

    hunk_header = [hunk[0]]
    hunk_body = hunk[1:]
    available_body_lines = max_lines - len(preamble) - len(hunk_header)
    if available_body_lines <= 0:
        return _split_line_windows(preamble + hunk, max_lines=max_lines, overlap=_window_overlap(max_lines))

    overlap = _window_overlap(available_body_lines)
    body_windows = _split_line_windows(
        hunk_body,
        max_lines=available_body_lines,
        overlap=overlap,
    )
    return [preamble + hunk_header + window for window in body_windows if window]


def _split_line_windows(lines: list[str], *, max_lines: int, overlap: int) -> list[list[str]]:
    if not lines:
        return []
    if max_lines <= 0:
        return [[]]
    if len(lines) <= max_lines:
        return [lines]

    effective_overlap = min(max(overlap, 0), max_lines - 1) if max_lines > 1 else 0
    step = max(1, max_lines - effective_overlap)

    windows: list[list[str]] = []
    start = 0
    while start < len(lines):
        window = lines[start : start + max_lines]
        if not window:
            break
        windows.append(window)
        if len(window) < max_lines:
            break
        start += step

    return windows


def _window_overlap(max_lines: int) -> int:
    if max_lines <= 8:
        return 0
    return min(12, max(2, max_lines // 6))
