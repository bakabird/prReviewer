import json
import logging

from pr_reviewer.llm import LLMError
import pytest

from pr_reviewer.models import Category, DiffStats, ReviewFinding, ReviewResult, Severity, Verdict
from pr_reviewer.reviewer import PRReviewer, aggregate_review_results

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

    def complete_json(
        self, *, model: str, system_prompt: str, user_prompt: str, json_schema: dict | None = None
    ) -> str:
        self.prompts.append(user_prompt)
        response = self._responses[self._idx]
        self._idx += 1
        return response


class FailingProvider:
    """Provider that raises LLMError to test timeout/failure handling."""

    def __init__(self, error_message: str = "Request timed out") -> None:
        self._error_message = error_message
        self.call_count = 0

    def complete_json(
        self, *, model: str, system_prompt: str, user_prompt: str, json_schema: dict | None = None
    ) -> str:
        self.call_count += 1
        raise LLMError(self._error_message)


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


def test_single_pass_review_splits_large_diffs_into_chunks(caplog: pytest.LogCaptureFixture) -> None:
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
        json.dumps(
            {
                "summary": "Cross-chunk synthesis found two serious risks spanning correctness and secret handling.",
                "verdict": "high risk",
            }
        ),
    ]

    provider = FakeProvider(responses)
    reviewer = PRReviewer(provider)
    with caplog.at_level(logging.INFO, logger="pr_reviewer.reviewer"):
        result = reviewer.review(diff_text=BIG_DIFF, model="fake", review_mode="single", max_lines=11)

    assert len(provider.prompts) == 3
    assert "Chunk: 1 of 2" in provider.prompts[0]
    assert "Chunk: 2 of 2" in provider.prompts[1]
    assert "Synthesize the final review outcome for a chunked diff review." in provider.prompts[2]
    assert result.summary == (
        "Cross-chunk synthesis found two serious risks spanning correctness and secret handling."
    )
    assert result.verdict.value == "high risk"
    assert len(result.findings) == 2
    assert any("split the diff into 2 chunk(s)" in warning for warning in result.warnings)
    assert any("Diff chunking:" in record.message and "-> 2 chunk(s)" in record.message for record in caplog.records)


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
        json.dumps(
            {
                "summary": "Cross-chunk synthesis confirms the patch is high risk because it combines input masking with token leakage.",
                "verdict": "high risk",
            }
        ),
    ]

    provider = FakeProvider(responses)
    reviewer = PRReviewer(provider)
    result = reviewer.review(diff_text=BIG_DIFF, model="fake", review_mode="multi", max_lines=11)

    assert len(provider.prompts) == 7
    assert result.passes_run == ["correctness", "security", "performance"]
    assert "Synthesize the final review outcome for a chunked diff review." in provider.prompts[-1]
    assert result.summary == (
        "Cross-chunk synthesis confirms the patch is high risk because it combines input masking "
        "with token leakage."
    )
    assert len(result.findings) == 2
    assert any(warning == "Deduped 1 overlapping finding(s) across review passes and diff chunks." for warning in result.warnings)
    duplicate_merged = next(f for f in result.findings if "reciprocal behavior" in f.title)
    assert duplicate_merged.confidence == 0.91


def test_chunk_synthesis_falls_back_to_heuristics_when_response_is_invalid() -> None:
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
        "not json",
    ]

    reviewer = PRReviewer(FakeProvider(responses))
    result = reviewer.review(diff_text=BIG_DIFF, model="fake", review_mode="single", max_lines=11)

    assert result.summary.startswith("Chunked review across 2 diff chunks surfaced 2 unique findings")
    assert result.verdict.value == "high risk"
    assert any("[synthesis] Chunk synthesis returned non-JSON output" in warning for warning in result.warnings)


def test_single_pass_with_empty_diff() -> None:
    """Review with empty diff should produce a result with no findings."""
    responses = [
        json.dumps({
            "summary": "Nothing to review.",
            "verdict": "looks good",
            "findings": [],
        })
    ]

    reviewer = PRReviewer(FakeProvider(responses))
    result = reviewer.review(diff_text="", model="fake", review_mode="single")

    assert result.verdict.value == "looks good"
    assert len(result.findings) == 0


def test_single_pass_multi_chunk_exercises_fixed_indentation_bug_path() -> None:
    """Exercises the multi-chunk single-pass path that was broken by the indentation bug."""
    responses = [
        json.dumps({
            "summary": "Chunk one looks good.",
            "verdict": "looks good",
            "findings": [],
        }),
        json.dumps({
            "summary": "Chunk two has a note.",
            "verdict": "needs attention",
            "findings": [
                {
                    "severity": "low",
                    "category": "maintainability",
                    "title": "Consider adding a docstring",
                    "explanation": "The function serialize lacks documentation.",
                    "file": "app/b.py",
                    "line": 10,
                    "confidence": 0.60,
                }
            ],
        }),
        json.dumps({
            "summary": "Overall the PR has minor documentation gaps.",
            "verdict": "needs attention",
        }),
    ]

    provider = FakeProvider(responses)
    reviewer = PRReviewer(provider)
    result = reviewer.review(diff_text=BIG_DIFF, model="fake", review_mode="single", max_lines=11)

    # Verify the synthesis path was used (3 prompts: 2 chunks + 1 synthesis)
    assert len(provider.prompts) == 3
    assert result.verdict.value == "needs attention"
    assert result.summary == "Overall the PR has minor documentation gaps."


def test_llm_failure_surfaces_warning_instead_of_crash() -> None:
    """When the LLM fails (e.g., timeout), _run_pass catches it and returns a warning."""
    provider = FailingProvider("Request timed out")
    reviewer = PRReviewer(provider)
    result = reviewer.review(diff_text=SAMPLE_DIFF, model="fake", review_mode="single")

    # Should not crash — fallback finding should be generated
    assert result.verdict.value == "needs attention"
    assert any("Could not parse model response" in f.title for f in result.findings)
    assert provider.call_count == 1


def test_multi_pass_partial_failure() -> None:
    """If one pass fails but others succeed, results from successful passes are used."""

    class MixedProvider:
        def __init__(self):
            self.call_count = 0

        def complete_json(self, *, model, system_prompt, user_prompt, json_schema=None):
            self.call_count += 1
            if self.call_count == 1:
                # First pass succeeds
                return json.dumps({
                    "summary": "Correctness found an issue.",
                    "verdict": "needs attention",
                    "findings": [{
                        "severity": "medium",
                        "category": "bug",
                        "title": "Possible null dereference",
                        "explanation": "x could be None when x==0 returns 0.",
                        "file": "app/main.py",
                        "line": 2,
                        "confidence": 0.80,
                    }],
                })
            if self.call_count == 2:
                # Second pass fails
                raise LLMError("Rate limited")
            # Third pass succeeds
            return json.dumps({
                "summary": "No performance issues.",
                "verdict": "looks good",
                "findings": [],
            })

    reviewer = PRReviewer(MixedProvider())
    result = reviewer.review(diff_text=SAMPLE_DIFF, model="fake", review_mode="multi")

    # Should still return results from successful passes
    assert len(result.findings) >= 1
    assert "correctness" in result.passes_run
    assert "performance" in result.passes_run


def test_review_with_file_context_included_in_prompt(sample_diff: str) -> None:
    """File context should appear in the user_prompt passed to the LLM."""
    captured_prompts: list[str] = []

    class CapturingProvider:
        def complete_json(self, *, model: str, system_prompt: str, user_prompt: str, json_schema=None) -> str:
            captured_prompts.append(user_prompt)
            return '{"summary": "ok", "verdict": "looks good", "findings": []}'

    reviewer = PRReviewer(CapturingProvider())
    # sample_diff touches app/main.py — use that as the context key so it matches stats.files
    file_context = {"app/main.py": "def foo():\n    return 42\n"}
    reviewer.review(
        diff_text=sample_diff,
        model="gpt-4.1-mini",
        file_context=file_context,
    )

    assert captured_prompts, "No LLM calls were made"
    assert "app/main.py" in captured_prompts[0], "File context not injected into prompt"
    assert "def foo():" in captured_prompts[0], "File content not in prompt"
    assert "File context" in captured_prompts[0], "Context label not in prompt"


def test_review_many_runs_models_in_configured_order() -> None:
    provider = FakeProvider([
        json.dumps({"summary": "first ok", "verdict": "looks good", "findings": []}),
        json.dumps({"summary": "second ok", "verdict": "looks good", "findings": []}),
    ])
    seen_models: list[str] = []
    original_complete_json = provider.complete_json

    def capture_model(**kwargs):
        seen_models.append(kwargs["model"])
        return original_complete_json(**kwargs)

    provider.complete_json = capture_model
    reviewer = PRReviewer(provider)

    result = reviewer.review_many(diff_text=SAMPLE_DIFF, models=["model-a", "model-b"], review_mode="single")

    assert seen_models == ["model-a", "model-b"]
    assert result.model == "aggregate(model-a,model-b)"
    assert "model-a, model-b" in result.summary


def test_review_many_uses_single_model_fallback_path() -> None:
    provider = FakeProvider([
        json.dumps({"summary": "single ok", "verdict": "looks good", "findings": []}),
    ])
    reviewer = PRReviewer(provider)

    result = reviewer.review_many(diff_text=SAMPLE_DIFF, models=["fallback"], review_mode="single")

    assert result.model == "fallback"
    assert result.summary == "single ok"


def test_review_many_model_failure_blocks_aggregation() -> None:
    class ExplodingProvider:
        def complete_json(self, *, model, system_prompt, user_prompt, json_schema=None):
            if model == "bad":
                raise RuntimeError("provider exploded")
            return json.dumps({"summary": "ok", "verdict": "looks good", "findings": []})

    reviewer = PRReviewer(ExplodingProvider())

    with pytest.raises(RuntimeError):
        reviewer.review_many(diff_text=SAMPLE_DIFF, models=["ok", "bad"], review_mode="single")


def test_review_many_handled_llm_failure_blocks_aggregation() -> None:
    reviewer = PRReviewer(FailingProvider("Request timed out"))

    with pytest.raises(LLMError, match="Request timed out"):
        reviewer.review_many(diff_text=SAMPLE_DIFF, models=["bad", "other"], review_mode="single")


def test_multi_pass_continues_after_initial_provider_failure() -> None:
    class RecoveringProvider:
        def __init__(self) -> None:
            self.call_count = 0

        def complete_json(self, *, model, system_prompt, user_prompt, json_schema=None):
            self.call_count += 1
            if self.call_count == 1:
                raise LLMError("temporary outage")
            if self.call_count == 2:
                return json.dumps({
                    "summary": "Security found an issue.",
                    "verdict": "needs attention",
                    "findings": [{
                        "severity": "medium",
                        "category": "security",
                        "title": "Sensitive path remains reachable",
                        "explanation": "Later passes should still run after the provider recovers.",
                        "file": "app/main.py",
                        "line": 2,
                        "confidence": 0.80,
                    }],
                })
            return json.dumps({
                "summary": "No further issues.",
                "verdict": "looks good",
                "findings": [],
            })

    provider = RecoveringProvider()
    reviewer = PRReviewer(provider)

    result = reviewer.review(diff_text=SAMPLE_DIFF, model="fake", review_mode="multi")

    assert provider.call_count == 3
    assert "security" in result.passes_run
    assert "performance" in result.passes_run
    assert any("LLM call failed: temporary outage" in warning for warning in result.warnings)
    assert any(finding.title == "Sensitive path remains reachable" for finding in result.findings)


def test_aggregate_review_results_dedupes_and_selects_highest_risk_verdict() -> None:
    duplicate_a = ReviewFinding(
        severity=Severity.medium,
        category=Category.bug,
        title="Null value can crash request",
        explanation="The added code dereferences a value without a None check.",
        file="app/main.py",
        line=2,
        confidence=0.75,
    )
    duplicate_b = ReviewFinding(
        severity=Severity.high,
        category=Category.bug,
        title="Null value can crash the request",
        explanation="The same dereference can crash when the value is None.",
        file="app/main.py",
        line=2,
        confidence=0.92,
    )
    diff = DiffStats(files=["app/main.py"], files_changed=1, additions=1, deletions=1, line_count=6)

    result = aggregate_review_results(
        [
            ReviewResult(
                summary="first",
                verdict=Verdict.needs_attention,
                findings=[duplicate_a],
                model="model-a",
                diff=diff,
                passes_run=["general"],
            ),
            ReviewResult(
                summary="second",
                verdict=Verdict.high_risk,
                findings=[duplicate_b],
                model="model-b",
                diff=diff,
                passes_run=["general"],
            ),
        ],
        models=["model-a", "model-b"],
    )

    assert result.model == "aggregate(model-a,model-b)"
    assert result.verdict == Verdict.high_risk
    assert len(result.findings) == 1
    assert result.findings[0].severity == Severity.high
    assert any("across configured models" in warning for warning in result.warnings)


def test_aggregate_review_results_rejects_mismatched_metadata() -> None:
    first_diff = DiffStats(files=["app/a.py"], files_changed=1, additions=1, deletions=0, line_count=5)
    second_diff = DiffStats(files=["app/b.py"], files_changed=1, additions=1, deletions=0, line_count=5)

    with pytest.raises(ValueError, match="different diffs"):
        aggregate_review_results(
            [
                ReviewResult(
                    summary="first",
                    verdict=Verdict.looks_good,
                    findings=[],
                    model="model-a",
                    diff=first_diff,
                    review_mode="single",
                ),
                ReviewResult(
                    summary="second",
                    verdict=Verdict.looks_good,
                    findings=[],
                    model="model-b",
                    diff=second_diff,
                    review_mode="single",
                ),
            ],
            models=["model-a", "model-b"],
        )


def test_duplicate_finding_merge_preserves_alternative_fix() -> None:
    diff = DiffStats(files=["app/main.py"], files_changed=1, additions=1, deletions=1, line_count=6)
    first = ReviewFinding(
        severity=Severity.medium,
        category=Category.bug,
        title="Null value can crash request",
        explanation="The added code dereferences a value without a None check.",
        file="app/main.py",
        line=2,
        confidence=0.75,
        suggested_fix="Return early when value is None.",
    )
    second = ReviewFinding(
        severity=Severity.medium,
        category=Category.bug,
        title="Null value can crash the request",
        explanation="The same dereference can crash when the value is None.",
        file="app/main.py",
        line=2,
        confidence=0.80,
        suggested_fix="Validate value before calling the helper.",
    )

    result = aggregate_review_results(
        [
            ReviewResult(
                summary="first",
                verdict=Verdict.needs_attention,
                findings=[first],
                model="model-a",
                diff=diff,
            ),
            ReviewResult(
                summary="second",
                verdict=Verdict.needs_attention,
                findings=[second],
                model="model-b",
                diff=diff,
            ),
        ],
        models=["model-a", "model-b"],
    )

    assert len(result.findings) == 1
    assert "Return early" in result.findings[0].suggested_fix
    assert "Validate value" in result.findings[0].suggested_fix
    assert "Alternative:" in result.findings[0].suggested_fix
