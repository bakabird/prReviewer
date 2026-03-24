from __future__ import annotations

from io import StringIO

import pytest

from pr_reviewer import __version__
from pr_reviewer import cli
from pr_reviewer.models import DiffStats, ReviewResult, Verdict


SAMPLE_DIFF = """diff --git a/app/main.py b/app/main.py
index 1111111..2222222 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,1 +1,1 @@
-print("old")
+print("new")
"""


def _sample_result() -> ReviewResult:
    return ReviewResult(
        summary="Looks mostly fine.",
        verdict=Verdict.looks_good,
        findings=[],
        model="fake-model",
        diff=DiffStats(files=["app/main.py"], files_changed=1, additions=1, deletions=1, line_count=6),
    )


def _install_fake_review_flow(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}

    class FakeReviewer:
        def __init__(self, provider: object) -> None:
            calls["provider"] = provider

        def review(self, **kwargs: object) -> ReviewResult:
            calls["review_kwargs"] = kwargs
            return _sample_result()

    monkeypatch.setattr(cli, "OpenAICompatibleProvider", lambda base_url=None: {"base_url": base_url})
    monkeypatch.setattr(cli, "PRReviewer", FakeReviewer)
    monkeypatch.setattr(
        cli,
        "format_review",
        lambda result, output_format, compact, color: "formatted review",
    )

    return calls


def test_main_rejects_dry_run_without_post(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["review", "--dry-run-post"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--dry-run-post requires --post" in captured.err


def test_main_rejects_repo_without_post(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["review", "--repo", "owner/repo"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--repo requires --post" in captured.err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["review", "--post", "github", "--repo", "owner/repo", "--mr", "9"], "--mr requires --post gitlab"),
        (["review", "--post", "gitlab", "--repo", "group/project", "--pr", "7"], "--pr requires --post github"),
    ],
)
def test_main_rejects_cross_platform_review_ids(
    argv: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(argv)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert message in captured.err


def test_main_rejects_multiple_input_sources(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patch_path = tmp_path / "sample.patch"
    patch_path.write_text(SAMPLE_DIFF, encoding="utf-8")

    stdin = StringIO(SAMPLE_DIFF)
    original_stdin = cli.sys.stdin
    cli.sys.stdin = stdin
    try:
        exit_code = cli.main(["review", str(patch_path), "--stdin"])
    finally:
        cli.sys.stdin = original_stdin

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "choose only one input source" in captured.err


def test_main_reads_patch_and_saves_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patch_path = tmp_path / "sample.patch"
    patch_path.write_text(SAMPLE_DIFF, encoding="utf-8")
    output_path = tmp_path / "reports" / "review.txt"

    calls = _install_fake_review_flow(monkeypatch)

    exit_code = cli.main(["review", str(patch_path), "--save", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "formatted review"
    assert f"Saved output to {output_path}" in captured.err
    assert output_path.read_text(encoding="utf-8") == "formatted review\n"
    assert calls["review_kwargs"] == {
        "diff_text": SAMPLE_DIFF,
        "model": "gpt-4.1-mini",
        "max_lines": 1200,
        "review_mode": "single",
    }


def test_main_reports_save_errors_cleanly(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patch_path = tmp_path / "sample.patch"
    patch_path.write_text(SAMPLE_DIFF, encoding="utf-8")

    _install_fake_review_flow(monkeypatch)

    exit_code = cli.main(["review", str(patch_path), "--save", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "could not save output" in captured.err
    assert str(tmp_path) in captured.err


def test_main_reports_cached_diff_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailedProcess:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: FailedProcess())

    exit_code = cli.main(["review", "--cached"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "fatal: not a git repository" in captured.err


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert __version__ in captured.out
