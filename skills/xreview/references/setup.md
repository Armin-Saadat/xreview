# Setup, configuration and troubleshooting

## Contents

- [Requirements](#requirements)
- [Check a machine](#check-a-machine)
- [What the reviewer can do](#what-the-reviewer-can-do)
- [What the reviewer can read](#what-the-reviewer-can-read)
- [Permissions in the implementing agent](#permissions-in-the-implementing-agent)
- [Configuration](#configuration)
- [Project review rules](#project-review-rules)
- [What is sent and stored](#what-is-sent-and-stored)
- [Councils](#councils)
- [Troubleshooting](#troubleshooting)

## Requirements

- Python 3.8 or newer and git, on the machine where the agents run (for a remote
  workspace, the remote host, not the laptop).
- Both agent CLIs installed and logged in there: `codex` (`codex login`) and
  `claude` (`claude auth login`). The desktop apps' sign-ins do not cover the CLIs.
- Optional, for `--reviewer gemini`: Google's Antigravity CLI (`agy`), logged in by
  running `agy` once in a terminal. It replaced the Gemini CLI for personal accounts in
  June 2026. xreview looks for it on `PATH` and in `~/.gemini/bin`. Gemini reviews use
  your Antigravity AI credits.
- A git repository. To review a folder that is not one yet, run `git init` and
  commit a baseline first.

## Check a machine

```bash
python3 <skill-dir>/scripts/xreview.py doctor          # installs, logins, Codex sandbox
python3 <skill-dir>/scripts/xreview.py doctor --live   # plus one tiny real review per reviewer
```

`doctor` also checks that Codex's shell, under xreview's permission profile, can
read the review copy but nothing next to it. `--live` reviews a planted bug that
can only be found by opening a file outside the diff, so it proves login, model
access and file reading together. It uses a little of each account's quota.

## What the reviewer can do

| Reviewer | Command xreview runs | Access |
|---|---|---|
| Codex | `codex exec -c default_permissions="xreview" -c permissions.xreview.filesystem={...} --cd <review copy> --ephemeral --ignore-user-config --disable apps --disable plugins ...` with web search off, and xreview's tool server for history | A shell whose permission profile reads only the review copy and the system paths tools need: not your home folder, other repositories, `/tmp` or Codex's own login. No writes, no network. History (log, show, diff between commits) comes from xreview's `git` tool, because the copy has no `.git`. None of your MCP servers, plugins, hooks or connected ChatGPT apps (GitHub, Gmail, Drive, Slack...) |
| Codex, repo-tools mode (`"tools": "repo"`) | `codex exec --sandbox read-only ...`, plus `--disable shell_tool --disable unified_exec --disable view_image`, and xreview's own read-only tool server (`xreview.py _repo_tools <review copy> --git-dir <repo>/.git`) as its only MCP server | No shell. Reads the review copy through `list_files`, `read_file` and `search`, and history through `git` (log, show, diff between commits), with no `.git` internals, credential-like or private paths (also not in git output). Git runs against the git directory only, never a working tree, so no git filters run; it ignores your global and system git config, and settings that would make it read files by path are overridden. No writes, no network |
| Codex, diff-only mode (`"tools": false`) | `codex exec --sandbox read-only ...`, plus `--disable shell_tool --disable unified_exec --disable code_mode_host --disable view_image` | No shell or files either: only the diff in the prompt, with whole changed functions. Writes are still rejected by the read-only policy |
| Claude Code | `claude -p --restricted --tools Read,Grep,Glob --strict-mcp-config --permission-mode dontAsk --no-session-persistence --disable-slash-commands --json-schema ...`, run in the review copy | Read, Grep and Glob inside the review copy only (`--restricted` refuses paths outside the working directory); no shell, settings files, hooks, plugins or MCP servers |
| Gemini (`--reviewer gemini`) | `agy --input-format stream-json --output-format stream-json --json-schema ... --settings {...} --sandbox --disable-slash-commands`, run in the review copy, with the prompt on stdin | `read_file` inside the review copy (plain absolute paths only, so that nothing agy might expand or decode can point elsewhere), and xreview's read-only tools (`list_files`, `search`, `read_file`, `git` history) as its only MCP server. Everything else is blocked by a gate: a `PreToolUse` hook xreview places in the copy's `.agents` folder, which denies every other tool (shell, edits, web search, scheduling, messages, subagents, other MCP servers) and every read outside the copy; a failing hook blocks the tool too. Session settings add deny rules for commands, writes and URLs, and agy's terminal sandbox. The repository's own agent configuration folders (`.agents`, `.agent`, `_agents`, `_agent`, `.gemini`) are left out of the copy, because agy loads and runs the hooks and MCP servers it finds there |

All run in a fresh session with `XREVIEW_ROLE=reviewer`, which stops xreview from
starting a nested review. None can review its own work: xreview refuses
`--reviewer claude` inside Claude Code, `--reviewer codex` inside Codex and
`--reviewer gemini` inside agy, where the default reviewer is Codex.

agy has no option to ignore your own agy configuration, so Gemini reviews still load
your global agy hooks and plugins (in `~/.gemini/config`), and are saved in your agy
conversation history.

## What the reviewer can read

Whatever a reviewer opens is sent to its provider, so reviewers never work in your
repository itself. Each review gets a private **review copy** in its job folder,
deleted when the review ends. It holds what git would commit, in its current
state:

- tracked files, including your uncommitted edits, and new files that are not
  git-ignored;
- minus files whose names look like credentials, and paths the project marks
  `private`;
- plus git-ignored files the project marks `readable`.

Symlinks are not recreated: one that points at another file of the copy is
copied as that file's content, and any other is left out, as is any path that
runs through a symlinked folder. Files over 32 MB,
binary files over 2 MB, and anything past 50,000 files or 1 GB are left out too.
The prompt lists everything left out, so the reviewer knows a missing file may
exist.

Set the two lists in `.xreview.json` at the repository root, with `.gitignore`
syntax (no `!` negation):

```json
{
  "comment": "Per-study records stay on this server; aggregate metrics may be reviewed.",
  "private": ["data/", "splits/*_ids.csv"],
  "readable": ["outputs/*/metrics.json"]
}
```

- `private` paths never reach a reviewer, even when git tracks them. They are
  left out of the diff, `git status`, the review copy and history, and the review
  only reports how many changed. Uncommitted additions apply at once. Patterns
  match paths, not contents: a file moved out of a private folder is no longer
  private.
- `readable` adds git-ignored files, such as aggregate results, that a reviewer
  may open to check the claims of a change. Only the committed `.xreview.json`
  counts, so an uncommitted edit, by you or an agent, cannot widen access: `start`
  says which entries are waiting for a commit. Outputs are not versioned, so the
  prompt shows each file's modification time next to the newest code change, so
  the reviewer can tell when an output may come from older code.
- An invalid `.xreview.json` stops the review instead of running without it.

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
| Gemini, only with `--reviewer gemini` | `gemini-4-argon` (Gemini 4 Argon) | `high` |

To change them on one machine, create `~/.config/xreview/config.json` (or the
path in `XREVIEW_CONFIG`) with only the fields you want to change:

```json
{
  "timeout_minutes": 30,
  "max_diff_kb": 300,
  "codex":  {"model": "gpt-6-astra", "effort": "medium"},
  "claude": {"model": "claude-opus-5-5", "effort": "high"},
  "gemini": {"model": "gemini-4-argon", "effort": "high"}
}
```

For Gemini, `model` is a base name from `agy models` without the effort suffix
(`gemini-4-argon`, not `gemini-4-argon-high`); agy refuses the two together.

- Set `model` or `effort` to `null` to use that CLI's built-in default instead.
  Codex reviews ignore your `~/.codex/config.toml` (so that none of your MCP servers,
  plugins or hooks reach the reviewer), so its settings do not apply to reviews.
- `doctor` prints the model and effort each reviewer will request on that machine.
- An account without access to the requested model gets a failed review, never a
  silent switch to another model.
- `bin` points at a specific executable when the CLI is not on `PATH`.
- `"codex": {"tools": "repo"}` is for hosts where Codex's sandbox cannot start (see
  [troubleshooting](#troubleshooting)). Codex then has no shell and reads the review copy
  through xreview's read-only tools instead, so it can still open callers, helpers, tests
  and history. Use it only where `doctor` reports the sandbox as broken.
- `"codex": {"tools": false}` is the last resort: diff-only reviews, where Codex sees the
  diff (with whole changed functions) but cannot open other files, so it misses bugs that
  need other code; every such review says "diff only".
- `--model` and `--effort` on `start` override the file for one review.
- Reviews are stored under `~/.local/share/xreview/jobs/` (or `XREVIEW_HOME`). Keep
  `XREVIEW_HOME` out of `/tmp` and `$TMPDIR`: Codex's profile blocks those folders, and
  Codex then refuses to read a review copy inside them.

## Project review rules

A `.xreview.md` file at the repository root is added to every review there as
the user's own instructions. It is written for the reviewer, so it is sent even
when git-ignored, but not when it is a symlink or marked `private`, and only its
first 64 KB. Use it for invariants a reviewer cannot infer, for example:

```markdown
- data/splits/test.csv is the held-out test set: it must never feed model or
  checkpoint selection.
- Preprocessing statistics are fitted on the training split only.
- Experiments must be reproducible from a seed in configs/.
```

## What is sent and stored

- Sent to the reviewer's provider, under your account: the diff, `git status`,
  the author's notes and `.xreview.md`, plus whatever the reviewer opens in the
  [review copy](#what-the-reviewer-can-read).
- Never sent: files whose names look like credentials (`.env*`, `*.pem`, `*.key`,
  `id_rsa*`, `.netrc`, `credentials.json` and similar), which the review lists by
  name; `private` paths, which it only counts; and git-ignored files not marked
  `readable`.
- Inlined in order of importance: code, then configuration, documentation and the
  rest. Data and other non-code changes over 16 KB, binary files, new files over
  200 KB and anything past `max_diff_kb` are listed instead, for the reviewer to
  open in the copy.
- Each job folder keeps the exact prompt, the raw answer, `review.md`,
  `review.json` and `meta.json` (including the model the CLI reports it used). The
  review copy is deleted when the review ends.

## Councils

`xreview.py council start --brief-file <brief>` (the `council` skill) has fresh
sessions of Claude Code, Codex and Gemini deliberate on a question without a diff:
brainstorming, planning, reading results, choosing a research direction.

1. **Proposals.** Each member proposes one to three directions (one or two in a
   council of five or more) without seeing the others.
2. **Cross-critique.** Each member critiques every proposal and ranks the other
   members' ones. Proposals carry member letters, never model names, so no member
   defers to a brand or favours its own.
3. **Memo and sign-off.** The member whose proposal ranked best drafts a joint memo:
   direction, plan, risks, results that would show it wrong, alternatives and open
   questions. Every member signs it, asks for specific changes or objects, and the
   drafter revises, until every member signs the same version or the round limit
   (default 5) is reached. Then the result shows the final memo and the dissent.

By default the members are one session each of Claude Code, Codex and Gemini, with
the reviewer models above. `--members`, or `"members"` in the configuration, takes
any number of members, each written `AGENT[:MODEL][@EFFORT]`; an agent may appear
more than once:

```bash
xreview.py council start --brief-file brief.md \
  --members claude,claude:claude-fable-5-1,codex:gpt-6-astra@high,codex:gpt-6.1-sol,gemini
```

A member without a model or effort uses that agent's reviewer setting. xreview passes
model names to the CLI unchecked: a misspelt one makes that member fail in round 1,
which rules out consensus. Every member is an equal member: it answers each round at
the same time as the others, its ranking counts the same when the drafter is chosen,
and consensus takes its signature. Members of one agent share that agent's account, so
reports name them by model (and by effort too when two share a model), and if they
fail on the account's rate limits, `"parallel_per_agent": N` in the configuration lets
only N members of one agent answer at once.

Members are fresh sessions that cannot start reviews or councils, and they work like
reviewers: each in its own read-only copy of the project (what git would commit,
minus credential-like and `private` paths), with the same confinement as in
[What the reviewer can do](#what-the-reviewer-can-do). `--include` adds files or
folders that git ignores, such as results and logs, for that council only;
credential-like and private files among them are refused. Outside a git repository,
members get only the brief and the included files.

With web search on (the default; `--no-web`, or `"web": false` in the configuration,
turns it off):

| Member | Web access |
|---|---|
| Codex | Codex's web search in cached mode, which searches OpenAI's index; Codex fetches no pages itself, and its shell still has no network |
| Claude Code | `WebSearch`, and `WebFetch` for pages on the hosts in `fetch_hosts` only (exact host names: `arxiv.org` does not cover `export.arxiv.org`); every other fetch is refused |
| Gemini | agy's `search_web`, allowed by the gate; it cannot open URLs |

So a page that tries to talk a member into sending your results somewhere can
reach only the search providers and the listed research sites. Search queries go to
the providers, so with web search on, details of unpublished work can reach them.

A council of n members makes 3n + 1 to 2n + (rounds - 2)(n + 1) calls when nobody
leaves: 10 to 18 for three members and the default five rounds, 22 to 38 for seven.
Three members usually take 10 to 25 minutes, and larger councils longer; from nine
members on, the drafter reads shortened critiques. A council uses every member's
quota, including Antigravity credits. Each
job folder keeps the brief, every prompt and answer (`calls/`), `council.md` (what
`show` prints) and `transcript.md` (the whole discussion, with model names). Gemini
members' sessions are saved in your agy conversation history, as reviews are.

Settings, in the same configuration file:

```json
{
  "council": {"members": ["claude", "codex", "gemini"], "rounds": 5, "web": true,
              "fetch_hosts": ["arxiv.org", "openreview.net", "pubmed.ncbi.nlm.nih.gov"]}
}
```

Setting `fetch_hosts` replaces the default list (research and preprint sites, journals,
GitHub, Hugging Face and library documentation); `xreview.py council start --help`
lists the other options. Gemini is optional: when agy is not set up, the default
council has two members.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Claude Code is not logged in for command-line use` | Run `claude auth login` in a terminal on that machine |
| `Codex is not logged in` | Run `codex login` |
| `codex-code-mode-host is missing` warning, or Codex returns `cannot_review` saying it could not read files | That Codex is a bare binary without its companion tools. Reinstall it with OpenAI's installer: `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` |
| `doctor` says Codex's sandbox cannot keep its shell inside the review copy, with `bwrap: ... Operation not permitted` or `unsupported host mount` (hosts whose home is a bind mount) in the output | Codex cannot run commands in its sandbox on that host, and xreview never runs it without one. Add `"codex": {"tools": "repo"}` to that machine's config: Codex then reads the repository through xreview's read-only tools instead of a shell |
| `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` (Codex 0.157+), or `unsupported host mount` (0.156.x) | The host blocks the user namespaces Codex's sandbox needs; common on Ubuntu 24.04 with `kernel.apparmor_restrict_unprivileged_userns=1` and no system `bubblewrap`, for example container login nodes. An administrator can install the distribution's `bubblewrap` package (it carries the AppArmor profile); until then use `"tools": "repo"` there |
| `Codex could not confine its shell to the review copy`, or `doctor` says it `cannot keep its shell inside the review copy` | xreview runs Codex's shell only under a permission profile that can read the copy alone, and never falls back to Codex's plain sandbox, which reads every file you can. Profiles need Codex 0.138 or later, so upgrade Codex; if that is not possible, use `"tools": "repo"` |
| `fs sandbox helper failed` | Codex re-runs its own binary inside the profile, and could not read it. Point `"codex": {"bin": ...}` at the native Codex binary rather than a wrapper script, or use `"tools": "repo"` |
| `.xreview.json is not valid JSON`, `unknown key`, `negated patterns are not supported` | Fix `.xreview.json`; xreview will not review without knowing what is private |
| Gemini: `the Antigravity CLI is not logged in`, or `authentication failed` | Run `agy` once in a terminal on that machine and finish the Google sign-in |
| Gemini: `Your AI credits balance is too low to continue` | The Antigravity account is out of AI credits. Wait for them to refill, or choose a cheaper model such as `"gemini": {"model": "gemini-3.8-flash"}` |
| Council: `<member> left the council: ...` | That member's call failed (see the reason, for example an expired login, no credits or a misspelt model name). The council went on without it, and the result is at best `no_consensus` |
| Council: `FAILED - fewer than two members ...` | A council needs at least two members; check each with `doctor --reviewer <name>` |
| Gemini: `the Gemini review gate failed its check` | The hook that keeps agy read-only could not run (for example the Python that runs xreview moved), so the review did not start. `doctor --reviewer gemini` shows why |
| `running inside the Codex sandbox` | Re-run `start` with escalated permissions |
| `Operation not permitted` writing `~/.local/share/xreview` | The shell is sandboxed; run `start` outside the sandbox |
| `nothing to review` | No changes against HEAD; for committed work pass `--base <branch-or-commit>` |
| `already reviewed` | The exact diff was reviewed before; `show <id>` prints it, `--force` asks again |
| A review that never finishes | `cancel <id>`; the job fails after `timeout_minutes` anyway |
