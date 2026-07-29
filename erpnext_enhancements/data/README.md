# `data/` — Frappe Assistant Core skills

Workflow prompt templates ("skills") that Frappe Assistant Core exposes to connected AI
assistants, registered via the `assistant_skills` hook in `hooks.py`.

| Path | Contents |
|---|---|
| `assistant_skills.json` | The registry FAC reads |
| `skills/*.md` | One markdown file per skill |

## Current skills

| Skill | Purpose |
|---|---|
| `project_status_reporter.md` | Guides an assistant through producing a project status report |
| `maintenance_dispatcher.md` | Maintenance scheduling and dispatch workflow |
| `time_tracking_analyst.md` | Time-tracking analysis |
| `ai_write_confirmation.md` | How the AI write-confirmation gate works, from the assistant's point of view |

## Skills are prompts, not permissions

A skill tells an assistant *how* to approach a task. It grants nothing. The tools it can
actually call are the ones in [`../assistant_tools/`](../assistant_tools/README.md), and every
mutation still goes through the write-confirmation gate regardless of what a skill says.

Write them accordingly: a skill that instructs the model to "confirm and proceed" describes a
step the model cannot take, and will produce confident wrong answers about what happened.
`ai_write_confirmation.md` exists precisely to tell the model the truth about that boundary.

## Adding one

Add the markdown file under `skills/`, register it in `assistant_skills.json`, and add a row
above. Keep each skill focused — they are loaded by name, so a broad skill costs context on
every task that matches it loosely.
