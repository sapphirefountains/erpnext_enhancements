# Working in this repository

`erpnext_enhancements` is Sapphire Fountains' custom Frappe app: ~94k lines of Python
across 40+ modules customising ERPNext for a fountain design, build, service and rental
business. It runs on Frappe/ERPNext v16 and deploys from `main` to Frappe Cloud.

[`README.md`](README.md) has the module map, the wiring model, and an index of the
per-directory READMEs — every module documents itself next to its code. Read those rather
than the tree. [`PLAN.md`](PLAN.md) is the migration master plan; open work is tracked as
`WI-xxx` files in [`work-items/`](work-items/).

## Mandatory: version + changelog on every change

**Every change bumps the version and adds a changelog entry.** Three things must agree:

1. `erpnext_enhancements/__init__.py` → `__version__`
2. `package.json` → `"version"`
3. A new dated section in [`CHANGELOG.md`](CHANGELOG.md), Keep a Changelog format

Semver: PATCH for fixes/docs/refactors, MINOR for new features or endpoints, MAJOR for
breaking changes. CI has a hard `version-sync` gate comparing (1) and (2) on every push, and
`release.yml` refuses to tag when they disagree — so a half-finished bump fails the PR.

Write the changelog entry properly. This changelog is the best available history of *why*
a workaround exists — when you work around an upstream Frappe bug or a platform limit, that
explanation belongs here. The `/release-prep` skill walks the whole ritual.

## Gotchas

Verified, and all of them expensive to rediscover:

- **Indentation is mixed and you must match the file you're editing.** Frappe convention is
  **tabs**, and `ruff format` is configured for tabs, double quotes, `line-length = 110`.
  But several files — most of `api/`, for instance — use 4 spaces. `api/README.md` lists
  which files in that package are tabs. Never "normalise" a file you're only touching.
- **`ruff check` is advisory in CI** (`continue-on-error`) because of a known pre-existing
  backlog. A red lint job is probably not your bug. Do not run a repo-wide `ruff --fix` or
  `ruff format` as a drive-by — it will bury your actual change in thousands of lines.
- **There is no Frappe integration-test job in CI.** It was removed because it never
  reached our assertions: Frappe v16's test-record auto-generation walks the whole ERPNext
  doctype dependency graph and kept aborting on environment gaps. Most suites under
  `tests/` need a real bench (`bench --site <site> run-tests --app erpnext_enhancements`);
  CI runs only the bench-free ones.
- **Bench-free suites split between `unittest` and `pytest`, and the split is load-bearing.**
  `python -m unittest` silently cannot collect pytest-style function tests — the QuickBooks
  suite ran nowhere and broke unnoticed for weeks because of this. A new bench-free
  *pytest* suite must be added to a `python -m pytest` step in `ci.yml`, not appended to a
  unittest module list. Each bench-free suite installs its own `frappe` stub in
  `setUpModule`, so several get their own CI step to keep them from cross-talking.
- **Defensive hooks are load-bearing.** `doc_events` fire during ERPNext's own test
  bootstrap, before this app's custom fields exist. That is why custom-field reads use
  `getattr(obj, "field", None) or ""` and column-filtered queries guard with
  `frappe.db.has_column(...)`. Preserve those guards; removing one turns a fresh-DB install
  into a crash.
- **The repo is the source of truth for customizations, and deletion is two steps.**
  ~425 Custom Fields and ~349 Property Setters live in `fixtures/*.json` and are applied by
  `bench migrate`. Removing a record from the JSON only stops managing it — it does **not**
  delete it from the database. You also need a one-shot patch calling
  `frappe.delete_doc(...)`. See [`fixtures/README.md`](erpnext_enhancements/fixtures/README.md).
- **Every custom DocType must sit in its declared module.** `tests/test_doctype_modules.py`
  asserts it against the filesystem and fails the build otherwise.
- **A `www/` controller whose filename contains a hyphen is never imported by Frappe**, so
  its `get_context()` silently never runs. `stripe-return.py` was broken this way from the
  day it was written. `scripts/check_www_controllers.py` guards it in CI.
- **Global assets ship as esbuild bundles (`name.bundle.js/css`), not raw `/assets` paths.**
  Raw paths are served immutable for a year with no content hash, so edits never reach a
  device that already cached them — the "fix works on desktop, phones still broken" bug.
  The two vendored UMD libraries are deliberate exceptions: bundling them would capture
  their exports instead of letting them set `window.Vue` / `window.Gantt`.
- **`stripe_payments` deliberately has no Stripe SDK dependency.** The host is a managed
  server where packages can't be pip-installed, so it talks to the Stripe REST API with
  `requests` and hand-rolls webhook signature verification, mirroring the QuickBooks
  client. Don't "fix" this by adding the SDK.

## Conventions

Match the file you're editing — indentation, quoting, and comment density all vary by
module, and the surrounding code is the spec. Keep `hooks.py` and the relevant module
README in sync when you add a customization; `hooks.py` is annotated and that annotation is
part of the documentation. A docs-only change must not alter executable behaviour.

Deeper procedures live in [`.claude/skills/`](.claude/skills/) and load when relevant:
`add-doctype`, `add-endpoint`, `run-tests`, `fixtures-and-patches`, `release-prep`,
`work-item`.
