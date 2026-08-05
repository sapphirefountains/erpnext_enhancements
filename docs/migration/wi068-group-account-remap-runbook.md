# WI-068 apply runbook — group-account remap

Moves 1,813 draft pre-2026 Journal Entry lines ($724,230.37 across 22 group accounts, in
1,726 entries) off group accounts and onto postable ledgers, so the pre-2026 GL can be
submitted at all.

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
