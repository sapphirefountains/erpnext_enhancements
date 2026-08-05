# WI-068: Move draft pre-2026 Journal Entry lines off group accounts so the GL can post
**Phase:** 0   **Type:** DATA   **Size:** M
**Blocked by:** verified backup of the target site   **Blocks:** pre-2026 GL posting (WI-028 draft triage, WI-032 opening TB)

## Why
**1,726 draft Journal Entries cannot be submitted.** 1,813 of their lines — $724,230.37
gross across 22 accounts — post to **group (parent) accounts**, and ERPNext refuses to
submit a Journal Entry whose line names a group account. Nothing pre-2026 reaches the GL
until this is cleared.

QuickBooks permits posting to an account that also has sub-accounts. ERPNext does not.
So QBO **genuinely booked this money at the parent level** and there is no finer-grained
truth in the source to recover — the import did not lose a classification, the
classification was never made.

The business decision is settled: a **`- General` ledger child** under each affected
parent. The parent's rollup total stays identical to the penny, and the child represents
"posted at this level in QuickBooks" honestly rather than inventing a split nobody chose.
Per-child reclassification was considered and rejected as not determinable from the
import; the determinable subset is deferred to [WI-069](WI-069-general-ledger-reclassification.md),
which is explicitly **not** a prerequisite for posting.

Which QBO mappers produced them (verified 2026-08-04, `prod_je_group_lines`):
Purchase 1,470 JEs / 1,532 lines · Bill 157/173 · JournalEntry 52/57 · VendorCredit 31/33
· Deposit 16/18.

## Native-first check
There is no native ERPNext mechanism for this. ERPNext's rule that a group account is not
postable is deliberate and correct; the defect is on the QBO side of the boundary, where
the source system has the opposite rule. ERPNext's own Chart of Accounts UI will convert a
group to a ledger only when it has no children — not applicable here, since every one of
these parents has real children carrying real balances. The **native answer to "money was
booked at a level ERPNext cannot post to" is a ledger account at that level**, which is
exactly what this creates. No custom doctype, no patch to ERPNext behaviour.

The forward fix (Part 1 below) is likewise the minimum: a resolution step inside the QBO
mapper, not a change to how ERPNext validates accounts.

## Preconditions
- **A verified backup of the target site**, restore-tested. This rewrites 1,813 rows.
- **Staging rehearsal first.** Run dry-run → apply → dry-run on staging and diff the
  subtree totals before production is considered.
- **The forward fix must deploy with (or before) the data change** — see below. Without
  it the remap silently reverts.
- **The QuickBooks sync must be paused before anything is submitted.** ERPNext cannot
  update a submitted document, so every later QBO edit to a posted document becomes a
  sync failure. This constraint is shared with the Sales Invoice re-import (the v1.244.0
  qty fix and the outstanding sales-tax work) even though the populations are different.

## Scope

### Part 1 — CODE: stop it recurring (this is the load-bearing half)
`mapping._ledger_for_posting` redirects a resolved **group** Account to its `- General`
ledger child, applied at every point where a QBO account reference becomes a **posting**
account:

- `_resolve_account` — the main chokepoint (Purchase, Bill, VendorCredit, Deposit,
  Transfer, BillPayment, CreditCardPayment, CreditMemo, RefundReceipt, Payment).
- `_journal_accounts` — **easy to miss**: it calls `_linked_name` directly rather than
  `_resolve_account`, and it is the native JournalEntry mapper behind 52 of the 1,726.
- `_item_expense_account` / `_item_income_account` — an Item Default can name a group
  account too.

Paths that merely test whether a QBO account is *mapped* (`_unresolved_account_refs`) or
that pick a **parent** for a new account (`_qbo_parent_account`) deliberately do **not**
redirect — a group is the right answer there.

**The mappers stay read-only.** If the `- General` child is missing, `_ledger_for_posting`
returns None and the existing balance guard parks the transaction for review. It does not
auto-create the account. `_ensure_group_parent` sets the opposite precedent — it *does*
write during mapping — and that precedent was considered and not followed: promoting an
existing parent to a group is a reversible property change on a record the sync already
owns, whereas creating a ledger invents chart-of-accounts structure mid-payload-transform,
in a code path with no review step and no operator watching. Parking is recoverable;
a silently invented ledger with money in it is not.

Why this half matters more than the data half: these Journal Entries are **drafts** and
stay re-syncable indefinitely, so the next CDC poll that touches one rewrites the line
straight back to the group parent.

### Part 2 — DATA: the accounts and the remap
`quickbooks_online/core/group_account_remap.py`, dry-run by default, idempotent, batched
with commits, scoped to `docstatus = 0 AND posting_date < 2026-01-01`.

**20 new ledger children**, each inheriting its parent's `root_type` and `account_type`:

| Parent | Lines | Amount | New child |
|---|---|---|---|
| 60300 Research & Development | 658 | $205,994.41 | 60301 R&D - General |
| 53100 Rent Materials | 217 | $57,957.53 | 53109 Rent Materials - General |
| 60400 Marketing Expense | 211 | $64,758.88 | 60401 Marketing - General |
| 61400 Insurance | 161 | $38,560.77 | 61401 Insurance - General |
| 61500 Accounting & Bookkeeping | 143 | $27,786.44 | 61501 Accounting & Bookkeeping - General |
| 60100 Auto and Trailer Expense | 113 | $5,308.83 | 60101 Auto and Trailer - General |
| 60420 Travel | 107 | $9,047.71 | 60421 Travel - General |
| 60810 Payroll Expenses | 55 | $8,922.64 | 60811 Payroll Expenses - General |
| 60210 Lease of Building | 49 | $114,957.09 | 60211 Lease of Building - General |
| 51000 Build COGS | 26 | $13,041.47 | 51001 Build COGS - General |
| 42000 Service Income | 13 | $8,368.58 | 42001 Service Income - General |
| 113000 Machinery and Equipment | 4 | $58,916.77 | 113001 Machinery and Equipment - General |
| 60000 Operating Expenses | 3 | $119.03 | 60001 Operating Expenses - General |
| 46000 Other Income | 3 | $462.35 | 46001 Other Income - General |
| 80000 Uncategorized Expense | 3 | $253.38 | 80001 Uncategorized Expense - General |
| 50000 Design COGS | 3 | $3,232.58 | 50001 Design COGS - General |
| 61000 General & Administrative | 1 | $20.41 | 61001 G&A - General |
| 60200 Physical Facilities | 1 | $1.43 | 60201 Physical Facilities - General |
| 53000 Rent COGS | 1 | $92.13 | 53001 Rent COGS - General |
| 60800 Payroll Processing | 1 | $2,000.00 | 60801 Payroll Processing - General |

**Two exceptions — no `- General` child.** An obviously-correct ledger already exists and
a `- General` sibling beside it would be worse:

| Parent | Lines | Amount | Destination |
|---|---|---|---|
| 10000 Accounts Receivable | 8 | $84,961.73 | 1310 Debtors |
| 20000 Accounts Payable | 32 | $19,466.21 | 2110 Creditors |

Merging into the real ledgers is what puts these balances into **AR/AP aging**. The 8
receivable lines include two for **Crystal Fountains totalling $20,082.04 that exactly
match three existing payments from that customer** — split them across two accounts and
those payments become unexplained credits. All 40 lines carry `party_type` + `party`
(verified: **zero** missing), so they satisfy ERPNext's Receivable/Payable party
requirement as they stand; the remap must not drop those fields.

### Verified against production, 2026-08-04 (`prod_je_group_lines`)
- 22 group accounts · 1,813 lines · **$724,230.37** gross · 1,726 distinct draft JEs —
  every per-parent count and total in the tables above reproduced exactly.
- All 20 target account numbers **free**; **no** existing account uses `- General`.
- `1310 - Debtors - SF` (Asset/Receivable) and `2110 - Creditors - SF` (Liability/Payable)
  both exist as **ledgers**, already children of 10000 and 20000 respectively.
- Only 10000 (Receivable) and 20000 (Payable) carry a non-blank `account_type` — and both
  are the exceptions. **All 20 accounts that get a `- General` child have a blank
  `account_type`**, so no Tax/Receivable/Payable type is at risk of being flattened in
  practice. The script still inherits `account_type` explicitly, because relying on
  today's blank values would be a landmine for any re-run after the CoA changes.
- 1,675 JEs carry exactly one group line; the rest carry 2–7 (max 7).
- **315 group-account lines are out of scope** (submitted, or dated 2026+) and must be
  left alone.
- All 1,726 in-scope JEs currently balance, and every header total already equals the sum
  of its line debits/credits.

## Acceptance criteria
Run the script's own verification block; all of these must hold.

- `SELECT COUNT(*) FROM \`tabJournal Entry Account\` jea JOIN \`tabJournal Entry\` je ON je.name=jea.parent JOIN \`tabAccount\` a ON a.name=jea.account WHERE a.is_group=1 AND je.docstatus=0 AND je.posting_date<'2026-01-01'` = **0**.
- `SELECT COUNT(*) FROM \`tabAccount\` WHERE account_name LIKE '%- General' AND company='Sapphire Fountains'` = **20**, each with `is_group=0` and its parent's `root_type`.
- The same out-of-scope count as before the run: **315** group-account lines remain on
  submitted or 2026+ entries, untouched.
- Every touched Journal Entry still balances: `total_debit = total_credit`, and both still
  equal the sum of the line debits/credits. (Only `account` changes, so this cannot fail
  by construction — assert it anyway.)
- Each affected parent's **subtree total** (parent + descendants, same draft/pre-2026
  filter) is **identical** before and after. A line only ever moves within its own
  subtree, so any difference means one escaped.
- All 40 AR/AP lines still carry their original `party_type` and `party` after the move.
- **Re-running the script is a no-op**: 0 accounts created, 0 lines remapped.
- After Part 1 deploys: re-sync one remapped Journal Entry from QBO and confirm its line
  still points at the `- General` child, not back at the parent. *This is the check that
  proves the migration will not undo itself, and it is the one most likely to be skipped.*

## Rollback
- **Data:** restore from the pre-run backup. There is no in-place inverse — the script
  records every touched Journal Entry in its report, so a targeted reversal is possible,
  but a restore is the supported path. Nothing is deleted and no amount changes, so the
  blast radius is confined to the `account` column of 1,813 draft rows.
- **The 20 new accounts** can be deleted while they carry no submitted GL entries. Once
  the backlog is submitted against them they are permanent — which is the real reason the
  staging rehearsal comes first.
- **Code:** revert the `_ledger_for_posting` commit. `_resolve_account` returns to
  returning group accounts directly; nothing else depends on the redirect.

## Explicitly NOT in this work item
- **Reclassifying `- General` balances into real child accounts.** That is
  [WI-069](WI-069-general-ledger-reclassification.md), it runs against posted data, and it
  does **not** block posting.
- **Submitting the drafts.** This unblocks submission; deciding what to submit and when is
  WI-028 / WI-032.
- **The chart-of-accounts rebuild.** [WI-029](WI-029-coa-rebuild-execution.md) owns
  the CoA structure and **these 20 new accounts fall inside its scope** — it may fold,
  rename or renumber them. Coordinate: if WI-029 runs first, re-derive the numbers.
- **Sales Invoices.** A different population with a different blocker (the v1.244.0 qty
  fix, and sales tax still outstanding). Only the "pause the sync before submitting"
  constraint is shared.
- **The 315 out-of-scope group lines** on submitted or 2026+ entries. Submitted documents
  cannot be updated, and 2026+ entries are outside the migration window.
- **Auto-creating the `- General` child during mapping.** Deliberately rejected — see
  Part 1.
