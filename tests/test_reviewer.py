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


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._idx = 0

    def complete_json(self, *, model: str, system_prompt: str, user_prompt: str) -> str:
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
