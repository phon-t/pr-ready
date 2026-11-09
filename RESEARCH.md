# PR-Ready Checker — research + scope (Hermes Agent Plugin Hackathon)

Researched 2026-11-05 by the Hermes Agent. No code built, nothing pushed,
no PR opened. Phon approved research/preparation only.

## Verdict

**The gap is real and still open.** Nothing in the live Hermes Plugin
Catalog is a pre-PR gate: no catalog plugin runs a repo's tests/lint,
checks the PR-template checklist, and blocks the push/PR until it passes.
The nearest plugins each cover one slice, after the fact or by hand:

| Plugin (live catalog) | What it actually does | What it does NOT do |
|---|---|---|
| `git-review` (mister-ideas, v0.2.2, Desktop pane) | Reads the diff in one scroll; stages, commits, pushes, opens PRs from the pane; cites lines into the composer | No tests, no lint, no checklist, no blocking — it will happily commit/push a failing change |
| `hermes-pr-studio` (Murali-lns, v0.3.0, Desktop) | Inbox of *already-open* PRs across repos; diffs, inline threads, Approve/Request-changes, "Send to Hermes" to fix a review comment; shells out to logged-in `gh` | Post-PR review only. Nothing before a PR exists; no gate, no checks run |
| `diff-review` (sprmn24, v1.1.0, tool `diff_review`) | One-shot regex scan of added lines: debug prints, console.*, hardcoded secrets, bare/broad except, TODO/FIXME; `target=origin/main` gives the PR diff view | Advisory only — returns JSON warnings, blocks nothing, runs no tests/lint, no fixes, no checklist |
| `git-hook` (hooks only, no tools) | Auto pull/commit/push of the agent's own edits at turn end; refuses only when the remote rejects direct pushes | The *opposite* philosophy: it pushes automatically and never runs a check. Fail-open by design |
| `background-review-watch`, `memory-review`, `mock-review` | Status chips / review summaries for Hermes' own self-improvement review and memory | Unrelated to PRs |

Live-catalog check (2026-11-05, GitHub API `contents/plugin-catalog`,
`ref=main`): **979 entries, no `pr-ready*` entry**, and no entry name
matching test/lint/CI/readiness/gate semantics beyond the ones above
(`git-hook`, `doctorpack`, `jev-cron-gate`, `provenance-gate` checked by
name; none is a PR gate). Local checkout at `f0d94882` had 912 entries —
the catalog grew since, so the live count is the one that matters.
Catalog-description grep over the local checkout: only 3 lines in all 912
entries mention pytest/pre-push/pre-commit at all.

## Hackathon deadline — one flag, please confirm

I could **not** verify a public "Hermes Agent Plugin Hackathon" page with
a Nov 11, 5pm EST deadline. Web search finds only the June 2026 Accelerated
Business Hackathon (NVIDIA/Stripe, submissions closed June 30). The Nov 11 /
Nov 12 03:30 IST deadline comes from the main chat, not from a public page
I can cite — it may be a Discord-announced plugin event that search doesn't
index. **Before building to that date, confirm the submission route**
(Discord channel? form? catalog PR?) in the main chat. Everything below
assumes a ~6-day build window anyway, so the plan is safe either way.

## Why this fits Hermes, specifically

Hermes' own PR template (`.github/PULL_REQUEST_TEMPLATE.md`) is already a
checklist — and it is exactly what slips in practice. Our own recent PRs
prove the pain: #148070 left the full-`pytest` and `scripts/check` boxes
unchecked (only scoped tests had run). The checker is the machine-readable
version of that template: run the checks the template asks for, before the
PR exists.

Hermes already has the pieces, in-core, that a gate can reuse instead of
reinventing:
- `python scripts/check` — the blocking lint/code-health checks CI runs,
  with `--install-hook` for pre-commit/pre-push hooks and `--no-verify`
  escape hatch. The plugin generalises this pattern to any repo.
- `scripts/run_tests.sh` — the mandated test runner (never bare `pytest`).
- Plugin authoring surface (developer guide, `website/docs/developer-guide/plugins/index.md`):
  `register_tool`, `register_cli_command`, `register_hook`, `register_skill`.
  Validation: `hermes plugins validate <path> --install-deps` (same check
  catalog CI runs) and `hermes plugins doctor`.

## Key architecture finding: Hermes hooks cannot be the blocker

Plugin hooks (`pre_tool_call`, `post_tool_call`, `on_session_end`, …) are
advisory: hook return values are discarded by Hermes, and a raising hook
only refuses a tool call. `git-hook`'s own docs say the same, and its
callbacks are fail-open with a 30s budget. So the blocking must come from
plain **git**: the plugin installs a real `pre-push` hook in the repo that
shells out to the checker. The Hermes side is a tool + CLI the agent and
the user can both call. Be honest about this in the demo: the hook blocks
the happy path; `--no-verify` and CI remain the backstop, same as
`scripts/check` today.

## Draft scope — v1 ("PR-Ready Checker")

Standalone plugin repo (owner submission, catalog rule 5), e.g.
`phon-t/hermes-pr-ready`. Python, stdlib + git + `gh` CLI only, no new
dependencies. No stored credentials: GitHub writes go through the user's
logged-in `gh`, the same pattern `hermes-pr-studio` and `git-review` use
(catalog rules 11–13: disclose shell-outs, network, writes).

### Entry points (v1)
- Hermes tool `pr_ready_check` — agent asks "is this PR ready?" and gets
  a structured pass/fail report.
- CLI `hermes pr-ready check [--strict] [--fix] [--full]` via
  `register_cli_command` — same engine, runnable in a terminal and from
  the git hook.
- `hermes pr-ready install-hook` — writes `.git/hooks/pre-push` (refuses
  to clobber a foreign hook; `--force` to replace one it owns).
- Stretch, not v1 core: `hermes pr-ready create-pr` — runs the gate, then
  drafts the PR body from the template and calls `gh pr create`. If time
  runs short, cut this first; the gate is the entry.

### Gate checks (each returns pass / warn / fail + the exact fix command)
Severity: FAIL blocks, WARN doesn't ( `--strict` promotes WARN → FAIL).

| # | Check | How (v1) | Severity |
|---|---|---|---|
| 1 | Branch hygiene | Not on default branch; branch has ≥1 commit vs merge-base; no unpushed merge commits | FAIL if on `main` / no commits |
| 2 | Clean tree | `git status --porcelain`: uncommitted changes and untracked, non-ignored files listed | FAIL in strict hook mode; WARN in plain `check` (so a mid-work review can still run) |
| 3 | Whitespace | `git diff --check` on the PR range | FAIL |
| 4 | Secrets on added lines | Regex scan (keys/tokens/passwords, `.env` forms) on the PR diff, same rule family as `diff-review` | FAIL |
| 5 | Debug prints / bare except / TODO | Added-lines scan: `print(`/`pprint(`, `console.*`, bare `except:`, `except Exception`, `TODO/FIXME/HACK` | WARN (FAIL with `--strict`) |
| 6 | Lint | If repo has it: `python scripts/check` for hermes-agent repos; else `ruff check` if configured; else skip with a note, never a guess | FAIL |
| 7 | Tests | Map changed source files → test files; run those (Hermes: `scripts/run_tests.sh <files>`). `--full` runs `pytest tests/ -q`. No changed/added test for a changed source file → WARN ("PR template requires tests for bug fixes") | FAIL if run tests fail |
| 8 | Commit messages | Every commit on the branch matches Conventional Commits (`fix(scope):` …) | FAIL (list offenders) |
| 9 | PR body draft | Generate from the repo's PR template: `Fixes #` link present? Type checkbox ticked? "How to Test" filled? Single-purpose heuristic (files span >2 unrelated areas → WARN) | WARN if Fixes # missing |
| 10 | Docs/config drift | Config keys changed but `cli-config.yaml.example` untouched; tool behaviour changed but tool descriptions untouched | WARN, path-keyed |
| 11 | CI status | If pushed and `gh` available: `gh pr checks` / branch checks | WARN only, never blocks on unpushed work |

`--fix` auto-fixes only the safe ones: `ruff check --fix` / `ruff format`
where configured. Everything else prints the exact command to run — the
checker never rewrites code it doesn't understand, and never commits.

### What v1 is NOT (non-goals)
- Not an LLM reviewer and not a replacement for `diff-review`/`git-review`/
  `hermes-pr-studio` — it composes with them (run `diff_review` first,
  review in `git-review`, open the PR, then watch it in PR Studio).
- No auto-commit, no auto-push (unlike `git-hook`), no auto-PR without a
  green gate, no `--no-verify` bypass of its own (git's own flag still
  exists; say so).
- No full-test-suite run by default (too slow for a hook); scoped tests +
  opt-in `--full`.
- Generic repos first via detection (Python → ruff/pytest; JS/TS → package
  scripts if present; Rust/Go → cargo/go test if present); hermes-agent
  itself is the demo repo, since its `scripts/check` + PR template give the
  richest gate.

### Repo layout (v1)
```
hermes-pr-ready/
├── plugin.yaml          # manifest: name pr-ready-checker, version, capabilities (tools, cli), requires_hermes floor, requires_env: []
├── __init__.py          # register(ctx): tool + CLI command
├── pr_ready/
│   ├── __init__.py
│   ├── git_range.py     # merge-base, diff, status (argv lists only, no shell)
│   ├── checks.py        # the 11 checks, pure functions over a diff + repo facts
│   ├── fixers.py        # --fix: ruff only
│   ├── report.py        # JSON + human-readable report, exit codes
│   └── hook.py          # install-hook (owns its marker comment)
├── tests/               # pytest on throwaway git repos (diff-review's pattern)
├── README.md            # install, usage, disclosures (shell-outs, gh, no telemetry, no stored creds)
└── .github/workflows/   # plugin's own CI: pytest + hermes plugins validate
```

### Validation before calling it done
- `hermes plugins validate ./hermes-pr-ready --install-deps` green.
- `hermes plugins doctor pr-ready-checker` green.
- Tests green on throwaway repos: clean branch passes; secret on an added
  line fails; bad commit message fails; `install-hook` blocks a `git push`
  to a local bare remote and passes after fix.
- Demo: run it against one of Phon's own Hermes branches (e.g. a fresh
  branch in the `hermes-147939` worktree) — gate goes red, `--fix`/manual
  fix, gate goes green, PR body draft shown.
- Catalog submission (`plugin-catalog/pr-ready-checker.yaml`, exact 40-char
  SHA pin) is a **follow-up after the hackathon**, not part of the entry:
  it needs maintainer review and the SHA pin, and must not be rushed.

### Catalog admission rules that shape v1 (plugin-catalog/README.md)
Rule 3 no self-updating code; rule 6 declared capabilities must match
reality; rule 7 security scan (`dangerous` fails); rule 8 Desktop SDK
surface (v1 has no Desktop half — keep it that way); rule 9 no core
overrides (public `register_*` only, no private imports); rule 11
credentials only via Hermes routes / `gh` (never read `.env`/`auth.json`);
rule 12 respect approvals and unattended runs (gate fails cleanly under
cron, never hangs); rule 13 disclose everything; rule 14 truthful
`requires_hermes` floor. English-first UI.

## Risks / open questions
1. **Submission route + deadline unconfirmed** (see flag above) — confirm
   in main chat before building.
2. Test runtime: scoped tests keep the hook fast; `--full` is opt-in.
3. `gh` not installed / not logged in → checks 11 and create-pr degrade
   to "skipped, here's the command", never a false pass.
4. Windows/macOS: git subprocess argv lists only; no shell; timeouts on
   every subprocess (diff-review uses 30s — same budget).
5. Name: `pr-ready-checker` (catalog key) vs tool `pr_ready_check` — check
   no collision at build time; no `pr-ready*` name exists today.
6. Backups if this stalls: Phone-First Task Inbox, Release Notes Writer
   (Phon's kept options).

## Build status — 2026-11-05 (built after Phon's "Ok go for building this plugin!")

Built locally in this directory (`~/workspace/hermes-pr-ready/`), per the
scope above. No PR opened, nothing published, nothing submitted — that still
needs Phon's explicit approval for that specific step.

- Code generated through the Lotus harness (DeepSeek V4.1 Flash default;
  it failed twice with a null-reply error, so generation fell back to
  sarvam/GLM 5.3 high, which produced all files; the harness `generate`
  output is capped at 4000 completion tokens, so `git_range.py` and the
  test file arrived truncated and were completed by hand).
- Engine: `pr_ready/git_range.py` (safe git plumbing, argv-only, 30s
  timeouts, ref validation), `checks.py` (checks 1–5, 8–10), `runners.py`
  (lint/tests/CI), `fixers.py` (ruff only), `report.py` (JSON + text +
  PR-body draft), `hook.py` (pre-push install/remove/status),
  `pr_ready/__init__.py` (`run_gate`), root `__init__.py` (registers the
  `pr_ready_check` tool, the `hermes pr-ready` CLI and the `/pr-ready`
  slash command).
- `plugin.yaml` (name `pr-ready-checker`, 0.1.0, `requires_hermes
  ">=0.21.6"`), `README.md` with disclosures, `pyproject.toml`,
  `.github/workflows/ci.yml`.
- Bugs found and fixed during testing (not by the generator): `git diff
  --check` reports on stdout with exit 1 (was swallowed); numstat `-z`
  records are TAB-separated inside NUL-separated records (was parsed as
  NUL-separated fields, so no files were seen); `check_docs_drift` had no
  PASS return; the gate's own pytest run dirtied the tree with
  `.pytest_cache/` (now run with `PYTHONDONTWRITEBYTECODE=1` and
  `PYTEST_ADDOPTS="-p no:cacheprovider"`); pytest missing in the runner
  Python is SKIP ("not verified"), not a false FAIL; pytest has no ini
  option for import mode (CLI-only), so the root `__init__.py` imports
  `pr_ready` with a try/except fallback instead.
- Tests: `tests/test_pr_ready.py`, **14/14 passing** (`python -m pytest
  tests/ -q`, ~4s, throwaway git repos, no network), including a real
  `git push` blocked by the installed hook and passing after the fix.
  `ruff check` and `ruff format --check` clean.
- Validation: `hermes plugins validate` **passed** (manifest, tools match,
  security scan safe, no core override); `hermes plugins doctor`
  **passed** (1 tool, 0 hooks — by design). One validate note:
  `ctx.register_cli_command()` is skipped when the plugin runs in the
  host-isolation mode; the CLI is wired in when it runs in-process.
- Demo (completed 2026-11-05 ~17:10 IST): a fresh worktree, branch
  `fix/1234-demo-pr-ready`, with a deliberately bad commit (secret +
  `print` + "wip demo stuff"). The real `hermes pr-ready check` (plugin
  enabled in a temp HERMES_HOME) went **RED, exit 1**: secrets FAIL,
  `scripts/check` FAIL (unused variable), commits FAIL, debug WARN,
  no-covering-test WARN. After fixing (key from env, conventional
  commit, proper test under `tests/`), the same command went **GREEN,
  exit 0: PR-READY: PASS (0 failed, 0 warnings, 1 skipped)** — the only
  skip is CI (no open PR). One hiccup on the way: a runtime restart wiped
  /tmp (the first worktree lived there), so the demo was rebuilt under
  `~/workspace/`; and the first green attempt failed because a `git rm`
  error broke the shell chain and the old bad file got committed instead
  of the fixed one — the gate correctly stayed red, which is itself the
  point. Demo worktree, branch and temp HERMES_HOME removed afterwards;
  the hermes checkout is back to its prior state.

## Polish — 2026-11-05 evening (deadline + route confirmed)

Phon confirmed the deadline (Nov 11, 5PM EST) comes from the official
Hermes Discord announcement, and the entry route is catalog PR + under-90s
demo video on X tagging @NousResearch + Typeform. Submission planned Nov
9–10. Nothing is submitted or published before then.

Three audit findings from a scratch-repo re-check, all fixed and tested:

- `infer_issue_ref` required 4+ digits, so `fix/42-slug` never linked
  its issue. Now `fix|hotfix|bugfix|issue/<n>-` matches any issue number
  (the 4-digit floor stays as the fallback for other branch shapes).
- Lint skipped on repos with ruff configured but no `scripts/check`,
  because ruff lives in the Hermes venv and is not on PATH. The locator
  now also tries `python -m ruff` in the running interpreter, and
  `apply_ruff_fixes` uses the located binary instead of a bare `ruff`
  (which silently failed when ruff was only in the repo `.venv`).
- The PR-body draft echoed the template verbatim with empty sections.
  It now fills an empty summary section from the commit subjects (oldest
  first) and an empty issue section with `Fixes #N`; non-empty sections
  are the author's text and are left alone.

`create-pr` stretch goal — BUILT (CLI-only, gate-first): `hermes pr-ready
create-pr [--title ...] [--issue N] [--draft]` runs the gate, refuses on
red (gh never invoked), and on green runs `gh pr create` with the first
commit's subject as the title and the drafted body. 2 new tests with a
`gh` shim; suite is now 19/19. README updated (create-pr, ruff discovery,
branch naming). Submission materials added: `SUBMISSION.md` (catalog entry
template with SHA placeholder, Typeform answers, Nov 9–10 checklist) and
`VIDEO_SCRIPT.md` (under-90s demo, 6 shots). The plugin directory is now
a local git repo (commit `746a6ca`, no remote — publishing still needs
Phon's go-ahead).
- NOT built: the `create-pr` stretch goal, and the catalog submission
  (exact-SHA pin) — both are post-hackathon follow-ups, not part of v1.
- Deadline/submission route: STILL UNCONFIRMED (no public page found).
  Flag it again when reporting back.

## Suggested build order (6 days to Nov 11)
1. Skeleton plugin + `check` engine + checks 1–5, 8 (pure git, no test runs) + tests on throwaway repos.
2. Checks 6–7 (ruff / scripts/check / scoped pytest) + `--fix` + report.
3. `install-hook` + CLI + README/disclosures + `validate`/`doctor`.
4. Demo on a real Hermes branch + 1–3 min demo video + submission.
5. Only then: `create-pr` stretch, then catalog PR follow-up.
