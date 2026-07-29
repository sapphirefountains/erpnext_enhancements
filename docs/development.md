# Development guide

## Local setup

This is a Frappe app, so it runs inside a [bench](https://github.com/frappe/bench):

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app erpnext_enhancements <repo-url> --branch main
bench install-app erpnext_enhancements
```

Installing pulls the Python dependencies from [`pyproject.toml`](../pyproject.toml) (Google
API and Gemini client libraries). Frappe itself is managed by bench. Requires Python ≥ 3.10;
CI runs 3.12.

After install, configure the integrations you need through their Single settings doctypes —
see [External integrations](../README.md#external-integrations) in the root README.

> **HTTPS is required for the Time Kiosk.** Geolocation, service workers, and PWA install
> only work over HTTPS (`localhost` is exempt).

## Linting and formatting

Install pre-commit once:

```bash
cd apps/erpnext_enhancements
pre-commit install
```

Hooks ([`.pre-commit-config.yaml`](../.pre-commit-config.yaml)): **ruff** (lint + format),
**eslint**, **prettier** (JSON/CSS/Markdown/YAML), **pyupgrade**.

`ruff format` is configured for **tabs**, double quotes, `line-length = 110`
([`pyproject.toml`](../pyproject.toml)). But **indentation in this repo is mixed** — Frappe
convention is tabs and most files follow it, while several (much of `api/`, all of
`water_engineering/engine/`) use 4 spaces. **Match the file you are editing.** Never
normalise a file you are only passing through.

`ruff check` runs in CI as **advisory** (`continue-on-error`) because of a known pre-existing
backlog. A red lint job is probably not your change. Do not run a repo-wide `ruff --fix` or
`ruff format` as a drive-by — it will bury the actual diff.

## Testing

Tests split three ways, and picking the wrong one is the most common way to write a suite
that never runs.

| Kind | Command | Runs in CI |
|---|---|---|
| Bench-required (`FrappeTestCase`) | `bench --site <site> run-tests --app erpnext_enhancements` | **No** |
| Bench-free, `unittest` | `python -m unittest erpnext_enhancements.tests.<module> -v` | Yes |
| Bench-free, `pytest` | `python -m pytest erpnext_enhancements/tests/<file>.py -q` | Yes |

**`python -m unittest` silently cannot collect pytest-style function tests.** It does not
error — it collects nothing and reports success. The QuickBooks Online suite was listed on a
unittest step, ran nowhere, and stayed broken for weeks. A new bench-free *pytest* suite must
get its own `python -m pytest` step in [`ci.yml`](../.github/workflows/ci.yml).

Several bench-free suites get their **own CI step** rather than being folded into a
multi-module list, because each installs its own `frappe` stub in `setUpModule` and a
separate process keeps them from cross-talking.

There is **no Frappe integration-test job**. It was removed because it never reached our own
assertions: Frappe v16's test-record auto-generation walks the entire ERPNext doctype
dependency graph and kept aborting on environment gaps, so each fix only surfaced the next.
Reintroduce one once the upstream harness stabilises.

Two non-test guards also run in CI:

- `scripts/check_www_controllers.py` — a `www/` controller whose filename contains a hyphen
  is never imported by Frappe, so its `get_context()` silently never runs.
  `stripe-return.py` was broken this way from the day it was written until v1.159.10.
- `tests/test_doctype_modules.py` — every custom DocType must sit under its declared module's
  directory, and that module must be registered in `modules.txt`.

See [`erpnext_enhancements/tests/README.md`](../erpnext_enhancements/tests/README.md).

## Versioning and releases

**Every PR bumps the version.** Three things must stay in sync:

1. `erpnext_enhancements/__init__.py` → `__version__`
2. `package.json` → `"version"`
3. A new dated section in [`CHANGELOG.md`](../CHANGELOG.md) (Keep a Changelog format)

The `version-sync` CI job compares (1) and (2) on **every push and PR** — a hard gate. It
exists because bumping only one used to pass CI, merge, and then fail `release.yml` *after*
the merge, leaving `main` deployed but untagged (this happened at v0.9.0).

[`release.yml`](../.github/workflows/release.yml) tags and publishes a GitHub Release whenever
a new `__version__` lands on `main`, using the matching `CHANGELOG.md` section as the release
notes. Because Frappe Cloud deploys from `main`, the Releases page is a 1:1 log of what is
deployed — which is why changelog entries here are long and explain *why*.

## Where a change goes

| You're adding… | Put it in… | Also update… |
|---|---|---|
| A whitelisted HTTP endpoint | `erpnext_enhancements/api/<area>.py` | `api/README.md` file map |
| A DocType | `<module>/doctype/<name>/` | `modules.txt` if new module, module README |
| A Custom Field / Property Setter | `fixtures/*.json` | a deletion also needs a patch |
| A one-time data migration | `patches/<name>.py` | `patches.txt`, `patches/README.md` |
| Something re-asserted on every migrate | `setup/<name>.py` | `hooks.py`, `setup/README.md` |
| An MCP tool for AI assistants | `assistant_tools/<tool>.py` | `hooks.py`, `assistant_tools/README.md` |
| A browser asset | `public/{js,css}/<module>/` | the relevant bundle entry |

Any hook-driven customization also needs a line in `hooks.py`, which is annotated — keep that
density.

## Deployment

Frappe Cloud deploys from `main`. [`cloudbuild.yaml`](../cloudbuild.yaml) and
[`infra/`](../infra/) cover the supporting Google Cloud infrastructure (the Terraform modules
live in [`modules/`](../modules/)).

`bench migrate` runs the `before_migrate` / `after_migrate` hooks in
[`setup/`](../erpnext_enhancements/setup/README.md), then patches, then fixture sync. Expect a
noticeably longer migrate on any deploy that changes the fixture files — it re-imports all
~774 records.

## Context files for AI contributors

- [`CLAUDE.md`](../CLAUDE.md) — repo purpose, the version rule, and the gotchas that are
  costly to rediscover. Deliberately short; it is always in context.
- [`.claude/skills/`](../.claude/skills/) — procedures that load only when relevant:
  `add-doctype`, `add-endpoint`, `run-tests`, `fixtures-and-patches`, `release-prep`,
  `work-item`.

When a convention or a gotcha changes, update the skill that carries it rather than growing
`CLAUDE.md`.
