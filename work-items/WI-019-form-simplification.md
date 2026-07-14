# WI-019: Form simplification — hide/require fields via Property Setter fixtures
**Phase:** 0   **Type:** FIXTURE   **Size:** M
**Blocked by:** WI-018 (field inventory produced with the accountant)   **Blocks:** WI-022

## Why
Quotation/SO/SI/PO/PE forms carry dozens of fields Sapphire never uses (multi-currency, campaign attribution, shipping rules). Every unused visible field is friction for a 1-2 person finance team and invites bad data. Hard rule 2: these Property Setters MUST be fixtures — hand-clicked prod form tweaks that vanish on deploy are a defect.

## Native-first check
Native **Property Setter** (hidden, reqd, in_list_view, default properties) — SUFFICIENT; no form overrides or custom scripts. The app's Property Setter fixture filter (`is_system_generated=0`, minus the LMS exclusion — repo_ops §4) already exports these automatically.

## Preconditions
- Field-by-field walkthrough of Quotation, Sales Order, Sales Invoice, Purchase Order, Purchase Invoice, Payment Entry with the accountant + a sales user; produce the hide-list and the minimal-mandatory list (deliverable of WI-018's inventory session).
- Rule: only hide, never delete; never hide fields the maintenance/Stripe modules stamp (`custom_stripe_payment_status`, `custom_stripe_payment_link` on SI — repo_payments; `project` fields).

## Scope
- Create Property Setters on TEST via Customize Form for the agreed list (typical candidates, to be confirmed in the walkthrough: currency/price-list sections given single-currency USD company, campaign/source marketing fields on sell docs, shipping/packing sections given no Delivery Notes).
- Includes WI-008's `Sales Invoice.project` in_list_view setter and any WI-016 costing-field permlevel setters.
- `bench export-fixtures` → commit `fixtures/property_setter.json`; deploy promotes to prod.

## Acceptance criteria
- Repo diff shows only intended new entries in `fixtures/property_setter.json`; CI green.
- On prod post-deploy: `SELECT COUNT(*) FROM `tabProperty Setter` WHERE doc_type IN ('Quotation','Sales Order','Sales Invoice','Purchase Order','Purchase Invoice','Payment Entry') AND is_system_generated=0` equals the fixture count (no unmanaged strays).
- Accountant re-walkthrough sign-off: each target form shows only agreed fields.

## Rollback
Remove the entries from the fixture JSON and redeploy; Property Setters are non-destructive (fields reappear).

## Explicitly NOT in this work item
Custom Fields (none needed here); DocType JSON edits; hiding anything on custom-app doctypes; print formats (WI-020).
