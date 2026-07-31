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

## 4. Keep the rest in sync

- `hooks.py` and the relevant module `README.md` if you added a customization.
- The per-directory README's file-map table if you added a file.
- A new patch needs a row in `patches/README.md` and a line in `patches.txt` — see the
  `fixtures-and-patches` skill.
