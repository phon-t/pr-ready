"""
Hermetic tests for the pr-ready-checker Hermes Agent plugin.

Every test operates inside a temporary git repository (tmp_path) and
requires no network access, no Hermes installation, and writes nothing
outside of pytest's tmp dir.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from pr_ready import run_gate, git_range, hook as hook_mod  # noqa: E402
from pr_ready.report import Severity  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test User")
    (r / "README.md").write_text("# repo\n")
    # Keep pytest bytecode out of the tree: a gate run executes pytest,
    # and a second gate run in the same test must still see a clean tree.
    (r / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "feat: initial commit")
    return r


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _branch(repo: Path, name: str) -> None:
    _git(repo, "switch", "-c", name)


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _result(report, check_id: str):
    for res in report.results:
        if res.id == check_id:
            return res
    raise AssertionError(f"no result with id {check_id!r}")


def test_clean_branch_passes(repo: Path) -> None:
    _branch(repo, "fix/42-add-thing")
    _write(repo, "src/thing.py", "def thing() -> int:\n    return 42\n")
    _write(
        repo,
        "tests/test_thing.py",
        "from src.thing import thing\n\n\ndef test_thing():\n    assert thing() == 42\n",
    )
    _commit(repo, "feat(thing): add thing")

    report = run_gate(cwd=repo)

    assert report.ok is True
    assert report.exit_code == 0
    assert _result(report, "branch").severity == Severity.PASS
    assert _result(report, "large-files").severity == Severity.PASS
    assert _result(report, "tests").severity == Severity.PASS
    assert _result(report, "commits").severity == Severity.PASS


def test_on_main_branch_fails(repo: Path) -> None:
    _write(repo, "app.py", "x = 1\n")
    _commit(repo, "feat: work on main")

    report = run_gate(cwd=repo)

    assert report.ok is False
    assert _result(report, "branch").severity == Severity.FAIL


def test_secret_on_added_line_fails(repo: Path) -> None:
    _branch(repo, "fix/7-secret")
    _write(repo, "app.py", 'api_key = "sk-live-abcdefgh12345678"\n')
    _commit(repo, "feat: add key")

    report = run_gate(cwd=repo)

    assert report.ok is False
    secrets = _result(report, "secrets")
    assert secrets.severity == Severity.FAIL
    assert any("app.py" in item for item in secrets.items)


def test_debug_print_warns_not_fails(repo: Path) -> None:
    _branch(repo, "fix/8-debug")
    _write(repo, "app.py", 'print("hello")\n')
    _commit(repo, "feat: add print")

    default_report = run_gate(cwd=repo)
    assert _result(default_report, "debug").severity == Severity.WARN
    assert default_report.ok is True

    strict_report = run_gate(cwd=repo, strict=True)
    assert _result(strict_report, "debug").severity == Severity.FAIL
    assert strict_report.ok is False


def test_bad_commit_message_fails(repo: Path) -> None:
    _branch(repo, "fix/9-wip")
    _write(repo, "app.py", "x = 1\n")
    _commit(repo, "wip stuff")

    report = run_gate(cwd=repo)

    commits = _result(report, "commits")
    assert commits.severity == Severity.FAIL
    assert any("wip stuff" in item for item in commits.items)


def test_no_tests_warns(repo: Path) -> None:
    _branch(repo, "fix/10-notests")
    _write(repo, "lonely.py", "x = 1\n")
    _commit(repo, "feat: add lonely")

    report = run_gate(cwd=repo)

    assert _result(report, "tests").severity == Severity.WARN


def test_whitespace_fails(repo: Path) -> None:
    _branch(repo, "fix/11-ws")
    _write(repo, "app.py", "x = 1 \n")
    _commit(repo, "feat: whitespace")

    report = run_gate(cwd=repo)

    assert _result(report, "whitespace").severity == Severity.FAIL


def test_fix_strips_trailing_whitespace(repo: Path) -> None:
    _branch(repo, "fix/11-ws-fix")
    _write(repo, "app.py", "x = 1 \n\ny = 2\t\n")
    _commit(repo, "feat: whitespace")

    report = run_gate(cwd=repo, fix=True)

    assert _result(report, "autofix-whitespace").severity == Severity.PASS
    assert (repo / "app.py").read_text() == "x = 1\n\ny = 2\n"


def test_fix_whitespace_nothing_to_fix(repo: Path) -> None:
    _branch(repo, "fix/11-ws-clean")
    _write(repo, "app.py", "x = 1\n")
    _commit(repo, "feat: clean")

    report = run_gate(cwd=repo, fix=True)

    assert _result(report, "autofix-whitespace").severity == Severity.SKIP


def test_binary_file_fails_large_files(repo: Path) -> None:
    _branch(repo, "fix/12-binary")
    binary = repo / "assets" / "logo.png"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 64)
    _commit(repo, "feat: add logo")

    report = run_gate(cwd=repo)

    large = _result(report, "large-files")
    assert large.severity == Severity.FAIL
    assert any("assets/logo.png" in item and "binary" in item for item in large.items)


def test_oversized_file_warns(repo: Path) -> None:
    _branch(repo, "fix/12-big")
    _write(repo, "data/notes.txt", "x\n" * (300 * 1024 // 2))
    _commit(repo, "feat: add notes")

    report = run_gate(cwd=repo)

    large = _result(report, "large-files")
    assert large.severity == Severity.WARN
    assert any("data/notes.txt" in item for item in large.items)


def test_huge_file_fails_large_files(repo: Path) -> None:
    _branch(repo, "fix/12-huge")
    _write(repo, "data/dump.txt", "x\n" * (1100 * 1024 // 2))
    _commit(repo, "feat: add dump")

    report = run_gate(cwd=repo)

    large = _result(report, "large-files")
    assert large.severity == Severity.FAIL
    assert any("data/dump.txt" in item for item in large.items)


def _make_hermes_shim(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    plugin_dir = str(Path(__file__).resolve().parent.parent)
    # Use this test process's interpreter (the Hermes venv, which has
    # pytest), the way the real `hermes` binary runs under its own venv —
    # not the system python3, which may not have pytest at all.
    shim = bin_dir / "hermes"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {plugin_dir!r})\n"
        "from pr_ready import run_gate\n"
        "r = run_gate(cwd='.', strict=True)\n"
        "print(r.render_text())\n"
        "sys.exit(r.exit_code)\n"
    )
    shim.chmod(0o755)


def test_hook_blocks_push(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    _git(repo, "remote", "add", "origin", str(remote))

    bin_dir = tmp_path / "bin"
    _make_hermes_shim(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    # Branch carries a 4-digit issue and the change has a covering test, so
    # that once the secret is fixed the strict gate has nothing left to fail
    # on (strict promotes the no-tests and no-issue WARNs to FAIL).
    _branch(repo, "fix/1234-hook")
    _write(repo, "app.py", 'api_key = "sk-live-abcdefgh12345678"\n')
    _write(repo, "tests/test_app.py", "def test_sanity():\n    assert True\n")
    _commit(repo, "feat: add secret")

    ok, _msg = hook_mod.install_hook(repo)
    assert ok is True
    hook_path = repo / ".git" / "hooks" / "pre-push"
    assert hook_path.exists()
    assert hook_path.stat().st_mode & 0o111
    assert hook_mod.MARKER in hook_path.read_text()

    push = subprocess.run(
        ["git", "push", "origin", "fix/1234-hook"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert push.returncode != 0

    _write(repo, "app.py", "api_key = 'from env'\n")
    _commit(repo, "feat: remove secret")

    push_fixed = subprocess.run(
        ["git", "push", "origin", "fix/1234-hook"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert push_fixed.returncode == 0


def test_install_hook_refuses_foreign_hook(repo: Path) -> None:
    hook_path = repo / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\nexit 0\n")

    ok, _msg = hook_mod.install_hook(repo)
    assert ok is False
    assert hook_mod.MARKER not in hook_path.read_text()

    ok_forced, _msg2 = hook_mod.install_hook(repo, force=True)
    assert ok_forced is True
    assert hook_mod.MARKER in hook_path.read_text()


def test_remove_hook_only_removes_managed(repo: Path) -> None:
    hook_path = repo / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\nexit 0\n")

    removed_foreign, _msg = hook_mod.remove_hook(repo)
    assert removed_foreign is False
    assert hook_path.exists()
    assert hook_mod.MARKER not in hook_path.read_text()

    # Replacing a foreign hook needs --force; that is the point of the guard.
    ok, _msg2 = hook_mod.install_hook(repo, force=True)
    assert ok is True
    removed_managed, _msg3 = hook_mod.remove_hook(repo)
    assert removed_managed is True
    assert not hook_path.exists()


def test_report_json_roundtrip(repo: Path) -> None:
    _branch(repo, "fix/42-add-thing")
    _write(repo, "src/thing.py", "def thing() -> int:\n    return 42\n")
    _write(
        repo,
        "tests/test_thing.py",
        "from src.thing import thing\n\n\ndef test_thing():\n    assert thing() == 42\n",
    )
    _commit(repo, "feat(thing): add thing")

    report = run_gate(cwd=repo)
    parsed = json.loads(report.to_json())
    assert parsed["ok"] is True
    assert parsed["counts"]["failed"] == 0
    assert parsed["repo"] == str(repo)
    assert parsed["branch"] == "fix/42-add-thing"
    for entry in parsed["results"]:
        assert set(entry) == {"id", "title", "severity", "detail", "fix", "items"}


def test_pr_body_draft_links_issue(repo: Path) -> None:
    from pr_ready import draft_pr_body

    _branch(repo, "fix/1234-add-thing")
    _write(repo, "src/thing.py", "def thing() -> int:\n    return 42\n")
    _write(
        repo,
        "tests/test_thing.py",
        "from src.thing import thing\n\n\ndef test_thing():\n    assert thing() == 42\n",
    )
    _commit(repo, "feat(thing): add thing")

    body = draft_pr_body(cwd=repo)
    assert "Fixes #1234" in body
    assert "PR-READY" in body


def test_merge_commit_fails_branch_check(repo: Path) -> None:
    _branch(repo, "fix/13-merge")
    _write(repo, "src/a.py", "A = 1\n")
    _commit(repo, "feat: add a")
    _git(repo, "switch", "main")
    _write(repo, "src/b.py", "B = 2\n")
    _commit(repo, "feat: add b on main")
    _git(repo, "switch", "fix/13-merge")
    _git(repo, "merge", "--no-ff", "main", "-m", "Merge branch main")

    report = run_gate(cwd=repo)
    assert _result(report, "branch").severity is Severity.FAIL


def test_collect_repo_rejects_bad_ref(repo: Path) -> None:
    with pytest.raises(ValueError):
        git_range.merge_base(repo, "main", "--evil")


def test_infer_issue_ref_short_branch_number(repo: Path) -> None:
    from pr_ready import checks
    from pr_ready.git_range import CommitInfo, RepoInfo

    def _info(branch: str) -> RepoInfo:
        return RepoInfo(
            root=repo,
            branch=branch,
            default_branch="main",
            base_ref="main",
            merge_base="0" * 40,
            commits=[CommitInfo(sha="a" * 40, subject="feat: add thing")],
            is_clean=True,
            untracked=[],
        )

    # fix/<n>-slug accepts any issue number, not just 4+ digits.
    assert checks.infer_issue_ref(_info("fix/42-polish")) == "42"
    assert checks.infer_issue_ref(_info("fix/7-thing")) == "7"
    assert checks.infer_issue_ref(_info("fix/1234-thing")) == "1234"
    # No false positives from version-like or 3D branch names.
    assert checks.infer_issue_ref(_info("feature/v2-api")) == ""
    assert checks.infer_issue_ref(_info("fix/3d-render")) == ""


def test_ruff_found_via_python_module(repo: Path) -> None:
    # ruff is configured but not on PATH and there is no repo .venv:
    # the gate must still find it via `python -m ruff` and --fix must work.
    _branch(repo, "fix/42-greet")
    _write(repo, "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    _write(repo, "greet.py", 'import os\n\n\ndef greet(name):\n    return f"Hello, {name}!"\n')
    _write(
        repo,
        "tests/test_greet.py",
        'from greet import greet\n\n\ndef test_greet():\n    assert greet("Phon") == "Hello, Phon!"\n',
    )
    _commit(repo, "feat(greet): add greeting")

    report = run_gate(cwd=repo, fix=True)
    assert _result(report, "autofix").severity is Severity.PASS
    assert _result(report, "lint").severity is Severity.PASS
    assert "import os" not in (repo / "greet.py").read_text(encoding="utf-8")


def test_build_pr_body_fills_template_sections() -> None:
    from pr_ready.report import CheckResult, GateReport, build_pr_body

    template = "## What does this PR do?\n\n## Related Issue\n\n## How to Test\n\nrun the thing\n"
    report = GateReport(
        results=[
            CheckResult(id="branch", title="Branch", severity=Severity.PASS),
            CheckResult(id="tests", title="Tests", severity=Severity.PASS),
        ],
        repo="repo",
        branch="fix/42-thing",
        base="main",
    )
    body = build_pr_body(template, report, "42", ["feat(thing): add thing", "fix(thing): tidy"])
    assert "- feat(thing): add thing" in body
    assert "- fix(thing): tidy" in body
    assert "Fixes #42" in body
    # Non-empty sections are the author's own text and stay untouched.
    assert "run the thing" in body

    filled = "## What does this PR do?\n\nCustom text\n\n## Related Issue\n\n"
    body2 = build_pr_body(filled, report, "42", ["feat(thing): add thing"])
    assert "Custom text" in body2
    assert "- feat(thing): add thing" not in body2


def _make_gh_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = bin_dir / "gh-calls.jsonl"
    shim = bin_dir / "gh"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "log = os.environ['GH_SHIM_LOG']\n"
        "with open(log, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('https://github.com/owner/repo/pull/123')\n"
    )
    shim.chmod(0o755)
    return log_path


def test_create_pr_refuses_red_gate(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pr_ready.create_pr import create_pr

    bin_dir = tmp_path / "bin"
    log_path = _make_gh_shim(bin_dir)
    monkeypatch.setenv("GH_SHIM_LOG", str(log_path))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    _branch(repo, "fix/7-secret")
    _write(repo, "app.py", 'api_key = "<redacted>"\n')
    _commit(repo, "feat: add key")

    ok, msg = create_pr(cwd=repo)
    assert ok is False
    assert "PR-READY: FAIL" in msg
    # gh pr create was never invoked on a failing branch. (The gate's CI
    # check may itself call `gh pr checks`; only a create is forbidden.)
    calls = (
        [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        if log_path.exists()
        else []
    )
    assert not any(call[:2] == ["pr", "create"] for call in calls)


def test_create_pr_calls_gh_on_green(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pr_ready.create_pr import create_pr

    bin_dir = tmp_path / "bin"
    log_path = _make_gh_shim(bin_dir)
    monkeypatch.setenv("GH_SHIM_LOG", str(log_path))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    _branch(repo, "fix/42-add-thing")
    _write(repo, "src/thing.py", "def thing() -> int:\n    return 42\n")
    _write(
        repo,
        "tests/test_thing.py",
        "from src.thing import thing\n\n\ndef test_thing():\n    assert thing() == 42\n",
    )
    _commit(repo, "feat(thing): add thing")

    ok, url = create_pr(cwd=repo)
    assert ok is True
    assert url == "https://github.com/owner/repo/pull/123"
    calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    # The gate's CI check also calls `gh pr checks`; exactly one call
    # must be the actual `pr create`.
    creates = [call for call in calls if call[:2] == ["pr", "create"]]
    assert len(creates) == 1
    argv = creates[0]
    assert argv[argv.index("--title") + 1] == "feat(thing): add thing"
    assert "Fixes #42" in argv[argv.index("--body") + 1]
