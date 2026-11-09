"""pr-ready-checker: a pre-PR gate. Run the gate with run_gate(); render with GateReport.render_text()/to_json()."""

from __future__ import annotations

from pathlib import Path

from . import checks, fixers, git_range, runners
from .git_range import GitError, collect_repo, pr_diff, diff_check, changed_paths
from .report import CheckResult, GateReport, Severity, build_pr_body

__all__ = ["run_gate", "GateReport", "CheckResult", "Severity", "build_pr_body"]


def run_gate(
    cwd: str | Path = ".",
    strict: bool = False,
    fix: bool = False,
    full: bool = False,
    issue: str = "",
) -> GateReport:
    """Run all pre-PR checks and return a GateReport.

    Check order: branch, clean-tree, whitespace, secrets, debug, lint,
    tests, commits, pr-body, docs-drift, ci. If ``fix`` is true, ruff
    auto-fixes are applied before the lint check runs.
    """
    try:
        info = collect_repo(cwd)
    except GitError as exc:
        return GateReport(
            results=[
                CheckResult(
                    id="repo",
                    title="Git repository",
                    severity=Severity.FAIL,
                    detail=str(exc),
                    fix="run inside a git repository, on a branch with commits",
                )
            ],
            repo=str(cwd),
            branch="",
            base="",
            strict=strict,
        )

    results: list[CheckResult] = []

    if fix:
        applied, detail = fixers.apply_ruff_fixes(info.root)
        results.append(
            CheckResult(
                id="autofix",
                title="Auto-fix (ruff)",
                severity=Severity.PASS if applied else Severity.SKIP,
                detail=detail,
                fix="" if applied else "ruff check --fix .",
            )
        )

    files, added = pr_diff(info.root, info.merge_base)
    changed = changed_paths(info.root, info.merge_base)
    ws_problems = diff_check(info.root, info.merge_base)

    _, changed_list, _untracked = git_range.status_porcelain(info.root)

    results.append(checks.check_branch(info))
    results.append(checks.check_clean_tree(info, changed_list))
    results.append(checks.check_whitespace(ws_problems))
    results.append(checks.check_secrets(added))
    results.append(checks.check_debug(added, strict=strict))
    results.append(runners.check_lint(info.root))
    results.append(runners.check_tests(info.root, changed, full=full))
    results.append(checks.check_commits(info))

    issue_ref = issue or checks.infer_issue_ref(info)
    template_path = info.root / ".github" / "PULL_REQUEST_TEMPLATE.md"
    template_text: str | None = (
        template_path.read_text(encoding="utf-8", errors="replace")
        if template_path.is_file()
        else None
    )
    results.append(checks.check_pr_body(issue_ref, template_text is not None))
    results.append(checks.check_docs_drift(changed))
    results.append(runners.check_ci(info.root))

    return GateReport(
        results=results,
        repo=str(info.root),
        branch=info.branch,
        base=info.base_ref,
        strict=strict,
    )


def draft_pr_body(
    cwd: str | Path = ".",
    strict: bool = False,
    full: bool = False,
    issue: str = "",
) -> str:
    """Run the gate and render a Markdown PR body from the report."""
    try:
        info = collect_repo(cwd)
    except GitError as exc:
        return f"Could not collect repo: {exc}"

    issue_ref = issue or checks.infer_issue_ref(info)
    template_path = info.root / ".github" / "PULL_REQUEST_TEMPLATE.md"
    template_text: str | None = (
        template_path.read_text(encoding="utf-8", errors="replace")
        if template_path.is_file()
        else None
    )
    report = run_gate(cwd, strict=strict, full=full, issue=issue)
    # info.commits is newest-first (git log order); the draft reads
    # better oldest-first.
    subjects = [c.subject for c in reversed(info.commits)]
    return build_pr_body(template_text, report, issue_ref, subjects)
