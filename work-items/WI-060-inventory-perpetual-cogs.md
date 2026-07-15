# WI-060: Inventory — perpetual valuation + COGS reclassification
**Phase:** 2   **Type:** CONFIG   **Size:** L
**Blocked by:** WI-025 (item groups populated), WI-029 (new CoA with stock/COGS accounts)   **Blocks:** nothing

## Why
Today inventory is not tracked as an asset: 111 items are flagged `is_stock_item=1` but `tabStock Entry` is EMPTY (0 rows ever), the QBO sync imported items as non-stock, and no stock account carries value. Material purchases expense immediately, so COGS and gross margin by period are wrong whenever stock is bought ahead of use. Perpetual valuation moves purchases through a stock asset account and books COGS on consumption/delivery.

## Native-first check
ERPNext perpetual inventory is **entirely native**: Stock Settings (enable perpetual on the Company via `enable_perpetual_inventory`), warehouse → account mapping (`Warehouse.account` / Company default inventory account), opening stock via native **Stock Reconciliation** (doctype verified present on prod), and stock-aware buying/selling flows (Purchase Receipt / Delivery Note, or invoice-only with `update_stock`). Native Stock Ledger and Stock Balance reports cover reporting. Verdict: native CONFIG + one DATA-style opening count; no custom code. Building any custom stock tracking would be a defect.

## Preconditions
- Physical count of the 111 stock items (quantities + acquisition cost) — a warehouse-floor exercise finance schedules.
- WI-025 done: items homed in the Item Group taxonomy so stock vs non-stock classification is reviewable per group.
- WI-029 done: the new chart contains the stock asset account(s), Stock Received But Not Billed, and COGS accounts.
- CPA sign-off on the COGS presentation change (P&L shape shifts when purchases stop expensing immediately).
- Decision with ops: full receipt/delivery flow (PR + DN) vs invoice-only with `update_stock=1` (lighter UI — consistent with the accountant's minimal-UI demand; enumerate both, recommend invoice-only to start).

## Scope
- Company/Stock Settings: enable perpetual inventory; set default inventory, stock-received-not-billed, and COGS accounts; define the warehouse tree (likely one 'Shop' warehouse to start) with account mapping.
- Review the 111 `is_stock_item=1` items (and the 472 non-stock) — correct misflagged ones per the count.
- Opening stock: one native Stock Reconciliation dated the go-forward date, quantities/valuations from the physical count. The offsetting entry posts to the temporary/opening mechanism per the native tool.
- SOP updates: buying flow (PO → PR or PI-with-update_stock), selling/consumption flow for job materials (material issue to project or DN), cycle-count cadence.

## Acceptance criteria
- `SELECT value FROM tabSingles WHERE doctype='Stock Settings' ...` / Company perpetual flag verified ON.
- After the opening Stock Reconciliation: native Stock Balance report total valuation == GL balance of the stock asset account (to the cent).
- One trial month: COGS booked via stock consumption ties to the Stock Ledger for the period; Balance Sheet shows the stock asset.
- Zero items remain misclassified per the count review (`is_stock_item` audit list signed off).

## Rollback
Disable perpetual inventory on the Company (GL postings already made stay; new transactions revert to expense-on-purchase); cancel the opening Stock Reconciliation if the count was wrong.

## Explicitly NOT in this work item
Barcode/WMS tooling; manufacturing BOM flows (the Product Configurator module covers configure-to-order separately); serialized/batch tracking; historical restatement of pre-cutover COGS.
