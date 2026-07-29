# 0007. Tolerate mixed indentation rather than reformat

- **Status:** Accepted, temporary
- **Date:** 2026-07-29 (recorded retroactively)

## Context

Frappe's convention is **tabs** for Python, and `ruff format` here is configured to match
(`indent-style = "tab"`, `quote-style = "double"`, `line-length = 110`). Most of the app
follows it.

Several parts do not. Most of `api/` uses 4 spaces — though `analytics.py`, `collab.py`,
`comments.py`, `user_drafts.py` and `integrations_health.py` are tabs — and all of
`water_engineering/engine/` is 4-space. The app also absorbed code from more than one origin,
including ported database-stored scripts.

Running `ruff format` across the repository would fix it in one commit. It would also rewrite
tens of thousands of lines, making every subsequent `git blame` useless for the history that
matters most — the workaround comments explaining upstream Frappe bugs — and turning any
review of a real change during that window into archaeology.

There is a related backlog: `ruff check` reports pre-existing findings, mostly whitespace and
import ordering.

## Decision

Tolerate the inconsistency. **Match the file you are editing** — never normalise a file you
are only passing through.

`ruff check` runs in CI as **advisory** (`continue-on-error`), so the backlog is visible on
every PR without blocking merges.

The indentation split is documented where someone will actually hit it: at the top of
`api/README.md`, in the root README's Conventions section, and in `CLAUDE.md`.

## Consequences

- **A mixed-indentation file is a correctness hazard, not just an aesthetic one.** Python
  cares. `ruff` and `tabnanny` catch it, but only after you have written it.
- Contributors — human and AI — must read before writing. "The house style is tabs" is true
  and insufficient; the file in front of you is the specification.
- The advisory lint job is easy to learn to ignore, which is the cost of this decision. A red
  lint job is probably not your bug, and that is exactly the habit that lets a real finding
  slide past.
- **Do not run a drive-by `ruff --fix` or `ruff format`.** It will bury the actual change.
- The intended end state is a dedicated cleanup PR whose only content is the reformat,
  followed by dropping `continue-on-error` to make lint a hard gate. It has to be its own
  change precisely so it can be reviewed as one — which is why it has not happened by
  accident.
