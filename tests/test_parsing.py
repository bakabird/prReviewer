from pr_reviewer.parsing import build_finding_annotation, parse_diff_stats, parse_unified_diff, truncate_diff


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
