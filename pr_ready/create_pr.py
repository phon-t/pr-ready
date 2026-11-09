"""Create a pull request for a branch that passes the PR-Ready gate.

CLI-only on purpose: opening a PR writes to the remote, so it is never
exposed as a Hermes tool. Running ``hermes pr-ready create-pr`` is the
user's explicit command to open one. The gate runs first and a red gate
refuses to open anything — ``gh`` is never invoked on a failing branch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import draft_pr_body, run_gate
from .git_range import GitError, collect_repo

__all__ = ["create_pr"]

_GH_TIMEOUT = 120


def create_pr(
    cwd: str | Path = ".",
    title: str = "",
    issue: str = "",
    draft: bool = False,
    strict: bool = False,
    full: bool = False,
) -> tuple[bool, str]:
    """Open a PR for the current branch, but only if the gate passes.

    Returns (True, pr_url) on success, (False, explanation) otherwise.
    The explanation is the gate report when the gate fails.
    """
    try:
        info = collect_repo(cwd)
    except GitError as exc:
        return False, f"Could not collect repo: {exc}"

    report = run_gate(cwd, strict=strict, full=full, issue=issue)
    if not report.ok:
        return False, report.render_text()

    body = draft_pr_body(cwd, strict=strict, full=full, issue=issue)
    if title:
        pr_title = title
    elif info.commits:
        # Oldest commit first: the branch's first change is its title.
        pr_title = info.commits[-1].subject
    else:
        pr_title = info.branch.replace("-", " ").replace("/", " ").strip() or "PR"

    if shutil.which("gh") is None:
        return (
            False,
            "gh CLI not found; install it and authenticate with `gh auth login`",
        )

    cmd = ["gh", "pr", "create", "--title", pr_title, "--body", body]
    if draft:
        cmd.append("--draft")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"gh pr create failed to run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return False, f"gh pr create failed (exit {proc.returncode}): {detail[-500:]}"
    return True, proc.stdout.strip()
