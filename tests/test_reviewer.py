import json

from pr_reviewer.reviewer import PRReviewer


SAMPLE_DIFF = """diff --git a/app/main.py b/app/main.py
index 1111111..2222222 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,2 +1,4 @@
-def reciprocal(x):
-    return 1 / x
+def reciprocal(x):
+    if x == 0:
+        return 0
+    return 1 / x
"""

BIG_DIFF = """diff --git a/app/a.py b/app/a.py
index 1111111..2222222 100644
--- a/app/a.py
+++ b/app/a.py
@@ -1,2 +1,4 @@
-def reciprocal(x):
-    return 1 / x
+def reciprocal(x):
+    if x is None:
+        return 0
+    return 1 / x
diff --git a/app/b.py b/app/b.py
index 3333333..4444444 100644
--- a/app/b.py
+++ b/app/b.py
@@ -10,2 +10,4 @@
-def serialize(user):
-    return user["id"]
+def serialize(user):
+    token = user["token"]
+    print(token)
+    return user["id"]
"""


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._idx = 0
        self.prompts: list[str] = []

    def complete_json(self, *, model: str, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        response = self._responses[self._idx]
        self._idx += 1
        return response


def test_multi_pass_review_dedupes_and_annotates_findings() -> None:
    responses = [
        json.dumps(
            {
                "summary": "Correctness review found an edge-case issue.",
                "verdict": "needs attention",
                "findings": [
                    {
                        "severity": "high",
                        "category": "bug",
                        "title": "Potential divide-by-zero semantics regression",
                        "explanation": "Returning 0 for x==0 may mask data quality issues.",
                        "file": "app/main.py",
                        "line": 2,
                        "confidence": 0.73,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "summary": "Security review found no material concerns.",
                "verdict": "looks good",
                "findings": [],
            }
        ),
        json.dumps(
            {
                "summary": "Performance pass repeated a concern and added one extra note.",
                "verdict": "needs attention",
                "findings": [
                    {
                        "severity": "high",
                        "category": "bug",
                        "title": "Potential divide-by-zero semantics regression",
                        "explanation": "This duplicate should be deduped in merged output.",
                        "file": "app/main.py",
                        "line": 2,
                        "confidence": 0.91,
                    },
                    {
                        "severity": "low",
                        "category": "performance",
                        "title": "Minor branching overhead",
                        "explanation": "An extra branch was added in the hot path.",
                        "file": "app/main.py",
                        "line": 3,
                        "confidence": 0.55,
                    },
                ],
            }
        ),
    ]

    reviewer = PRReviewer(FakeProvider(responses))
    result = reviewer.review(diff_text=SAMPLE_DIFF, model="fake", review_mode="multi")

    assert result.review_mode == "multi"
    assert result.passes_run == ["correctness", "security", "performance"]
    assert len(result.findings) == 2

    duplicate_merged = next(f for f in result.findings if "divide-by-zero" in f.title)
    assert duplicate_merged.confidence == 0.91
    assert duplicate_merged.code_frame is not None
    assert duplicate_merged.on_changed_line is True


def test_single_pass_review_splits_large_diffs_into_chunks() -> None:
    responses = [
        json.dumps(
            {
                "summary": "Chunk one found a correctness issue.",
                "verdict": "needs attention",
                "findings": [
                    {
                        "severity": "high",
                        "category": "bug",
                        "title": "None case changes reciprocal behavior",
                        "explanation": "Returning 0 for None hides invalid input.",
                        "file": "app/a.py",
                        "line": 3,
                        "confidence": 0.88,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "summary": "Chunk two found a security issue.",
                "verdict": "needs attention",
                "findings": [
                    {
                        "severity": "high",
                        "category": "security",
                        "title": "Sensitive token is printed",
                        "explanation": "Printing the token leaks secrets into logs.",
                        "file": "app/b.py",
                        "line": 12,
                        "confidence": 0.94,
                    }
                ],
            }
        ),
    ]

    provider = FakeProvider(responses)
    reviewer = PRReviewer(provider)
    result = reviewer.review(diff_text=BIG_DIFF, model="fake", review_mode="single", max_lines=11)

    assert len(provider.prompts) == 2
    assert "Chunk: 1 of 2" in provider.prompts[0]
    assert "Chunk: 2 of 2" in provider.prompts[1]
    assert result.summary.startswith("Chunked review across 2 diff chunks surfaced 2 unique findings")
    assert result.verdict.value == "high risk"
    assert len(result.findings) == 2
    assert any("split the diff into 2 chunk(s)" in warning for warning in result.warnings)


def test_multi_pass_review_handles_chunked_diffs_and_dedupes_results() -> None:
    responses = [
        json.dumps(
            {
                "summary": "Correctness chunk one found an issue.",
                "verdict": "needs attention",
                "findings": [
                    {
                        "severity": "high",
                        "category": "bug",
                        "title": "None case changes reciprocal behavior",
                        "explanation": "Returning 0 for None hides invalid input.",
                        "file": "app/a.py",
                        "line": 3,
                        "confidence": 0.83,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "summary": "Correctness chunk two found no issue.",
                "verdict": "looks good",
                "findings": [],
            }
        ),
        json.dumps(
            {
                "summary": "Security chunk one found no issue.",
                "verdict": "looks good",
                "findings": [],
            }
        ),
        json.dumps(
            {
                "summary": "Security chunk two found a leak.",
                "verdict": "high risk",
                "findings": [
                    {
                        "severity": "high",
                        "category": "security",
                        "title": "Sensitive token is printed",
                        "explanation": "Printing the token leaks secrets into logs.",
                        "file": "app/b.py",
                        "line": 12,
                        "confidence": 0.95,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "summary": "Performance chunk one repeated the concern.",
                "verdict": "needs attention",
                "findings": [
                    {
                        "severity": "high",
                        "category": "bug",
                        "title": "None case changes reciprocal behavior",
                        "explanation": "This duplicate should be deduped across chunks and passes.",
                        "file": "app/a.py",
                        "line": 3,
                        "confidence": 0.91,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "summary": "Performance chunk two found no issue.",
                "verdict": "looks good",
                "findings": [],
            }
        ),
    ]

    provider = FakeProvider(responses)
    reviewer = PRReviewer(provider)
    result = reviewer.review(diff_text=BIG_DIFF, model="fake", review_mode="multi", max_lines=11)

    assert len(provider.prompts) == 6
    assert result.passes_run == ["correctness", "security", "performance"]
    assert result.summary.startswith("Chunked multi-pass review across 2 diff chunks")
    assert len(result.findings) == 2
    assert any("Deduped 1 overlapping finding(s) across review passes and diff chunks." == warning for warning in result.warnings)
    duplicate_merged = next(f for f in result.findings if "reciprocal behavior" in f.title)
    assert duplicate_merged.confidence == 0.91
