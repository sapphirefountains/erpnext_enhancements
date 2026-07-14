# WI-036: Sales tax templates rebuild (OD-2-gated)
**Phase:** 1   **Type:** CONFIG   **Size:** M
**Blocked by:** OD-2, WI-029   **Blocks:** WI-037, WI-038

## Why
The 3 QBO-imported templates ('US ST 4% - SF' @4.0, 'US ST 6% - SF' @6.0 is_default, 'US ST 6.25% - SF' @6.25 — all single-row 'On Net Total' with account_heads 'ST 4% - SF'/'ST 6% - SF'/'ST 6.25% - SF', plus 3 mirror Item Tax Templates; prod_finance_native) reflect QBO history, not a designed Utah position. The CPA ruling (OD-2) on Build (real-property improvement: contractor pays tax on materials, customer not charged) vs Service (taxable) determines which streams charge tax at all.

## Native-first check
Native **Sales Taxes and Charges Template** + **Item Tax Template** (both verified in use). Verdict: native config; nothing custom.

## Preconditions
- **OD-2 resolved** (CPA ruling in writing). Branches: (a) Build non-taxable to customer → Build-stream documents use a zero/exempt path and purchase-side tax cost handling is documented; (b) Build taxable → Build uses the standard templates; (c) mixed (materials vs labor split) → templates per component with Item Tax Templates carrying the split.
- New chart live (WI-029) with the per-jurisdiction tax liability sub-accounts from WI-004.

## Scope
- Create new Sales Taxes and Charges Templates per required Utah jurisdiction/rate combination, each row charge_type 'On Net Total' with account_head = the matching new liability sub-account; set `is_default=1` on the primary home-jurisdiction template; create a 'Non-Taxable / Out of State' zero-rate template.
- Create matching Item Tax Templates where the ruling taxes item categories differently (assignment leans on WI-025's groups).
- Set `disabled=1` on the 3 QBO templates (and retire their 'ST x% - SF' account_heads via the WI-004 mapping — they map to the new sub-accounts).

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabSales Taxes and Charges Template\` WHERE name LIKE 'US ST%' AND disabled=0` = 0.
- New template count equals the ruling's jurisdiction matrix; exactly one is_default=1.
- A test Sales Invoice per branch computes tax to the cent against a hand-calculated fixture.

## Rollback
Re-enable the QBO trio; disable the new set (templates are inert until referenced).

## Explicitly NOT in this work item
Tax Rules/Categories (WI-037); liability reporting (WI-038); use-tax/purchase-side accrual automation (note as a Phase-2 candidate if branch (a) wins).
