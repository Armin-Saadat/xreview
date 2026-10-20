# Setup, configuration and troubleshooting

## Contents

- [Requirements](#requirements)
- [Check a machine](#check-a-machine)
- [What the reviewer can do](#what-the-reviewer-can-do)
- [Permissions in the implementing agent](#permissions-in-the-implementing-agent)
- [Configuration](#configuration)
- [Project review rules](#project-review-rules)
- [What is sent and stored](#what-is-sent-and-stored)
- [Troubleshooting](#troubleshooting)

## Requirements

- Python 3.8 or newer and git, on the machine where the agents run (for a remote
  workspace, the remote host, not the laptop).
- Both agent CLIs installed and logged in there: `codex` (`codex login`) and
  `claude` (`claude auth login`). The desktop apps' sign-ins do not cover the CLIs.
- A git repository. To review a folder that is not one yet, run `git init` and
  commit a baseline first.

## Check a machine

```bash
python3 <skill-dir>/scripts/xreview.py doctor          # installs, logins, Codex sandbox
python3 <skill-dir>/scripts/xreview.py doctor --live   # plus one tiny real review per reviewer
```

`--live` reviews a planted bug that can only be found by opening a file outside
the diff, so it proves login, model access and file reading together. It uses a
little of each account's quota.

## What the reviewer can do

| Reviewer | Command xreview runs | Access |
|---|---|---|
| Codex | `codex exec --sandbox read-only -c approval_policy="never" --ephemeral --output-schema ...` | Read-only shell inside Codex's sandbox (cat, rg, git diff, ...); no writes, no network |
| Claude Code | `claude -p --restricted --tools Read,Grep,Glob --strict-mcp-config --permission-mode dontAsk --no-session-persistence --disable-slash-commands --json-schema ...` | Read, Grep and Glob inside the repository only; no shell, settings files, hooks, plugins or MCP servers |

Both run in a fresh session with `XREVIEW_ROLE=reviewer`, which stops xreview from
starting a nested review. Neither can review its own work: xreview refuses
`--reviewer claude` inside Claude Code and `--reviewer codex` inside Codex.

## Permissions in the implementing agent

- **Claude Code** asks before running the script. Approve it, or choose the
  option not to ask again for that command.
- **Codex** runs commands in a sandbox without network access by default, so
  `start` must run with escalated permissions; Codex asks you to approve that.
  `wait`, `show` and `list` only read files and work inside the sandbox.

## Configuration

Reviewers use these models unless you configure others:

| Reviewer | Model | Reasoning effort |
|---|---|---|
| Codex | `gpt-6-astra` | `medium` |
| Claude Code | `claude-opus-5-5` (Opus 5.5) | `high` |

To change them on one machine, create `~/.config/xreview/config.json` (or the
path in `XREVIEW_CONFIG`) with only the fields you want to change:

```json
{
  "timeout_minutes": 30,
  "max_diff_kb": 300,
  "codex":  {"model": "gpt-6-astra", "effort": "medium"},
  "claude": {"model": "claude-opus-5-5", "effort": "high"}
}
```

- Set `model` or `effort` to `null` to use that CLI's own default instead: your
  Codex `config.toml` settings, or Claude Code's default model.
- `doctor` prints the model and effort each reviewer will request on that machine.
- An account without access to the requested model gets a failed review, never a
  silent switch to another model.
- `bin` points at a specific executable when the CLI is not on `PATH`.
- `--model` and `--effort` on `start` override the file for one review.
- Reviews are stored under `~/.local/share/xreview/jobs/` (or `XREVIEW_HOME`).

## Project review rules

A `.xreview.md` file at the repository root is added to every review there as
the user's own instructions. Use it for invariants a reviewer cannot infer, for
example:

```markdown
- data/splits/test.csv is the held-out test set: it must never feed model or
  checkpoint selection.
- Preprocessing statistics are fitted on the training split only.
- Experiments must be reproducible from a seed in configs/.
```

## What is sent and stored

- Sent to the reviewer's provider, under your account: the diff, `git status`,
  the author's notes and `.xreview.md`. The reviewer can also read other files in
  the repository.
- Never sent: files whose names look like credentials (`.env*`, `*.pem`, `*.key`,
  `id_rsa*`, `.netrc`, `credentials.json` and similar). The review lists them by name.
- Not inlined: binary files, new files over 200 KB and anything past
  `max_diff_kb`. The reviewer is told to read those files directly.
- Each job folder keeps the exact prompt, the raw answer, `review.md`,
  `review.json` and `meta.json` (including the model the CLI reports it used).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Claude Code is not logged in for command-line use` | Run `claude auth login` in a terminal on that machine |
| `Codex is not logged in` | Run `codex login` |
| `codex-code-mode-host is missing` warning, or Codex returns `cannot_review` saying it could not read files | That Codex is a bare binary without its companion tools. Reinstall it with OpenAI's installer: `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` |
| `Codex read-only sandbox does not work here` (for example `bwrap: ... Operation not permitted` on HPC or container hosts) | Codex reviews cannot run on that host. Use Claude Code as the reviewer there; nothing falls back to running without a sandbox |
| `running inside the Codex sandbox` | Re-run `start` with escalated permissions |
| `Operation not permitted` writing `~/.local/share/xreview` | The shell is sandboxed; run `start` outside the sandbox |
| `nothing to review` | No changes against HEAD; for committed work pass `--base <branch-or-commit>` |
| `already reviewed` | The exact diff was reviewed before; `show <id>` prints it, `--force` asks again |
| A review that never finishes | `cancel <id>`; the job fails after `timeout_minutes` anyway |
