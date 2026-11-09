"""Subprocess-running checks for the pr-ready-checker plugin.

Each check shells out to real tooling (lint, tests, CI) using argv lists,
never shell=True, with explicit timeouts and cwd pinned to the repo root.
When a tool is not configured or not available, checks SKIP rather than
reporting a false PASS.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .fixers import ruff_argv
from .report import CheckResult, Severity

_LINT_TIMEOUT = 600
_RUFF_TIMEOUT = 300
_TEST_TIMEOUT = 600
_CI_TIMEOUT = 60


def _run(
    cmd: list[str],
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run cmd in cwd, returning (returncode, stdout, stderr).

    Never raises: a missing binary yields 127, a timeout yields 124.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return (127, "", f"{cmd[0]} not found")
    except subprocess.TimeoutExpired:
        return (124, "", f"timed out after {timeout}s")
    return (proc.returncode, proc.stdout, proc.stderr)


def _tail(text: str, n: int = 15) -> list[str]:
    """Return the last n non-empty, stripped lines of text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-n:]


def _ruff_configured(repo: Path) -> bool:
    """True when ruff is explicitly configured in the repo."""
    if (repo / "ruff.toml").exists() or (repo / ".ruff.toml").exists():
        return True
    pyproject = repo / "pyproject.toml"
    try:
        if pyproject.is_file() and "[tool.ruff]" in pyproject.read_text(encoding="utf-8"):
            return True
    except OSError:
        pass
    setup_cfg = repo / "setup.cfg"
    try:
        if setup_cfg.is_file() and "[ruff]" in setup_cfg.read_text(encoding="utf-8"):
            return True
    except OSError:
        pass
    return False


def _ruff_bin(repo: Path) -> list[str] | None:
    """Locate ruff as an argv prefix (see fixers.ruff_argv)."""
    return ruff_argv(repo)


def check_lint(repo: Path) -> CheckResult:
    """Run linting: repo scripts/check if present, else ruff if configured."""
    check = repo / "scripts" / "check"
    if check.exists():
        rc, stdout, stderr = _run(
            [sys.executable, "scripts/check"], cwd=repo, timeout=_LINT_TIMEOUT
        )
        if rc == 0:
            return CheckResult(
                id="lint",
                severity=Severity.PASS,
                detail="scripts/check passed",
                items=[],
                fix="",
            )
        return CheckResult(
            id="lint",
            severity=Severity.FAIL,
            detail=f"scripts/check failed (exit {rc})",
            items=_tail(stderr or stdout),
            fix="python scripts/check",
        )

    ruff = _ruff_bin(repo)
    if ruff is not None and _ruff_configured(repo):
        rc, stdout, stderr = _run([*ruff, "check", "."], cwd=repo, timeout=_RUFF_TIMEOUT)
        if rc == 0:
            return CheckResult(
                id="lint",
                severity=Severity.PASS,
                detail="ruff check passed",
                items=[],
                fix="",
            )
        return CheckResult(
            id="lint",
            severity=Severity.FAIL,
            detail=f"ruff check failed (exit {rc})",
            items=_tail(stdout + stderr),
            fix="ruff check --fix .",
        )

    return CheckResult(
        id="lint",
        severity=Severity.SKIP,
        detail="no lint configured (no scripts/check, no ruff)",
        items=[],
        fix="add ruff or a scripts/check",
    )


def map_test_files(repo: Path, changed: list[str]) -> list[str]:
    """Map changed source files to their likely test files.

    Source file pkg/mod.py maps to tests/test_mod.py and
    tests/pkg/test_mod.py, kept only when they exist. Changed test files
    are included directly. Order is preserved, duplicates removed.
    """
    result: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)

    for path in changed:
        p = Path(path)
        posix = p.as_posix()
        if not posix.endswith(".py"):
            continue
        if posix.startswith("tests/"):
            _add(posix)
            continue
        stem = p.stem
        candidates = [f"tests/test_{stem}.py"]
        parent = p.parent.as_posix()
        if parent != ".":
            candidates.append(f"tests/{parent}/test_{stem}.py")
        for candidate in candidates:
            if (repo / candidate).is_file():
                _add(candidate)

    return result


def check_tests(repo: Path, changed: list[str], full: bool = False) -> CheckResult:
    """Run the test suite: targeted files for changed code, or all of tests/."""
    if full:
        files: list[str] = ["tests/"] if (repo / "tests").is_dir() else []
        if not files:
            return CheckResult(
                id="tests",
                severity=Severity.SKIP,
                detail="no tests/ directory",
                items=[],
                fix="",
            )
    else:
        files = map_test_files(repo, changed)
        if not files:
            if any(path.endswith(".py") for path in changed):
                return CheckResult(
                    id="tests",
                    severity=Severity.WARN,
                    detail="changed Python files have no covering test file",
                    items=[],
                    fix="add a test file, e.g. tests/test_<module>.py",
                )
            return CheckResult(
                id="tests",
                severity=Severity.SKIP,
                detail="no Python changes; nothing to test",
                items=[],
                fix="",
            )

    if (repo / "scripts" / "run_tests.sh").exists():
        cmd: list[str] = ["bash", "scripts/run_tests.sh", *files]
    else:
        cmd = [sys.executable, "-m", "pytest", "-q", *files]

    # The gate must not dirty the tree it is checking: a test run that
    # writes .pytest_cache/ or __pycache__ would fail the *next* gate run's
    # clean-tree check. PYTEST_ADDOPTS is honoured by pytest even when it is
    # launched through a wrapper script such as run_tests.sh.
    test_env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
    }
    rc, stdout, stderr = _run(cmd, cwd=repo, timeout=_TEST_TIMEOUT, env=test_env)
    if rc == 0:
        return CheckResult(
            id="tests",
            severity=Severity.PASS,
            detail=f"tests passed ({len(files)} file(s))",
            items=[],
            fix="",
        )
    # The runner itself could not start (pytest not installed in this
    # Python, or the wrapper is missing): that is "not verified", not
    # "tests failed". Report SKIP honestly instead of a false FAIL.
    if rc == 127 or "No module named pytest" in stderr:
        return CheckResult(
            id="tests",
            severity=Severity.SKIP,
            detail="pytest not available in this Python; tests not run",
            items=[],
            fix="install pytest, or run the gate with a Python that has it",
        )
    return CheckResult(
        id="tests",
        severity=Severity.FAIL,
        detail=f"tests failed (exit {rc})",
        items=_tail(stdout + stderr),
        fix="run: " + " ".join(cmd),
    )


def check_ci(repo: Path) -> CheckResult:
    """Check CI status via the gh CLI; advisory only, never FAIL."""
    if shutil.which("gh") is None:
        return CheckResult(
            id="ci",
            severity=Severity.SKIP,
            detail="gh CLI not installed; CI status unknown",
            items=[],
            fix="install gh and run: gh auth login",
        )

    rc, stdout, stderr = _run(["gh", "pr", "checks"], cwd=repo, timeout=_CI_TIMEOUT)
    if rc != 0:
        return CheckResult(
            id="ci",
            severity=Severity.SKIP,
            detail="no open PR for this branch, or gh not authenticated",
            items=[],
            fix="gh pr checks",
        )

    lines = [line.strip() for line in (stdout + stderr).splitlines() if line.strip()]

    failing = [
        line
        for line in lines
        if "fail" in line.lower() or line.startswith("X") or line.startswith("✗")
    ]
    if failing:
        return CheckResult(
            id="ci",
            severity=Severity.WARN,
            detail="CI has failing checks",
            items=failing[:10],
            fix="",
        )

    pending = [
        line for line in lines if "pending" in line or "in_progress" in line or "queued" in line
    ]
    if pending:
        return CheckResult(
            id="ci",
            severity=Severity.WARN,
            detail="CI still running",
            items=pending,
            fix="",
        )

    return CheckResult(
        id="ci",
        severity=Severity.PASS,
        detail="all CI checks passing",
        items=[],
        fix="",
    )
