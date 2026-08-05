# WI-067: Make imported Sales Invoices equal what QuickBooks says they are
**Phase:** 0   **Type:** DATA   **Size:** M
**Blocked by:** v1.244.0 + v1.246.0 deployed   **Blocks:** pre-2026 Sales Invoice GL posting (WI-028 draft triage, WI-032/WI-033 openings)

## Why
Across the **1,413 unposted pre-2026 Sales Invoices**, ERPNext totals **$6,996,286.32**
against QuickBooks' **$4,365,679.06**, and **615 invoices (43.5%) differ**. Two mapper
defects caused it, pulling in opposite directions, which is why neither was obvious from
the aggregate:

| Defect | Direction | Scale | Fixed in |
|---|---|---|---|
| `Qty: 0` progress-billing lines billed at full unit price | **OVER**states | ~$2.32M of a $2.63M overstatement, 615 invoices | v1.244.0 |
| `TxnTaxDetail.TotalTax` never imported | **UNDER**states | $58,162.96 across 424 invoices | v1.246.0 |

Scope note: **$58,162.96 / 424 is the pre-2026 Sales Invoice slice.** The fix itself is
wider, because `_map_sales_receipt` delegates to `_map_sales_invoice`: measured 2026-08-05
it creates tax rows on **509 documents totalling $71,141.69** (1,558 Invoices → 505 taxed /
$71,008.30, plus 36 SalesReceipts → 4 taxed / $133.39). Quote whichever is relevant, but
do not quote $58,162.96 as the exposure of the change.

The tax one: QuickBooks carries invoice tax **outside** the `Line` array, on
`TxnTaxDetail`, and `_sales_items` reads only `Line`. Invoice I100549 (Myers Mortuary,
2022-09-08) is $385.56 in QBO — $360.00 of lines plus $25.56 of Utah tax at 7.1% — and
imported as $360.00, with an empty taxes table.

A caution on the reconciliation figures previously quoted for `25010 Sales Tax Agency
Payable` (**$882.66** ERPNext vs **$37,096.89** QuickBooks): the ERPNext side is a
*draft-only* total — the sum of five `docstatus = 0` Journal Entry lines; a Trial Balance
shows $0.00 because nothing is submitted. The QuickBooks figure could **not** be reproduced
from any cached QBO data (Account 191's own `CurrentBalance` is −10,448.05; total QBO
invoice tax is $71,240.26), so treat $37,096.89 as unsourced and do not use it as an
acceptance target. The verifiable form of the same argument is stronger anyway: the 36
distinct TaxCode-mapped Accounts have **zero** Journal Entry lines at any docstatus and
**zero** GL rows, so 25010 is the only tax account QBO data has ever been routed to.

**Do not evaluate the two fixes independently.** They offset, so fixing only one moves the
aggregate in a direction that looks wrong. The success criterion below is per-invoice and
binary for exactly that reason.

Underneath both sits a structural cause worth naming: **there was no Sales Invoice
reconciliation guard.** The buy side has compared its mapped total against QuickBooks'
`TotalAmt` since the mixed-Bill fix (`_purchase_invoice_imbalance`); the sell side never
did, so a Sales Invoice could validate, save and look entirely clean while posting a number
QuickBooks disagreed with. v1.246.0 adds `_sales_invoice_shortfall`, which would have
caught **both** defects on the first import.

## Native-first check
Native and sufficient throughout.

- **Tax** posts as an ERPNext **Sales Taxes and Charges** row of `charge_type: "Actual"` —
  the same mechanism an accountant uses by hand, and the sell-side twin of the
  `_purchase_charges` path already in use for Bills. No custom tax doctype.
  Note `Sales Taxes and Charges` has **no** `category` or `add_deduct_tax` field; those are
  Purchase-only, and setting them would be silently dropped by Frappe rather than rejected.
- **Propagation** uses the integration's own `preview_resync` / `run_resync` pair, not a
  bespoke backfill script and not `import_all`. See Scope.
- **Destination account** is a Link field on QuickBooks Online Settings, so changing it
  needs no deploy.

## Preconditions
- v1.244.0 (qty) and v1.246.0 (tax + guard) both deployed to the target site.
- `25010 - Sales Tax Agency Payable - SF` present, `is_group=0`, not disabled — **confirmed**
  (2026-08-05), with `account_type` set to `Tax` by `patches/set_sales_tax_account_type.py`.
  `sales_tax_account` does not yet exist in the production Singles table, so the
  account-number fallback is the live path on first deploy — confirmed to resolve.
- **Staging first, and validate each fix separately there** so you know which code did what.
  Production gets a **single** resync once both are deployed.
- **The QuickBooks sync must be paused before anything is submitted.** ERPNext cannot update
  a submitted document, so every later QBO edit to a posted one becomes a sync failure.
  Shared with [WI-068](WI-068-group-account-remap.md); it is one decision, not two.

## Scope

### Where tax posts — decided, not open
All sales tax posts to **one** account (`25010`, configurable via
`QuickBooks Online Settings.sales_tax_account`), with the resolved TaxCode's name carried on
the charge row's **description** so the jurisdiction survives at transaction level.

QBO TaxCodes are **rate definitions, not GL accounts**. QuickBooks itself books all sales
tax to a single agency-payable account and keeps jurisdiction detail in its Sales Tax Centre,
outside the ledger. Posting to the accounts `_map_tax_code` creates would build a parallel
tax-liability structure that could never tie to QuickBooks — and tying to it is the entire
point. One account matches the source; the description keeps the detail.

### The id-space trap
`TxnTaxCodeRef` and `TaxRateRef` are **different QBO id spaces**, and confusing them
resolves silently to a real-but-wrong account. On I100549, `TxnTaxCodeRef` 8 is
`Utah - Weber - Ogden - Inactive - SF`; `TaxRateRef` 15 read as a TaxCode is
`Sandy Utah - SF` — a different city, no error. Identity comes from **`TxnTaxCodeRef`**,
amount from **`TotalTax`**. TaxRate is not imported by this integration at all, so a
`TaxRateRef` lookup can only ever collide with an unrelated record sharing the number.

### Unmapped tax codes
`TaxCode` is deliberately **not** in `CDC_ENTITIES` (QBO's CDC endpoint does not support it),
so a tax code created in QuickBooks today does not arrive until the next **full import**.
Until then its invoices still import with the correct tax **amount**, labelled
`"Sales Tax"` instead of the jurisdiction; a later full import plus resync fills the label
in. Losing a label beats losing the money.

### Propagation — resync, not Import All
Deploying code corrects nothing already imported.

1. `preview_resync(entity_types=["Invoice", "SalesReceipt"])` — writes nothing; stores a
   per-record plan against a `preview_id`.
2. **Read the preview.** Expect roughly 615 invoice updates from the qty fix plus 509
   documents gaining a tax row.
3. `run_resync(preview_id)` — replays the stored payloads with `overwrite=True`.

`import_all` does **not** pass `overwrite`, so any record where a user edited a QBO-owned
field returns a conflict instead of updating. Conversely `overwrite=True` resolves conflicts
in QuickBooks' favour and **will discard manual edits** — which is why step 2 is not
optional.

## Acceptance criteria

**Primary (binary, no attribution needed):** for every one of the 1,413 pre-2026 Sales
Invoices, `base_grand_total` equals its QBO `TotalAmt`.

```sql
-- must return 0
SELECT COUNT(*) FROM `tabSales Invoice` si
JOIN `tabQuickBooks Sync Mapping` m ON m.erpnext_name = si.name AND m.erpnext_doctype = 'Sales Invoice'
WHERE si.docstatus = 0 AND si.posting_date < '2026-01-01'
  AND ABS(si.base_grand_total - <QBO TotalAmt from the latest raw payload>) > 0.005
```

**This release does not reach 0, and the gap is known and measured.** Modelling the mapper
against every cached payload (2026-08-05) gives, after both fixes:

| Outcome | Invoices | Amount |
|---|---|---|
| Reconcile exactly | **1,315** of 1,413 | — |
| `DiscountLineDetail` not modelled | **36** | **$89,561.00** of discounts |
| Rounding-order and other differences | **62** | largest I100853 −$304.37, I101039 +$258.31 |

Across the full mapped population (including 2026), **120** of 1,556 park.

So the honest post-resync target is **1,315 / 1,413**, and the **discount gap ($89,561) is
larger than the tax gap this release fixes ($58,162.96)**. Those 98 do not import silently
wrong — `_sales_invoice_shortfall` **parks each one for manual review naming the cause**,
which is the difference between a known 98 and an unknown 615.

The 62 grew from 25 when the guard's rounding was corrected to match ERPNext's (round rate
and qty first, then multiply, with half-to-even `flt`). Those extra 37 are invoices the
naive form blessed while ERPNext would post a different total — see the 1.246.0 changelog
and `_sales_invoice_shortfall`'s docstring. Discounts are scoped as
follow-on work below.

Also required:
- `25010 - Sales Tax Agency Payable - SF` has `account_type = 'Tax'`.
- Sum of `tax_amount` over imported Sales Invoice taxes rows ≈ **$71,141.69** across 509
  documents (**$58,162.96** over the 424 pre-2026 Invoices alone).
- ERPNext's `25010` draft-side total moves from $882.66 to roughly $882.66 + $71,141.69.
  It will not appear on a Trial Balance until the backlog is submitted; do not treat a
  $0.00 Trial Balance as a failure of this work item.
- Invoice **I100549** specifically: `base_grand_total` = **385.56**, one taxes row of
  **25.56** whose description is the Ogden jurisdiction, **not** Sandy.
- Spot-check that no invoice's taxes row points at a `_map_tax_code` account.

## Rollback
- **Code:** revert the v1.246.0 mapper commit. `_sales_charges` disappears, `_map_sales_invoice`
  stops mapping `taxes`, and the next resync strips the rows (the taxes key is always mapped,
  so a re-sync replaces the table rather than leaving stale rows).
- **Data:** re-running `preview_resync` → `run_resync` on the reverted code restores the
  previous (tax-free) state. Nothing is deleted and no document is submitted, so the blast
  radius is confined to draft field values.
- **Destination account:** changeable in Settings without a deploy.
- The qty half of v1.244.0 is **not** separately revertible here; it shipped earlier and has
  its own entry.

## Explicitly NOT in this work item
- **QBO discount lines (`DiscountLineDetail`).** 36 invoices, **$89,561.00** — measured, and
  the single largest remaining fidelity gap. Needs a decision on whether an ERPNext discount
  belongs on the item line (`discount_amount`) or as a negative Actual charge, which changes
  what the GL sees. Deferred deliberately, not overlooked.
- **The 62 other non-reconciling invoices.** Two distinct populations, and they need
  different answers:
  - **Precision-unrepresentable lines.** QBO stores unit prices and quantities at more
    decimals than ERPNext keeps (`UnitPrice 2051.9872727` on I100352, `Qty 0.6666999` on
    I101613). ERPNext rounds rate to 2 and qty to 3 before multiplying, so these invoices
    *genuinely cannot* post QuickBooks' total — the difference is real, not a mapper bug,
    and usually a cent. Deciding whether to accept a per-invoice tolerance is a business
    call, and accepting one would weaken the binary criterion above.
  - **Real differences** needing per-invoice inspection (largest: I100853 −$304.37,
    I101039 +$258.31, I100936 −$187.50), which the shortfall guard now makes findable.
- **A per-jurisdiction tax liability breakdown.** Rejected above; QuickBooks does not keep
  one in its ledger either.
- **Sales Taxes and Charges Templates**, tax rules, or item tax templates — nothing here
  needs a template, and WI-036 owns forward-looking Utah tax treatment.
- **Purchase-side tax.** Bills route account-based lines through `_purchase_charges` already.
- **Submitting anything.** This makes the numbers right; WI-028 / WI-032 decide what posts.
- **The group-account remap** — [WI-068](WI-068-group-account-remap.md), a different
  population (Journal Entries) with a different blocker. Only the pause-before-submit
  constraint is shared.
