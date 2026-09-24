---
name: release-prep
description: Bump the erpnext_enhancements version and write the CHANGELOG entry that every change requires. Use when finishing any change, when the version-sync CI job fails, or when asked to bump the version or update the changelog.
---

# Release prep

Every PR bumps the version. `main` deploys automatically and `release.yml` tags each
new `__version__`, so the Releases page is a 1:1 log of what is deployed — a change that
lands without a bump is invisible in that log.

## 1. Pick the bump

Current value is in `erpnext_enhancements/__init__.py`.

| Bump | When |
|---|---|
| **PATCH** | Bug fixes, docs, refactors, comment changes, fixture re-exports with no behaviour change |
| **MINOR** | A new DocType, endpoint, module, patch, integration, or workspace |
| **MAJOR** | Breaking changes to a doctype contract, an endpoint, or a settings field other code depends on |

## 2. Update both files

| File | Field |
|---|---|
| `erpnext_enhancements/__init__.py` | `__version__ = "X.Y.Z"` |
| `package.json` | `"version": "X.Y.Z"` |

These are a **hard CI gate**, not a convention. The `version-sync` job compares them on
every push and PR. It exists because bumping only one used to pass CI, merge, and then fail
`release.yml` *after* the merge — leaving `main` deployed but untagged until someone noticed
(this happened at v0.9.0).

Check:

```bash
sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' erpnext_enhancements/__init__.py
sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p' package.json | head -1
```

## 3. Write the CHANGELOG entry

`CHANGELOG.md`, [Keep a Changelog](https://keepachangelog.com/) format, newest at the top:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Fixed
- What was broken, what the user saw, what happens now, and *why* it was broken.
```

Groups: `Added` / `Changed` / `Fixed` / `Removed` / `Deprecated` / `Security`.

**Match the surrounding depth.** The entries in this file are long on purpose — this
changelog is the best available history of upstream-Frappe-bug context and platform
workarounds, and `release.yml` publishes the matching section verbatim as the GitHub Release
notes. A one-line entry that says "fix bug" is a real loss, because six months from now it
is the only record of why an odd construct exists.

If you worked around an upstream bug, name it (`frappe/frappe#24156`). If a change is
dormant until a patch flips it on, say so.

### The `Refs:` line: shipped feedback work reports back

If the release ships Tasks that came from an Enhancement Request (their Claude Code brief
lists them, and so does the request's "On the board" panel), put one line anywhere in the
release's section:

```markdown
Refs: ER-2026-00012, TASK-2026-00345, TASK-2026-00346
```

Within an hour of the deploy completing, `product_feedback/release_sync.py` moves each named
Task to `Pending Review` with a `review_date` 14 days out and a comment naming the release. It
never moves one to `Completed`: a person closes shipped work.

- **Copy the line from the brief, and paste it bare.** The brief prints this exact line in a
  block of its own under its last acceptance criterion: the request id, then its open leaf Task
  ids. Paste it on its own line **without backticks or bold**, even though house style would
  put an identifier in a code span. A backticked or bolded line is ignored, silently.
- **Name only the Tasks this release actually shipped.** Only `TASK-…` ids move anything, so a
  release that ships part of a request lists the Tasks it shipped and leaves the rest open.
  Keep the `ER-…` id first: it is a cross-check, and a Task on the line that belongs to another
  request is skipped, so a typo in a Task id cannot move a neighbor's work. Anything else on the
  line, such as a `WI-079`, is ignored.
- **Register it in the test, in the same change.** `tests/test_feedback_release_sync.py` runs
  the parser over the real CHANGELOG and fails the build on any Refs line that names a Task
  unless `SHIPPED_REFS` in that file lists the release and its exact line. That is what stops an
  example of this convention from moving real Tasks. So add
  `"<version>": ["Refs: ER-…, TASK-…"]` there beside the CHANGELOG line.
- **The parser is strict on purpose**, because this file documents the convention with
  examples. `Refs:` is case-sensitive and starts its line, after at most three spaces and an
  optional list marker (`- Refs: …` works). A line that starts with a backtick or `**`, one
  indented four spaces or a tab, one inside a fenced block (like the example above) or an HTML
  comment, and a mention mid-sentence, are not Refs lines. Write every example that way.
- **It acts only on what is installed.** The sync reads the version `tabInstalled Application`
  records, which a migrate writes only when it gets that far. A section above that version
  waits, so a deploy whose migrate aborted never marks its Tasks.
- **It is safe to be wrong in one direction.** A Task that did not come from an Enhancement
  Request, or is already `Pending Review`, `Completed`, `Canceled`, `Invoiced` or `Template`, is
  left alone, and so is an `Overdue` one a person had canceled (ERPNext's overdue job flips this
  site's `Canceled` to `Overdue`). A Task id that does not exist is skipped, never retried.

## 4. Keep the rest in sync

- `hooks.py` and the relevant module `README.md` if you added a customization.
- The per-directory README's file-map table if you added a file.
- A new patch needs a row in `patches/README.md` and a line in `patches.txt` — see the
  `fixtures-and-patches` skill.
