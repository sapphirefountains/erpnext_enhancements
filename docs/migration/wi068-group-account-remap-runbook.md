# WI-068 apply runbook — group-account remap

Moves draft Journal Entry lines off group accounts and onto postable ledgers, so the GL can
be submitted at all.

The script runs against a named **window**. Steps 0–7 below are written for `pre-2026` and
are the record of the run already applied to production. For the 2026 window — needed by
TASK-2026-01236 before the QuickBooks backlog can be posted — read those steps first, then
follow **[the 2026 window](#the-2026-window)** at the end, which lists only what differs.

| Window | Range | Lines | Gross | Accounts | Entries | Status |
|---|---|---|---|---|---|---|
| `pre-2026` | `< 2026-01-01` | 1,813 | $724,230.37 | 22 | 1,726 | Applied |
| `2026` | `>= 2026-01-01` | 315 | $154,602.92 | 15 | 193 | **Not yet applied** |

Work item: [`WI-068`](../../work-items/WI-068-group-account-remap.md).
Script: `erpnext_enhancements/quickbooks_online/core/group_account_remap.py`.

**Run every step on staging first, against a verified backup. Do not start on production
until the staging rehearsal has passed step 6.**

---

## 0. Preconditions

- [ ] Backup taken **and restore-tested**. This rewrites 1,813 rows; a backup you have not
      restored is not a backup.
- [ ] The release carrying `mapping._ledger_for_posting` is deployed to the target site.
      **Check this first.** Without it the next CDC sync rewrites the remapped lines back
      onto group parents and the whole run is undone — silently, because the entries are
      drafts and re-syncing them is normal behaviour, not an error.
- [ ] Nobody is mid-import: QuickBooks Online Settings → confirm no run in progress.
- [ ] Bulk-write rule read (WI-050): [WI-051 Appendix A](wi051-cutover-runbook.md). Bypass
      `doc_events` with `frappe.db`-level writes where the logic permits; where an ORM save is
      unavoidable, run off-hours in committed batches and watch the default RQ queue.

Verify the forward fix is actually live:

```bash
bench --site <site> console
```

```python
from erpnext_enhancements.quickbooks_online.core.mapping import _ledger_for_posting
_ledger_for_posting("60300 - Research & Development - SF")   # -> None before WI-068 data step
```

`None` is correct at this point (the child does not exist yet). An `ImportError` means the
release is **not** deployed — stop.

---

## 1. Pause the QuickBooks sync

```bash
bench --site <site> set-value "QuickBooks Online Settings" None sync_enabled 0
```

Inbound **webhooks bypass `sync_enabled`**. For a production run, also remove the Intuit
webhook subscription (see `MIGRATION_NOTES.md` §6 and WI-045), or a webhook can re-import
over the top of the remap while it is in flight.

---

## 2. Dry run — establish the baseline

```bash
bench --site <site> execute erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines
```

Writes nothing. Check the printed report:

- `BEFORE` shows **22 group accounts, 1813 lines, 724,230.37 gross**.
- `population matches expected: True`.
- Accounts created lists **20** `(would create)` entries.
- `ERRORS: 0`.

**Save the printed `parent subtree totals` block.** It is the before-picture for step 5.

If the BEFORE figures differ from the table in WI-068, the data has moved since
2026-08-04 — reconcile before proceeding rather than assuming the script's constants are
still right.

---

## 3. Apply

```bash
bench --site <site> execute erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines --kwargs "{'apply': True}"
```

Commits every 200 rows, so an interruption leaves completed work in place and the run is
resumable by simply re-running it.

Expect: 20 accounts created, 1,813 lines remapped across 22 accounts, 0 errors.

---

## 4. Verify — the run checks itself

The report's `VERIFICATION` block must show:

- `every touched entry balances: True` — 1,726 checked, 0 unbalanced, 0 header/line
  mismatch.
- `in-scope lines still on a group account: 0 (ok=True)`.
- `out-of-scope group lines left untouched: 315` — unchanged from the dry run. These are on
  submitted or 2026+ entries and must not have moved.

---

## 5. Verify — the subtree totals are unchanged

Re-run the **dry run** (step 2). Its `parent subtree totals` block must be **identical, to
the penny**, to the one saved in step 2.

A line only ever moves within its own subtree — parent onto its own child, or A/R onto
Debtors which already sits under A/R — so any difference means a line escaped. Investigate
before going further; do not "fix it up".

---

## 6. Verify — the remap survives a re-sync

**This is the step most likely to be skipped, and the one that proves the migration will
not undo itself.**

Pick a remapped Journal Entry from the report, re-enable the sync briefly, and re-sync that
single QBO record (dashboard → the entity type, its QBO id → **Sync**). Then confirm:

```bash
bench --site <site> console
```

```python
import frappe
frappe.db.sql("""
    SELECT jea.account FROM `tabJournal Entry Account` jea
    WHERE jea.parent = %s
""", "<the JE name>")
```

The line must still name the `- General` child. If it has reverted to the group parent, the
forward fix is not in effect — **stop, restore, and fix step 0** before doing anything else.

---

## 7. Re-enable the sync (or leave it paused)

```bash
bench --site <site> set-value "QuickBooks Online Settings" None sync_enabled 1
```

**Leave it paused if the next step is submitting the backlog.** ERPNext cannot update a
submitted document, so once these entries are posted every later QBO edit to one becomes a
sync failure. Submission and sync-pause are a single decision — see WI-068 preconditions.

---

## Rollback

| Situation | Action |
|---|---|
| Mid-run failure | Re-run with `apply=True`. Idempotent: completed accounts and lines are skipped. |
| Wrong result, nothing submitted yet | Restore the backup. The 20 new accounts can also be deleted while they carry no submitted GL entries. |
| Already submitted against the new accounts | The accounts are permanent. Correct by reclassifying Journal Entry, not by editing posted rows. This is why staging goes first. |
| Forward fix missing, remap reverted | Deploy the release, then re-run `apply=True`. No data is lost — the lines simply moved back. |

---

## The 2026 window

Everything above applies unchanged except the command, the expected numbers, and the two
notes at the end. Pass `window` on every invocation — **omitting it silently runs
`pre-2026`**, which on a site where that window is already applied is a no-op that reports
success and moves nothing.

```bash
# dry run
bench --site <site> execute \
  erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines \
  --kwargs "{'window': '2026'}"

# apply
bench --site <site> execute \
  erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines \
  --kwargs "{'window': '2026', 'apply': True}"
```

Expected figures, measured on production 2026-08-07:

- `population matches expected: True` — 315/315 lines, 154,602.92/154,602.92.
- 15 group accounts, spanning 2026-01-01 to 2026-08-01, in 193 Journal Entries.
- **One account is created: `52001 - Service COGS - General`.** The other 14 destinations
  already exist — 13 `- General` children from the pre-2026 run, plus `2110 - Creditors`.
- `in-scope lines still on a group account: 0 (ok=True)` after applying.
- `out-of-scope group lines left untouched: 1813` on a site where `pre-2026` has **not**
  been applied; **0** where it has. This number is the mirror image of the other window,
  not a constant — check it against which windows have run, not against the pre-2026 value.

Two things that differ in kind, not just in number:

- **`20000 - Accounts Payable` merges into `2110 - Creditors`, not into a `- General`
  child**, and Creditors is a Payable ledger, so ERPNext requires a party on every line.
  All 10 lines carry a Supplier (verified 2026-08-07). The script re-checks this and skips
  the whole account with an error naming the offending rows if any line has lost its party
  — so a skip here is a data problem to fix, never something to force past.
- **Step 6 (the re-sync survival check) behaves differently for those 10 AP lines.**
  `mapping._ledger_for_posting` redirects a group account to its `- General` child, and
  `20000` has none — it returns `None`, which parks the transaction for manual review
  rather than reverting the line. So on the AP account the check is "does it park", not
  "does it revert". The remap is not undone either way. This is inherited from the pre-2026
  run, where `10000 - Accounts Receivable` merges into Debtors on exactly the same terms.
