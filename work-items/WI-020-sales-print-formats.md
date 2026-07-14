# WI-020: Customer-facing print formats for Quotation, Sales Order, Sales Invoice
**Phase:** 0   **Type:** FIXTURE   **Size:** M
**Blocked by:** nothing (OD-2 direction set: Utah-law stream-differentiated — the formats render whatever tax rows exist; only wording could change on the CPA's written matrix)   **Blocks:** WI-022

## Why
NO print formats exist for Quotation, Sales Order, or Sales Invoice (repo_ops §5: fixtures ship only Maintenance Record Print + Project Contract Print; migrate-generated formats cover fleet/dispatch/configurator/water only). On 2027-01-01 the first customer-facing quote and invoice MUST leave ERPNext looking professional — this is a hard cutover gate.

## Native-first check
Native **Print Format** engine (Print Format Builder or Jinja) — SUFFICIENT. Stock 'Standard' format evaluated: functional but unbranded and shows fields we hide; a branded Print Format per doctype is standard ERPNext practice, not a reimplementation. Rule 2: MUST ship as FIXTURE.

## Preconditions
- Logo/letterhead assets + remit-to/payment-instructions copy from the business (native **Letter Head** doctype record created alongside).
- OD-2 branch: tax display works either way (the format renders whatever Sales Taxes rows exist); if the CPA ruling changes tax presentation (e.g., tax-included wording), only copy changes — enumerate, don't block.
- Stripe pay-link inclusion decision: SI format may render `custom_stripe_payment_link` when set (field exists — repo_payments) — include behind an if-set guard.

## Scope
- Three Print Formats: 'Sapphire Quotation', 'Sapphire Sales Order', 'Sapphire Sales Invoice' (Jinja recommended for layout control), authored on TEST.
- hooks.py fixtures: extend the Print Format allowlist (currently `["Maintenance Record Print","Project Contract Print"]` — repo_ops §4) with the three names; `bench export-fixtures` → commit `fixtures/print_format.json`.
- Property Setter `default_print_format` per doctype (rides WI-019's fixture batch).

## Acceptance criteria
- `SELECT COUNT(*) FROM `tabPrint Format` WHERE name IN ('Sapphire Quotation','Sapphire Sales Order','Sapphire Sales Invoice')` = 3 on both sites post-deploy.
- PDF renders correctly for a UAT invoice with 1 item, 10 items, tax rows, and (SI) a Stripe link present/absent — 6 sample PDFs attached to UAT evidence.
- Business owner signs off on visual proof before cutover (go/no-go checklist line).

## Rollback
Remove names from the hooks allowlist + fixture JSON, redeploy; docs fall back to 'Standard'.

## Explicitly NOT in this work item
PO/PE print formats (internal docs; Phase 2 if wanted); statement-of-account/dunning templates; email templates.
