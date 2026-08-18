# Working in this repository

`erpnext_enhancements` is Sapphire Fountains' custom Frappe app: ~94k lines of Python
across 40+ modules customising ERPNext for a fountain design, build, service and rental
business. It runs on Frappe/ERPNext v16 and deploys from `main` to Google Cloud.

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
  ~521 Custom Fields and ~409 Property Setters live in `fixtures/*.json` and are applied by
  `bench migrate`. Removing a record from the JSON only stops managing it — it does **not**
  delete it from the database. You also need a one-shot patch calling
  `frappe.delete_doc(...)`. See [`fixtures/README.md`](erpnext_enhancements/fixtures/README.md).
- **Every custom DocType must sit in its declared module.** `tests/test_doctype_modules.py`
  asserts it against the filesystem and fails the build otherwise.
- **A `default` on a *new* field of a Single doctype never reaches the row that already
  exists.** A Single stores one row per field in `tabSingles`; `bench migrate` adds no row for
  a newly declared field, and `load_from_db` applies no defaults — they fire in `new_doc()`,
  i.e. on a fresh install and never again. So the field reads `None`/`0` on every existing
  site while the JSON says otherwise. Harmless until a controller *rejects* that value: adding
  37 fields to Chat Settings made its settings page **unsaveable**, because `validate` refused
  the fifteen zeros (v1.277.3). Note the shape of it — saving a Single deletes and re-inserts
  every field row, so a page anyone actually uses self-heals on the next save, and the ones
  that bite are the settings for **dormant** features, where the first save is the one you
  need and the one that fails. Ship a backfill patch with the fields; there are 20 Singles in
  this app. See [`patches/backfill_chat_settings_defaults.py`](erpnext_enhancements/patches/backfill_chat_settings_defaults.py).
- **On a *normal* doctype the same `default` reaches every existing row — the exact opposite
  — and a backfill patch written for the Single behaviour will silently match nothing.**
  Adding a column with a default is one `ALTER`, and MariaDB writes the default into every row
  as part of it. So `Chat Relay Job.auth_identity` (`default: "USER"`) was `USER` on the entire
  existing queue before any patch could look at it, and the patch keyed on
  `coalesce(auth_identity, '') = ''` matched zero rows, committed, and recorded itself in
  `tabPatch Log` — which is indistinguishable from a successful run (v1.280.3). **"New field,
  existing rows" has two opposite answers depending on the storage model, and neither is the
  one you assume.** Check which you are on before choosing the predicate, and prefer a
  backfill keyed on the *rule the writer applies* over one keyed on emptiness: emptiness is a
  fact about the schema migration, not about the data.
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
- **The sibling `frappe` and `erpnext` checkouts are on `develop`, and production is not.**
  If you have `../frappe` and `../erpnext` alongside this repo, their working trees read
  `17.0.0-dev` while the site this app deploys to runs **16.x**. Reading them to settle a
  question about framework behaviour is the right instinct and the wrong tree: line numbers
  differ substantially in `permissions.py`, `db_query.py` and `api/v2.py`, and behaviour
  differs too — v17 adds `QUERY` to `SAFE_HTTP_METHODS`, and deprecates the legacy `?cmd=`
  route that v16 still dispatches *before* it looks at `request.path`. Use
  `git show origin/version-16:<path>` instead. Two design documents in one day cited v17
  line numbers as v16 fact before this was noticed; a claim about the framework is only
  worth as much as the tree it was read from.
- **Frappe's email wrapper is already full-width, and a narrow email is always your own
  doing.** `frappe/templates/emails/standard.html` sets its container to
  `width="{% if header or with_container %} 600 {% else %} 100% {% endif %}"`, and nothing in
  this app passes either argument — so the framework gives us 100% width, no card, no
  padding. Every centred box this app ever sent came from a `max-width` div written into the
  message body. All email chrome now lives in `templates/emails/_shell.html`; add a container
  anywhere else and `tests/test_email_design.py` fails the build. Related, and both verified
  on prod rather than assumed: Premailer keeps `@media` blocks *and* inlines descendant rules
  onto the attribute-less tags `md_to_html()` emits (the only way to style the morning
  briefing), and MSO conditional comments survive its lxml round-trip. See
  [`docs/email-design-system.md`](docs/email-design-system.md).
- **A `Notification` body *can* `{% extends %}` a file template — don't.** frappe's jenv keeps
  its loader through `overlay()`, so inheritance resolves from a DB-stored string. But in a
  child template anything outside a `{% block %}` is **silently discarded**, and a mistyped
  extends path raises inside `Notification.send()`'s own `except`, which logs an Error Log and
  drops the email. Both failures are invisible in a field people edit through the Desk, so
  Notification bodies call the `ee_*` Jinja globals instead. For the same reason they use
  `doc.get("field")`, never `doc.field`: a Document raises `AttributeError` on a missing
  field, and that is another silently dropped email.
- **Merging to `main` does more than deploy this app's code.** The prod deploy `FLUSHDB`s
  **both** redis instances — `:13000` and `:11000` — and restarts the bench. The `:11000`
  flush destroys every queued background job, silently, whether or not it had anything to
  do with the change being merged; it is the confirmed cause of a batch of Drive folders
  that were never created. If a change enqueues work that matters, it must be re-drivable
  after a deploy rather than assumed to have run.

## Conventions

Match the file you're editing — indentation, quoting, and comment density all vary by
module, and the surrounding code is the spec. Keep `hooks.py` and the relevant module
README in sync when you add a customization; `hooks.py` is annotated and that annotation is
part of the documentation. A docs-only change must not alter executable behaviour.

Deeper procedures live in [`.claude/skills/`](.claude/skills/) and load when relevant:
`add-doctype`, `add-endpoint`, `run-tests`, `fixtures-and-patches`, `release-prep`,
`work-item`.
