"""Pure-diff readiness checks for the pr-ready-checker Hermes Agent plugin.

Every check in this module operates only on data that has already been
collected (repo info, added lines, whitespace output, changed paths).
No check shells out, touches the filesystem, or has import-time side
effects, so the module is safe to import in any context.
"""

from __future__ import annotations

import re

from .git_range import AddedLine, RepoInfo
from .report import CheckResult, Severity

__all__ = [
    "CONVENTIONAL_RE",
    "SECRET_PATTERNS",
    "DEBUG_PATTERNS",
    "check_branch",
    "check_clean_tree",
    "check_whitespace",
    "check_secrets",
    "check_debug",
    "check_commits",
    "check_pr_body",
    "infer_issue_ref",
    "check_docs_drift",
]

# Conventional Commits subject: type(scope)?: description
CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([\w\-/]+\))?!?:\s+\S"
)

# Secret patterns scanned against every added line.  Each pattern captures
# the suspected value in group 1 so common placeholder values can be
# filtered out.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    (
        "github-token",
        re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    (
        "generic-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
        ),
    ),
    ("private-key", re.compile(r"(\"?-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\"?)")),
    ("slack-token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")),
]

# Placeholder values that are almost certainly not real secrets.
_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"xxx", "changeme", "example", "test", "placeholder"}
)

# Debug / hygiene patterns scanned against every added line.
DEBUG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("print-call", re.compile(r"^\s*(?:print|pprint)\s*\(")),
    ("console-call", re.compile(r"console\.(?:log|debug|info|warn|error|trace)\s*\(")),
    ("bare-except", re.compile(r"^\s*except\s*:")),
    ("broad-except", re.compile(r"^\s*except\s+(?:Exception|BaseException)\b")),
    ("todo-marker", re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b")),
]

_MAX_ITEMS = 10

_BRANCH_ISSUE_RE = re.compile(r"(?:^|[/-])(\d{4,})\b")
# fix/<n>-slug, hotfix/<n>-slug, ...: any issue number, not just 4+ digits.
# The 4-digit floor above stays as the fallback for other branch shapes
# (it avoids matching version/date fragments in free-form branch names).
_BRANCH_FIX_RE = re.compile(r"(?:^|/)(?:fix|hotfix|bugfix|issue)[-/](\d+)\b")
_COMMIT_ISSUE_RE = re.compile(r"(?i)(?:Fixes|Closes|Resolves)\s+#(\d+)")


def _is_placeholder(value: str | None) -> bool:
    """True when a matched value is a well-known placeholder, not a secret."""
    if value is None:
        return False
    return value.strip().lower() in _PLACEHOLDER_VALUES


def _match_secret(line: str) -> str | None:
    """Return the pattern name for the first secret match on *line*, if any."""
    for name, pattern in SECRET_PATTERNS:
        match = pattern.search(line)
        if match and not _is_placeholder(match.group(1)):
            return name
    return None


def _match_debug(line: str) -> str | None:
    """Return the pattern name for the first debug match on *line*, if any."""
    for name, pattern in DEBUG_PATTERNS:
        if pattern.search(line):
            return name
    return None


def _clip(text: str, limit: int) -> str:
    """Trim *text* to at most *limit* characters."""
    trimmed = text.strip()
    return trimmed if len(trimmed) <= limit else trimmed[: limit - 1] + "…"


def check_branch(info: RepoInfo) -> CheckResult:
    """Ensure the PR is being prepared on a non-default, committed branch."""
    branch = info.branch
    base = info.base_ref

    if branch == info.default_branch:
        return CheckResult(
            id="branch",
            title="Branch",
            severity=Severity.FAIL,
            detail=f"on default branch {branch}",
            fix="git switch -c <name>",
            items=[],
        )

    if not info.commits:
        return CheckResult(
            id="branch",
            title="Branch",
            severity=Severity.FAIL,
            detail=f"no commits vs {base}",
            fix="commit your change first",
            items=[],
        )

    merge_subjects = [c.subject for c in info.commits if c.subject.startswith("Merge ")]
    if merge_subjects:
        return CheckResult(
            id="branch",
            title="Branch",
            severity=Severity.FAIL,
            detail=f"{len(merge_subjects)} merge commit(s) in the range",
            fix="git rebase <base>",
            items=merge_subjects[:_MAX_ITEMS],
        )

    return CheckResult(
        id="branch",
        title="Branch",
        severity=Severity.PASS,
        detail=f"{len(info.commits)} commit(s) on {branch} vs {base}",
        fix="",
        items=[],
    )


def check_clean_tree(info: RepoInfo, changed: list[str] | None = None) -> CheckResult:
    """Ensure the working tree has no uncommitted or untracked changes."""
    changed = list(changed or [])

    if info.is_clean and not changed and not info.untracked:
        return CheckResult(
            id="clean-tree",
            title="Clean tree",
            severity=Severity.PASS,
            detail="working tree clean",
            fix="",
            items=[],
        )

    offending = changed + list(info.untracked)
    detail = (
        f"{len(info.untracked)} untracked and {len(changed)} changed path(s)"
        if info.untracked or changed
        else "working tree is dirty"
    )
    fix = "git add -A && git commit -m ..." if changed else "git stash -u"
    return CheckResult(
        id="clean-tree",
        title="Clean tree",
        severity=Severity.FAIL,
        detail=detail,
        fix=fix,
        items=offending[:_MAX_ITEMS],
    )


def check_whitespace(problems: list[str]) -> CheckResult:
    """Check the output lines of ``git diff --check`` for whitespace errors."""
    if problems:
        return CheckResult(
            id="whitespace",
            title="Whitespace",
            severity=Severity.FAIL,
            detail=f"{len(problems)} whitespace error(s)",
            fix="git diff --check, then fix trailing whitespace",
            items=problems[:_MAX_ITEMS],
        )
    return CheckResult(
        id="whitespace",
        title="Whitespace",
        severity=Severity.PASS,
        detail="no whitespace errors",
        fix="",
        items=[],
    )


def check_secrets(added: list[AddedLine]) -> CheckResult:
    """Scan added lines for potential secrets."""
    hits: list[str] = []
    for line in added:
        name = _match_secret(line.text)
        if name is not None:
            hits.append(f"{line.path}:{line.new_line}: {name}")
            if len(hits) >= _MAX_ITEMS:
                break

    if hits:
        return CheckResult(
            id="secrets",
            title="Secrets",
            severity=Severity.FAIL,
            detail=f"{len(hits)} potential secret(s) on added lines",
            fix="remove the secret from the diff; rotate it if it was ever pushed",
            items=hits,
        )
    return CheckResult(
        id="secrets",
        title="Secrets",
        severity=Severity.PASS,
        detail="no secrets on added lines",
        fix="",
        items=[],
    )


def check_debug(added: list[AddedLine], strict: bool = False) -> CheckResult:
    """Scan added lines for debug prints, broad excepts and TODO markers."""
    hits: list[str] = []
    for line in added:
        name = _match_debug(line.text)
        if name is not None:
            hits.append(f"{line.path}:{line.new_line}: {name}: {_clip(line.text, 80)}")
            if len(hits) >= _MAX_ITEMS:
                break

    if hits:
        return CheckResult(
            id="debug",
            title="Debug hygiene",
            severity=Severity.FAIL if strict else Severity.WARN,
            detail=f"{len(hits)} debug/hygiene issue(s) on added lines",
            fix="remove debug prints / resolve TODOs before opening the PR",
            items=hits,
        )
    return CheckResult(
        id="debug",
        title="Debug hygiene",
        severity=Severity.PASS,
        detail="no debug prints or TODO markers on added lines",
        fix="",
        items=[],
    )


def check_commits(info: RepoInfo) -> CheckResult:
    """Ensure every commit subject follows Conventional Commits."""
    bad: list[str] = []
    for commit in info.commits:
        subject = commit.subject
        if not CONVENTIONAL_RE.match(subject):
            bad.append(subject)
            if len(bad) >= _MAX_ITEMS:
                break

    if bad:
        return CheckResult(
            id="commits",
            title="Commit subjects",
            severity=Severity.FAIL,
            detail=f"{len(bad)} commit subject(s) not Conventional Commits",
            fix="git rebase -i <base> and rewrite subjects as type(scope): description",
            items=bad,
        )
    return CheckResult(
        id="commits",
        title="Commit subjects",
        severity=Severity.PASS,
        detail=f"all {len(info.commits)} commit subject(s) follow Conventional Commits",
        fix="",
        items=[],
    )


def check_pr_body(issue_ref: str, template_found: bool) -> CheckResult:
    """Check that the drafted PR body will link an issue."""
    suffix = (
        "PR template found"
        if template_found
        else "no PR template in repo; a default body will be drafted"
    )
    if issue_ref:
        return CheckResult(
            id="pr-body",
            title="PR body",
            severity=Severity.PASS,
            detail=f"links Fixes #{issue_ref} ({suffix})",
            fix="",
            items=[],
        )
    return CheckResult(
        id="pr-body",
        title="PR body",
        severity=Severity.WARN,
        detail=f"no Fixes # reference found (branch/commit gave no issue number) ({suffix})",
        fix="re-run with --issue N or name the branch fix/<issue>-...",
        items=[],
    )


def infer_issue_ref(info: RepoInfo) -> str:
    """Infer an issue number from the branch name, then commit subjects."""
    match = _BRANCH_FIX_RE.search(info.branch)
    if match:
        return match.group(1)

    match = _BRANCH_ISSUE_RE.search(info.branch)
    if match:
        return match.group(1)

    for commit in info.commits:
        match = _COMMIT_ISSUE_RE.search(commit.subject)
        if match:
            return match.group(1)

    return ""


def _is_config_path(path: str) -> bool:
    """True when *path* looks like a configuration file."""
    basename = path.rsplit("/", 1)[-1]
    return (
        basename in {"config.yaml", "cli-config.yaml"}
        or path.endswith("config_backend.py")
        or "/config" in path
    )


def _is_test_path(path: str) -> bool:
    """True when *path* looks like a test file."""
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]
    return (
        lowered.startswith("tests/")
        or "/tests/" in lowered
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or basename.endswith("test.py")
    )


def check_docs_drift(changed: list[str]) -> CheckResult:
    """Warn when config or tools changed but docs/examples did not."""
    config_changed = any(_is_config_path(p) for p in changed)
    example_updated = "cli-config.yaml.example" in changed

    config_drift = config_changed and not example_updated
    config_detail = "config changed without updating cli-config.yaml.example"
    config_fix = "update cli-config.yaml.example for new/changed config keys"

    tools_changed = any(p.startswith("tools/") for p in changed)
    docs_updated = any(p.startswith("docs/") for p in changed)
    tests_updated = any(_is_test_path(p) for p in changed)
    tools_drift = tools_changed and not docs_updated and not tests_updated
    tools_detail = "tools/ changed without docs/ or test changes"
    tools_fix = "update tool descriptions/schemas and docs if behaviour changed"

    if config_drift and tools_drift:
        return CheckResult(
            id="docs-drift",
            title="Docs/config drift",
            severity=Severity.WARN,
            detail=f"{config_detail}; {tools_detail}",
            fix=f"{config_fix}; {tools_fix}",
            items=[],
        )
    if config_drift:
        return CheckResult(
            id="docs-drift",
            title="Docs/config drift",
            severity=Severity.WARN,
            detail=config_detail,
            fix=config_fix,
            items=[],
        )
    if tools_drift:
        return CheckResult(
            id="docs-drift",
            title="Docs/config drift",
            severity=Severity.WARN,
            detail=tools_detail,
            fix=tools_fix,
            items=[],
        )
    return CheckResult(
        id="docs-drift",
        title="Docs/config drift",
        severity=Severity.PASS,
        detail="no docs/config drift detected",
        fix="",
        items=[],
    )
