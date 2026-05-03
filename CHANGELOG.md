# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- Added deterministic post-review finding calibration before inline comment posting.
- Added structured `evidence` and optional `impact` metadata to review findings so direct bugs, inferred risks, speculative concerns, and missing-context guesses are distinguishable.
- Added `inline`, `summary`, and `drop` finding dispositions so low-evidence findings can be retained in reports without becoming inline review noise.
- Added report metadata for filtering outcomes, including `filter_counts`, `summary_findings`, `dropped_findings`, and posting counts.
- Added review schema and prompt guidance requiring models to classify each finding's evidence basis.

### Changed

- Inline posting now only attempts findings that pass the calibration stage.
- Text, Markdown, JSON, fallback summary, and inline comment outputs now surface evidence and posting intent where useful.
- Low-confidence, speculative security, unmapped, contradicted undefined-symbol, and low-impact suggestion findings are downgraded or dropped before posting.
- Speculative or missing-context findings are summarized instead of being represented as direct inline bugs.
- Review prompts now use a two-tier evidence contract: the diff is the only source of new review targets, while file and project context may validate, disprove, or downgrade findings about changed lines.
- Prompt guidance now explicitly rejects undefined-symbol findings when file context shows the declaration, and prevents findings for unchanged context-only code.

### Tests

- Added coverage for evidence schema requirements, prompt evidence guidance, low-confidence drops, undefined-symbol context suppression, speculative security downgrades, direct bug preservation, and posting report filter counts.
- Added coverage for the prompt context-validation contract.
