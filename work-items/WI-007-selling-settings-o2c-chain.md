# WI-007: Selling Settings & native O2C chain configuration (Quotation → Sales Order → Sales Invoice)
**Phase:** 0   **Type:** CONFIG   **Size:** S
**Blocked by:** nothing (OD-3 RESOLVED 2026-07-14: one stream, renamed 'Events' — WI-065 performs the rename; stream names below read as 'Events' post-rename)   **Blocks:** WI-008, WI-023, WI-022

## Why
Today the sell-side loop is: quote in QuickBooks → project in ERPNext → re-key → invoice in QuickBooks. Prod has 638 draft Quotations (QBO Estimates), ZERO Sales Orders, and 1,563 draft Sales Invoices (prod_customers_items). At cutover the entire chain must be authored natively in ERPNext, with the Sales Order as the new commercial commitment document that carries `project` into invoicing.

## Native-first check
Native **Selling Settings** (single), native Quotation→Sales Order→Sales Invoice mapping (`Create > Sales Order` / `Create > Sales Invoice`), and native `Sales Order.project` / `Sales Invoice.project` header fields — SUFFICIENT; no custom flow code. Native **Auto Repeat** evaluated for recurring service billing and REJECTED for the maintenance stream because the custom Sapphire Maintenance module already drafts per-visit Sales Invoices with `invoice.project = doc.project` (verified `erpnext_enhancements/api/maintenance_workflow.py:408`); Auto Repeat remains a valid Phase-2 option for fixed-fee monthly contracts only (OD-3 dependent).

## Preconditions
- Verified: `tabSales Order` = 0 rows, `tabDelivery Note` = 0, `tabStock Entry` = 0 on prod (prod_customers_items) — no legacy SO data to reconcile.
- Verified: maintenance record controller reads submitted Sales Orders with `order_type='Maintenance'` (`sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.py:162`).
- Business sign-off on which streams REQUIRE an SO (proposed: Design/Build/Rent/Products = SO required by procedure; Service/maintenance = SO optional, invoices come from Maintenance Record submission).

## Scope
- `Selling Settings.so_required` = 'No' and `Selling Settings.dn_required` = 'No' (MUST stay 'No': the maintenance module auto-drafts SO-less Sales Invoices, and opening-AR invoices have no SO; enforcement of SO-per-Build-job is procedural + UAT-checked, not system-hard).
- Document the standard flow per value stream (Design/Build/Service/Rent — Project.project_type values verified in prod_projects_opps): Quotation (AE) → Closed-Won handoff creates Project (existing engine) → Sales Order created from Quotation with `Sales Order.project` = handoff project, `order_type` = 'Sales' ('Maintenance' for maintenance-anchor SOs) → Sales Invoice created from SO (project carried natively).
- OD-3 resolution applied: Rent and Events are ONE stream renamed 'Events' (WI-065 executes the rename); this item's per-stream SOPs use 'Events' as the stream name.
- No changes to Delivery Note/stock fulfilment (out: stock flows day one; 111 stock items exist but Stock Entry is unused).

## Acceptance criteria
- `SELECT field,value FROM tabSingles WHERE doctype='Selling Settings' AND field IN ('so_required','dn_required')` → both 'No' (or empty/'No' default) on test and prod.
- On TEST: one Quotation submitted → SO created via native mapping with `project` set → SI created via native mapping; `SELECT project FROM `tabSales Invoice` WHERE name=<test SI>` equals the SO's project.
- Written SOP (one page per stream) attached to the runbook (WI-051).

## Rollback
Settings are two Select values; revert to prior values. Test documents cancelled/deleted.

## Explicitly NOT in this work item
Disposition of the 638 legacy draft Quotations (WI-023); print formats (WI-020); tax templates/Tax Rules (finance workstream — WI-036/WI-037, OD-2); Stripe payment links (payments workstream — WI-039).
