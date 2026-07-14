# WI-034: Re-key open Purchase Orders (and confirm zero open Sales Orders)
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** WI-003, WI-029, WI-025   **Blocks:** WI-035

## Why
Commitments that straddle the cutover must live in ERPNext to be received and billed against in 2027. Prod's existing 96 POs are legacy QBO imports (65 submitted/30 draft/1 cancelled — prod_customers_items) and are cancelled/cleared during WI-029 prep; genuinely-open orders are re-keyed fresh on the new chart/items. Sales Orders: `tabSales Order` is empty on prod (verified) and QBO has no SO concept — nothing to migrate; open customer work is represented by Projects.

## Native-first check
Native Purchase Order entry / Data Import. Verdict: native, no tooling.

## Preconditions
- WI-003 close package includes the QBO open-PO list as of 2026-12-31.
- Item Group rollout (WI-025) done so re-keyed PO lines land on correctly-classified items; suppliers exist (1,156 imported).

## Scope
- Enter one ERPNext Purchase Order per open QBO PO (new 2027-side naming, original expected dates, `project` link where applicable), expense accounts per the new chart. Population is the QBO open-PO list (expected small: prod's whole PO history is 96 docs).
- Record a re-key crosswalk (QBO PO # → ERPNext PO name) in the migration runbook.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabPurchase Order\` WHERE docstatus=1 AND status IN ('To Receive and Bill','To Receive','To Bill')` equals the open-PO list count.
- Sum of open PO amounts matches the QBO list total.
- Crosswalk archived.

## Rollback
Cancel the re-keyed POs.

## Explicitly NOT in this work item
Receipts/billing against them (business-as-usual 2027); the dollar-threshold PO Authorization Rule (WI-013); Material Request history.
