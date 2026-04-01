# Contributing

Thanks for your interest in contributing to `pr-reviewer`!

## Development Setup

```bash
git clone https://github.com/NoahLundSyrdal/prReviewer.git
cd prReviewer
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Running Tests

```bash
pytest -v
```

## Linting

```bash
ruff check .
ruff check --fix .   # auto-fix
```

## Pull Request Guidelines

1. Create a feature branch from `main`.
2. Write tests for new functionality.
3. Ensure all tests pass (`pytest -v`) and linting is clean (`ruff check .`).
4. Keep PRs focused — one logical change per PR.
5. Write a clear PR description explaining what changed and why.
