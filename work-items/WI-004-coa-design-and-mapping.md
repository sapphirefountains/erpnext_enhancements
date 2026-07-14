# WI-004: Chart of Accounts target design + old→new account mapping workbook
**Phase:** 0   **Type:** DATA   **Size:** M
**Blocked by:** OD-1 (enumerated, non-blocking — enumerate branches; design must not require resolution)   **Blocks:** WI-029, WI-031, WI-032, WI-036, WI-053

## Why
Prod's 359-account chart is a raw QBO import: '(deleted)'-suffixed dead accounts (e.g. 'American Express Prime (deleted)'), locality tax junk ('VA-Fredericksburg City', 'Out of State'), and `account_number` mostly NULL despite the chart being labeled 'Standard with Numbers' (prod_finance_native). Test has a divergent 281-account chart (test_vs_prod). The brief demands a clean rebuild designed to work in a multi-company world pending OD-1. This item produces the design and the 359-row mapping so the rebuild (WI-029) and every account-referencing config item can execute mechanically.

## Native-first check
Native **Chart of Accounts Importer** (charts are per-company; company-abbr suffixing of account names is native) is the delivery vehicle — no custom CoA tooling. Native Account tree numbering (`Account.account_number`) is used. Verdict: native sufficient; this item only produces the CSV + mapping data. **Decision recorded: clean rebuild, not curation** — 359 accounts with dead/QBO-locality entries and no numbering are cheaper to replace than to renumber/rename in place, and prod's ledger is effectively empty (4 GL Entries), which is the one moment a full re-import is possible.

## Preconditions
- Frozen export of prod `tabAccount` (name, account_name, account_number, account_type, root_type, is_group, parent) — 359 rows (prod_finance_native).
- CPA consulted on required statutory rollups (informs numbering only; does not require OD-2 resolution).

## Scope
- Design a numbered chart (~150–220 accounts): 1xxx Asset / 2xxx Liability / 3xxx Equity / 4xxx Income / 5xxx COGS / 6xxx Expense, with income broken out by value stream (Design/Build/Service/Rent — mirror `Project Type` values verified in prod_projects_opps; hold a reserved sibling number for 'Events' pending OD-3), a Stripe Clearing asset account, an Undeposited Funds asset account, per-jurisdiction Utah sales-tax liability sub-accounts (structure only; rates/taxability wait on OD-2), a `Temporary` account_type account (required by the opening tooling — `opening_balances.py::_plug_line` throws without one), and a 'Historical P&L Offset' equity account for WI-053.
- Produce two version-controlled artifacts committed to the erpnext_enhancements repo (documentation assets, not fixtures): (1) the Chart of Accounts Importer CSV, (2) `coa_mapping.csv` mapping each of the 359 prod accounts → new account number (or `RETIRE` for '(deleted)'/junk rows). Suggested path: `docs/migration/`.
- **OD-1 branches (enumerated, not resolved):**
  - (a) JDH becomes a second Company: import the SAME CSV for the JDH company — native per-company charts give a structurally parallel tree with automatic ' - <abbr>' suffixing; keep all account_numbers identical across companies; no account name may embed 'SF' or 'Sapphire'.
  - (b) JDH stays outside ERPNext: single company, no change.
  - (c) JDH tracked as a dimension within Sapphire Fountains: no second chart; add a native Accounting Dimension instead (coordinate with WI-054's dimension design so only one dimension scheme exists).
- The design must be identical under all three branches (that is what "structurally parallel + numbering" buys), so OD-1 blocks only WI-029's *scope* (one company vs two), not this design.

## Acceptance criteria
- CSV validates in Chart of Accounts Importer preview on the TEST site with zero errors.
- `coa_mapping.csv` has exactly 359 source rows; every row maps to a target number or `RETIRE`; every Company default-account field (`default_receivable_account`, `default_payable_account`, `default_bank_account`, `default_cash_account` — field names per repo_qbo_sync) has a designated target account.
- Every new leaf account has a non-NULL account_number; exactly one account with account_type='Temporary'.
- Both files merged to `main` (deploy log = GitHub Releases, per repo_app_inventory).

## Rollback
Files are additive artifacts in git; revert the commit.

## Explicitly NOT in this work item
Executing the import on any site (WI-029); tax template contents (WI-036); deciding OD-1/OD-2/OD-3.
