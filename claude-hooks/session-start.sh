#!/bin/sh
# Claude Code SessionStart hook: adds xreview's "when to review" rule to the context.
# Claude picks skills by the topic of the request, so with the skill description
# alone it rarely reviewed its own finished work (evals/fires-after-implementing).
# Codex does not load this file; it triggers the skill from its description.
cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "xreview is installed. When you finish a substantial or risky code change (new logic, multi-file edits, refactors, non-obvious bug fixes, or data, training, evaluation, metrics or job-script code), invoke the xreview skill before telling the user the work is done. Skip it for questions, docs, formatting and small obvious edits, and when the user says not to."}}
JSON
