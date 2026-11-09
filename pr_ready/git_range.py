"""Safe git plumbing for the pr-ready-checker Hermes Agent plugin.

Safety contract:
    * Every git invocation goes through :func:`_run_git`, which always uses
      ``subprocess.run`` with an argv list (never ``shell=True``), pins the
      working directory to the repository root, captures output, decodes it
      as text, and enforces a 30-second timeout.
    * Any caller-supplied ref/branch name is passed through
      :func:`_validate_ref` *before* git runs.  A ref that is empty, starts
      with ``-`` (option injection), or contains whitespace or ``..`` is
      rejected with ``ValueError``.  The ``...`` range separators used by
      the diff/log helpers are built internally from already-validated refs
      and the literal ``HEAD``.
    * The module has no side effects on import and performs no I/O beyond
      what its functions explicitly request.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT = 30
_MAX_FILES = 500
_MAX_ADDED_LINES = 2000

_COMMIT_SEP = "\x00\x00"


class GitError(Exception):
    """Raised when a git command fails, times out, or git is missing."""


@dataclass
class AddedLine:
    """A single line added by the diff, located in the new file."""

    path: str
    new_line: int
    text: str


@dataclass
class FileChange:
    """Per-file numstat summary of the diff."""

    path: str
    status: str
    added: int
    removed: int


@dataclass
class CommitInfo:
    """One commit in the PR range."""

    sha: str
    subject: str


@dataclass
class RepoInfo:
    """Aggregated repository state for the PR-Ready gate."""

    root: Path
    branch: str
    default_branch: str
    base_ref: str
    merge_base: str
    commits: list[CommitInfo]
    is_clean: bool
    untracked: list[str]


def _run_git(repo: Path, args: list[str]) -> str:
    """Run ``git <args>`` inside *repo* and return stdout.

    Raises:
        GitError: if git is missing, times out, or exits non-zero.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except FileNotFoundError:
        raise GitError("git not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out") from None
    if proc.returncode != 0:
        err = proc.stderr.strip()[:500]
        raise GitError(err)
    return proc.stdout


def _validate_ref(ref: str) -> None:
    """Reject refs that are empty, option-like, or contain bad characters."""
    if not ref:
        raise ValueError("ref must not be empty")
    if ref.startswith("-"):
        raise ValueError(f"ref must not start with '-': {ref!r}")
    if re.search(r"\s", ref):
        raise ValueError(f"ref must not contain whitespace: {ref!r}")
    if ".." in ref:
        raise ValueError(f"ref must not contain '..': {ref!r}")


def find_repo_root(cwd: str | Path) -> Path:
    """Return the absolute path of the repository containing *cwd*."""
    out = _run_git(Path(cwd), ["rev-parse", "--show-toplevel"])
    root = out.strip()
    if not root:
        raise GitError("could not determine repository root")
    return Path(root)


def current_branch(repo: Path) -> str:
    """Return the checked-out branch name; fail on detached HEAD."""
    out = _run_git(repo, ["branch", "--show-current"])
    branch = out.strip()
    if not branch:
        raise GitError("detached HEAD; no current branch")
    return branch


def default_branch(repo: Path) -> str:
    """Best-effort detection of the repository's default branch."""
    try:
        out = _run_git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"])
        ref = out.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            name = ref[len(prefix) :]
            if name:
                return name
    except GitError:
        pass
    for candidate in ("main", "master"):
        try:
            _run_git(repo, ["show-ref", "--verify", f"refs/heads/{candidate}"])
            return candidate
        except GitError:
            continue
    return "main"


def merge_base(repo: Path, branch: str, base: str) -> str:
    """Return the merge-base commit of *branch* and *base*."""
    _validate_ref(branch)
    _validate_ref(base)
    out = _run_git(repo, ["merge-base", base, branch])
    mb = out.strip()
    if not mb:
        raise GitError(f"no merge base found for {base} and {branch}")
    return mb


def list_commits(repo: Path, merge_base: str) -> list[CommitInfo]:
    """List commits in ``merge_base..HEAD`` (oldest not guaranteed)."""
    _validate_ref(merge_base)
    out = _run_git(
        repo,
        ["log", "--format=%H%x00%s%x00%x00", f"{merge_base}..HEAD"],
    )
    commits: list[CommitInfo] = []
    for record in out.split(_COMMIT_SEP):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x00", 1)
        sha = parts[0].strip()
        subject = parts[1] if len(parts) > 1 else ""
        if sha:
            commits.append(CommitInfo(sha=sha, subject=subject))
    return commits


def _parse_numstat(out: str) -> list[FileChange]:
    """Parse ``git diff --numstat -z`` output into FileChange records.

    With ``-z``, records are NUL-separated and the fields inside one record
    are TAB-separated: ``added<TAB>removed<TAB>path`` (a rename record carries
    a fourth field, the origin path).
    """
    records = [r for r in out.split("\x00") if r]
    changes: list[FileChange] = []
    for record in records[:_MAX_FILES]:
        fields = record.split("\t")
        if len(fields) < 3:
            continue
        added_tok, removed_tok, path = fields[0], fields[1], fields[2]
        status = "renamed" if len(fields) > 3 else ""
        binary = added_tok == "-" or removed_tok == "-"
        if binary:
            status = "binary"
            added = 0
            removed = 0
        else:
            try:
                added = int(added_tok)
                removed = int(removed_tok)
            except ValueError:
                # Malformed record; skip defensively.
                continue
            if status != "renamed":
                if added > 0 and removed == 0:
                    status = "added"
                elif removed > 0 and added == 0:
                    status = "deleted"
                else:
                    status = "modified"

        changes.append(FileChange(path=path, status=status, added=added, removed=removed))
    return changes


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _parse_unified_added(out: str) -> list[AddedLine]:
    """Collect added lines (new-file line numbers) from a unified diff."""
    added: list[AddedLine] = []
    current_path: str | None = None
    new_line = 0

    for line in out.splitlines():
        if line.startswith("+++ "):
            header = line[4:]
            if header.startswith("b/"):
                header = header[2:]
            current_path = header.strip('"')
            continue
        if line.startswith("--- "):
            continue
        match = _HUNK_RE.match(line)
        if match:
            new_line = int(match.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_path is not None and len(added) < _MAX_ADDED_LINES:
                added.append(
                    AddedLine(
                        path=current_path,
                        new_line=new_line,
                        text=line[1:],
                    )
                )
            new_line += 1
        elif line.startswith("-"):
            # Removed lines do not advance the new-file line counter.
            continue
        elif line.startswith(" "):
            # Context lines advance the new-file line counter.
            new_line += 1
    return added


def pr_diff(repo: Path, merge_base: str) -> tuple[list[FileChange], list[AddedLine]]:
    """Summarize ``merge_base...HEAD`` as per-file stats and added lines."""
    _validate_ref(merge_base)
    numstat_out = _run_git(repo, ["diff", "--numstat", "-z", f"{merge_base}...HEAD"])
    changes = _parse_numstat(numstat_out)[:_MAX_FILES]

    unified_out = _run_git(repo, ["diff", "--unified=0", f"{merge_base}...HEAD"])
    added_lines = _parse_unified_added(unified_out)[:_MAX_ADDED_LINES]

    return changes, added_lines


def status_porcelain(repo: Path) -> tuple[bool, list[str], list[str]]:
    """Return ``(is_clean, changed_paths, untracked_paths)``."""
    out = _run_git(repo, ["status", "--porcelain=v1", "-z"])
    records = [r for r in out.split("\x00") if r]

    changed: list[str] = []
    untracked: list[str] = []
    for record in records:
        xy = record[:2]
        path = record[3:]
        if not path:
            continue
        if xy == "??":
            untracked.append(path)
        else:
            changed.append(path)
    return not records, changed, untracked


def diff_check(repo: Path, merge_base: str) -> list[str]:
    """Return whitespace/conflict-marker problems found by ``diff --check``.

    ``git diff --check`` exits 1 when it finds problems and prints them to
    stdout, so a non-zero exit is the *expected* signal here, not an error:
    run git directly and return the output lines whatever the exit code.
    """
    _validate_ref(merge_base)
    try:
        proc = subprocess.run(
            ["git", "diff", "--check", f"{merge_base}...HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise GitError("git not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git diff --check timed out") from exc
    combined = (proc.stdout or "") + (proc.stderr or "")
    return [ln.strip() for ln in combined.splitlines() if ln.strip()]


def changed_paths(repo: Path, merge_base: str) -> list[str]:
    """List all paths touched by the range plus untracked files."""
    _validate_ref(merge_base)
    out = _run_git(repo, ["diff", "--name-only", f"{merge_base}...HEAD"])
    paths = [p for p in out.splitlines() if p]

    untracked_out = _run_git(repo, ["ls-files", "--others", "--exclude-standard"])
    paths.extend(p for p in untracked_out.splitlines() if p)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def collect_repo(cwd: str | Path) -> RepoInfo:
    """Collect everything the gate needs to know about the current branch."""
    root = find_repo_root(cwd)
    branch = current_branch(root)
    default = default_branch(root)
    base = merge_base(root, branch, default)
    commits = list_commits(root, base)
    is_clean, _changed, untracked = status_porcelain(root)
    try:
        _run_git(root, ["show-ref", "--verify", f"refs/remotes/origin/{default}"])
        base_ref = f"origin/{default}"
    except GitError:
        base_ref = default
    return RepoInfo(
        root=root,
        branch=branch,
        default_branch=default,
        base_ref=base_ref,
        merge_base=base,
        commits=commits,
        is_clean=is_clean,
        untracked=untracked,
    )
