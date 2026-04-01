import pytest

from pr_reviewer.parsing import (
    build_finding_annotation,
    chunk_diff,
    parse_diff_stats,
    parse_unified_diff,
    read_patch_file,
    truncate_diff,
)

SAMPLE_DIFF = """diff --git a/api/user.py b/api/user.py
index abc123..def456 100644
--- a/api/user.py
+++ b/api/user.py
@@ -1,5 +1,6 @@
 def get_user_name(user):
-    return user[\"name\"]
+    if user is None:
+        return \"\"
+    return user[\"name\"]

diff --git a/core/cache.py b/core/cache.py
index 111111..222222 100644
--- a/core/cache.py
+++ b/core/cache.py
@@ -10,4 +10,3 @@
-    for item in items:
-        cache[item.id] = expensive_lookup(item.id)
+    cache.update({item.id: expensive_lookup(item.id) for item in items})
     return cache
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


def test_parse_diff_stats_extracts_files_and_counts() -> None:
    stats = parse_diff_stats(SAMPLE_DIFF)

    assert stats.files_changed == 2
    assert stats.files == ["api/user.py", "core/cache.py"]
    assert stats.additions == 4
    assert stats.deletions == 3
    assert stats.patch_like is True


def test_parse_diff_stats_handles_non_patch_input() -> None:
    stats = parse_diff_stats("just some text\nnot a patch")

    assert stats.patch_like is False
    assert stats.files_changed == 0


def test_truncate_diff_applies_marker_and_preserves_bounds() -> None:
    diff_text = "\n".join(f"line {idx}" for idx in range(40))

    truncated, was_truncated, original_count = truncate_diff(diff_text, max_lines=12)
    lines = truncated.splitlines()

    assert was_truncated is True
    assert original_count == 40
    assert len(lines) == 12
    assert "diff truncated" in truncated


def test_truncate_diff_distributes_budget_across_multiple_files() -> None:
    truncated, was_truncated, original_count = truncate_diff(SAMPLE_DIFF, max_lines=10)

    assert was_truncated is True
    assert original_count == len(SAMPLE_DIFF.splitlines())
    assert len(truncated.splitlines()) == 10
    assert "diff --git a/api/user.py b/api/user.py" in truncated
    assert "diff --git a/core/cache.py b/core/cache.py" in truncated
    assert "distributed excerpts across 2 file sections" in truncated


def test_chunk_diff_splits_multi_file_patch_into_reviewable_chunks() -> None:
    chunks, was_chunked, original_count = chunk_diff(SAMPLE_DIFF, max_lines=10)

    assert was_chunked is True
    assert original_count == len(SAMPLE_DIFF.splitlines())
    assert len(chunks) >= 2
    assert chunks[0].stats.files == ["api/user.py"]
    assert chunks[-1].stats.files == ["core/cache.py"]
    assert {file for chunk in chunks for file in chunk.stats.files} == {"api/user.py", "core/cache.py"}
    assert all(chunk.stats.line_count <= 10 for chunk in chunks)


def test_chunk_diff_splits_large_single_hunk_into_multiple_windows() -> None:
    big_hunk_diff = "\n".join(
        [
            "diff --git a/app/huge.py b/app/huge.py",
            "index 1111111..2222222 100644",
            "--- a/app/huge.py",
            "+++ b/app/huge.py",
            "@@ -1,1 +1,18 @@",
            "-return 1",
            "+def compute():",
        ]
        + [f"+    value_{idx} = {idx}" for idx in range(1, 15)]
        + ["+    return value_14"]
    )

    chunks, was_chunked, _ = chunk_diff(big_hunk_diff, max_lines=9)

    assert was_chunked is True
    assert len(chunks) > 1
    assert all(chunk.stats.files == ["app/huge.py"] for chunk in chunks)
    assert all(chunk.stats.line_count <= 9 for chunk in chunks)
    assert all("diff --git a/app/huge.py b/app/huge.py" in chunk.diff_text for chunk in chunks)
    assert all("@@ -1,1 +1,18 @@" in chunk.diff_text for chunk in chunks)


def test_build_finding_annotation_maps_to_hunk_context() -> None:
    parsed = parse_unified_diff(SAMPLE_DIFF)

    hunk_header, code_frame, on_changed_line = build_finding_annotation(
        parsed,
        file_path="api/user.py",
        line=3,
    )

    assert hunk_header is not None
    assert code_frame is not None
    assert "@@" in code_frame
    assert 'return user["name"]' in code_frame
    assert on_changed_line is True


def test_read_patch_file_large_file_warning(tmp_path, caplog) -> None:
    """Files over 1MB should log a warning."""
    import logging
    large_file = tmp_path / "large.patch"
    # Create a file just over 1MB
    large_file.write_text("+" * 1_100_000, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="pr_reviewer.parsing"):
        content = read_patch_file(large_file)

    assert len(content) == 1_100_000
    assert any("large" in record.message.lower() for record in caplog.records)


def test_read_patch_file_rejects_over_10mb(tmp_path) -> None:
    """Files over 10MB should raise ValueError."""
    huge_file = tmp_path / "huge.patch"
    huge_file.write_text("+" * 10_100_000, encoding="utf-8")

    with pytest.raises(ValueError, match="too large"):
        read_patch_file(huge_file)


def test_rename_detection() -> None:
    """Renamed files should have both old and new paths tracked."""
    parsed = parse_unified_diff(RENAME_DIFF)

    assert "app/new_name.py" in parsed.files_by_path
    assert "app/old_name.py" in parsed.files_by_path

    diff_file = parsed.files_by_path["app/new_name.py"]
    assert diff_file.old_path == "app/old_name.py"
    assert diff_file.new_path == "app/new_name.py"


def test_overlapping_hunks_in_same_file() -> None:
    """Multiple hunks in the same file should all be tracked."""
    multi_hunk_diff = """diff --git a/app/multi.py b/app/multi.py
index 1111111..2222222 100644
--- a/app/multi.py
+++ b/app/multi.py
@@ -1,3 +1,4 @@
 def first():
+    audit()
     return 1
@@ -10,3 +11,4 @@
 def second():
+    audit()
     return 2
"""
    parsed = parse_unified_diff(multi_hunk_diff)

    assert parsed.stats.files_changed == 1
    assert parsed.stats.additions == 2
    hunks = parsed.hunks_by_file["app/multi.py"]
    assert len(hunks) == 2
    assert hunks[0].new_start == 1
    assert hunks[1].new_start == 11


def test_chunk_diff_with_empty_input() -> None:
    """Empty diff should return a single chunk and not be chunked."""
    chunks, was_chunked, original_count = chunk_diff("", max_lines=100)

    assert was_chunked is False
    assert len(chunks) == 1
    assert chunks[0].diff_text == ""


def test_build_finding_annotation_unmapped_file() -> None:
    """Annotation for a non-existent file should return None."""
    parsed = parse_unified_diff(SAMPLE_DIFF)

    hunk_header, code_frame, on_changed_line = build_finding_annotation(
        parsed,
        file_path="nonexistent.py",
        line=1,
    )

    assert hunk_header is None
    assert code_frame is None
    assert on_changed_line is False
