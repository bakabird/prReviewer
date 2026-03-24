from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from . import __version__
from .formatters import format_review
from .llm import LLMError, OpenAICompatibleProvider, ProviderConfigError
from .parsing import read_patch_file
from .reviewer import PRReviewer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-reviewer",
        description=(
            "Review a unified diff with an LLM and emit structured, actionable PR feedback."
        ),
        epilog=dedent(
            """\
            Quick examples:
              python -m pr_reviewer review path/to/changes.diff
              git diff | python -m pr_reviewer review --stdin
              python -m pr_reviewer review --cached --mode multi --compact --color always
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser(
        "review",
        help="Analyze a diff from file, stdin, or staged git changes",
        description=(
            "Review code changes in unified diff format and return categorized findings "
            "(bug, security, performance, maintainability) plus a final verdict."
        ),
        epilog=dedent(
            """\
            Inputs are mutually exclusive:
              1) Provide [patch] file path
              2) Use --stdin
              3) Use --cached (runs: git diff --cached)
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    review_parser.add_argument(
        "patch",
        nargs="?",
        help="Path to a unified .diff/.patch file",
    )
    review_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read diff text from stdin (for example: git diff | ...)",
    )
    review_parser.add_argument(
        "--cached",
        action="store_true",
        help="Read staged changes using `git diff --cached`",
    )
    review_parser.add_argument(
        "--model",
        default=os.getenv("PR_REVIEWER_MODEL", "gpt-4.1-mini"),
        help="LLM model identifier sent to the provider",
    )
    review_parser.add_argument(
        "--mode",
        choices=["single", "multi"],
        default="single",
        help="Review strategy: single-pass or multi-pass (correctness/security/performance)",
    )
    review_parser.add_argument(
        "--max-lines",
        type=int,
        default=1200,
        help="Approximate per-request diff line budget before the review splits into chunks",
    )
    review_parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Render format for review output",
    )
    review_parser.add_argument(
        "--save",
        help="Write rendered output to a file (in addition to stdout)",
    )
    review_parser.add_argument(
        "--compact",
        action="store_true",
        help="Compact list view (one line per finding)",
    )
    review_parser.add_argument(
        "--base-url",
        default=os.getenv("PR_REVIEWER_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
        help="OpenAI-compatible API base URL (default: OpenAI v1 endpoint)",
    )
    review_parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Color mode for text output",
    )
    review_parser.add_argument(
        "--post",
        choices=["github", "gitlab"],
        help="Post findings as inline review comments to a PR/MR",
    )
    review_parser.add_argument(
        "--repo",
        help=(
            "Repository reference for posting: GitHub owner/name or GitLab project id/path"
        ),
    )
    review_parser.add_argument(
        "--pr",
        type=int,
        help="GitHub pull request number (required with --post github)",
    )
    review_parser.add_argument(
        "--mr",
        type=int,
        help="GitLab merge request IID (required with --post gitlab)",
    )
    review_parser.add_argument(
        "--integration-token",
        help="Token override for posting (otherwise uses GITHUB_TOKEN or GITLAB_TOKEN)",
    )
    review_parser.add_argument(
        "--integration-base-url",
        help="API base URL override for posting (GitHub Enterprise / self-hosted GitLab)",
    )
    review_parser.add_argument(
        "--dry-run-post",
        action="store_true",
        help="Simulate posting without making network calls",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "review":
        return run_review(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


def run_review(args: argparse.Namespace) -> int:
    validation_error = _validate_post_args(args)
    if validation_error:
        print(f"error: {validation_error}", file=sys.stderr)
        return 2

    try:
        diff_text = _resolve_diff_input(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not diff_text.strip():
        print("error: empty diff input", file=sys.stderr)
        return 2

    try:
        provider = OpenAICompatibleProvider(base_url=args.base_url)
        reviewer = PRReviewer(provider)
        result = reviewer.review(
            diff_text=diff_text,
            model=args.model,
            max_lines=args.max_lines,
            review_mode=args.mode,
        )
    except (ProviderConfigError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    color = _use_color(args.color, args.format)
    rendered = format_review(
        result,
        output_format=args.format,
        compact=args.compact,
        color=color,
    )
    print(rendered)

    if args.save:
        output_path = Path(args.save).expanduser()
        try:
            if output_path.exists() and output_path.is_dir():
                raise IsADirectoryError(f"{output_path} is a directory")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: could not save output to {output_path}: {exc}", file=sys.stderr)
            return 1

        print(f"Saved output to {output_path}", file=sys.stderr)

    if args.post:
        from .integrations import IntegrationError, post_findings

        try:
            report = post_findings(
                platform=args.post,
                result=result,
                diff_text=diff_text,
                repo=args.repo,
                pr_number=args.pr,
                mr_iid=args.mr,
                token=args.integration_token,
                base_url=args.integration_base_url,
                dry_run=args.dry_run_post,
            )
        except IntegrationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        _print_posting_report(report=report, dry_run=args.dry_run_post)

    return 0


def _resolve_diff_input(args: argparse.Namespace) -> str:
    selected_modes = sum(bool(mode) for mode in [args.stdin, args.cached, bool(args.patch)])
    if selected_modes == 0:
        raise ValueError("provide one input source: patch path, --stdin, or --cached")
    if selected_modes > 1:
        raise ValueError("choose only one input source")

    if args.stdin:
        return sys.stdin.read()

    if args.cached:
        proc = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "failed to run git diff --cached")
        return proc.stdout

    if not args.patch:
        raise ValueError("missing patch file path")

    return read_patch_file(args.patch)


def _use_color(color_mode: str, output_format: str) -> bool:
    if output_format != "text":
        return False
    if color_mode == "always":
        return True
    if color_mode == "never":
        return False
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _validate_post_args(args: argparse.Namespace) -> str | None:
    if args.dry_run_post and not args.post:
        return "--dry-run-post requires --post"

    if args.repo and not args.post:
        return "--repo requires --post"

    if args.integration_token and not args.post:
        return "--integration-token requires --post"

    if args.integration_base_url and not args.post:
        return "--integration-base-url requires --post"

    if args.pr and args.post != "github":
        return "--pr requires --post github"

    if args.mr and args.post != "gitlab":
        return "--mr requires --post gitlab"

    if not args.post:
        return None

    if not args.repo:
        return "--post requires --repo"

    if args.post == "github" and not args.pr:
        return "--post github requires --pr <number>"

    if args.post == "github" and args.mr:
        return "--post github does not accept --mr"

    if args.post == "gitlab" and not args.mr:
        return "--post gitlab requires --mr <iid>"

    if args.post == "gitlab" and args.pr:
        return "--post gitlab does not accept --pr"

    return None


def _print_posting_report(*, report, dry_run: bool) -> None:
    mode = "dry-run posted" if dry_run else "posted"
    print(
        f"{report.platform} comments {mode}: {report.posted}/{report.attempted} "
        f"(skipped: {report.skipped})",
        file=sys.stderr,
    )
    for error in report.errors[:8]:
        print(f"- {error}", file=sys.stderr)
