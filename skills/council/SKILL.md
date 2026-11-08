---
name: council
description: Convene a council of AI agents (Claude Code, Codex and Gemini) that deliberate over several rounds, critique each other's proposals and sign off on one direction. Use when the user asks the agents to brainstorm together, plan next steps, analyze experiment results, choose a research direction or settle a design question, or asks for a council, a roundtable or the other agents' views on a plan. Not for reviewing code changes (use xreview) or questions you can answer yourself.
---

# Council

Fresh sessions of Claude Code, Codex and Gemini discuss a question in rounds:
independent proposals, an anonymous cross-critique, then a joint memo that every
member must sign. You are the chair: you write the brief, start the council and
report what it concluded. You do not argue; a fresh session of your own model is
one of the members. The conclusion is advice for the user, never permission to act.

The council runs the xreview script at `<skill-dir>/../xreview/scripts/xreview.py`
(`xreview.py` below). It usually takes 10–25 minutes and uses the quota of every
member's account, so convene one only when the user asks for it.

## Steps

1. **Pin down the question.** If it is vague, ask the user one short question
   first: what decision they need, and what a good answer looks like.
2. **Write a neutral brief** to a file outside the repository, for example in the
   system temp folder: the question; the goal; the context; what has been tried;
   key results with their numbers and where they live; constraints (compute,
   data, deadlines, what must not change); and what the user wants back. Leave
   out your own preferred answer, so that members start from the facts.
3. **Start it** from the project folder:

   ```bash
   python3 <skill-dir>/../xreview/scripts/xreview.py council start --brief-file <brief>
   ```

   - `--include <files or folders>` adds results or logs that git ignores, for the
     members to read. Include only what the question is about; credential-like
     and private files are refused anyway.
   - `--no-web` if the user wants no web search; it is on by default.
   - `--members claude,codex` or `--rounds N` (3 to 10, default 5) only if asked.

   It prints a council id. If your shell runs commands in a sandbox without
   network access, run it outside the sandbox.
4. **Wait for the result.**
   - If your shell tool can run a command in the background and notify you, run
     `python3 xreview.py wait <id> --timeout 3600` that way.
   - Otherwise run `wait <id>` (it returns within about 2 minutes and says which
     round the council is in) and repeat it until it prints a result.
5. **Report** the outcome line, the agreed direction and plan, the risks, the
   results that would show the direction is wrong, any dissent (who objected and
   why) and the open questions, plus the paths of `council.md` and
   `transcript.md`. Present it as the council's advice: the user decides. Do not
   start experiments, jobs or large changes on its strength without the user's
   go-ahead.
6. **Follow up** when new results arrive or the user pushes back:
   `council start --followup <id> --brief-file <new brief>` gives the members the
   earlier memo and sign-offs.

## Reading the result

The last line is
`XCOUNCIL <id>: <consensus|no_consensus> (<n> of <m> members signed memo version <v>; <r> rounds)`.

| Exit code | Meaning |
|---|---|
| 0 | Consensus: every member signed the same version of the memo |
| 2 | No consensus: the final memo, with who objected or asked for changes and why |
| 1 | The council failed (for example, fewer than two members could take part): no conclusion |
| 4 | Still running |

A member whose call fails (an error, a timeout, a malformed answer, no credits)
leaves the council, and the result says so; a missing answer never counts as
agreement. What members can read and do, web access and settings are in
[setup](../xreview/references/setup.md#councils).
