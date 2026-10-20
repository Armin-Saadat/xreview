# xreview

Cross-agent code review for **Claude Code** and **Codex**: when one agent
finishes substantial work, the other one reviews it, read-only, in a fresh
session, from the real git diff. It is one plugin that installs in both tools
from this repository.

- **The agent decides when.** The `xreview` skill tells the implementing agent
  to request a review before it reports substantial or risky work as done:
  multi-file logic, refactors, non-obvious bug fixes, and anything touching data
  splits, preprocessing, training, evaluation, metrics or job scripts. Questions,
  docs and small edits get no review. You can always ask for one, or say no.
  - Claude Code picks skills by the topic of the request, so the plugin also adds
    a one-line reminder of this rule when a Claude Code session starts. In the
    trigger evals, Claude reviewed its own finished work in 0 of 3 runs without
    the reminder and in 3 of 3 with it, and still did not review questions or
    typo fixes (3 of 3 each).
  - Codex triggered the skill from its description alone.
- **The reviewer sees what actually changed.** xreview collects staged,
  unstaged and untracked changes from git itself. The author's notes are
  labelled as unverified claims, and files that look like credentials are
  never sent.
- **The reviewer cannot change anything.** Codex runs in its read-only sandbox.
  Claude Code runs with only Read, Grep and Glob, and without settings files,
  hooks, plugins or MCP servers.
- **Reviewer models:** Codex reviews with GPT-6 Astra at medium effort, and
  Claude Code with Opus 5.5 at high effort. Either can be changed per machine
  ([configuration](skills/xreview/references/setup.md#configuration)).
- **Reviews run in the background**, so no agent's command timeout kills them.
  Answers must match a JSON schema. A crash, timeout, malformed answer or
  `cannot_review` is never treated as an approval.
- **Built for research code too.** Besides correctness, the reviewer checks for
  data leakage, train/validation/test separation, checkpoint and
  hyperparameter selection, metric correctness and baseline fairness.

## Install

You need both CLIs logged in on the machine where the agents run:
`codex login` and `claude auth login`. The desktop apps' sign-ins do not cover
the command-line tools.

**Claude Code**, so that Codex reviews Claude's work:

```
/plugin marketplace add Armin-Saadat/xreview
/plugin install xreview@xreview --scope user
```

On Claude Code 2.1.275 or later, `/plugin install xreview --marketplace Armin-Saadat/xreview`
does both in one step.

| Scope | Effect |
|---|---|
| `user` | Every project on this machine |
| `project` | This repository; recorded in `.claude/settings.json`, which you commit |
| `local` | This repository, for you only |

For `project`, declare the marketplace in the project as well, so collaborators
who open the repository are offered both:

```
/plugin marketplace add Armin-Saadat/xreview --scope project
/plugin install xreview@xreview --scope project
```

**Codex**, so that Claude Code reviews Codex's work:

```
codex plugin marketplace add Armin-Saadat/xreview
codex plugin add xreview@xreview
```

That enables it for every project. To use it in some projects only, turn it off
in `~/.codex/config.toml` and on in each project's `.codex/config.toml`, which
you can commit. Codex applies project settings only in projects you have trusted.

```toml
# ~/.codex/config.toml
[plugins."xreview@xreview"]
enabled = false

# <project>/.codex/config.toml
[plugins."xreview@xreview"]
enabled = true
```

Collaborators still install the plugin once themselves.

**Check each machine once.** Ask your agent to "run the xreview doctor with
`--live`", or run it from a clone of this repository:

```bash
python3 skills/xreview/scripts/xreview.py doctor --live
```

It checks both CLIs and their logins, Codex's read-only sandbox, and one real
review per reviewer. That review uses a little of each account's quota.

**Without a plugin system**, copy `skills/xreview/` into `~/.claude/skills/` (Claude
Code) or `~/.agents/skills/` (Codex) for every project, or into a project's
`.claude/skills/` or `.agents/skills/`.

## Use

Work as usual. When the agent finishes something substantial, it starts a
review, waits, checks every finding against the code, fixes what holds up and
reports the verdict, the fixes, any disputed findings and the path of
`review.md`. To ask for a review yourself, use `/xreview:xreview` in Claude Code,
`$xreview` in Codex, or just say "get a second opinion on this".

Put a `.xreview.md` at a repository root to give every review there rules a
reviewer cannot infer, such as "the test split never feeds model selection".
Models, timeouts, permissions, what is sent, and troubleshooting are in
[skills/xreview/references/setup.md](skills/xreview/references/setup.md).

**HPC and container hosts.** Codex's sandbox does not work on some of them
(`bwrap: ... Operation not permitted`). `doctor` reports this, and Codex reviews
fail there with an explicit error instead of running unsandboxed. Use Claude Code
as the reviewer on those hosts.

## Get updates

| Tool | Command |
|---|---|
| Claude Code | `/plugin marketplace update xreview`, then `/reload-plugins` (or enable auto-update under `/plugin`) |
| Codex | `codex plugin marketplace upgrade xreview`, then start a new session |

Claude Code tracks every commit on `main`. Codex updates when `version` in
[`plugin.json`](plugin.json) changes.

## Repository layout

| Path | Purpose |
|---|---|
| `skills/xreview/` | The skill: `SKILL.md`, `scripts/xreview.py`, `references/`, and `agents/openai.yaml` (Codex metadata) |
| `plugin.json` | Plugin manifest (portable format; Codex reads its `version` and display metadata) |
| `.claude-plugin/` | Claude Code plugin manifest and marketplace |
| `claude-hooks/` | Claude Code session-start reminder of when to review (declared only in the Claude manifest) |
| `.agents/plugins/marketplace.json` | Codex marketplace |
| `.agents/skills` | Symlink to `skills/`, so Codex finds the skill while working inside this repository |
| `tests/` | Offline tests with fake `codex` and `claude` executables (no network, no quota) |
| `evals/` | Claude Code trigger evals: the skill fires after real work and on request, and stays quiet otherwise |
| `tools/validate.py` | Pre-commit checks: frontmatter, Codex metadata, links, manifest agreement, version bump, evals, secrets |
| [`AGENTS.md`](AGENTS.md) (and `CLAUDE.md`) | How to maintain and release this repository |

## Development

```bash
python3 tools/validate.py
python3 -m unittest discover -s tests -v
claude plugin validate .
```

Read [AGENTS.md](AGENTS.md) before changing the skill. CI runs the first two on
every push.

## License

[MIT](LICENSE)
