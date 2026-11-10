# pr-ready-checker

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that
answers **"is this branch ready for a pull request?"** — before the PR
exists. It runs the checks a PR template asks for (lint, tests, secrets,
commit messages, …) against the current branch and reports PASS / FAIL, so
checklist slips are caught before review instead of during it.

It never commits, never pushes, and never opens a PR by itself. The one
exception is the explicit `hermes pr-ready create-pr` command, which opens
a PR only after the gate passes.

## Why

PR templates are checklists, and checklists slip. A real Hermes PR left
the full-`pytest` and `scripts/check` boxes unchecked because only scoped
tests had run — exactly the slip this gate exists to catch. It is the
machine-readable version of the template: run the checks the template
asks for, before the PR exists.

## Install

```sh
git clone https://github.com/phon-t/pr-ready ~/.hermes/plugins/pr-ready-checker
hermes plugins enable pr-ready-checker
```

Verify it:

```sh
hermes plugins validate ~/.hermes/plugins/pr-ready-checker
hermes plugins doctor pr-ready-checker
```

No packaging step: Hermes loads the plugin straight from that directory.

## Requirements

- Hermes `>= 0.21.6`
- Python `>= 3.11`
- `git` on PATH
- No extra Python dependencies — stdlib only. `gh` is optional (CI status
  only); `ruff` and `pytest` are used when the repo under check has them.

## Usage

### Ask the agent

Ask "is this PR ready?" in a Hermes session — the agent calls the
`pr_ready_check` tool and gets a JSON gate report. Arguments: `path`
(default `.`), `strict`, `fix`, `full`, `issue`, `format` (`text` or
`json`).

### CLI

```sh
hermes pr-ready check [--strict] [--fix] [--full] [--issue N] [--json] [--draft-pr]
hermes pr-ready install-hook [--force]
hermes pr-ready remove-hook
hermes pr-ready status
hermes pr-ready create-pr [--title ...] [--issue N] [--draft] [--strict] [--full]
```

Exit code `0` = ready, `1` = not ready, `2` = usage error.

### Slash command

`/pr-ready [--strict] [--fix] [--full]` inside a Hermes session.

### Opening the PR

`hermes pr-ready create-pr` runs the gate first; on a red gate it prints
the report and opens nothing. On green it drafts the PR body (see below)
and runs `gh pr create` with the first commit's subject as the title
(`--title` overrides it, `--draft` opens a draft). It is CLI-only on
purpose: opening a PR writes to the remote, so it is never a tool the
agent can call on its own — running the command is the explicit decision.

## The 12 checks

| Check | What it does | Severity |
|---|---|---|
| Branch hygiene | Not on the default branch, at least one commit vs merge-base, no merge commits | FAIL |
| Clean tree | No uncommitted changes or untracked files | FAIL |
| Whitespace | `git diff --check` on the PR range | FAIL |
| Large files | Binary files in the diff, or files over 1 MB, fail; files over 256 KB warn | FAIL / WARN |
| Secrets | AWS keys, GitHub/Slack tokens, `api_key`/`secret`/`password` assignments, private keys on added lines (known placeholders ignored) | FAIL |
| Debug hygiene | `print()` / `console.*`, bare or broad `except`, TODO / FIXME / HACK on added lines | WARN (FAIL with `--strict`) |
| Lint | `scripts/check` when the repo has one, else `ruff check` when ruff is configured (ruff from PATH, the repo's `.venv`, or `python -m ruff`); skipped when neither exists | FAIL |
| Tests | Changed Python files mapped to their test files and run (`scripts/run_tests.sh` when present, else `pytest`); `--full` runs the whole suite; a changed file with no covering test is a WARN | FAIL if the run fails |
| Commit messages | Every branch commit follows Conventional Commits (`type(scope): description`) | FAIL |
| PR body | A draft body is generated from the repo's PR template — empty summary/issue sections are filled from the commit subjects and the inferred `Fixes #N` (branch `fix/<n>-…`, or `Fixes #N` in a commit subject); a missing `Fixes #` link is a WARN | WARN |
| Docs/config drift | Config changed without updating `cli-config.yaml.example`; `tools/` changed without docs or test changes | WARN |
| CI status | `gh pr checks`, when `gh` is installed and a PR exists | WARN only, never blocks |

FAIL blocks; WARN does not (unless `--strict`, which promotes WARN to
FAIL); SKIP means "could not verify" — never a false pass.

## Blocking: an honest note

Hermes plugin hooks **cannot** block a push — Hermes discards hook return
values. So enforcement is a real git `pre-push` hook, installed by
`hermes pr-ready install-hook`. It runs `hermes pr-ready check --strict`
before every push. Bypass once with `git push --no-verify`; CI remains the
backstop, same as `scripts/check` today. The installer refuses to clobber a
foreign hook (use `--force` to replace one it owns) and `remove-hook` only
removes a hook it manages.

## --fix

`--fix` runs `ruff check --fix` and `ruff format` where ruff is
configured, and strips trailing whitespace from the exact lines
`git diff --check` flagged in the PR range. Everything else prints the
exact command to run. Nothing is ever committed for you, and the gate
never rewrites code it does not understand.

## Disclosures

- **Shell-outs:** `git`, `gh`, `ruff`, `pytest`, `scripts/check`,
  `scripts/run_tests.sh`. All via argv lists, never a shell, each with a
  timeout. `gh pr create` runs only from `create-pr`, never from the gate.
- **Network:** only via `gh pr checks`, when `gh` is installed and logged
  in. No telemetry, no phoning home.
- **Credentials:** none stored or read. GitHub access goes through the
  user's logged-in `gh`, never `.env` or `auth.json`.
- **Writes:** the pre-push hook file, and whatever `ruff --fix` and the
  whitespace fixer change. Nothing else. The gate leaves no trace in the
  repo it checks: test runs are done with bytecode writing and the pytest
  cache disabled.

## Development

```sh
python -m pytest tests/        # 24 tests on throwaway git repos, no network
ruff check pr_ready tests __init__.py
ruff format --check pr_ready tests __init__.py
hermes plugins validate .      # same check catalog CI runs
hermes plugins doctor pr-ready-checker
```

CI (`.github/workflows/ci.yml`) runs the test suite and `ruff check` on
every push and pull request.

To add a check, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Limitations

- Linux and macOS only; Windows is untested.
- Scans are capped at 500 files / 2000 added lines per diff, so a huge PR
  is checked in part (the report still runs every other check).
- `gh` missing means CI is skipped, not failed.
- The hook cannot stop `git push --no-verify`.

## License

MIT — see [LICENSE](LICENSE).
