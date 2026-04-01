# pr-reviewer

[![CI](https://github.com/NoahLundSyrdal/prReviewer/actions/workflows/ci.yml/badge.svg)](https://github.com/NoahLundSyrdal/prReviewer/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

`pr-reviewer` is a product-grade CLI for LLM-powered PR review.

It ingests a unified diff, runs structured analysis, and returns developer-first feedback with severity, category, confidence, and inline code-frame context. It can also post findings directly as inline comments on GitHub PRs or GitLab MRs.

## Why this exists

Most AI code-review demos are either vague or overbuilt. `pr-reviewer` optimizes for high signal and real workflow integration:

- grounded findings tied to visible diff hunks
- strict output schema and validation
- clean terminal UX with fast local loop
- optional PR/MR comment publishing on changed lines

## Feature highlights

- Review from patch file, stdin, or staged diff (`--cached`)
- Single-pass mode (`--mode single`) and multi-pass mode (`--mode multi`)
  - `multi` runs correctness, security, and performance passes, then dedupes and merges
- Automatic chunked review for large diffs so broad PRs keep more context than a single truncated excerpt
- Cross-chunk synthesis pass for large reviews so the final summary/verdict reflects the whole PR, not just isolated chunk outputs
- Structured findings with:
  - severity: `low|medium|high`
  - category: `bug|security|performance|maintainability`
  - confidence: `0.0-1.0`
  - exact hunk code-frame annotation
- Output formats: `text`, `markdown`, `json`
- Inline comment publishing:
  - GitHub PR review comments
  - GitLab MR discussions
- Robust fallback when LLM returns malformed JSON
- Structured logging with `--verbose` and `--debug` flags

## Install

```bash
pip install git+https://github.com/NoahLundSyrdal/prReviewer.git
```

## Quickstart

Set your API key:

```bash
export PR_REVIEWER_API_KEY="your-openai-api-key"
```

Review a diff:

```bash
git diff | pr-reviewer review --stdin
```

Review staged changes with multi-pass analysis:

```bash
pr-reviewer review --cached --mode multi
```

Review a patch file:

```bash
pr-reviewer review path/to/changes.patch --mode multi --format markdown --save review.md
```

## Core usage

After installation, you can use either `pr-reviewer ...` or `python -m pr_reviewer ...`.

`pr-reviewer` also supports repo-local defaults via `.pr-reviewer.toml` or `[tool.pr-reviewer]` in `pyproject.toml`.

Optional model/provider settings:

```bash
export PR_REVIEWER_BASE_URL="https://api.openai.com/v1"
export PR_REVIEWER_MODEL="gpt-4.1-mini"
```

Compact terminal output:

```bash
pr-reviewer review examples/travelsync_demo.patch --mode multi --compact --color always
```

Enable verbose logging:

```bash
pr-reviewer --verbose review --cached
pr-reviewer --debug review --cached
```

## GitHub Action

You can add automated PR review to your repository with a GitHub Actions workflow. See [docs/github-action-setup.md](docs/github-action-setup.md) for full instructions.

Quick example — add this to `.github/workflows/pr-review.yml`:

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
      - run: pip install git+https://github.com/NoahLundSyrdal/prReviewer.git
      - run: git diff origin/${{ github.event.pull_request.base.ref }}...HEAD > /tmp/pr.patch
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

## Config files

Config is discovered from the current working directory upward in this order:

1. `.pr-reviewer.toml`
2. `pyproject.toml` with `[tool.pr-reviewer]`

Explicit CLI flags override config values. Config values override environment/default values.

Example `.pr-reviewer.toml`:

```toml
model = "gpt-4.1-mini"
mode = "multi"
max_lines = 900
format = "markdown"
color = "always"
compact = false
```

Example `pyproject.toml`:

```toml
[tool.pr-reviewer]
mode = "multi"
max_lines = 900
color = "always"
```

You can also point directly at a config file:

```bash
pr-reviewer --config /path/to/.pr-reviewer.toml review --cached
```

## Posting findings to PR/MR

Only findings mapped to changed lines are posted.

GitHub PR comments:

```bash
export GITHUB_TOKEN="ghp_xxx"
python -m pr_reviewer review --cached \
  --mode multi \
  --post github \
  --repo owner/repo \
  --pr 123
```

GitLab MR comments:

```bash
export GITLAB_TOKEN="glpat-xxx"
python -m pr_reviewer review --cached \
  --mode multi \
  --post gitlab \
  --repo group/project \
  --mr 42
```

Dry run posting:

```bash
python -m pr_reviewer review --cached --post github --repo owner/repo --pr 123 --dry-run-post
```

## Demo assets

- Real project demo diff (travelSync): [`examples/travelsync_demo.patch`](./examples/travelsync_demo.patch)
- Real project demo output (terminal): [`examples/travelsync_demo_output.txt`](./examples/travelsync_demo_output.txt)

## CLI synopsis

```text
pr-reviewer [--config FILE] [-v | --verbose] [--debug]
            review [patch] [--stdin] [--cached]
                   [--mode single|multi]
                   [--model MODEL]
                   [--max-lines N]
                   [--format text|json|markdown]
                   [--save FILE]
                   [--compact]
                   [--base-url URL]
                   [--color auto|always|never]
                   [--post github|gitlab]
                   [--repo REPO]
                   [--pr N]
                   [--mr N]
                   [--integration-token TOKEN]
                   [--integration-base-url URL]
                   [--dry-run-post]
```

`--max-lines` is the approximate per-request chunk budget. Large diffs are automatically split across multiple review calls and merged back into one result.

## Project layout

```text
pr_reviewer/
  cli.py          # command UX and orchestration
  parsing.py      # diff stats, hunk parsing, code-frame extraction
  reviewer.py     # prompt strategy, single/multi pass pipeline, dedupe
  llm.py          # provider abstraction + OpenAI-compatible client
  integrations.py # GitHub/GitLab inline comment publishing
  formatters.py   # text / markdown / json rendering
  models.py       # typed schema
```

## Development

```bash
git clone https://github.com/NoahLundSyrdal/prReviewer.git
cd prReviewer
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Run tests:

```bash
pytest -v
```

Lint:

```bash
ruff check .
```

## Known limitations

- Input is still a unified diff: chunking and cross-chunk synthesis give a coherent summary across a large patch, but the model is not given the wider codebase (imports, callers, types outside the hunk).
- Provider/model quality affects finding quality.
- Some platform APIs may reject comments if diff position changed server-side.

## Roadmap

- Repo-local config exists for CLI defaults (`.pr-reviewer.toml`); extend with explicit policy/rule text or pack files the prompts must follow.
- Add GitHub Checks / GitLab pipeline summary mode.
- Add consensus mode (compare two models, merge intersection).
