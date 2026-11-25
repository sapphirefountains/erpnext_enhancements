# WI-034: Re-key QuickBooks purchase orders missing from ERPNext (and confirm zero open Sales Orders)
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** WI-003 (the open-PO list from QuickBooks' December close, locked 2027-01-13)   **Blocks:** WI-035
**Re-scoped 2026-11-25.** Purchasing already runs in ERPNext, so this item no longer re-keys every open order. It re-keys only the QuickBooks purchase orders open at 2026-12-31 that have no ERPNext counterpart. Due **2027-01-15**; Parker keys, Lisa checks. The old dependencies on WI-029 (chart rebuild, canceled) and WI-025 (Item Group rollout, moved to Phase 2) are gone: orders land on the live chart and items. ERPNext Task: TASK-2026-00927.

## Why
Commitments that straddle the cutover must live in ERPNext to be received and billed against in 2027. Since about September 2026 purchase orders have been raised in ERPNext directly (on 2026-11-25 prod held 183 open submitted POs, 140 of them created since September), so most open commitments are already there. What can still be missing is a purchase order raised only in QuickBooks: the Record Matching page had 31 QuickBooks PO records Pending Review on 2026-11-25, 8 of them dated 2026. Sales Orders: QuickBooks has no Sales Order concept and nothing to migrate; open customer work is represented by Projects.

## Native-first check
Native Purchase Order entry / Data Import. Verdict: native, no tooling.

## Preconditions
- WI-003: QuickBooks' open-PO list as of 2026-12-31, exported from the December close.
- The QuickBooks PO records on the Record Matching page triaged (Meg; Lisa decides): each is linked to the ERPNext PO it duplicates, or marked as missing from ERPNext.

## Scope
- For each QuickBooks PO open at 2026-12-31 **with no ERPNext counterpart**: enter one ERPNext Purchase Order (2027-side naming per WI-030, original expected dates, `project` where applicable) on the live chart.
- Record a crosswalk (QuickBooks PO number → ERPNext PO name) on the Task, covering every QuickBooks PO on the open list: either the ERPNext PO it already matched, or the one keyed here.

## Acceptance criteria
- Every QuickBooks PO on the 2026-12-31 open list has a crosswalk row.
- ``SELECT COUNT(*) FROM `tabPurchase Order` WHERE name IN (<crosswalk ERPNext names>) AND docstatus=1`` = the crosswalk's row count.
- The open amount of each keyed PO matches its QuickBooks line.
- Crosswalk archived on TASK-2026-00927.

## Rollback
Cancel the re-keyed POs.

## Explicitly NOT in this work item
Receipts and billing against them (business as usual in 2027); POs already raised in ERPNext; the dollar-threshold PO Authorization Rule (WI-013); Material Request history.
