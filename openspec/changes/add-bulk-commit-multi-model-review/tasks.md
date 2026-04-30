## 1. Action Inputs and Trigger Routing

- [x] 1.1 Update `action.yml` to document `trigger: bulk_commit|comment` and add a `models` input while keeping `model` as the single-model fallback.
- [x] 1.2 Update `action/run_review.py` trigger validation to reject `auto` and `pull_request`.
- [x] 1.3 Preserve existing `comment` trigger behavior and tests for PR comment authorization, command parsing, model override, and skip cases.
- [x] 1.4 Add `bulk_commit` routing for `pull_request.opened` and `pull_request.synchronize` events, and skip unsupported event/action combinations.
- [x] 1.5 Update action tests for removed trigger modes, `bulk_commit` opened routing, `bulk_commit` synchronize routing, and comment-event skip behavior.

## 2. Bulk Commit Diff Selection and State

- [x] 2.1 Add GitHub helpers to read, create, and update a hidden per-PR `pr-reviewer-state` marker comment.
- [x] 2.2 Store a versioned state payload with `last_reviewed_sha` and `updated_at`.
- [x] 2.3 Resolve opened-event reviews to the full PR diff and current PR head SHA.
- [x] 2.4 Resolve synchronize-event reviews from stored `last_reviewed_sha` to current PR head SHA.
- [x] 2.5 Add fallback behavior for missing or invalid stored SHA by reviewing a safe full PR or base-to-head diff.
- [x] 2.6 Update state only after review generation and comment posting complete successfully.
- [x] 2.7 Add tests for successful state advancement, missing state fallback, invalid SHA fallback, review failure without state advancement, and posting failure without state advancement.

## 3. Multi-Model Review Execution

- [x] 3.1 Add parsing for the ordered `models` input and fallback to the existing `model` input when `models` is empty.
- [x] 3.2 Extend review execution so every configured model runs sequentially against the same selected diff.
- [x] 3.3 Ensure any configured model failure fails the overall review before posting and before bulk state advancement.
- [x] 3.4 Add unit tests for model list parsing, configured order, single-model fallback, and model failure behavior.

## 4. Aggregation and Posting

- [x] 4.1 Add an aggregation layer that combines multiple `ReviewResult` objects into one final `ReviewResult`.
- [x] 4.2 Reuse existing finding deduplication behavior for cross-model duplicate findings.
- [x] 4.3 Compute the aggregate verdict from the highest-risk model result and summarize the configured model set.
- [x] 4.4 Post only the aggregated result to GitHub.
- [x] 4.5 Add tests for duplicate finding aggregation, aggregate verdict selection, aggregate model labeling, and single final posting.

## 5. Documentation and Validation

- [x] 5.1 Update GitHub Action setup docs to replace `trigger: pull_request` and `trigger: auto` with `trigger: bulk_commit`.
- [x] 5.2 Document `models`, precedence over `model`, all-or-nothing multi-model behavior, and cost implications.
- [x] 5.3 Document required workflow permissions for posting review comments and updating the hidden state marker comment.
- [x] 5.4 Document that short-window commit batching is not implemented and each synchronize event is eligible for review.
- [x] 5.5 Run the test suite and targeted action/reviewer/integration tests for the new behavior.
