from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from .git_range import diff_check

__all__ = [
    "ruff_argv",
    "ruff_available",
    "ruff_configured",
    "apply_ruff_fixes",
    "apply_whitespace_fixes",
]

_WS_RE = re.compile(r"^(.+?):(\d+): trailing whitespace\.?$")


def apply_whitespace_fixes(root: Path, merge_base: str) -> tuple[bool, str]:
    """Strip trailing whitespace from lines flagged by ``git diff --check``.

    Safety contract (same spirit as :func:`apply_ruff_fixes`):
      - Only touches lines ``git diff --check`` flagged in the PR range,
        and only removes trailing spaces/tabs on those lines.
      - Never stages, never commits. Every change is reversible via git.
      - Files are handled as bytes, so any encoding survives; a flagged
        line without trailing whitespace (stale line number on a dirty
        tree) is left alone, and a missing file is skipped.

    Returns (applied, detail) where *detail* explains what happened.
    """
    problems = diff_check(root, merge_base)
    flagged: dict[str, set[int]] = {}
    for problem in problems:
        match = _WS_RE.match(problem)
        if not match:
            continue
        flagged.setdefault(match.group(1), set()).add(int(match.group(2)))

    if not flagged:
        return (False, "no whitespace errors; nothing to fix")

    fixed_lines = 0
    fixed_files = 0
    for rel, line_numbers in sorted(flagged.items()):
        path = (root / rel).resolve()
        if not path.is_relative_to(root.resolve()):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        lines = raw.split(b"\n")
        changed = False
        for number in line_numbers:
            index = number - 1
            if 0 <= index < len(lines):
                stripped = lines[index].rstrip(b" \t")
                if stripped != lines[index]:
                    lines[index] = stripped
                    fixed_lines += 1
                    changed = True
        if changed:
            try:
                path.write_bytes(b"\n".join(lines))
            except OSError:
                continue
            fixed_files += 1

    if fixed_lines == 0:
        return (False, "flagged lines had no trailing whitespace left; nothing to fix")
    noun = "line" if fixed_lines == 1 else "lines"
    return (
        True,
        f"stripped trailing whitespace from {fixed_lines} {noun} in {fixed_files} file(s)",
    )


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str, str]:
    """Run *cmd* (argv list, never through a shell) and return (rc, stdout, stderr).

    Missing executables and timeouts are normalised to pseudo exit codes
    (127 / 124) so callers never have to handle exceptions.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return (127, "", f"{' '.join(cmd)}: not found")
    except subprocess.TimeoutExpired:
        return (124, "", f"{' '.join(cmd)}: timed out after {timeout}s")
    return (proc.returncode, proc.stdout or "", proc.stderr or "")


def ruff_argv(cwd: Path) -> list[str] | None:
    """Locate ruff for this working tree, as an argv prefix.

    Order: a ``ruff`` on PATH, the repo's own ``.venv``, then
    ``python -m ruff`` in the interpreter running this plugin. The last
    fallback matters because the Hermes venv ships ruff while ``ruff``
    is usually not on the user's PATH, so without it lint would SKIP on
    every repo that has ruff configured but no ``scripts/check``.
    """
    found = shutil.which("ruff")
    if found:
        return ["ruff"]
    posix_bin = cwd / ".venv" / "bin" / "ruff"
    if posix_bin.exists():
        return [str(posix_bin)]
    win_bin = cwd / ".venv" / "Scripts" / "ruff.exe"
    if win_bin.exists():
        return [str(win_bin)]
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0:
        return [sys.executable, "-m", "ruff"]
    return None


def ruff_available(cwd: Path) -> bool:
    """True when a ruff executable can be located for this working tree."""
    return ruff_argv(cwd) is not None


def ruff_configured(cwd: Path) -> bool:
    """True when the project opts in to ruff via a config file.

    Deliberately defensive: any read or parse problem counts as
    "not configured" rather than an error.
    """

    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    if (cwd / "ruff.toml").exists() or (cwd / ".ruff.toml").exists():
        return True

    pyproject = _read(cwd / "pyproject.toml")
    if pyproject is not None and "[tool.ruff]" in pyproject:
        return True

    setup_cfg = _read(cwd / "setup.cfg")
    if setup_cfg is not None and "[ruff]" in setup_cfg:
        return True

    tox_ini = _read(cwd / "tox.ini")
    if tox_ini is not None and "ruff" in tox_ini:
        return True

    return False


def apply_ruff_fixes(cwd: Path) -> tuple[bool, str]:
    """Apply the only automatic fixes this gate is allowed to apply.

    Safety contract:
      - Runs `ruff check --fix .` (safe, mechanical lint autofixes).
      - Runs `ruff format .` only when formatting is actually needed,
        so already-clean repositories are left byte-identical.
      - Never runs anything else, never stages, never commits.
      - Every change is reversible via git (`git diff` / `git checkout --`).

    Returns (applied, detail) where *detail* explains what happened.
    """
    argv = ruff_argv(cwd)
    if argv is None or not ruff_configured(cwd):
        return (False, "ruff not available/configured; skipped")

    check_rc, _check_out, check_err = _run([*argv, "check", "--fix", "."], cwd)
    if check_rc != 0:
        return (
            False,
            f"ruff check --fix failed (exit {check_rc}): {check_err.strip()[-300:]}",
        )

    fmt_check_rc, _fc_out, _fc_err = _run([*argv, "format", "--check", "."], cwd)
    if fmt_check_rc == 0:
        return (
            True,
            f"ruff check --fix (exit {check_rc}); ruff format skipped (already formatted)",
        )

    if fmt_check_rc == 124 or fmt_check_rc == 127:
        return (
            False,
            f"ruff format --check failed (exit {fmt_check_rc}): {_fc_err.strip()[-300:]}",
        )

    fmt_rc, _f_out, fmt_err = _run([*argv, "format", "."], cwd)
    if fmt_rc != 0:
        return (False, f"ruff format failed (exit {fmt_rc}): {fmt_err.strip()[-300:]}")

    return (True, f"ruff check --fix (exit {check_rc}); ruff format (exit {fmt_rc})")
