# WI-012: Purchasing flow — Material Request (team lead) → Purchase Order (PM) with role split
**Phase:** 0   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-011   **Blocks:** WI-013, WI-022

## Why
Purchasing barely exists in ERPNext today (17 Material Requests, 96 POs, 1 draft Purchase Invoice — prod_customers_items) while real spend flows through QBO. Day one, field team leads must request materials without being able to commit company money, and PMs must convert requests to POs — a permission split, not custom code.

## Native-first check
Native **Material Request → Purchase Order** mapping plus native **Role Permissions** (Custom DocPerm via Role Permission Manager) — SUFFICIENT. There is no native "MR required before PO" toggle in Buying Settings; enforcement = role permissions (team leads lack PO create) + procedure. Native Buying Settings reviewed: leave `po_required`/`pr_required` at 'No' (subcontract labor bills arrive without POs day one; hard-requiring PO would block 77%-Staffing supplier invoices — prod_customers_items).

## Preconditions
- Roles decided and seeded (WI-010/WI-011): a team-lead role (proposal: reuse native 'Stock User' or seed 'Field Team Lead') and PM role with Purchase Order create/submit.
- Supplier master reviewed: 1,156 suppliers, 892 'Staffing' (prod_customers_items) — confirm active subset for buying.

## Scope
- Role Permission Manager: Material Request — team-lead role gets read/write/create/submit; Purchase Order — team-lead role read-only; PM role gets create/write; PO submit governed by WI-013's Authorization Rule.
- Material Request type 'Purchase' as the standard request; `Material Request Item.project` filled by requester (native field).
- SOP: MR raised against a Project (or an 'Internal' project for overhead) → PM reviews → Create > Purchase Order.
- The MR/PO Custom DocPerm split ships version-controlled, consistent with WI-010 (review correction C9): either a `Custom DocPerm` name-in allowlist entry in the hooks.py fixtures list or a seed patch following the app's `seed_*` precedent — NOT hand-clicked-only permission state. (The item's Type stays CONFIG because it is mostly Desk/master-data work, but the DocPerm artifact itself must be version-controlled.)

## Acceptance criteria
- Logged in as a team-lead test user on TEST: can submit a Material Request; gets PermissionError creating a Purchase Order.
- Logged in as PM test user: Create > Purchase Order from the MR succeeds and PO items inherit `project` from MR items.
- `SELECT COUNT(*) FROM `tabCustom DocPerm` WHERE parent IN ('Material Request','Purchase Order')` > 0 documenting the applied split.
- The applied Custom DocPerm rows are present in the repo (fixture JSON or seed patch) — review correction C9.

## Rollback
Delete the added Custom DocPerm rows in Role Permission Manager (restores standard perms) and revert the fixture/seed-patch commit.

## Explicitly NOT in this work item
Dollar-threshold escalation (WI-013); making project mandatory on PO lines (WI-014); supplier master cleanup; stock/receipt flows.
