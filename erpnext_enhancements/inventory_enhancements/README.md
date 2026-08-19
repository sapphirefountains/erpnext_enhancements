# `inventory_enhancements/` — barcode counting and storage locations

A resumable physical-count workflow driven by a barcode scanner, plus a storage-location
model finer-grained than ERPNext's Warehouse.

Backend endpoints are in `api/inventory_scanner.py`; the scanner UI is under `public/js/`.
This module holds the data model and the audit page.

## Contents

| Path | Purpose |
|---|---|
| `doctype/inventory_count_session/` | A resumable physical-count run |
| `doctype/inventory_count_line/` | One counted row |
| `doctype/storage_location/` | Sub-warehouse storage locations |
| `doctype/inventory_scanner_settings/` | Single — scanner configuration |
| `page/inventory_scanner_audit/` | Desk audit view over count sessions |
| `item_naming_rules.py` | The Item naming schema as executable rules. No Frappe, no I/O |
| `item_naming.py` | The reads behind it — corpus, brands, reserved codes, and the audit |
| `item_naming_triton.py` | The "Check with Triton" button — deterministic findings as context |
| `report/item_naming_audit/` | The naming work list, worst-first |

## The count flow

An **Inventory Count Session** is a persistent, resumable audit run by one clerk. Each
counted row lands in the `lines` child table with **the system-quantity snapshot and variance
captured at scan time** — not recomputed later.

That timing is the point. Stock keeps moving while a count is in progress, so a variance
computed at finalize time would be measured against a different reality than the one the
clerk was standing in.

Finalizing (`api.inventory_scanner.finalize_session`) aggregates the lines per
(item, warehouse) into a **draft** Stock Reconciliation for a Stock Manager to review and
submit; `stock_reconciliation` on the session links back to it.

Nothing here submits stock movements. The clerk counts, the Stock Manager decides.

## Item naming

`item_naming_rules.py` implements the *ERPNext Item Naming Schema* SOP v1.0
([`docs/item-naming-schema.md`](../../docs/item-naming-schema.md)): the seven-segment
`item_name` schema, the four Item Code families, and the approved category vocabulary.
`item_naming.py` does the reads; `assistant_tools/item_naming_check.py` is the MCP surface.

**It is advisory and there is no `Item` doc_event.** The SOP says so itself — compliance is
procedural because ERPNext applies no naming series to Item — and a third of the live
catalogue would fail the comma rule, so anything that blocked a save would fire constantly on
legitimate edits to records that were already there.

The rules module lives here rather than under `assistant_tools/` because nothing in the app
outside `assistant_tools/` and `tests/` may import that package (`TestFacOptionalInvariant`),
and Item-master vocabulary has to stay reachable from a report or a patch.

### Four callers, one engine

`item_naming_rules.py` is the only place a naming judgement is made. Everything else is a thin
caller, and that is load-bearing rather than tidy: two definitions of "compliant" that disagree
by one row is a bug report nobody can close.

| Surface | Entry point | Notes |
|---|---|---|
| **Item Naming Audit** report | `report/item_naming_audit/` | The work list. Worst-first, tombstones hidden by default |
| **Item form** | `public/js/item_naming_advisor.js` | Headline on refresh, two buttons under *Naming* |
| **KPI** `item_naming_compliance_pct` | `kpi_dashboards/snapshots.py` | Nightly, on the Product dashboard |
| **MCP** `item_naming_check` | `assistant_tools/item_naming_check.py` | For Triton and any MCP client |

Two costs worth knowing before editing the form script. `refresh` calls `check_item(mode="record")`,
which reads **no corpus** — a full check on every form open would read the whole catalogue every
time anybody looked at an Item. Only the buttons call `mode="full"`.

And `audit()` is deliberately **not** `evaluate()` in a loop: `evaluate` scores near-neighbours by
document frequency across the whole corpus, so per-record it is O(n²). `audit()` does one pass of
per-record checks plus one grouping pass for collisions. `tests/test_item_naming_rules.py` poisons
`similar_records` to assert `audit()` never reaches for it, because the obvious simplification
does not look wrong.

### A proposal and a saved record ask opposite questions

`evaluate(..., existing=)` is not a nicety. `item_code` is the primary key, so an exact code
match in the corpus means *a collision* when checking a proposed new Item and *this very row*
when re-checking a saved one. Getting it wrong made every saved Item a STOP (v1.337.1). The form
always sends `existing: 1`; the MCP tool exposes it and defaults to false.

Self-exclusion is matched on the **exact** code, never the normalised one — otherwise it would
also drop a punctuation-variant sibling, which is the collision `duplicate_code_normalised`
exists to find.

### The Triton button

`item_naming_triton.py` runs the deterministic check **first** and sends its findings to Triton as
context, so the model is asked only for what the rules refuse to decide — parsing a vendor
description into segments, choosing a category, and whether two records are the same physical
part. The prompt declares the findings authoritative; without that the model re-litigates them,
and a confident second opinion that contradicts a regex is worse than none.

It goes through `triton_chat._request` rather than being a third Triton client. That seam already
refuses when the assistant is off, mints the token **under the current session user**, retries a
stale-token 401, and scrubs URLs. Do **not** reach for `chat/invoke/triton_client.ask()` here: it
calls `frappe.set_user` unconditionally, which on an HTTP request overwrites `session.sid` and
logs the user out — that shipped as v1.325.0.

### Two traps that made the obvious query wrong

Both are why block occupancy is decided in Python and asserted in CI rather than written as a
MariaDB regex, and both were live in a hand-written validator prompt.

- **`item_code REGEXP '^PDT-[0-9]{4}$'` reports `PDT-0008` free.** The `$` rejects the trailing
  text on `PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy`, which is the only record of that
  product. Trailing text does not free a number.
- **Unanchoring it invents taken numbers.** `CAST(REGEXP_SUBSTR(item_code,'[0-9]+'))` swallows
  the five-digit QuickBooks family — `PDT-00000 (deleted)` … `PDT-00013`, `PDT-00040`, and the
  live `PDT-00051` — collapsing them onto four-digit slots that are genuinely free. **`PDT-0051`
  and `PDT-00051` are different items.**

`block_slot()` is the single place that boundary is decided: exactly the declared width in
digits, not followed by another digit, trailing text allowed.

A third trap belongs to the name checks rather than the codes: **MariaDB's PAD SPACE collation
makes `item_name <> TRIM(item_name)` always false**, so the SQL form of the trailing-whitespace
check returns 0 on a corpus with three offenders. Compared by length, in Python, where a
trailing space is still a character. Use `BINARY` if you must write it in SQL.

## Tests

```bash
python -m unittest erpnext_enhancements.tests.test_inventory_scanner -v
```

Bench-free, and wired into CI:

```bash
python -m unittest erpnext_enhancements.tests.test_item_naming_rules -v
```
