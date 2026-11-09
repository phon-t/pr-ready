# Submission plan — Hermes Agent Plugin Hackathon

**Deadline: Nov 11, 5PM EST = Nov 12, 03:30 IST.** Confirmed by Phon from the
official Hermes Discord server announcement (no public page exists, which is
why web search found nothing). Submission planned for **Nov 9 or 10**, when
the plugin is fully complete and polished. **Nothing is submitted or
published before then, and nothing goes public without Phon's explicit
go-ahead for that step.**

## The three parts (from the announcement)

1. **Catalog PR** — a PR to `NousResearch/hermes-agent` adding
   `plugin-catalog/pr-ready-checker.yaml` (template below). Owner
   submission (phon-t owns the plugin repo), exact 40-char SHA pin
   mandatory — branches, tags and short SHAs are rejected. The SHA can
   only be filled in **after** the plugin repo is published.
2. **Demo video, under 90 seconds, on X, tagging @NousResearch** —
   script in `VIDEO_SCRIPT.md`.
3. **Typeform submission** — link is in the Discord announcement (Phon
   has it). Prepared answers are below.

## Pre-submission checklist (Nov 9–10, in order)

- [ ] Phon gives the go-ahead to publish the plugin repo.
- [ ] Create `phon-t/hermes-pr-ready` on GitHub, push the local repo
      (see "Local repo" below), note the exact 40-char HEAD SHA.
- [ ] Fill the SHA into the catalog entry below; run
      `hermes plugins validate` against the pinned commit.
- [ ] Open the catalog PR from phon-t's fork of `NousResearch/hermes-agent`
      (Lotus in the main chat, after Phon's approval for that PR).
- [ ] Record the demo video per `VIDEO_SCRIPT.md` (< 90s), post on X
      tagging @NousResearch, copy the post URL.
- [ ] Submit the Typeform with the prepared answers + repo / catalog-PR /
      video links.

## Catalog entry (fill in the SHA on Nov 9/10)

```yaml
name: pr-ready-checker
repo: https://github.com/phon-t/hermes-pr-ready
sha: <40-hex commit SHA of the published HEAD — fill in after publishing>
description: Gate a branch before the PR exists: branch hygiene, clean tree, whitespace, secrets/debug scan, lint, scoped tests, Conventional Commits, PR-body draft, docs drift, CI status. FAIL blocks; a real git pre-push hook enforces it.
maintainer: phon-t
tier: community
category: tools
requires_hermes: ">=0.21.6"
docs_url: https://github.com/phon-t/hermes-pr-ready#readme
version: "0.1.0"
platforms: [linux, macos]
capabilities:
  provides_tools:
  - pr_ready_check
  provides_hooks: []
  provides_middleware: []
  requires_env: []
```

Notes for the catalog PR: `provides_tools` must match reality (one tool,
`pr_ready_check`; the CLI and the pre-push hook are not catalog
capabilities). `version` is a cosmetic label for the SHA. The repo root at
the pinned SHA must contain `plugin.yaml` + `__init__.py` + `pr_ready/` —
it does.

## Typeform — prepared answers

- **Plugin name:** pr-ready-checker
- **Author:** phon-t
- **Repo:** https://github.com/phon-t/hermes-pr-ready
- **Catalog PR:** <link, after it is opened>
- **Demo video:** <X post URL, after posting>
- **One-line description:** A pre-PR gate for Hermes: it runs the checks
  from the repo's own PR template — branch, clean tree, whitespace,
  secrets, lint, scoped tests, Conventional Commits, PR-body draft,
  docs drift, CI — before the PR exists, and blocks the push when they
  fail.
- **What makes it different:** the nearest catalog plugins don't gate —
  `git-review` pushes without checks, `hermes-pr-studio` reviews
  already-open PRs, `diff-review` only warns, `git-hook` auto-pushes
  fail-open. This one FAIL-blocks, and it is honest about the one thing
  it can't do: Hermes plugin hooks can't block, so enforcement is a real
  git pre-push hook, disclosed in the README.

## Local repo

The plugin directory is a local git repo (initialised as preparation
only — **not pushed, no GitHub repo created yet**):

```sh
cd ~/workspace/hermes-pr-ready
git log --oneline -1   # the commit to publish on Nov 9/10
```

Publishing = create `phon-t/hermes-pr-ready` on GitHub, then
`git remote add origin https://github.com/phon-t/hermes-pr-ready.git`
and `git push -u origin main`. That step needs Phon's go-ahead.
