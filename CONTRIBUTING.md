# Contributing to pr-ready-checker

## Setup

Python `>= 3.11`, no extra dependencies for the plugin itself. For
development, install the test/lint tools (a virtualenv is recommended):

```sh
python -m venv .venv
.venv/bin/pip install pytest ruff
```

## Tests

```sh
python -m pytest tests/ -q
```

The suite (19 tests) builds throwaway git repos in temp directories and
runs the gate against them. No network, no `gh`, no Hermes install needed.
Keep it that way: a test that needs the network or a logged-in `gh` does
not belong here.

## Lint

```sh
ruff check pr_ready tests __init__.py
ruff format --check pr_ready tests __init__.py
```

The lint rule set is pinned in `pyproject.toml` (`[tool.ruff.lint]`),
so any recent ruff version gives the same result. Run `ruff format`
before committing.

## How to add a check

1. **Pure check** (works on data already collected — repo info, added
   lines, changed paths): add a function to `pr_ready/checks.py` and
   call it from `run_gate()` in `pr_ready/__init__.py`, in the same
   order as the other checks.
2. **Subprocess check** (needs to run a tool): add it to
   `pr_ready/runners.py`, shelling out via argv lists only — never
   `shell=True` — with an explicit timeout, and call it from
   `run_gate()` the same way.
3. **Severity:** FAIL blocks the PR, WARN is advisory (promoted to FAIL
   by `--strict`), SKIP means "could not verify" — use SKIP, never a
   false PASS, when a tool is missing or a repo has no such config.
4. **Side effects:** the gate must not modify the repo it checks (no
   writes, no commits, no staging). The only exception is `--fix`,
   which may run exactly `ruff check --fix` / `ruff format`
   (`pr_ready/fixers.py`).
5. **Test it:** add a case to `tests/test_pr_ready.py` covering the
   pass and the fail paths, using the existing throwaway-repo helpers.
6. **Document it:** add a row to the checks table in `README.md` and
   bump the count in the section heading if the total changes.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat: …`, `fix: …`, `docs: …`, …) — the plugin's own gate enforces
this on its own branches.
