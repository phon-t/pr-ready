"""pr-ready-checker — a pre-PR gate for the Hermes Agent.

This plugin wires the ``pr_ready`` package into Hermes in three ways:

* a Hermes tool (``pr_ready_check``) the agent can call before committing,
  pushing, or opening a pull request;
* a ``hermes pr-ready`` CLI command with ``check``, ``install-hook``,
  ``remove-hook``, ``status`` and ``create-pr`` subcommands;
* a ``/pr-ready`` slash command;
* a git pre-push hook installer that enforces the gate on every push.

The gate checks, in order: branch hygiene, a clean working tree, trailing
whitespace, secrets and debug prints on added lines, lint (ruff), scoped
tests, Conventional Commits, a drafted PR body, docs/config drift, and CI
status.  It returns a JSON gate report; the branch is PR-ready only when
the report says ``ok``.

The plugin never commits, never pushes, and never opens a pull request on
its own.  The only writes it can perform are via ``--fix``, which runs
exactly ``ruff --fix`` and ``ruff format`` — nothing else is ever modified.
``hermes pr-ready create-pr`` is the one explicit command that opens a PR,
and it refuses unless the gate passes first.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

try:
    # Normal case: Hermes loads this directory as a package.
    from .pr_ready import draft_pr_body, hook as hook_mod, run_gate
    from .pr_ready.create_pr import create_pr as create_pr_fn
    from .pr_ready.report import GateReport
except ImportError:
    # Fallback: imported as a top-level module (e.g. pytest resolving the
    # plugin root as a package). The absolute imports work once this
    # directory is on sys.path, which is how the fallback is reached.
    from pr_ready import draft_pr_body, hook as hook_mod, run_gate
    from pr_ready.create_pr import create_pr as create_pr_fn
    from pr_ready.report import GateReport

PR_READY_CHECK_SCHEMA: dict[str, Any] = {
    "name": "pr_ready_check",
    "description": (
        "Check whether the current git branch is ready for a pull request: "
        "branch hygiene, clean tree, whitespace, secrets and debug prints on "
        "added lines, lint, scoped tests, Conventional Commits, PR-body "
        "draft, docs/config drift, and CI status. Returns a JSON gate "
        "report; the branch is PR-ready only when the report says ok. Use "
        "before committing, pushing, or opening a PR."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the git repository (default: the agent workspace, .)",
            },
            "strict": {
                "type": "boolean",
                "description": "Promote warnings to failures (default false)",
            },
            "fix": {
                "type": "boolean",
                "description": (
                    "Apply safe automatic fixes first (ruff --fix / ruff format only; default false)"
                ),
            },
            "full": {
                "type": "boolean",
                "description": (
                    "Run the full test suite instead of only tests for changed files (default false)"
                ),
            },
            "issue": {
                "type": "string",
                "description": (
                    "Issue number to link as Fixes #N in the PR-body draft "
                    "(default: inferred from branch/commit)"
                ),
            },
            "format": {
                "type": "string",
                "enum": ["text", "json"],
                "description": "Report format (default text)",
            },
        },
        "required": [],
    },
}


def _tool_handler(args: dict) -> str:
    """Handle a ``pr_ready_check`` tool call; never raise."""
    try:
        path = str(args.get("path") or ".")
        strict = bool(args.get("strict", False))
        fix = bool(args.get("fix", False))
        full = bool(args.get("full", False))
        issue = str(args.get("issue") or "")
        report_format = str(args.get("format") or "text")

        report: GateReport = run_gate(cwd=path, strict=strict, fix=fix, full=full, issue=issue)

        if report_format == "json":
            return report.to_json()

        text = report.render_text()
        draft = draft_pr_body(path, strict=strict, full=full, issue=issue)
        return text + "\n\n--- PR body draft ---\n" + draft
    except Exception as exc:  # never raise out of a tool handler
        return f"pr-ready-checker failed: {exc}"


def _setup_cli(subparser: Any) -> None:
    """Register the ``hermes pr-ready`` subcommands."""
    commands = subparser.add_subparsers(dest="pr_ready_action")

    check = commands.add_parser("check", help="Run the PR-Ready gate on the current branch")
    check.add_argument("--path", default=".", help="Path to the git repository")
    check.add_argument("--strict", action="store_true", help="Promote warnings to failures")
    check.add_argument(
        "--fix", action="store_true", help="Apply safe fixes (ruff --fix / ruff format only)"
    )
    check.add_argument(
        "--full", action="store_true", help="Run the full test suite instead of scoped tests"
    )
    check.add_argument("--issue", default="", help="Issue number to link as Fixes #N")
    check.add_argument("--json", action="store_true", help="Emit the report as JSON")
    check.add_argument("--draft-pr", action="store_true", help="Also print a PR-body draft")

    install = commands.add_parser(
        "install-hook", help="Install the pre-push hook that enforces the gate"
    )
    install.add_argument("--path", default=".", help="Path to the git repository")
    install.add_argument("--force", action="store_true", help="Overwrite an existing pre-push hook")

    remove = commands.add_parser(
        "remove-hook", help="Remove the pre-push hook installed by this plugin"
    )
    remove.add_argument("--path", default=".", help="Path to the git repository")

    status = commands.add_parser(
        "status", help="Show hook status and current branch (does not run the gate)"
    )
    status.add_argument("--path", default=".", help="Path to the git repository")

    create = commands.add_parser(
        "create-pr",
        help="Open a PR for the current branch, but only if the gate passes",
    )
    create.add_argument("--path", default=".", help="Path to the git repository")
    create.add_argument("--title", default="", help="PR title (default: first commit subject)")
    create.add_argument("--issue", default="", help="Issue number to link as Fixes #N")
    create.add_argument("--draft", action="store_true", help="Open the PR as a draft")
    create.add_argument("--strict", action="store_true", help="Promote warnings to failures")
    create.add_argument(
        "--full", action="store_true", help="Run the full test suite instead of scoped tests"
    )


def _cli_command(args: Any) -> int:
    """Dispatch a ``hermes pr-ready`` invocation."""
    action = getattr(args, "pr_ready_action", None)

    if not action:
        print("Usage: hermes pr-ready {check|install-hook|remove-hook|status|create-pr}")
        return 2

    if action == "check":
        report: GateReport = run_gate(
            cwd=args.path,
            strict=args.strict,
            fix=args.fix,
            full=args.full,
            issue=args.issue,
        )
        print(report.to_json() if args.json else report.render_text())
        if args.draft_pr:
            print(
                "\n--- PR body draft ---\n"
                + draft_pr_body(args.path, strict=args.strict, full=args.full, issue=args.issue)
            )
        return report.exit_code

    if action == "install-hook":
        ok, msg = hook_mod.install_hook(Path(args.path), force=args.force)
        print(msg)
        return 0 if ok else 1

    if action == "remove-hook":
        ok, msg = hook_mod.remove_hook(Path(args.path))
        print(msg)
        return 0 if ok else 1

    if action == "create-pr":
        ok, msg = create_pr_fn(
            args.path,
            title=args.title,
            issue=args.issue,
            draft=args.draft,
            strict=args.strict,
            full=args.full,
        )
        print(msg)
        return 0 if ok else 1

    if action == "status":
        print(f"hook: {hook_mod.hook_status(Path(args.path))}")
        try:
            branch = subprocess.run(
                ["git", "-C", str(args.path), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            current = branch.stdout.strip() if branch.returncode == 0 else ""
            if current:
                print(f"branch: {current}")
        except Exception:
            pass
        return 0

    print(f"Unknown pr-ready action: {action}")
    print("Usage: hermes pr-ready {check|install-hook|remove-hook|status|create-pr}")
    return 2


def _slash_handler(raw_args: str) -> str:
    """Handle the ``/pr-ready`` slash command; never raise."""
    try:
        strict = "--strict" in raw_args
        fix = "--fix" in raw_args
        full = "--full" in raw_args
        as_json = "--json" in raw_args

        path = "."
        for token in raw_args.split():
            if not token.startswith("-"):
                path = token
                break

        report: GateReport = run_gate(cwd=path, strict=strict, fix=fix, full=full, issue="")
        return report.to_json() if as_json else report.render_text()
    except Exception as exc:
        return f"pr-ready-checker failed: {exc}"


def register(ctx: Any) -> None:
    """Register the plugin with Hermes."""
    ctx.register_tool(
        name="pr_ready_check",
        toolset="pr_ready",
        schema=PR_READY_CHECK_SCHEMA,
        handler=_tool_handler,
    )
    ctx.register_cli_command(
        name="pr-ready",
        help="Check whether a branch is ready for a pull request",
        setup_fn=_setup_cli,
        handler_fn=_cli_command,
        description=(
            "Run the PR-Ready gate (branch, tests, lint, secrets, commits, "
            "PR draft) or install the pre-push hook that enforces it."
        ),
    )
    ctx.register_command(
        "pr-ready",
        handler=_slash_handler,
        description="Run the PR-Ready gate on the current repository",
    )
