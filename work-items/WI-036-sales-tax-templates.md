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

## Expected taxability matrix (direction recorded 2026-11-23, not yet CPA-confirmed)
**Source:** four research summaries Nikolas collected on 2026-11-23 (Build, Design Services, Maintenance & Repair Work, Rentals). They are research, not a ruling. They agree with branch (a) and add the Service and Rent detail branch (a) left open. **The hard gate above is unchanged.** The CPA's general framework, and the reason Build is still open, are in [`docs/migration/wi036-utah-tax-guidance.md`](../docs/migration/wi036-utah-tax-guidance.md).

| Stream / kind of line | Charge the customer sales tax? | Notes |
|---|---|---|
| **Design**: design fees, electronic deliverables | No | A separately billed hard-copy reproduction may be taxable. Sapphire pays tax on its own supplies and SaaS. |
| **Build**: permanent feature (plumbed in, built into the ground or a structure) | No | Real-property improvement. Sapphire is the consumer and pays sales or use tax on materials at purchase (`2136 Use Tax Payable` / `60920 Sales & Use Tax Expense`). |
| **Product sale**: portable or standalone fountain, parts sold | Yes | Tangible personal property. |
| **Service**: on-site repair of a permanent feature, its pump included | No, on labor or parts | Sapphire pays tax on the parts when it buys them. |
| **Service**: repair of a portable feature, or of a component taken back to the shop | Yes, on labor and parts | A component removed for shop repair is tangible property while it is off site. Parts are bought tax-free on a resale certificate. |
| **Service**: cleaning | Real property: no. Tangible personal property: yes. | Routine maintenance of a permanent fountain is open: question 1 below. |
| **Rent**: equipment rented without an operator | Yes | At the event location's combined rate. Operated rentals are open: question 4. |
| **Out-of-state work or events** | Per that state's rules | For example, California: register for a seller's permit, charge the rate where the equipment is used, and treat delivery in Sapphire's own truck as taxable there. |

**Rules that hold across streams**
- **Itemize** labor, parts, equipment and delivery on every invoice. A lump sum can make the whole charge taxable.
- **The rate follows the destination:** where the service is performed or the event is held, not the shop's location. The source is the Utah State Tax Commission's quarterly *Combined Sales and Use Tax Rates* chart. The 2026 Q4 chart (in effect 2026-10-01) gives, for example, Bountiful 06-004 at 7.25%, Salt Lake City 18-122 at 8.45%, Park City 22-030 at 9.55% and St. George 27-020 at 6.75%.
- **Customer exemptions** (resale, exempt organization, contractual) follow the guidance doc's Tax Category, per-document checkbox and certificate layers.

**What ERPNext needs on a line to decide**
- **The stream or item type:** Item Tax Templates through WI-025's item groups.
- **Whether the feature is permanent or portable:** new. It probably belongs on the Project or the fountain record, not chosen per invoice.
- **On site or off site,** for service lines.
- **The customer's Tax Category,** from WI-037.
- **The jurisdiction:** the job-site or event address picks the template.

**Questions for the CPA** (send these with the matrix)
1. **Routine maintenance of a permanent fountain** (cleaning, water treatment, inspection). Is it cleaning real property (nontaxable) or cleaning machinery attached to real property (taxable)? The research gives both answers. It decides the tax on most maintenance contracts.
2. **Chemicals:** when are they a sale to the customer, and when does Sapphire consume them?
3. **A fountain product Sapphire installs permanently:** a taxable product sale, or Build (contractor as consumer)?
4. **Operated rentals,** with Sapphire staff running the equipment: a rental or a service under Utah law? The research cites only other states.
5. **Confirm the Build real-property treatment itself.** It is still open in the guidance doc.
6. **The governing Utah publications.** The research cites Publication 42.
7. **Use-tax accrual timing:** per purchase or at period end.
8. **Credit cards on the Cash Flow statement** (added 2026-11-24 for WI-056; not a tax question, but it goes to the same CPA). Should the credit-card liability ledgers be typed **Bank** so the Plaid card feeds can be reconciled? ERPNext then counts them as cash, net of the balances owed, and their whole history moves out of operating activities. The cards held until the answer are Capital One Spark (22500), American Express Prime (22600) and the US Bank company Mastercard. Background: `erpnext_enhancements/plaid_banking/README.md`, "Credit cards".

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
