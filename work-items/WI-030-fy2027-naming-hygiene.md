# WI-030: Cutover hygiene — Fiscal Year 2027 + disable legacy fiscal years + naming-series reset
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-029   **Blocks:** WI-032, WI-033

## Why
Prod has 19 fiscal years (2008–2026), all enabled, and **no FY 2027** (prod_finance_native) — the first 2027 posting will fail. Open ancient FYs invite backdated mis-posting. Separately, the naming-series counters consumed by 15k+ mirror drafts pollute document numbering; live 2027 documents and opening documents should be visually distinguishable from any legacy residue.

## Native-first check
Native Fiscal Year doctype (`Fiscal Year.disabled` verified) and native naming-series management (**Document Naming Settings** / per-doctype series options — review correction C13: there is no 'Naming Series' DocType in this build; the 'Update Series Number' action lives in Document Naming Settings). Verdict: fully native, pure CONFIG.

## Preconditions
- WI-028 deletion done (never reset a series counter while documents using that prefix still exist).

## Scope
- Create Fiscal Year '2027' (year_start_date 2027-01-01, year_end_date 2027-12-31).
- Set `disabled=1` on FYs 2008–2024 (17 rows). **Keep 2025 and 2026 enabled**: the Opening Entry posts on 2026-12-31 and WI-053's trend JEs post into 2025/2026. Disable 2025/2026 only after WI-053 completes (or is formally declined).
- Define NEW naming-series prefixes via Document Naming Settings (exact strings decided with finance; requirement is *new* prefixes, not reset of existing counters): a distinct opening series for Sales Invoice and Purchase Invoice opening documents (e.g. an `OPEN-` prefixed member added to each doctype's series options) and year-scoped live series for Journal Entry / Sales Invoice / Payment Entry from 2027-01-01. Record chosen prefixes in the migration runbook.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabFiscal Year\` WHERE name='2027' AND disabled=0` = 1.
- `SELECT COUNT(*) FROM \`tabFiscal Year\` WHERE disabled=1` = 17; 2025 and 2026 have disabled=0.
- New series prefixes appear in the respective doctypes' naming series options and `tabSeries` rows exist with current=0 for each new prefix.

## Rollback
Re-enable fiscal years (set disabled=0); remove unused series options (safe while current=0).

## Explicitly NOT in this work item
Posting anything; changing autoname of custom doctypes; Document Naming Rules for non-finance doctypes.
