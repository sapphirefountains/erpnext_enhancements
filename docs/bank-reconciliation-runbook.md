# Bank reconciliation runbook — weekly statement import and Stripe payout matching

The day-one bank tie-out for [WI-043](../work-items/WI-043-bank-reconciliation-runbook.md).
Written for the accountant who runs it every week, not for whoever wrote the payout code.
Everything here is the **native** ERPNext path — Bank Statement Import → Bank Transaction →
Bank Reconciliation Tool → Bank Reconciliation Statement — because the Plaid module is
balances-only and never creates a Bank Transaction. Manual CSV import is the process, not a
stopgap.

Companion documents: [WI-042](../work-items/WI-042-bank-masters.md) (the masters),
[WI-040](../work-items/WI-040-stripe-payout-ingestion.md) and
[`stripe_payments/README.md`](../erpnext_enhancements/stripe_payments/README.md) (the payout
Journal Entry), [WI-049](../work-items/WI-049-month-end-close-adoption.md) (Month-End Close
adoption). Native behaviour below was read from `erpnext` `origin/version-16`, not `develop`.

> **State of production, read-only, 2026-09-01:** 0 Bank Transactions, 0 payout Journal
> Entries, 0 Month-End Close records, 0 saved column mappings. **There is no TEST site. The
> first live statement month is the rehearsal** — §9 is written for that month.

---

## 1. The accounts

Eight company bank accounts across three `Bank` records (`America First`, `Key Bank`,
`US Bank`), read from `tabBank Account WHERE is_company_account=1`:

| Bank | Bank Account (master name) | GL account | Last 4 |
|---|---|---|---|
| America First | `America First Checking - America First` | `13200 - America First Checking - SF` | *(blank)* |
| America First | `America First Savings - America First` | `13201 - America First Savings - SF` | *(blank)* |
| America First | `America First Auto Loan - America First` | `13202 - America First Auto Loan - SF` | *(blank)* |
| Key Bank | `Key Bank Checking - Key Bank` | `13100 - Key Bank Checking - SF` | *(blank)* |
| Key Bank | `Key Bank Savings - Key Bank` | `13101 - Key Bank Savings - SF` | *(blank)* |
| Key Bank | `Key MM Savings - Key Bank` | `13102 - Key MM Savings - SF` | *(blank)* |
| US Bank | `US Bank Checking - US Bank` | `13000 - US Bank Checking - SF` | *(blank)* |
| US Bank | `US Bank Savings - US Bank` | `13001 - US Bank Savings - SF` | *(blank)* |

All eight GL accounts are `account_type = Bank`, leaf, enabled — so each one appears in the
Bank Reconciliation Statement's account picker. Three things to know:

- **`bank_account_no` is empty on every row.** WI-042 asked for a masked number; fill in the
  last four digits on each Bank Account so the accountant can tell the portal export and the
  master apart at a glance. *(Flag: finance to supply.)*
- **`Stripe Clearing - SF` is deliberately not a Bank Account.** It is a GL clearing account
  (typed Bank so Payment Entries can use it); no statement exists for it, and it is reconciled
  through §6, not through this tool.
- **`13202 - America First Auto Loan`** is a liability-side balance typed as Bank. It
  reconciles from the lender statement by the same steps; whether it belongs in the weekly
  cycle or only at month-end is a controller call. *(Unverified.)*

`Company.default_bank_account` is `13100 - Key Bank Checking - SF`, and that is also where
Stripe pays out (§6).

---

## 2. Owners and cadence

Proposed from the Month-End Close seed (`DEFAULT_TASKS` in
[`month_end_close.py`](../erpnext_enhancements/enhancements_core/doctype/month_end_close/month_end_close.py))
and the WI-011 role matrix. **Confirm with the controller before the first live month.**

| What | Proposed owner | When |
|---|---|---|
| Export statements, import, match (§3–§6), all eight accounts | Lisa Symanski (Accounts User, preparer) | Weekly — Monday, for the week just ended |
| Review vouchers the preparer *created* from the tool (§5) | James Harris (Accounts Manager) | Weekly, from the tool's own audit trail |
| Stripe payout review alerts (§6) | Whoever holds `Accounts Manager` — the code notifies that role | As they arrive |
| Bank Reconciliation Statement per account, attached to the close (§7) | Lisa Symanski | Month-end, before the "Reconcile all bank accounts" task is marked Done |
| External review of the close | John Juntunen, CPA | Month-end, after §7 |

The review row exists because the tool lets the preparer **create and submit** a voucher in
one click (§5) — fine for bank fees, but a customer receipt created that way bypasses the
normal O2C path and the approver should see it.

---

## 3. Weekly — export the statement

For each account, in the bank portal, export the week's activity (or everything since the last
imported date) as CSV — one file per account. Keep the columns the bank gives you; the
mapping is done once in §4. Then:

- **Never overlap date ranges between two exports.** `Bank Transaction.transaction_id` is
  **not** unique in v16 and the importer has no duplicate check, so re-importing an
  overlapping week silently doubles those rows. If a bank only exports whole months, import
  the month once and match weekly against it.
- Name the file `<bank>-<account>-<from>-<to>.csv`. It is attached to the import document
  and becomes the audit copy.
- If the export has a **single signed Amount column**, split it into `Deposit` (positive) and
  `Withdrawal` (positive) in a spreadsheet first. The mapper is column → field and applies no
  sign logic; only the MT940 path splits signs, and none of our banks export MT940.

---

## 4. Weekly — import (Bank Statement Import)

**Path:** Accounting › *Bank Statement Import* › New — or the *Upload Bank Statement* button on
the Bank Reconciliation Tool, which opens the same form.

1. Set **Company** and **Bank Account** (the master name from §1; **Bank** fills itself).
2. Attach the CSV under **Import File**. The **Import Preview** renders each file column with
   a picker for the Bank Transaction field it feeds. Map:

   | Bank Transaction field | Feed it from | Notes |
   |---|---|---|
   | `Date` | the posted/settled date column | not the "pending" date |
   | `Deposit` | credits, positive | |
   | `Withdrawal` | debits, positive | |
   | `Description` | the memo/description column | this is what you read when matching |
   | `Reference Number` | check number / ACH trace / bank reference | **Auto Reconcile keys on this and nothing else** (§5) |
   | `Transaction ID` | the bank's unique transaction id, if it exports one | recommended; the only per-row identity you will have |
   | `Bank Account` | any column — leave it empty | the importer overwrites every row with the account chosen in step 1 |

   *Bank Account gotcha, verified in `add_bank_account()`:* the import refuses to start without
   a *Bank Account* column in the preview, and it fills that column itself — **unless it is the
   first column of the file**, where an upstream off-by-zero treats index 0 as "not found" and
   appends a stray value instead. Keep it anywhere but first (the downloaded template already
   does).

3. **Save.** The mapping is written to the **Bank** record's *Bank Transaction Mapping* table
   when the import starts (`update_mapping_db`), and every later import for any account at
   that bank pre-loads it (`validate`). **You map once per bank, not per account or per
   week.** If the bank changes its export layout, remap once and the new mapping replaces
   the old.
4. **Start Import.** It runs as a background job on the `default` queue and inserts each row
   as a **submitted** Bank Transaction (`submit_after_import` is fixed at 1), status
   `Unreconciled`. Watch the **Import Log**: successes must equal the rows in the file.
   - *"Scheduler is inactive"* — a bench problem, not yours; raise it.
   - A deploy landing mid-import kills the job — a merge to `main` flushes the queue redis
     and restarts the bench (see `CLAUDE.md`). Reopen the import document and press
     **Retry**; check the Bank Transaction list for a partial batch first (§8).
5. Confirm: Bank Transaction list, filter *Bank Account* + *Date* range → count equals the
   file. Duplicates from an overlapping export show up here as pairs — fix per §8 before
   matching anything.

---

## 5. Weekly — match (Bank Reconciliation Tool)

**Path:** Accounting › *Bank Reconciliation Tool*. Set **Company**, **Bank Account**, **From
Date** / **To Date** (the week), and **Closing Balance** from the statement, then **Get
Unreconciled Entries**. Every unreconciled Bank Transaction in the range is listed with an
**Actions** button; the dialog offers three actions:

- **Match Against Voucher** — the default. Candidates are submitted Payment Entries and
  Journal Entries on this bank GL account with no `clearance_date`, ranked by reference-number
  match + amount match (+ party match for PEs); tick *exact match* under Filters to hide
  everything but the amount. Select and **Reconcile the Bank Transaction**. Partial
  allocation is allowed — the row stays `Unreconciled` with the remaining *Unallocated
  Amount* until it reaches zero. Transfers between our own accounts: tick *Bank Transaction*
  under Filters and match the mirror-image row from the other account.
- **Create Voucher** — when nothing was ever recorded. **Document Type** = *Payment Entry*
  (a customer receipt or supplier payment nobody entered) or *Journal Entry* (bank fees,
  interest, loan payment, anything with no party). The tool prefills amount, date and
  reference, **inserts, submits and reconciles in one step** — read the second account and
  the party before you press it.
- **Update Bank Transaction** — fix *Reference Number* or *Party* on the row. Setting the
  reference to what the voucher carries is how you teach Auto Reconcile a recurring item.

**Auto Reconcile** matches only on **exact reference-number equality** — `Payment Entry
.reference_no` or `Journal Entry.cheque_no` equal to the statement's *Reference Number* — and
then on amount. It pairs check numbers and ACH trace ids if WI-031's AP process records them
as `reference_no`; it will never pair a Stripe payout (§6) or a bank fee. Run it first, then
work the remainder by hand.

**The reconciliation date.** Reconciling stamps `clearance_date` on the voucher — the *bank
transaction date* (or the allocation date, whichever is later). The voucher's own
`posting_date` stays the business date. A Payment Entry posted 30 June and cleared 2 July is
therefore *correctly* listed as outstanding on June's Bank Reconciliation Statement and gone
from July's. Never back-date or re-date a voucher to make the bank agree.

**Done for the week** when a Bank Transaction list filtered to *Status = Unreconciled*,
this account, *Date ≤ week end* returns nothing.

---

## 6. Stripe payouts

Each Stripe payout is one bank credit on `13100 - Key Bank Checking` and one Journal Entry
that [`stripe_payments/core/payouts.py`](../erpnext_enhancements/stripe_payments/core/payouts.py)
posts when the `payout.paid` webhook arrives. This is the entry, exactly as the code builds it:

| Journal Entry field | Value | Source |
|---|---|---|
| `voucher_type` | Journal Entry | fixed |
| `posting_date` | Stripe `arrival_date`, read as a **UTC** calendar date | the day the money is due at the bank |
| `cheque_no` (*Cheque/Reference No*) | the Stripe payout id, `po_…` | **the matching key and the idempotency key** |
| `cheque_date` (*Reference Date*) | same as `posting_date` | |
| `user_remark` | `Stripe payout po_…: net X USD, fees Y, N charge(s), refunds Z.` plus any *Fee variance* / *REVIEW* note | read it before matching |
| Row 1 | **Dr** `Stripe Payments Settings.payout_bank_account` — **net** | today: `13100 - Key Bank Checking - SF` |
| Row 2 | **Dr** `Stripe Payments Settings.fee_expense_account` — **fees** | today: `CC Processing fees - SF` |
| Row 3 | **Cr** `Stripe Payments Settings.deposit_account` — **net + fees** | today: `Stripe Clearing - SF` |

Fees are the sum of the per-transaction `fee` on the payout's balance transactions, never an
estimate. Every row carries the company cost center (the fee leg is P&L). A refund-heavy
*negative* payout flips the bank and clearing legs to the other side automatically. Field
values above were read from production on 2026-09-01; the **field names** are the contract,
the values are settings. The fee account is `CC Processing fees - SF` by decision (WI-005,
2026-09-01: keep the account the history already posts to; `61530 - Merchant Fees - SF` was
created for it and then disabled rather than deleted, so a later re-point is one settings
change, not a chart change). The chart-of-accounts rebuild (WI-029) was descoped, so nothing
else re-points these.

**Idempotency.** Before posting, the code looks for any Journal Entry with `cheque_no` equal
to the payout id, under a per-payout file lock. A draft or submitted one is reused; **if only
a cancelled one exists, nothing is re-posted** — the code reads that as "the accountant
reversed this on purpose". The hourly `poll_payouts` job re-reads Stripe's 20 most recent
payouts and posts any that are `paid` but missing, so a lost webhook self-heals within the
hour as long as the payout is still among the last twenty. A `payout.failed` event posts no
entry and raises an alert.

**Matching, weekly.** The bank credit says something like *STRIPE* — the exact descriptor Key
Bank prints is **unverified until the first statement**; what is certain is that it does not
carry the `po_` id, so Auto Reconcile cannot pair it. By hand:

1. In the Stripe dashboard, *Balance › Payouts*: note the payout's **id**, **net amount** and
   **arrival date** for each credit on the statement.
2. In the tool, **Match Against Voucher** with *Journal Entry* ticked. The candidate with
   `paid_amount` = net and *Reference* = the `po_` id is the one; the amount alone is not
   proof when two payouts share a figure. Allow the bank date to trail the arrival date by a
   business day or two.
3. Optionally **Update Bank Transaction** → *Reference Number* = the `po_` id first; then
   Auto Reconcile pairs it and the row records which payout it was.

**Degraded manual mode** — the credit is on the statement and no Journal Entry exists (a
payout older than the last twenty, a foreign-currency payout the module skips, a webhook and
poll both lost to a deploy). Build the same three-leg entry by hand from the Stripe payout
report — Accounting › *Journal Entry* › New, **Cheque/Reference No = the `po_` id**, Reference
Date and Posting Date = arrival date, rows exactly as the table above, remark in the same
shape — then match it. **Carry the `po_` id.** It is what stops `poll_payouts` posting a
second entry underneath yours, and what the month-end query in §9 counts. And if you cancel
a bad payout entry, replace it (with the id) rather than expecting the code to.

**Clearing at month-end.** After every payout of the month is matched, `Stripe Clearing - SF`
should hold only payouts in transit plus anything the remark flagged `REVIEW` (disputes,
adjustments — WI-041's side). WI-049 already plans a "Reconcile Stripe Clearing" task on the
close; until it exists, note the residual in the bank task's *Notes*.

---

## 7. Month-end — Bank Reconciliation Statement and the close

**Path:** Accounting › Reports › *Bank Reconciliation Statement* (search bar: the same
name). Filters: **Company**; **Bank Account** — this filter takes the *GL account*
(`13100 - Key Bank Checking - SF`), not the Bank Account master; **Date** = period end.

The report lists every voucher on that account with no `clearance_date`, or one after the
report date, and closes with four rows: *Bank Statement balance as per General Ledger*,
*Outstanding Cheques and Deposits to clear*, *Cheques and Deposits incorrectly cleared*, and
**Calculated Bank Statement balance**. **The last row must equal the statement's closing
balance for that date, for each of the eight accounts.** A difference is one of: a Bank
Transaction still `Unreconciled` in the period (import gap or unmatched row), a duplicate
import (§8), a voucher posted to the wrong bank GL account, or a voucher cleared against the
wrong week — the report's *Clearance Date* column shows the last two.

Then, for the period's **Month-End Close** record (search bar › *Month-End Close*; named
`MEC-#####`, one per period — WI-049 makes it the close vehicle from the January 2027 close,
and none exist yet):

1. Export the report (report toolbar menu › *Export*) once per account and **attach all
   eight** to the Month-End Close record from the form sidebar.
2. Mark the task **"Reconcile all bank accounts"** Done, and write the eight *calculated vs
   statement* figures in its *Notes*. Mark **"Reconcile credit card accounts"** Done or N/A
   the same way — card statements travel the identical CSV path.
3. Leave submission to the Accounts Manager: submitting the close sets
   `Company.accounts_frozen_till_date` to the period end and blocks back-dated postings for
   everyone else. Until the first Month-End Close exists, file the exports in the finance
   close folder instead. *(Folder location unverified.)*

---

## 8. Bad-import recovery

Symptoms: rows on the wrong account, an overlapping date range (pairs of identical rows),
Deposit and Withdrawal swapped, dates a day off. Three facts decide the fix:

- **Bank Transaction is submittable.** Delete needs cancel first.
- **Cancel is safe for the vouchers.** `on_cancel` delinks every matched Payment Entry /
  Journal Entry and clears its `clearance_date`, so the voucher simply becomes outstanding
  again — nothing is lost on the GL side. The row's status becomes `Cancelled`.
- **A voucher the tool *created* is not undone by cancelling the row.** If the bad batch led
  to a Create Voucher, that PE/JE must be cancelled separately (a GL reversal, not a delete —
  `enable_immutable_ledger` is off on this site, so cancel inserts reversing rows).

The procedure:

1. **Stop.** Do not import the corrected file first — it doubles the problem.
2. Isolate the batch: Bank Transaction list, filter *Bank Account* + *Date* range + created
   timestamp (or the import document's *Go to Bank Transaction List* button). Count must
   equal the file.
3. Filter the batch to *Status = Reconciled* and, for each, note the vouchers in its *Payment
   Entries* table. Decide which of those were created by the tool and need their own cancel.
4. Select the batch › Actions › **Cancel**, then Actions › **Delete**. More than 20 selected
   runs as a background job — a deploy mid-run kills it; recount and repeat.
5. Cancel any tool-created vouchers from step 3. Fix the file. Re-import (§4), re-match (§5),
   recount.

If the period is already closed (Month-End Close submitted), the freeze blocks voucher cancels
for everyone but `Accounts Manager` — either an Accounts Manager does step 5, or the close is
cancelled (which restores the previous frozen date exactly) and re-submitted. **Never repair
Bank Transactions or clearance dates in SQL** — `clearance_date` on the voucher is what the
month-end report reads.

---

## 9. First-month acceptance checklist

WI-043's criteria, restated for a live month because there is no TEST site to rehearse on.
Run the checks read-only after the first month-end; every query is a `SELECT`.

| # | Criterion | Check |
|---|---|---|
| 1 | One full statement month imported for **every** account in §1 | `SELECT bank_account, COUNT(*), MIN(date), MAX(date) FROM \`tabBank Transaction\` WHERE docstatus=1 GROUP BY bank_account` → eight rows spanning the month |
| 2 | Zero unallocated for the period | `SELECT COUNT(*), SUM(unallocated_amount) FROM \`tabBank Transaction\` WHERE docstatus=1 AND status='Unreconciled' AND date <= '<period end>'` → `0, 0` |
| 3 | Bank Reconciliation Statement shows zero unexplained difference | *Calculated Bank Statement balance* = statement closing balance, all eight accounts; exports attached per §7 |
| 4 | ≥ 1 Stripe payout matched to its Journal Entry | `SELECT name, cheque_no, posting_date, clearance_date FROM \`tabJournal Entry\` WHERE docstatus=1 AND cheque_no LIKE 'po_%' AND clearance_date IS NOT NULL` → ≥ 1 row |
| 5 | Column mapping persisted, one per bank | `SELECT parent, COUNT(*) FROM \`tabBank Transaction Mapping\` GROUP BY parent` → three banks |
| 6 | Owners and cadence confirmed | Controller has signed off §2; names corrected here if they changed |
| 7 | Runbook survived contact | Every step that did not match what the screen showed is edited in this file, not worked around |

A month that passes 1–5 with a Stripe payout matched by hand (not Auto Reconcile) still
passes. What a green month does **not** prove: that `Stripe Clearing - SF` nets to zero (that
needs WI-041's refund and dispute entries), or that the January 2027 close will lock cleanly
(WI-049's dry run).

---

## 10. What this runbook could not verify

Besides the items flagged *(unverified)* inline — the Auto Loan cadence, the close folder,
the §2 owners:

- The CSV export layout of each of the three bank portals — nobody has exported one yet, and
  the mapping table is empty. §4's field table is the target, not a description of the files.
- The statement descriptor Key Bank prints on a Stripe credit.
- The labels on the Reconciliation Tool's three summary cards (rendered HTML, not in the
  translatable strings read for §5); the button and action names are verified.
