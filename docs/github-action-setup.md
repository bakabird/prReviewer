# GitHub Action Setup

Add automated LLM-powered PR reviews to your repository using the `pr-reviewer` GitHub Action workflow.

## Quick Start

1. Copy `examples/workflows/pr-review.yml` into your repo's `.github/workflows/` directory.
2. Add the `PR_REVIEWER_API_KEY` secret to your repo (Settings > Secrets and variables > Actions).
3. Open a pull request — the review will run automatically.

## Secrets

| Secret | Required | Description |
|--------|----------|-------------|
| `PR_REVIEWER_API_KEY` | Yes | API key for the LLM provider (OpenAI, etc.) |
| `GITHUB_TOKEN` | Auto | Provided automatically by GitHub Actions for posting review comments |

## Customization

Edit the workflow to change:

- **Review mode**: `--mode single` (faster) or `--mode multi` (deeper, multi-pass analysis)
- **Model**: `--model gpt-4.1-mini` or any OpenAI-compatible model
- **Provider**: Set `PR_REVIEWER_BASE_URL` as an env var to use a different OpenAI-compatible API
- **Max lines**: `--max-lines 2000` to increase the chunk budget for large diffs

## Example Workflow

```yaml
name: PR Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install pr-reviewer
        run: pip install git+https://github.com/NoahLundSyrdal/prReviewer.git
      - name: Get PR diff
        run: |
          git diff origin/${{ github.event.pull_request.base.ref }}...HEAD > /tmp/pr.patch
      - name: Run review
        env:
          PR_REVIEWER_API_KEY: ${{ secrets.PR_REVIEWER_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          pr-reviewer review /tmp/pr.patch \
            --mode multi \
            --post github \
            --repo ${{ github.repository }} \
            --pr ${{ github.event.pull_request.number }}
```

## Troubleshooting

- **No comments posted**: Ensure `PR_REVIEWER_API_KEY` is set and the LLM provider is reachable.
- **Empty diff**: Make sure `fetch-depth: 0` is set in the checkout step so the full history is available.
- **Permission errors**: The workflow needs `pull-requests: write` permission to post review comments.
