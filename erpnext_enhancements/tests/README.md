# `tests/` — Test suite

Tests for the app's server-side behavior. Most extend Frappe's `FrappeTestCase` and therefore need a **real bench** to run; several suites are deliberately bench-free. Note that `bench run-tests` is BROKEN under Python 3.14 — bench-backed suites expose a module-level `run()` wrapper invoked with `bench execute` instead (see `test_contacts_ux.py` and `test_fountain_move_conversion.py`).

## Running

```bash
# Bench-backed tests (most of this folder):
bench --site <site> run-tests --app erpnext_enhancements

# Bench-free suites (plain pytest/unittest, no site required):
python -m pytest erpnext_enhancements/tests/test_quickbooks_online.py
python -m pytest erpnext_enhancements/tests/test_assistant_tools_schema.py
python -m pytest test_sync_time_kiosk.py        # at repo root
```

> CI currently runs only the standalone `unit-tests` job; the Frappe integration-test job was removed in v0.2.9 (see [`CHANGELOG.md`](../../CHANGELOG.md)) because it gated PRs on upstream/environment churn. The `FrappeTestCase` files remain and run against a real bench locally.
>
> Within that job, the unittest-style suites run via `python -m unittest` with an explicit module list, and `test_quickbooks_online.py` runs via a dedicated `python -m pytest` step — it is plain pytest functions (the `monkeypatch` fixture), which unittest cannot collect. A new bench-free suite must be added to one of those two steps in [`ci.yml`](../../.github/workflows/ci.yml) (pytest-style → the pytest step), or it will never run in CI.

## Coverage map

| Test file | Subsystem covered | Style / fixtures |
|---|---|---|
| `test_assistant_tools_schema.py` | `assistant_tools/` FAC tool contract (name == module filename, no FAC built-in collisions, schema validity), skills manifest, FAC-optional import tripwire | **Bench-free**: stub `frappe` + stub FAC `BaseTool` in `sys.modules`; hooks.py read via `ast` |
| `test_assistant_tools_integration.py` | FAC tool discovery via `get_tool_registry()` + execution smoke tests (intervals, contracts, project scopes, briefing) + roleless-user denial | `FrappeTestCase`-style `unittest`; skip-guarded — runs only on a bench with `frappe_assistant_core` installed |
| `test_collab.py` | `api.collab` live-collab relay (allowlist, write permission, field/child validation, size cap, publish payloads for field updates + focus presence) | `FrappeTestCase`; Task fixture; `frappe.publish_realtime` patched |
| `test_comments_api.py` | `api.comments` CRUD | `unittest.mock` (no DB) |
| `test_dashboard_override.py` | Project dashboard `get_dashboard_data` | Pure unit, no mocks |
| `test_geo_telemetry.py` | `api.time_kiosk` geolocation (single-point, batch, history, purge) | `FrappeTestCase`; two employees (unlinked + user-linked w/ Job Interval); `patch` for the DB-error path |
| `test_pickup_routing.py` | `api.pickup_routing` supplier pick-up run: the four-step address fallback in order (and `po.shipping_address` — our own yard — never reachable), the PO→Project header/item union, `per_received` beating the `status` label, stops keyed on supplier *and* address, money never summed across currencies | **Bench-free**: `_install_frappe_stub()` fakes `frappe`/`frappe.utils` in `sys.modules` and backs `get_all`/`db.get_value` with a tiny in-memory query engine; plain `unittest` |
| `test_po_approval.py` | Purchase Order approval threshold (WI-013): threshold resolution, 0-disables, at-threshold boundary, `PO Approver` gate, Administrator bypass | **Bench-free**: `frappe` stub installed in `setUpModule` (not at import, so bench-only suites' skip-guards still work) |
| `test_po_segregation.py` | Purchase Order separation of duties (WI-066): MR-link extraction (blank/whitespace/duplicate/sort), owner conflict, dangling-MR fail-open, Administrator-only bypass, **`PO Approver` does not bypass**, session-user-not-doc-owner, kill switch | **Bench-free**: same `setUpModule` stub pattern; `frappe.get_all` reads a `{mr_name: owner}` map |
| `test_po_order_stage.py` | Purchase Order Order Stage: the backfill rule (drafts, cancelled, closed/completed, partial receipt, `None` per_received, and that every result is a real Select option), the Purchase Receipt advance/revert hooks, and the wiring — patch order, options built from one `STAGES` list, `allow_on_submit`, nothing on PO submit, and the backfill never keying on emptiness | **Bench-free**: same `setUpModule` stub pattern; the stub's `db.set_value` records `update_modified` so the no-TimestampMismatch promise is testable |
| `test_procurement_status.py` | `project_enhancements` procurement rollup | `FrappeTestCase`; full company/item/supplier/warehouse + `custom_project`; `frappe.enqueue` patched |
| `test_project_enhancements.py` | Project-scoped comment endpoints | `unittest.mock` (no DB) |
| `test_project_merge.py` | `project_merge.merge_projects` | `FrappeTestCase`; source/target Project + linked Task |
| `test_quickbooks_online.py` | QBO sync (mapping, ordering, signature, datetime, preflight, result tracking) | **Bench-free**: `install_frappe_stub()` fakes `frappe`/`requests` in `sys.modules`; `monkeypatch` |
| `test_feedback_states.py` | The `Enhancement Request` lifecycle: the enum pinned against the DocType's own `Select` options (renaming one needs a data patch or existing rows refuse to save), and the whole transition cross-product enumerated — so `Submitted → Tasks Created`, which *is* the human approval gate, cannot become reachable by accident | **Bench-free**: `product_feedback/states.py` is stdlib-only; the DocType is read as JSON |
| `test_feedback_endpoint_surface.py` | `api/feedback.py`'s HTTP surface — POST-only on every endpoint, no `allow_guest`, every name the SPA dials resolves, and set equality so a new endpoint cannot be left both un-wired and un-explained. Plus the one worth the most: **only `product_feedback/task_writer.py` constructs a `Task`**, with a control asserting that module still does. Verified by reintroducing the bug | **Bench-free**: `ast` over the API module, text read of `transport.js` |
| `test_feedback_breakdown_parse.py` | The seam between a language model and two live project boards. A malformed *response* yields nothing, a malformed *item* is dropped alone, every drop is reported; a task names a `target` and never a Project; `parent_task` and duplicate ids must be ones ERPNext sent; dependency indices are remapped after drops and cycles are broken | **Bench-free**: `product_feedback/proposal.py` is stdlib-only; **plain pytest**, so it has its own `python -m pytest` step in `ci.yml` |
| `test_fountain_move.py` | Fountain-move intake: phone normalisation, customer-name rule, the guest field allowlist, input sanitation (bidi/control rejected, `< 3 ft` prose preserved), Turnstile decision table, honeypot semantics, image magic-byte sniffing, invite URLs, role gates | **Bench-free**: `install_frappe_stub()` fakes `frappe`/`frappe.rate_limiter`/`requests`; plain pytest |
| `test_fountain_move_conversion.py` | The Customer→Address→Contact→Lead→Opportunity engine against real erpnext hooks: link-before-insert naming, exactly-one-Contact, non-Guest ownership, reuse, duplicate review, failure + resume-on-retry | `unittest` + **`run()` bench-execute wrapper**; `frappe.enqueue` patched; fixtures carry unique email AND phone because the engine commits per step, so rollback does not undo them |
| `test_sapphire_maintenance.py` | Maintenance Record + predictive generation | `FrappeTestCase`; Item/Serial No/Project fixtures |
| `test_search.py` | `api.search` global-search permission filtering | `FrappeTestCase` + mocked SQL/`has_permission`/`get_all` |
| `test_time_kiosk.py` | Clock-in/out `log_time` Start→Stop cycle | `FrappeTestCase`; Employee linked to Administrator session |
| `test_time_kiosk_status.py` | `get_current_status` idle response shape | `FrappeTestCase`; regression guard for a JS truthy-dict issue |
| `test_user_drafts.py` | `api.user_drafts` save/update/delete | `FrappeTestCase`; `User Form Draft` upsert semantics |

The standalone Time Kiosk REST sync tool is tested separately by [`test_sync_time_kiosk.py`](../../test_sync_time_kiosk.py) at the repo root (34 tests, `httpx` mocked) — see the [www README](../www/README.md).

**The ~20 `test_chat_*.py` suites are documented in [`chat/README.md`](../chat/README.md#tests)**, not here — that README explains what each one defends and why, and splitting the explanation across two files is how one half goes stale. Two things about them belong in this file because they are general rules:

- **Every chat suite gets its own CI step.** Each installs its own `frappe` stub at module scope, and run together in one process they go red for reasons that have nothing to do with the code under test.
- **`test_chat_rawsql_guard.py` is a build gate, not a coverage suite.** It walks `chat/**/*.py` with `ast` and fails on a raw query against a conversation-bearing table that does not AND in `permissions.membership_filter_sql`. If it goes red the fix is almost always to name the DocType literally and AND in the filter — not to add an exemption.

The chat **client** is guarded by three plain-`node` scripts rather than by anything in this folder: `scripts/test_chat_citations.mjs`, `scripts/test_chat_client_logic.mjs` and `scripts/test_chat_source_rules.js`. No runner and no `npm install`, matching `test_pick_routing_lines.js` and `test_address_components.js`. See [`public/js/chat/README.md`](../public/js/chat/README.md#running-the-tests).

## Notes

- `test_quickbooks_online.py` must be importable **without** a bench, hence the `sys.modules` stub. It fails if run expecting a real `frappe`.
- `test_assistant_tools_schema.py` is likewise bench-free (same stub approach) and additionally stubs `frappe_assistant_core.core.base_tool.BaseTool`. `test_assistant_tools_integration.py` self-skips unless `frappe_assistant_core` is importable, so a FAC-less bench collects it cleanly.
- `test_time_kiosk_status.py` exists specifically to lock in the idle-status payload shape (a `get_current_status` response the JS treats as truthy must still mean "not clocked in").
