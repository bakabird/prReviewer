## Context

The GitHub Action currently routes review requests through `auto`, `pull_request`, and `comment` trigger modes. `auto` is a compatibility router that behaves like `comment` for `issue_comment` events and like `pull_request` for PR events; `pull_request` reviews the full PR diff whenever a PR event runs; `comment` parses maintainer commands and can review either the full PR or the latest commit range.

The new behavior replaces automatic full-PR reruns with a stateful `bulk_commit` trigger. A PR opened event reviews the full diff once, then synchronize events review only the commit range from the last successfully reviewed SHA to the current head. The action also needs to run multiple configured models for the same diff and post one aggregated result.

## Goals / Non-Goals

**Goals:**

- Replace `auto` and `pull_request` with explicit `bulk_commit` and `comment` trigger modes.
- Preserve the existing command-driven `comment` workflow.
- Persist a per-PR `last_reviewed_sha` after a review completes successfully.
- Review only newly pushed commits during `bulk_commit` synchronize events.
- Run every configured model in order for each review request.
- Aggregate multi-model findings into one final review result and post comments once.
- Keep the implementation deployable as a GitHub Action without introducing an external database.

**Non-Goals:**

- Do not batch multiple commits pushed within a short time window.
- Do not run models in parallel in the first version.
- Do not post one independent review per model.
- Do not keep supporting `auto` or `pull_request` as trigger aliases.
- Do not redesign the existing `single` and `multi` review strategies.

## Decisions

### Trigger model

Support only `bulk_commit` and `comment` for the action-level `trigger` input.

- `comment` remains tied to `issue_comment` events and continues to require an authorized PR comment command.
- `bulk_commit` is tied to `pull_request` events.
- `bulk_commit` handles `opened` by reviewing the full PR diff.
- `bulk_commit` handles `synchronize` by reviewing `last_reviewed_sha...current_head`.
- Unsupported trigger values fail fast with a clear error.

Alternative considered: keep `auto` as a backward-compatible alias. This was rejected because the change intentionally removes ambiguous trigger routing and makes workflows choose either automatic commit-based review or manual comment review.

### State storage

Store per-PR review state in a hidden marker comment on the PR conversation. The marker comment should be updated in place and contain a versioned JSON payload, for example:

```md
<!-- pr-reviewer-state
{"version":1,"last_reviewed_sha":"<sha>","updated_at":"<iso8601>"}
-->
```

The state is advanced only after review generation and comment posting complete successfully. If a run fails or is cancelled before state update, the next run may repeat review for the same range, but it will not silently skip unreviewed commits.

Alternative considered: GitHub Actions cache or artifacts. This was rejected because they are not a strong fit for mutable per-PR coordination state. A repository file or branch was rejected because it requires broader write permissions and is awkward for fork PRs.

### Invalid or missing state

If no marker state exists on `opened`, the action reviews the full PR diff and records the head SHA after success.

If no marker state exists on `synchronize`, the action falls back to reviewing the full PR diff or a safe base-to-head diff, then records the head SHA after success. If the recorded SHA is no longer reachable from the current PR head due to rebase or force-push, the action also falls back to a safe full/base-to-head diff.

This fallback favors review coverage over minimizing duplicate comments.

### Multi-model execution

Add a multi-model action input named `models`, parsed as an ordered list. Keep `model` as a single-model compatibility input. When both are provided, `models` takes precedence.

For each review request:

1. Build the target diff once.
2. Run the configured models sequentially against the same diff using the selected review strategy (`single` or `multi`).
3. Collect each model's `ReviewResult`.
4. Deduplicate findings across all model results.
5. Produce one aggregated summary, verdict, warning list, and finding list.
6. Post the aggregated result once.

If any configured model fails before aggregation completes, the automated review should fail and must not advance `last_reviewed_sha`. This keeps the state contract honest: the stored SHA means the configured review policy completed for that range.

Alternative considered: post each model's result independently. This was rejected because it would duplicate inline comments, increase PR noise, and make conflicting model outputs harder to reconcile.

### Aggregation

The aggregation layer should reuse the existing finding deduplication behavior where possible. The final verdict should reflect the highest-risk model result after dedupe, and the summary should mention that multiple models were used. The final `model` label may be a comma-separated list or a synthetic value such as `aggregate(model-a,model-b)`.

The first implementation can aggregate deterministically without another LLM call. A later enhancement could add an optional synthesis pass, but it should not be required for this change.

## Risks / Trade-offs

- [Marker comment is deleted] -> Treat missing state as unknown and review a safe full/base-to-head diff.
- [State update succeeds after comments fail] -> Avoid this by updating state only after posting succeeds.
- [Review succeeds but state update fails] -> Accept duplicate review on a later run rather than risk skipping commits.
- [Force-push invalidates the stored SHA] -> Detect unreachable state and fall back to a safe full/base-to-head diff.
- [Multiple workflow runs race] -> Recommend workflow-level `concurrency` keyed by PR number; the implementation should remain safe if duplicate runs occur.
- [Multiple models increase latency and cost] -> Document the cost multiplier and encourage controlling `models`, `mode`, `exclude`, and `max_lines`.
- [One flaky model blocks state advancement] -> This is intentional for correctness in the first version; fallback semantics can be added later as a separate change.

## Migration Plan

- Update workflows that use `trigger: auto` or `trigger: pull_request` to use `trigger: bulk_commit`.
- Keep comment-triggered workflows on `trigger: comment`.
- Add `issues: write` or equivalent permission if marker comments require issue comment updates in the target repository.
- Document that `bulk_commit` synchronize reviews begin from the stored last successfully reviewed SHA and may duplicate review if state is missing or invalid.

## Open Questions

- None.
