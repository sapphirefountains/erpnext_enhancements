# WI-004: Chart of Accounts target design + old→new account mapping workbook
**Phase:** 0   **Type:** DATA   **Size:** M
**Blocked by:** nothing (OD-1 RESOLVED 2026-07-14: "No" — single company; the branch-proof numbered design is kept anyway as free insurance. OD-3 RESOLVED: the stream income account is named **'Events'** directly, no reserved-sibling hedge needed)   **Blocks:** WI-029, WI-031, WI-032, WI-036, WI-053

## Why
Prod's 359-account chart is a raw QBO import: '(deleted)'-suffixed dead accounts (e.g. 'American Express Prime (deleted)'), locality tax junk ('VA-Fredericksburg City', 'Out of State'), and `account_number` mostly NULL despite the chart being labeled 'Standard with Numbers' (prod_finance_native). Test has a divergent 281-account chart (test_vs_prod). The brief demands a clean rebuild designed to work in a multi-company world pending OD-1. This item produces the design and the 359-row mapping so the rebuild (WI-029) and every account-referencing config item can execute mechanically.

## Native-first check
Native **Chart of Accounts Importer** (charts are per-company; company-abbr suffixing of account names is native) is the delivery vehicle — no custom CoA tooling. Native Account tree numbering (`Account.account_number`) is used. Verdict: native sufficient; this item only produces the CSV + mapping data. **Decision recorded: clean rebuild, not curation** — 359 accounts with dead/QBO-locality entries and no numbering are cheaper to replace than to renumber/rename in place, and prod's ledger is effectively empty (4 GL Entries), which is the one moment a full re-import is possible.

## Preconditions
- Frozen export of prod `tabAccount` (name, account_name, account_number, account_type, root_type, is_group, parent) — 359 rows (prod_finance_native).
- CPA consulted on required statutory rollups (informs numbering only; does not require OD-2 resolution).

## Scope
- Design a numbered chart (~150–220 accounts): 1xxx Asset / 2xxx Liability / 3xxx Equity / 4xxx Income / 5xxx COGS / 6xxx Expense, with income broken out by value stream (**Design / Build / Service / Events** — OD-3 resolved 2026-07-14: 'Rent' is renamed 'Events' by WI-065, so the income account is named 'Events' directly), a Stripe Clearing asset account, an Undeposited Funds asset account, per-jurisdiction Utah sales-tax liability sub-accounts (structure per the OD-2 Utah-law direction: Build-stream exempt handling + taxable streams; final rates/matrix confirmed by the CPA in writing before go-live), a `Temporary` account_type account (required by the opening tooling — `opening_balances.py::_plug_line` throws without one), and a 'Historical P&L Offset' equity account for WI-053.
- Produce two version-controlled artifacts committed to the erpnext_enhancements repo (documentation assets, not fixtures): (1) the Chart of Accounts Importer CSV, (2) `coa_mapping.csv` mapping each of the 359 prod accounts → new account number (or `RETIRE` for '(deleted)'/junk rows). Suggested path: `docs/migration/`.
- **OD-1 RESOLVED (2026-07-14): "No" — JDH stays outside ERPNext (former branch b).** Single-company scope for WI-029. The branch-proof discipline is kept anyway because it costs nothing: identical numbering, no 'SF'/'Sapphire' embedded in account names — so if OD-1 is ever reopened, the same CSV imports under a second Company (WI-061, ON HOLD) without redesign.

## Acceptance criteria
- CSV validates in Chart of Accounts Importer preview on the TEST site with zero errors.
- `coa_mapping.csv` has exactly 359 source rows; every row maps to a target number or `RETIRE`; every Company default-account field (`default_receivable_account`, `default_payable_account`, `default_bank_account`, `default_cash_account` — field names per repo_qbo_sync) has a designated target account.
- Every new leaf account has a non-NULL account_number; exactly one account with account_type='Temporary'.
- Both files merged to `main` (deploy log = GitHub Releases, per repo_app_inventory).

## Rollback
Files are additive artifacts in git; revert the commit.

## Explicitly NOT in this work item
Executing the import on any site (WI-029); tax template contents (WI-036); deciding OD-1/OD-2/OD-3.
