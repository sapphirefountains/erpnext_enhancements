# `tests/` — Test suite

Tests for the app's server-side behavior. Most extend Frappe's `FrappeTestCase` and therefore need a **real bench** to run; two suites are deliberately bench-free.

## Running

```bash
# Bench-backed tests (most of this folder):
bench --site <site> run-tests --app erpnext_enhancements

# Bench-free suites (plain pytest/unittest, no site required):
python -m pytest erpnext_enhancements/tests/test_quickbooks_online.py
python -m pytest test_sync_time_kiosk.py        # at repo root
```

> CI currently runs only the standalone `unit-tests` job; the Frappe integration-test job was removed in v0.2.9 (see [`CHANGELOG.md`](../../CHANGELOG.md)) because it gated PRs on upstream/environment churn. The `FrappeTestCase` files remain and run against a real bench locally.

## Coverage map

| Test file | Subsystem covered | Style / fixtures |
|---|---|---|
| `test_comments_api.py` | `api.comments` CRUD | `unittest.mock` (no DB) |
| `test_dashboard_override.py` | Project dashboard `get_dashboard_data` | Pure unit, no mocks |
| `test_geo_telemetry.py` | `api.time_kiosk` geolocation (single-point, batch, history, purge) | `FrappeTestCase`; two employees (unlinked + user-linked w/ Job Interval); `patch` for the DB-error path |
| `test_procurement_status.py` | `project_enhancements` procurement rollup | `FrappeTestCase`; full company/item/supplier/warehouse + `custom_project`; `frappe.enqueue` patched |
| `test_project_enhancements.py` | Project-scoped comment endpoints | `unittest.mock` (no DB) |
| `test_project_merge.py` | `project_merge.merge_projects` | `FrappeTestCase`; source/target Project + linked Task |
| `test_quickbooks_online.py` | QBO sync (mapping, ordering, signature, datetime, preflight, result tracking) | **Bench-free**: `install_frappe_stub()` fakes `frappe`/`requests` in `sys.modules`; `monkeypatch` |
| `test_sapphire_maintenance.py` | Maintenance Record + predictive generation | `FrappeTestCase`; Item/Serial No/Project fixtures |
| `test_search.py` | `api.search` global-search permission filtering | `FrappeTestCase` + mocked SQL/`has_permission`/`get_all` |
| `test_time_kiosk.py` | Clock-in/out `log_time` Start→Stop cycle | `FrappeTestCase`; Employee linked to Administrator session |
| `test_time_kiosk_status.py` | `get_current_status` idle response shape | `FrappeTestCase`; regression guard for a JS truthy-dict issue |
| `test_user_drafts.py` | `api.user_drafts` save/update/delete | `FrappeTestCase`; `User Form Draft` upsert semantics |

The standalone Time Kiosk REST sync tool is tested separately by [`test_sync_time_kiosk.py`](../../test_sync_time_kiosk.py) at the repo root (34 tests, `httpx` mocked) — see the [www README](../www/README.md).

## Notes

- `test_quickbooks_online.py` must be importable **without** a bench, hence the `sys.modules` stub. It fails if run expecting a real `frappe`.
- `test_time_kiosk_status.py` exists specifically to lock in the idle-status payload shape (a `get_current_status` response the JS treats as truthy must still mean "not clocked in").
