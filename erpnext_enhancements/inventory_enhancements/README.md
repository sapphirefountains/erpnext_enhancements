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

## Tests

```bash
python -m unittest erpnext_enhancements.tests.test_inventory_scanner -v
```
