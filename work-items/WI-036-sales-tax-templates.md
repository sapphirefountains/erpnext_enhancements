# WI-036: Sales tax templates rebuild (OD-2 direction set: Utah-law, stream-differentiated)
**Phase:** 1   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-029; CPA written taxability matrix = go-live sign-off gate (see Preconditions)   **Blocks:** WI-037, WI-038

## Why
The 3 QBO-imported templates ('US ST 4% - SF' @4.0, 'US ST 6% - SF' @6.0 is_default, 'US ST 6.25% - SF' @6.25 — all single-row 'On Net Total' with account_heads 'ST 4% - SF'/'ST 6% - SF'/'ST 6.25% - SF', plus 3 mirror Item Tax Templates; prod_finance_native) reflect QBO history, not a designed Utah position. The CPA ruling (OD-2) on Build (real-property improvement: contractor pays tax on materials, customer not charged) vs Service (taxable) determines which streams charge tax at all.

## Native-first check
Native **Sales Taxes and Charges Template** + **Item Tax Template** (both verified in use). Verdict: native config; nothing custom.

## Preconditions
- **OD-2 RESOLVED IN DIRECTION (2026-07-14): "follow the law"** → build the Utah-law stream-differentiated treatment. Under Utah law the expected matrix is this item's branch (a): Build (real-property improvement) → contractor pays tax on materials, customer NOT charged (zero/exempt path + purchase-side use-tax handling documented, Phase-2 automation candidate); Service on TPP, Events rentals, and Products → taxable to customer. Branches retained for completeness: (b) Build taxable → standard templates; (c) mixed materials/labor split → per-component templates. **Hard gate before this config goes LIVE: the CPA confirms the specific taxability matrix in writing** — the direction unblocks design/build now, but neither this plan nor its executors are the tax authority. Send the CPA request immediately.
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
