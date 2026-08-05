# WI-069: Reclassify `- General` balances into real child accounts where the vendor makes it determinable
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** [WI-068](WI-068-group-account-remap.md) applied and the backlog submitted   **Blocks:** nothing

## Why
[WI-068](WI-068-group-account-remap.md) moved 1,813 draft pre-2026 Journal Entry lines
onto `- General` ledger children so the pre-2026 GL could post at all. Those children are
honest — QuickBooks really did book the money at the parent level — but they are not
*informative*: a reader of 60100 Auto and Trailer sees a `- General` bucket where the
chart offers Gasoline and Vehicle Maintenance.

Where the **vendor on the source transaction** makes the real classification determinable,
those balances can be reclassified into the accounts that already exist. This is a
**reporting improvement against posted data**, not a correction: no total changes, only its
distribution within a parent.

**This does NOT block the GL posting, and must not be treated as if it does.** WI-068 is
complete and correct without it. Reclassifying before the backlog is submitted would mean
doing the work twice.

## Native-first check
Native and sufficient: a reclassification is an ordinary **Journal Entry** moving a balance
between two accounts, which is exactly what an accountant would post by hand. No tooling is
required beyond a query to size each move and, if the volume justifies it, a Data Import.
Explicitly do **not** rewrite posted GL rows — ERPNext's audit trail depends on posted
entries being immutable, and a reclassifying JE is the native, auditable mechanism.

## Preconditions
- WI-068 applied and verified on the target site.
- The pre-2026 backlog **submitted** — reclassifying drafts is wasted work, since the QBO
  sync can still rewrite a draft.
- An accounting sign-off on each mapping rule below. "Vendor X always means account Y" is a
  business judgement, not a query result.

## Scope
Realistic candidates only. Both have exactly two children and a vendor signal strong enough
to split on (children verified in production 2026-08-04):

| Parent | `- General` balance | Existing children to split into |
|---|---|---|
| 61500 Accounting & Bookkeeping | $27,786.44 over 143 lines | 61510 Quickbooks Online · 61520 QuickBooks Payments Fees |
| 60100 Auto and Trailer Expense | $5,308.83 over 113 lines | 60110 Gasoline · 60130 Vehicle Maintenance |

For each: group the source lines by the QBO vendor on the originating transaction, agree a
vendor → child mapping with Finance, and post **one reclassifying Journal Entry per
parent** (dated at or after the last affected posting date) moving the determinable portion
off `- General`. Whatever stays undetermined **stays on `- General`** — that is the point of
the account.

### Assessed and rejected as not determinable
Recorded so the question is not reopened without new information:

- **60300 Research & Development** — $205,994.41 over 658 lines, by far the largest. Its 4
  children are `Design`, `Build`, `Service` and `Rent` R&D: a **value-stream** split, not a
  vendor one. The same supplier legitimately serves all four streams, so the vendor carries
  no signal. The determining fact is which project the work belonged to, and the imported
  lines do not carry it.
- **53100 Rent Materials** — $57,957.53 over 217 lines across 6 children, five of them
  specific fountain products (Fountain Pillar, Jumping Laminar, Art Glass Vase, Water Light,
  Dancing Pond) plus a `Video Water Screen (deleted)` account carrying no account number. A
  materials supplier sells parts that go into several products; the product is determined by
  the job, which the import does not carry.

In both cases guessing would fabricate a value-stream or product-line breakdown that then
gets reported on as though someone had decided it. `- General` is the truthful answer.

## Acceptance criteria
- For each of the two parents: a **single submitted Journal Entry** exists whose lines move
  value off the `- General` child and onto its named siblings, and whose net effect on the
  parent's subtree total is **zero**.
- `SELECT SUM(...)` on each parent's subtree, before and after, is **unchanged** — this
  moves balances within a parent and must never change a parent total.
- The residual `- General` balance for each parent equals the portion Finance agreed was
  **not** determinable, and that figure is written down in the reclassification JE's
  `user_remark` — not left implicit.
- 60300 and 53100 `- General` balances are **untouched**, confirming the rejection above was
  honoured rather than quietly revisited.
- No posted GL row was edited in place: every change is a new Journal Entry.

## Rollback
Cancel and amend the reclassifying Journal Entry. Because it is a single balanced entry per
parent with no downstream dependency, reversal is a one-step native operation and the
`- General` balance returns to its pre-reclassification figure.

## Explicitly NOT in this work item
- **Anything that blocks GL posting.** If this item is on the critical path, the critical
  path is wrong.
- **60300 R&D and 53100 Rent Materials** — assessed and rejected above. Reopen only with a
  new source of the project/product attribution, not with a heuristic.
- **Rewriting posted GL rows.** Reclassification is a new Journal Entry, always.
- **Creating further accounts.** This splits into children that already exist. New accounts
  are [WI-029](WI-029-coa-rebuild-execution.md)'s business.
- **The remaining 18 `- General` accounts.** They are either too small to be worth an entry
  or have no children to split into; they stay as they are.
