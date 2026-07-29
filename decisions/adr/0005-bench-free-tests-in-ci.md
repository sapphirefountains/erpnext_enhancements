# 0005. CI runs only bench-free tests

- **Status:** Accepted, with a known gap
- **Date:** 2026-07-29 (recorded retroactively)

## Context

The natural CI job for a Frappe app is `bench run-tests --app erpnext_enhancements` against a
real bench. We had one. It never reached a single one of our own assertions.

Frappe v16's test-record auto-generation walks the entire ERPNext doctype dependency graph
before any test runs, and it tripped over environment gaps one after another — missing
utilities, uninstalled companion doctypes like "Payment Gateway", and so on. Each fix
surfaced the next. The practical effect was that pull requests were gated on upstream and
environment churn that had nothing to do with the change under review, which teaches everyone
to ignore CI.

## Decision

The integration job was removed (v0.2.9). CI runs **only bench-free suites** — tests that
install their own `frappe` stub in `setUpModule`, or are pure filesystem/algorithm checks.

Bench-dependent test files stay in the tree and are run locally against a real bench:

```bash
bench --site <site> run-tests --app erpnext_enhancements
```

New logic is written bench-free where the problem allows it. That is why
`water_engineering/engine/` may never import `frappe`, why `kpi_dashboards/metrics.py` is pure
math separated from `snapshots.py`, and why the permission and schema contracts have
stub-based suites.

## Consequences

- **This is a real coverage gap, and it should be stated rather than papered over.** Anything
  that genuinely needs the ORM is only tested when someone runs it locally. Weigh whether the
  logic can be lifted into a testable pure helper — that refactor is usually the right answer
  and is why several modules are shaped the way they are.
- **The unittest/pytest split became load-bearing.** `python -m unittest` silently collects
  nothing from pytest-style function tests and reports success. The QuickBooks Online suite
  was listed on a unittest step, ran nowhere, and stayed broken for weeks. A bench-free
  *pytest* suite needs its own `python -m pytest` step.
- **Suites get their own CI steps.** Each installs its own `frappe` stub, so separate
  processes keep them from cross-talking. This looks like redundant YAML; it isn't.
- **Cheap filesystem guards earn their place.** `test_doctype_modules` and
  `check_www_controllers.py` catch whole bug classes with no bench at all — a doctype in the
  wrong module, and a `www/` controller whose hyphenated filename means Frappe never imports
  it (`stripe-return.py` was broken that way from the day it was written). Prefer this kind of
  check when designing a guard.
- Reintroduce a bench-backed job once the upstream harness stabilises. The note in `ci.yml`
  records what to try.
