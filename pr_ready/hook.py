from __future__ import annotations

import stat
import subprocess
from pathlib import Path

"""Install, remove, and inspect the real git pre-push hook used by pr-ready-checker.

Why a plain git hook instead of a Hermes plugin hook? A Hermes Agent plugin hook
is advisory: Hermes discards the hook's return value, so a plugin hook cannot
actually stop anything from happening. The enforcement point for the PR-Ready
gate must be something that genuinely blocks the operation — and for pushes,
that is git's own pre-push hook. Git aborts a push when the pre-push hook exits
non-zero, which makes it the only reliable place to run
``hermes pr-ready check --strict`` with real teeth.

This module therefore manages a small shell script installed at
``.git/hooks/pre-push`` (resolved via ``git rev-parse --git-dir``). The script
simply execs the Hermes CLI gate. Every managed hook embeds MARKER so this
plugin can distinguish hooks it owns from hooks written by other tooling and
refuse to clobber or delete anything it did not install.
"""

MARKER = "# pr-ready-checker pre-push hook (managed — do not edit by hand)"

HOOK_SCRIPT = (
    "#!/bin/sh\n"
    + f"# {MARKER}\n"
    + "# Installed by `hermes pr-ready install-hook`. Runs the PR-Ready gate before\n"
    + "# every push; a failing gate blocks the push. Bypass once with: git push --no-verify\n"
    + "# Remove with: hermes pr-ready remove-hook\n"
    + "if command -v hermes >/dev/null 2>&1; then\n"
    + "  exec hermes pr-ready check --strict\n"
    + "else\n"
    + '  echo "pr-ready-checker: hermes not found on PATH; cannot verify this push." >&2\n'
    + '  echo "pr-ready-checker: install Hermes, or remove this hook with: rm .git/hooks/pre-push" >&2\n'
    + "  exit 1\n"
    + "fi\n"
)


def _git_dir(repo: Path) -> Path:
    """Return the absolute path to the git directory containing ``repo``.

    Raises:
        RuntimeError: If ``repo`` is not inside a git work tree, or git is not
            runnable within the 30-second timeout.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"not a git repository: {repo}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"not a git repository: {repo}")
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return git_dir


def install_hook(repo: Path, force: bool = False) -> tuple[bool, str]:
    """Install the managed pre-push hook in ``repo``.

    Args:
        repo: Path to the git repository (or any directory inside it).
        force: If True, overwrite an existing pre-push hook even when it is
            not managed by pr-ready-checker.

    Returns:
        A ``(success, message)`` tuple.
    """
    hooks_dir = _git_dir(repo) / "hooks"
    path = hooks_dir / "pre-push"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
        if MARKER not in existing and not force:
            return (
                False,
                "a pre-push hook already exists and is not managed by "
                "pr-ready-checker; re-run with --force to replace it, or "
                "chain `hermes pr-ready check --strict` into it by hand",
            )

    path.write_text(HOOK_SCRIPT, encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return (True, f"installed pre-push hook at {path}")


def remove_hook(repo: Path) -> tuple[bool, str]:
    """Remove the managed pre-push hook from ``repo``.

    Refuses to touch a pre-push hook that was not installed by
    pr-ready-checker (i.e. does not contain MARKER).

    Returns:
        A ``(success, message)`` tuple.
    """
    path = _git_dir(repo) / "hooks" / "pre-push"
    if not path.exists():
        return (False, "no pre-push hook installed")

    content = path.read_text(encoding="utf-8", errors="replace")
    if MARKER not in content:
        return (
            False,
            "the pre-push hook is not managed by pr-ready-checker; refusing to remove it",
        )

    path.unlink()
    return (True, f"removed pre-push hook at {path}")


def hook_status(repo: Path) -> str:
    """Describe the current state of the pre-push hook in ``repo``.

    Returns one of ``"installed"``, ``"foreign hook present"``,
    ``"not installed"``, or ``"unknown (<error>)"``. Never raises.
    """
    try:
        path = _git_dir(repo) / "hooks" / "pre-push"
        if not path.exists():
            return "not installed"
        content = path.read_text(encoding="utf-8", errors="replace")
        if MARKER in content:
            return "installed"
        return "foreign hook present"
    except Exception as exc:  # noqa: BLE001 - status must never raise
        return f"unknown ({exc})"
