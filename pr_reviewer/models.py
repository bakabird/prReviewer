from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class Category(StrEnum):
    bug = "bug"
    security = "security"
    performance = "performance"
    maintainability = "maintainability"


class Verdict(StrEnum):
    looks_good = "looks good"
    needs_attention = "needs attention"
    high_risk = "high risk"


class FindingDisposition(StrEnum):
    inline = "inline"
    summary = "summary"
    drop = "drop"


class EvidenceBasis(StrEnum):
    direct = "direct"
    inferred = "inferred"
    speculative = "speculative"
    missing_context = "missing-context"


class ReviewFinding(BaseModel):
    severity: Severity
    category: Category
    title: str = Field(min_length=3, max_length=160)
    explanation: str = Field(min_length=5, max_length=1200)
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    evidence: EvidenceBasis = EvidenceBasis.direct
    impact: str | None = Field(default=None, max_length=500)
    suggested_fix: str | None = Field(default=None, max_length=1200)
    hunk_header: str | None = Field(default=None, max_length=320)
    code_frame: str | None = Field(default=None, max_length=5000)
    on_changed_line: bool | None = None
    post_level: FindingDisposition | None = None
    filter_reason: str | None = Field(default=None, max_length=320)


class LLMReviewPayload(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    verdict: Verdict
    findings: list[ReviewFinding] = Field(default_factory=list)


class ChunkSynthesisPayload(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    verdict: Verdict


class DiffStats(BaseModel):
    files: list[str] = Field(default_factory=list)
    files_changed: int = 0
    additions: int = 0
    deletions: int = 0
    line_count: int = 0
    original_line_count: int | None = None
    truncated: bool = False
    patch_like: bool = True


class ReviewResult(LLMReviewPayload):
    model: str
    diff: DiffStats
    review_mode: str = "single"
    passes_run: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary_findings: list[ReviewFinding] = Field(default_factory=list)
    dropped_findings: list[ReviewFinding] = Field(default_factory=list)
    raw_response: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
