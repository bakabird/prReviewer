# GitHub Action Setup

## Quick Start

1. Go to your repo's **Settings → Secrets and variables → Actions**
2. Add a secret called `OPENAI_API_KEY` with your OpenAI API key
3. Create `.github/workflows/pr-review.yml` in your repo:

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

You can also copy `examples/workflows/bulk-commit-pr-review.yml` as a starting point, or use `examples/workflows/bulk-commit-pr-review-tailscale.yml` when your LLM endpoint is only reachable through Tailscale.

`opened` reviews the full PR diff when the PR is created. `synchronize` reviews the commit range from the last successfully reviewed SHA to the current head SHA. The action stores that SHA in a hidden PR marker comment, so automatic workflows need `issues: write` in addition to `pull-requests: write`.

If the hidden state marker comment is deleted or contains a SHA that can no longer be compared to the PR head, the next `synchronize` run falls back to a safe full PR review instead of skipping commits.

Short-window commit batching is not implemented. Each `synchronize` event is eligible for review according to the stored last reviewed SHA.

`mode: multi` runs separate correctness, security, and performance passes before merging the findings. `max_lines: '1200'` is the approximate diff-line budget per LLM request; lower it to make smaller requests, or raise it to reduce chunking for large diffs. For self-hosted or proxied providers, also tune `timeout_seconds`, `max_retries`, and optionally `max_tokens` so failed requests do not stall the workflow for too long or overrun a fragile proxy.

4. Push and open a PR — the review will run automatically.

## Probe `chat/completions` before review

If `GET /v1/models` works but reviews still time out, add a small probe step before `bakabird/prReviewer`. This verifies that the exact `POST /v1/chat/completions` path works for a real model:

```yaml
      - name: Probe chat completions
        shell: bash
        env:
          LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_PROBE_MODEL: ${{ secrets.LLM_MODEL }}
          LLM_MODELS: ${{ secrets.LLM_MODELS }}
        run: |
          python - <<'PY'
          import json
          import os
          import sys
          import urllib.error
          import urllib.request

          base_url = os.environ["LLM_BASE_URL"].rstrip("/")
          configured_models = [
              model.strip()
              for model in os.environ.get("LLM_MODELS", "").split(",")
              if model.strip()
          ]
          model = configured_models[0] if configured_models else os.environ["LLM_PROBE_MODEL"]
          payload = {
              "model": model,
              "messages": [{"role": "user", "content": "Reply with exactly OK"}],
              "temperature": 0,
              "max_tokens": 5,
              "stream": False,
          }
          request = urllib.request.Request(
              f"{base_url}/chat/completions",
              data=json.dumps(payload).encode("utf-8"),
              headers={
                  "Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
                  "Content-Type": "application/json",
              },
              method="POST",
          )

          try:
              with urllib.request.urlopen(request, timeout=45) as response:
                  body = response.read().decode("utf-8", errors="replace")
          except urllib.error.HTTPError as exc:
              detail = exc.read().decode("utf-8", errors="replace")
              print(f"chat/completions probe failed with HTTP {exc.code}", file=sys.stderr)
              print(detail[:4000], file=sys.stderr)
              raise
          except Exception as exc:
              print(f"chat/completions probe failed: {exc}", file=sys.stderr)
              raise

          data = json.loads(body)
          message = data.get("choices", [{}])[0].get("message", {}).get("content")
          if not message:
              print("chat/completions probe succeeded but no message content was returned", file=sys.stderr)
              print(body[:4000], file=sys.stderr)
              raise SystemExit(1)

          print(f"chat/completions probe ok for model {model}: {message!r}")
          PY
```

This probe prefers the first model from `LLM_MODELS` when present, which matches `prReviewer`'s sequential multi-model execution order. If `LLM_MODELS` is empty, it falls back to `LLM_PROBE_MODEL`.

If this probe succeeds but the real review still times out or your proxy starts refusing connections, the review request is likely larger or generating too much output for the provider path you are testing. In that case, lower `max_lines`, consider `mode: single`, and set `max_tokens` to a conservative cap such as `800` or `1200`.

## Triggering reviews from PR comments

Use this workflow when you only want reviews after a maintainer comments with a command such as `@reviewer001 full`, `@reviewer001 full gpt-5.4`, `@reviewer001 last`, or `@reviewer001 last 2 gpt-5.4`:

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
          mode: multi
          max_lines: '1200'
          timeout_seconds: '120'
          max_retries: '10'
          exclude: '*.lock,dist/**,node_modules/**'
```

The action ignores ordinary issue comments, non-command PR comments, and comments from users outside `OWNER`, `MEMBER`, or `COLLABORATOR` by default. The workflow `model` input is the default fallback model, and a matching comment can override it for a single run with commands like `@reviewer001 full gpt-5.4` or `@reviewer001 last 2 gpt-5.4`. The built-in `${{ github.token }}` is enough for posting review comments; store only your LLM provider key in a secret such as `LLM_API_KEY`.

For automatic reviews, configure `trigger: bulk_commit`. The removed `trigger: auto` and `trigger: pull_request` modes are rejected.

## Multiple models

Use `models` to run more than one configured model against the same selected diff:

```yaml
- uses: bakabird/prReviewer@v1.1
  with:
    api_key: ${{ secrets.OPENAI_API_KEY }}
    trigger: bulk_commit
    models: 'gpt-4.1-mini,gpt-4.1'
```

`models` takes precedence over `model`. When `models` is empty, the action falls back to `model`. Models run sequentially in the configured order, and the action posts one aggregated result after all models complete. If any configured model fails, the review fails, stops before posting, and does not advance the hidden last-reviewed SHA. Multiple models multiply latency and provider cost, so tune `models`, `mode`, `exclude`, `max_lines`, `timeout_seconds`, `max_retries`, and `max_tokens` together.

## Using a different LLM provider

Any OpenAI-compatible API works. Set `base_url` to your provider's endpoint:

```yaml
- uses: bakabird/prReviewer@v1.1
  with:
    api_key: ${{ secrets.LLM_API_KEY }}
    base_url: 'https://api.anthropic.com/v1'
    model: 'claude-3-sonnet'
```

## Excluding files

Skip generated files, lock files, or specific directories:

```yaml
- uses: bakabird/prReviewer@v1.1
  with:
    api_key: ${{ secrets.OPENAI_API_KEY }}
    exclude: '*.lock,*.min.js,dist/**,docs/**,*.generated.*'
```

## Single vs multi mode

- `single`: One balanced review pass. Faster, cheaper.
- `multi` (default): Three focused passes (correctness, security, performance), then deduplicates and merges. More thorough.

```yaml
- uses: bakabird/prReviewer@v1.1
  with:
    api_key: ${{ secrets.OPENAI_API_KEY }}
    mode: 'single'  # or 'multi'
```

## Review without posting comments

Set `post_comments: 'false'` to print the review in the action log without posting inline comments:

```yaml
- uses: bakabird/prReviewer@v1.1
  with:
    api_key: ${{ secrets.OPENAI_API_KEY }}
    post_comments: 'false'
```

## Secrets reference

| Secret | Required | Description |
|--------|----------|-------------|
| `OPENAI_API_KEY` (or any name) | Yes | Passed as `api_key` input |
| `GITHUB_TOKEN` | Auto | Automatically provided by GitHub Actions |

## Troubleshooting

- **No comments posted**: Check that `api_key` is set and the LLM provider is reachable. Look at the action logs.
- **Permission errors**: Automatic `bulk_commit` workflows need `pull-requests: write` and `issues: write` in the `permissions` block.
- **Rate limited**: The action retries with backoff, but very large PRs with `multi` mode make 3x the API calls. Try `mode: 'single'` or increase `max_lines`.
- **Probe succeeds but review times out or the proxy starts refusing connections**: The real review call is larger than the probe. Lower `max_lines`, try `mode: 'single'`, and set `max_tokens` to a conservative value such as `800` or `1200`.
- **Wrong model**: Make sure `model` matches what your `base_url` provider supports.
