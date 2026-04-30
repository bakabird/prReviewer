## Why

The current trigger model mixes compatibility routing (`auto`) with explicit workflows (`pull_request` and `comment`), which makes the action harder to reason about as review behavior grows. The next version should expose clear trigger semantics and support stronger review coverage by running multiple configured models and posting one aggregated result.

## What Changes

- **BREAKING**: Remove the `auto` trigger mode.
- **BREAKING**: Remove the `pull_request` trigger mode.
- Add a `bulk_commit` trigger mode for automatic PR review.
- Keep `comment` as the explicit manual trigger mode for maintainer-issued PR comment commands.
- In `bulk_commit`, review the full PR diff when a PR is opened.
- In `bulk_commit`, review the commit range from the last successfully reviewed SHA to the current PR head when new commits are pushed.
- Persist the last successfully reviewed SHA per PR after review and comment posting succeed.
- Add support for configuring multiple models.
- Run all configured models for a review and aggregate their findings into one final result before posting comments.
- Do not implement short-window commit batching in this change.

## Capabilities

### New Capabilities

- `bulk-commit-review`: Defines automatic PR opened/synchronize trigger behavior, last-reviewed SHA state, and multi-model aggregation for review results.

### Modified Capabilities

- None.

## Impact

- GitHub Action inputs in `action.yml`, especially trigger and model configuration.
- GitHub Action entrypoint logic in `action/run_review.py`.
- Review execution flow in the CLI and reviewer layer to support multiple models and aggregation.
- GitHub integration code for posting a single aggregated result and storing per-PR review state.
- Documentation for GitHub Action setup and trigger behavior.
- Tests covering trigger routing, SHA state handling, multi-model aggregation, and backward-incompatible trigger removal.
