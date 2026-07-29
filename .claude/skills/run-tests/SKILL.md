---
name: run-tests
description: Run the right test suite in erpnext_enhancements and add a new one so it actually runs in CI. Use when running tests, when a test passes locally but not in CI (or vice versa), or when adding a test suite for new code.
---

# Running tests

Tests here split three ways, and picking the wrong one is the most common way to write a
suite that never runs.

## The three kinds

| Kind | How it runs | Where |
|---|---|---|
| **Bench-required** | Needs a real Frappe bench (`FrappeTestCase`) | Most of `erpnext_enhancements/tests/` — **not run in CI** |
| **Bench-free, unittest** | Installs a `frappe` stub in `setUpModule`, plain `python -m unittest` | Listed explicitly in `.github/workflows/ci.yml` |
| **Bench-free, pytest** | Plain pytest functions, often using `monkeypatch` | Its own `python -m pytest` step in `ci.yml` |

```bash
# Bench-required (local only)
bench --site <site> run-tests --app erpnext_enhancements

# A bench-free unittest suite
python -m unittest erpnext_enhancements.tests.test_po_segregation -v

# A bench-free pytest suite
python -m pytest erpnext_enhancements/tests/test_quickbooks_online.py -q
```

## The trap

**`python -m unittest` silently cannot collect pytest-style function tests.** It does not
error — it collects nothing and reports success. The QuickBooks Online suite was listed in a
unittest step, ran nowhere, and stayed broken for weeks before anyone noticed.

So: if your new bench-free suite uses plain `def test_*()` functions or the `monkeypatch`
fixture, it **must** get a `python -m pytest` step in `ci.yml`. Appending it to a
`python -m unittest` module list will look fine and test nothing.

## Adding a suite to CI

Edit the `unit-tests` job in `.github/workflows/ci.yml`.

Several suites get their **own step** rather than being folded into a multi-module list.
That is deliberate: each bench-free suite installs its own `frappe` stub in `setUpModule`,
and a separate process keeps them from cross-talking. If your suite stubs `frappe`,
`requests`, or `httpx`, give it its own step.

Follow the surrounding style: each step carries a comment saying which class of bug it
guards, in concrete terms ("routes the truck to the wrong address, confidently"). That
comment is the reason the step survives future cleanups.

## What CI does and doesn't cover

- **No Frappe integration-test job.** It was removed because it never reached our own
  assertions — Frappe v16's test-record auto-generation walks the entire ERPNext doctype
  dependency graph and kept aborting on environment gaps, so each fix only surfaced the
  next. Bench-required tests stay in the tree and run locally.
- **`ruff check` is advisory** (`continue-on-error`) — a pre-existing backlog. Don't treat
  it as your failure, and don't mass-fix it inside an unrelated change.
- **`version-sync` is a hard gate** comparing `__init__.py` against `package.json` on every
  push. See the `release-prep` skill.
- Two non-test guards also run: `scripts/check_www_controllers.py` (a `www/` controller with
  a hyphen in its filename is never imported by Frappe, so its `get_context()` silently
  never runs) and the DocType module-placement check.

## Writing a bench-free suite

The existing ones are the reference. `tests/test_assistant_tools_schema.py` installs the
`sys.modules` stubs that several other suites then rely on; `tests/test_po_segregation.py`
is a good self-contained example. Read [`tests/README.md`](../../../erpnext_enhancements/tests/README.md).

Prefer bench-free where the logic allows it — pure algorithms, permission rules, schema
contracts, tone helpers. Anything that genuinely needs the ORM stays bench-required and
therefore only runs locally, so weigh whether the logic can be lifted into a testable helper.
