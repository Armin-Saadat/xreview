# xreview maintenance instructions

This repository is the `xreview` plugin: two skills, `xreview` (reviews) and
`council` (deliberation), that install in both **Claude Code** and **Codex** from
one GitHub repository. These instructions are for anyone (human or agent) editing
the repository itself; they are not loaded into projects that install the plugin.

## Layout

| Path | Role |
|---|---|
| `skills/xreview/SKILL.md` | Skill entry point: frontmatter `name` + `description`, then a short body |
| `skills/xreview/references/setup.md` | Setup, configuration and troubleshooting, loaded on demand |
| `skills/xreview/scripts/xreview.py` | The tool the skill runs: diff collection, reviewer launch, results |
| `skills/xreview/agents/openai.yaml` | Codex UI metadata (`display_name`, `short_description`, `default_prompt`) |
| `skills/council/` | The `council` skill: Claude, Codex and Gemini deliberate and sign off; it runs `../xreview/scripts/xreview.py council` |
| `plugin.json` | Portable/Codex manifest; Codex caches installs by its `version` |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | Claude Code manifest and marketplace (no `version`, so Claude tracks commits) |
| `claude-hooks/` | Claude Code SessionStart hook that restates when to review; Claude picks skills by topic and otherwise rarely reviews its own work. Keep its wording in step with the skill description |
| `.agents/plugins/marketplace.json` | Codex marketplace |
| `.agents/skills -> ../skills` | Lets Codex discover the skill while working inside this repository |
| `tests/` | Offline unit tests; fake `codex`/`claude` executables stand in for the reviewers |
| `evals/` | Claude Code trigger evals (`claude plugin eval`) |
| `tools/validate.py` | Pre-commit validation |

## Writing the skill

- The `description` is the only part always in context, and it is what makes the
  agent decide to review. Keep it under about 700 characters, with concrete
  triggers. Codex lists every installed skill's description within a small budget.
- After changing the description or the reminder in `claude-hooks/`, run the
  trigger evals. Both directions matter: the review must fire after real work
  and stay quiet for questions and small edits.
- Keep `SKILL.md` short: the steps, how to read the result, and the rules that
  keep reviews honest. Put setup and troubleshooting in `references/`.
- Explain why a rule exists instead of stacking MUST/NEVER; agents generalize
  from reasons.
- Write for both agents: plain Markdown, relative links, paths relative to the
  skill folder (`<skill-dir>/scripts/xreview.py`), and no tool names specific to
  one product. Describe capabilities instead ("if your shell can run a command
  in the background...").

## Changing the script

- Standard library only, Python 3.8+ (check with
  `python3 -c "import ast; ast.parse(open('skills/xreview/scripts/xreview.py').read(), feature_version=(3, 8))"`).
- **Fail closed.** Anything unexpected from a reviewer (non-zero exit, timeout,
  empty or schema-violating answer) is a failed review, never an approval.
  Reject malformed answers; do not repair or drop parts of them.
- Never run a reviewer with a permission bypass or outside its sandbox, and never
  fall back to one when the sandbox fails.
- Reviewers only ever see the review copy (what git would commit, minus
  credential-like and `private` paths, plus committed `readable` ones), never the
  repository itself: whatever a reviewer reads goes to its provider. Codex's shell
  must stay under the permission profile that confines its reads to that copy.
- Gemini reviews (agy) must keep xreview's PreToolUse gate in the copy's `.agents`
  folder, and the repository's own agent configuration folders out of it: agy runs
  workspace hooks and MCP servers without asking. The gate allows only reads inside
  the copy, xreview's repository tools and the final answer; anything new denies.
- Council members are reviewers in this respect too: fresh sessions with
  `XREVIEW_ROLE=reviewer`, each in its own copy, under the same confinement. Web
  access stays with the providers' search tools (Codex `web_search="cached"`, agy
  `search_web` through the gate's `--web` mode) and Claude's `WebFetch` on the exact
  `fetch_hosts`; never a shell with network. A failed or missing answer is never a
  signature.
- Councils read members' answers as a person would (the user's choice): a proposal
  named by label and title counts, and critiques and rankings need not be complete,
  because those fields only pick the drafter and inform the memo. What would fake
  agreement or leave nothing to use stays strict: an answer that is not the schema's
  JSON, a memo without a direction or plan, a "sign" that still asks for changes. Such
  an answer is asked for once more, with the reason, never repaired; a failed call
  (error, timeout) is not repeated. Reviews stay strict throughout.
- Never print or send credentials. Extend `SECRET_GLOBS` rather than special-casing.
- Every behaviour change gets a test in `tests/test_xreview.py`. Use the fake CLI
  (`FAKE_MODE`) rather than real model calls.
- After substantive changes, have xreview review itself: run
  `python3 skills/xreview/scripts/xreview.py start --intent "..."` in a checkout,
  and address the findings.

## Validate and release

```bash
python3 tools/validate.py                      # frontmatter, metadata, links, manifests, version bump, evals, secrets
python3 -m unittest discover -s tests -v       # offline, no quota
claude plugin validate .                       # Claude Code's own manifest check, if the CLI is installed
claude plugin eval . --scaffold --ablation none --allow-tools Edit "Bash(python3 *)" "Bash(git *)"   # trigger evals; uses Claude quota
```

1. Raise `version` in `plugin.json` (semantic versioning) whenever anything under
   `skills/` or a manifest changes. Codex installs are cached per version, so
   without a bump Codex users keep the old copy. `tools/validate.py` fails until
   you do. README-only edits need no bump. Do not add a version to
   `.claude-plugin/`.
2. Keep `plugin.json` and `.claude-plugin/plugin.json` identical in description,
   author, homepage, repository, license and keywords; the validator checks this.
3. Commit with a Conventional Commit message (`feat(review): ...`, `fix(script): ...`,
   `docs: ...`) and push to `main`; installs read the default branch.
4. Users pick it up with `/plugin marketplace update xreview` (or auto-update) in
   Claude Code and `codex plugin marketplace upgrade xreview` in Codex.
