# pr-reviewer

[![CI](https://github.com/NoahLundSyrdal/prReviewer/actions/workflows/ci.yml/badge.svg)](https://github.com/NoahLundSyrdal/prReviewer/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

LLM-powered code review that posts structured, actionable inline comments on your pull requests — automatically.

## Add to your repo in 30 seconds

1. Add your OpenAI API key as a repo secret: **Settings → Secrets → Actions → `OPENAI_API_KEY`**

2. Create `.github/workflows/pr-review.yml` (copy as-is; it already includes the minimum you need: `permissions`, `bakabird/prReviewer@v1.1`, and `api_key`):

```yaml
name: PR Review
on:
  pull_request:
    types: [opened, synchronize]
permissions:
  contents: read
  pull-requests: write
  issues: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: bakabird/prReviewer@v1.1
        with:
          api_key: ${{ secrets.OPENAI_API_KEY }}
          github_token: ${{ github.token }}
          trigger: bulk_commit
          mode: multi
          max_lines: '1200'
          timeout_seconds: '120'
          max_retries: '10'
          exclude: '*.lock,dist/**,node_modules/**'
```

You can also copy [examples/workflows/bulk-commit-pr-review.yml](examples/workflows/bulk-commit-pr-review.yml) as a starting point, or use [examples/workflows/bulk-commit-pr-review-tailscale.yml](examples/workflows/bulk-commit-pr-review-tailscale.yml) when your LLM endpoint is only reachable through Tailscale.

`opened` reviews the full PR diff when the PR is created. `synchronize` reviews the commit range from the last successfully reviewed SHA to the current head SHA. The action stores that SHA in a hidden PR marker comment, so automatic workflows need `issues: write` in addition to `pull-requests: write`.

If the hidden state marker comment is deleted or contains a SHA that can no longer be compared to the PR head, the next `synchronize` run falls back to a safe full PR review instead of skipping commits.

Short-window commit batching is not implemented. Each `synchronize` event is eligible for review according to the stored last reviewed SHA.

`mode: multi` runs separate correctness, security, and performance passes before merging the findings. `max_lines: '1200'` is the approximate diff-line budget per LLM request; lower it to make smaller requests, or raise it to reduce chunking for large diffs. For self-hosted or proxied providers, also tune `timeout_seconds`, `max_retries`, and optionally `max_tokens` so failed requests do not stall the workflow for too long or overrun a fragile proxy.

3. Open a pull request. That's it.

If `GET /v1/models` works but reviews still hang, add a small probe step before `bakabird/prReviewer` to verify `POST /v1/chat/completions` against a real model. The full step is documented in [docs/github-action-setup.md](docs/github-action-setup.md).

## Trigger reviews from PR comments

If you want AI review to run only when a maintainer asks for it, use an `issue_comment` workflow instead of the automatic `bulk_commit` trigger. Copy [examples/workflows/ai-pr-review.yml](examples/workflows/ai-pr-review.yml) to `.github/workflows/ai-pr-review.yml`.

The workflow stays small because the action handles command parsing, PR lookup, commit range selection, patch fetching, and GitHub posting:

```yaml
name: AI PR Review Command

on:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: read

env:
  REVIEWER_BOT_NAME: reviewer001
  REVIEWER_MODEL: your-model-name
  REVIEWER_BASE_URL: https://your-openai-compatible-endpoint/v1

jobs:
  review:
    if: ${{ github.event.issue.pull_request }}
    runs-on: ubuntu-latest
    steps:
      - uses: bakabird/prReviewer@main
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          github_token: ${{ github.token }}
          trigger: comment
          reviewer_bot_name: ${{ env.REVIEWER_BOT_NAME }}
          model: ${{ env.REVIEWER_MODEL }}
          base_url: ${{ env.REVIEWER_BASE_URL }}
          exclude: '*.lock,dist/**,node_modules/**'
```

The example responds only to PR comments from `OWNER`, `MEMBER`, or `COLLABORATOR` users, and only when the whole comment matches one of these commands:

```text
@reviewer001 full
@reviewer001 full gpt-5.4
@reviewer001 last
@reviewer001 last gpt-5.4
@reviewer001 last 2
@reviewer001 last 2 gpt-5.4
```

- `@reviewer001 full` reviews the full PR diff from base to head.
- `@reviewer001 full gpt-5.4` reviews the full PR diff with a one-off model override.
- `@reviewer001 last` reviews the latest commit.
- `@reviewer001 last gpt-5.4` reviews the latest commit with a one-off model override.
- `@reviewer001 last N` reviews the latest `N` commits as one combined diff. `N` must be an integer greater than or equal to 1.
- `@reviewer001 last N gpt-5.4` reviews the latest `N` commits with a one-off model override.

The workflow `model` input remains the default fallback model whenever the comment does not specify one.

Change the reviewer command name in one place:

```yaml
env:
  REVIEWER_BOT_NAME: reviewer001
```

The workflow uses the built-in `${{ github.token }}` for GitHub posting, so you do not need to create a separate GitHub token. Add your LLM key as a repository secret named `LLM_API_KEY` and pass it through the action's `api_key` input as shown above.

The command-triggered workflow grants `issues: read` in addition to `contents: read` and `pull-requests: write`, because `issue_comment` events are delivered through GitHub's issues API.

### Required workflow permissions

The job **must** grant the token permission to read the repo, write pull-request review comments, and create or update the hidden state marker comment. Without these permissions, the workflow can appear to succeed while GitHub API writes fail.

Add this at the **job** or **workflow** level (the quickstart example above already includes it):

```yaml
permissions:
  contents: read
  pull-requests: write
  issues: write
```

If you use `trigger: comment`, `issues: read` is sufficient for the triggering comment workflow unless that workflow also needs to update review state. If you use a custom `github_token`, ensure that token has the same scopes.

## What you get

Every PR gets reviewed with structured findings:

- **Severity**: `low` | `medium` | `high`
- **Category**: `bug` | `security` | `performance` | `maintainability`
- **Confidence**: `0.0 – 1.0`
- **Inline code context**: exact hunk annotation with suggested fixes

Findings are posted as inline review comments directly on the changed lines.

## Customize

Default mode is **`multi`**, which runs separate passes for correctness, security, and performance (best review quality). For **large PRs**, set `mode: 'single'` for **lower cost** and **faster** runs.

```yaml
- uses: bakabird/prReviewer@v1.1
  with:
    api_key: ${{ secrets.OPENAI_API_KEY }}
    mode: 'multi'              # 'single' (faster) or 'multi' (deeper: correctness + security + performance)
    model: 'gpt-4.1-mini'     # any OpenAI-compatible model
    models: ''                 # optional comma-separated ordered models; overrides model when set
    base_url: 'https://api.openai.com/v1'  # or any compatible provider
    max_lines: '1200'          # diff chunk budget per LLM call
    timeout_seconds: '120'     # provider chat completion timeout per request
    max_retries: '10'          # provider request retry budget
    max_tokens: ''             # optional completion cap; useful for fragile/self-hosted providers
    exclude: '*.lock,docs/**'  # glob patterns to skip (comma-separated)
    post_comments: 'true'      # set to 'false' to just print the review without posting
```

## Action inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `api_key` | Yes | — | API key for OpenAI (or compatible provider) |
| `github_token` | No | `${{ github.token }}` | Token for posting review comments |
| `model` | No | `gpt-4.1-mini` | Single fallback LLM model identifier |
| `models` | No | — | Ordered comma-separated models. Takes precedence over `model` |
| `mode` | No | `multi` | `single` (fast) or `multi` (deep, multi-pass) |
| `base_url` | No | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `max_lines` | No | `1200` | Max diff lines per review chunk |
| `timeout_seconds` | No | `120` | Per-request timeout in seconds for provider chat completions |
| `max_retries` | No | `10` | Retry attempts for transient provider failures |
| `max_tokens` | No | — | Optional completion-token cap sent to the provider |
| `exclude` | No | — | Comma-separated glob patterns to skip |
| `post_comments` | No | `true` | Whether to post inline PR comments |
| `trigger` | No | `bulk_commit` | `bulk_commit` or `comment` |
| `reviewer_bot_name` | No | `reviewer001` | Command name for comment-triggered reviews, without `@` |
| `allowed_author_associations` | No | `OWNER,MEMBER,COLLABORATOR` | Comma-separated GitHub author associations allowed to trigger comment reviews |

## How it works

1. Fetches the PR diff via GitHub API
2. Filters out excluded files
3. Splits large diffs into reviewable chunks
4. Sends each chunk through the LLM with a structured review prompt
5. In `multi` mode, runs separate correctness, security, and performance passes, then dedupes
6. If `models` is configured, runs each model sequentially against the same selected diff
7. Aggregates all configured model results into one final review
8. Posts findings as inline review comments on the changed lines

The removed `trigger: auto` and `trigger: pull_request` modes are rejected. `models` is all-or-nothing: if any configured model fails, the action fails, stops before posting comments, and does not advance the hidden last-reviewed SHA. Multiple models multiply latency and provider cost. If your provider sits behind Tailscale or another proxy, tune `timeout_seconds`, `max_retries`, `max_tokens`, `mode`, and `max_lines` together to avoid very long failed runs.

## CLI usage

You can also use `pr-reviewer` as a standalone CLI tool:

```bash
pip install git+https://github.com/NoahLundSyrdal/prReviewer.git
export PR_REVIEWER_API_KEY="your-api-key"

# Review a diff
git diff | pr-reviewer review --stdin --mode multi

# Review staged changes
pr-reviewer review --cached --mode multi

# Review a patch file and save markdown output
pr-reviewer review changes.patch --format markdown --save review.md

# Post comments to a GitHub PR
pr-reviewer review changes.patch --post github --repo owner/repo --pr 123
```

Run `pr-reviewer --help` for all options.

## Config files

`pr-reviewer` supports repo-local defaults via `.pr-reviewer.toml` or `[tool.pr-reviewer]` in `pyproject.toml`:

```toml
# .pr-reviewer.toml
model = "gpt-4.1-mini"
mode = "multi"
max_lines = 900
format = "markdown"
color = "always"
```

CLI flags override config values. Config values override environment defaults.

## Known limitations

- The model only sees the diff, not the full codebase (imports, callers, types outside the hunk)
- LLM quality varies by model and provider
- Some platform APIs may reject comments if the diff position changed since the review ran

## Roadmap

- Custom review rules via `.pr-reviewer.toml` policy/rule text
- GitHub Checks / GitLab pipeline summary mode
- Consensus mode (compare two models, merge intersection)
- GitLab CI integration

## Development

```bash
git clone https://github.com/NoahLundSyrdal/prReviewer.git
cd prReviewer
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -v
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT](LICENSE)
