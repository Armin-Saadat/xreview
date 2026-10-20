---
name: xreview
description: Final step for substantial code changes - before telling the user that a new feature or algorithm, multi-file or tricky change, refactor, non-obvious bug fix, or change to data loading, splits, preprocessing, training, evaluation, metrics, checkpoint selection, concurrency, security or job scripts is done, get an independent read-only review of it from the other coding agent (Codex reviews Claude Code's work, Claude Code reviews Codex's), built from the real git diff. Also use whenever the user asks for a review, second opinion or cross-check. Skip it for questions, docs, formatting and small obvious edits.
---

# Cross-agent review

A second model from another company reads your change in a fresh session and
reports concrete problems. It catches what self-review misses because it shares
none of your context or assumptions. You stay responsible for the result: the
review is evidence, not a verdict you have to accept.

## When

Use judgment, not a line count. Review a coherent piece of work at a meaningful
completion point, not every intermediate edit. A one-line change still deserves
a review when it changes an experiment rule (which split selects checkpoints,
what gets normalized on what data) or something hard to undo. Always review when
the user asks; skip when they say so.

## Steps

1. **Finish the change and run the relevant tests first.** The reviewer reads
   code but never runs it, so your test results are part of the evidence.
2. **Start the review** from the repository:

   ```bash
   python3 <skill-dir>/scripts/xreview.py start --intent "<what the user asked, what you changed and why, tests you ran with results, what you are unsure about>"
   ```

   - The reviewer defaults to the other agent. xreview collects the diff from git
     itself (staged, unstaged and untracked files), so do not paste code into
     `--intent`.
   - Work that is already committed: add `--base <branch-or-commit>`.
   - If your commands run in a sandbox without network access, run `start` with
     escalated permissions (outside the sandbox); it exits with that advice when
     it detects the sandbox.
3. **Wait for the result.** Reviews take a few minutes.
   - If your shell tool can run a command in the background and notify you, run
     `python3 <skill-dir>/scripts/xreview.py wait <job-id> --timeout 1800` that way.
   - Otherwise run `wait <job-id>` (it returns within about 2 minutes) and repeat
     it until it prints a result.
4. **Assess every finding against the code.** Fix the real ones and re-run the
   tests. When you disagree, say why with evidence. If your fixes were
   substantive, review again; stop after 3 rounds and report what is still open.
5. **Report to the user:** the reviewer and verdict, what you fixed, what you
   disputed and why, and the path of `review.md`.

## Reading the result

The last line is `XREVIEW <job-id>: <verdict> (<counts>)`. The exit code says the
same thing:

| Exit | Verdict | Meaning |
|---|---|---|
| 0 | `approve` | No blocker or major findings; minor ones may remain |
| 2 | `changes_requested` | At least one blocker or major finding |
| 3 | `cannot_review` | The reviewer could not judge the change; not an approval |
| 4 | still running | Run `wait` again |
| 1 | failed | Crash, timeout, login or sandbox problem; not an approval |

A failed, missing or `cannot_review` result never means the work is fine. Say
so instead of calling the work done, and show the error. Common causes are a
reviewer that is not logged in or a sandboxed shell; see
[troubleshooting](references/setup.md#troubleshooting).

## Rules that keep the review honest

- **Do not curate the evidence.** The reviewer sees the full diff; your notes are
  labelled as unverified claims. Say what you are unsure about instead of
  steering the reviewer away from it.
- **Do not outsource validation.** Never ask the reviewer to run tests or jobs.
- **Credentials stay out.** Files such as `.env`, keys and `.netrc` are never sent.
  Do not quote secrets in `--intent`.
- **One review per state.** xreview refuses to re-review an unchanged diff (pass
  `--force` only for a deliberate second opinion) and reuses a review that is
  still running for the same changes.

## Other commands

`show [id]` prints a finished review, `list` shows recent ones, `cancel [id]`
stops one, and `doctor [--live]` checks that the reviewer CLIs are installed,
logged in and sandboxed. Setup, configuration (models, timeouts, project review
rules in `.xreview.md`) and troubleshooting are in
[references/setup.md](references/setup.md).
