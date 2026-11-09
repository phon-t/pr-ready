# Demo video script — under 90 seconds

Post on X, tagging @NousResearch, with the repo link. Record a terminal
(the commands below are scripted; run them in a scratch repo so the
timings are real). Target ~85 seconds total.

## Shot 1 — the problem (0:00–0:10, ~10s)

Narration: *"PR templates are checklists, and checklists slip. A real
Hermes PR once shipped with the test boxes unchecked. So I built a gate
that runs the template before the PR exists."*

On screen: nothing fancy — just the terminal prompt. (Optional: flash
the Hermes PR template checklist for 2 seconds.)

## Shot 2 — red (0:10–0:35, ~25s)

Narration: *"Here's a branch with a leaked key, a debug print, and a
'wip' commit message."*

Commands (in a scratch repo with the bad commit already made):

```sh
hermes pr-ready check
```

On screen: the gate output — `[FAIL] Secrets`, `[FAIL] Lint`,
`[FAIL] Commit subjects`, ending on `PR-READY: FAIL (3 failed, 3
warnings, 1 skipped)`. Hold the summary line for 3 seconds.

## Shot 3 — fix and green (0:35–0:58, ~23s)

Narration: *"Fix the three things — key out, print out, proper commit
message — and run it again."*

Commands (quick cuts; don't narrate the typing):

```sh
# remove the secret + print, commit as "feat: add demo feature"
hermes pr-ready check
```

On screen: `PR-READY: PASS (0 failed, 0 warnings, 1 skipped)`. Hold
for 3 seconds.

## Shot 4 — the hook blocks a push (0:58–1:12, ~14s)

Narration: *"And it can't be forgotten, because the pre-push hook
enforces it."*

Commands:

```sh
hermes pr-ready install-hook
git push   # blocked: "PR-READY: FAIL"
```

On screen: the push being refused by the hook. (One line of the
refusal is enough; cut fast.)

## Shot 5 — open the PR (1:12–1:24, ~12s)

Narration: *"Green? Then one command opens the PR."*

Commands:

```sh
hermes pr-ready create-pr
```

On screen: the `https://github.com/.../pull/...` URL the command
prints.

## Shot 6 — close (1:24–1:30, ~6s)

Narration: *"Eleven checks, no LLM, no auto-commit. pr-ready-checker —
link in the post. @NousResearch"*

On screen: the repo URL, `github.com/phon-t/hermes-pr-ready`.

## Recording notes

- Use a large terminal font; the video will be watched on phones.
- The red→green demo in `RESEARCH.md` (branch `fix/1234-demo-pr-ready`)
  is the rehearsal for shots 2–3 — same bad commit, same fix.
- Keep the typing out of the video: pre-type the commands, or cut
  between them. The audience should see outputs, not keystrokes.
- If a take runs long, cut shot 4 (the hook) first — the red→green arc
  is the story; the hook is the bonus.
