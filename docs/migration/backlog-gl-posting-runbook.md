# Backlog GL-Posting Runbook — carry QuickBooks' full history into ERPNext

**Decision:** [OD-6 REVERSED 2026-08-31 → carry full history](../../decisions/OPEN-DECISIONS.md) (branch d).
**Event:** the QBO backlog is fixed and **submitted to the GL** as its **own window, before** the Jan-1-2027 cutover — not deleted.
**Anchor analysis:** `TASK-2026-01236` "Final Review Before Cutover" (2026-08-05). **Mechanism WIs:** [WI-067](../../work-items/WI-067-qbo-mapper-data-fidelity.md) (mapper fidelity), [WI-068](../../work-items/WI-068-group-account-remap.md) (group-account remap), [WI-069](../../work-items/WI-069-general-ledger-reclassification.md) (reclass).
**Status:** DRAFT authored 2026-08-31, live figures re-verified same day. **Nothing is submitted until §1 (backup capability) is green.**

This is the executable counterpart to the analysis in `TASK-2026-01236`: an ordered, gated, machine-checked runbook for posting ~16,100 draft QBO documents into the general ledger. It does **not** repeat the full analysis — read the task for the *why*; this is the *how, in order*.

---

## 0. What this event is — and is not

- **IS:** fix the imported drafts (mapper fidelity + group-account remap) and **submit** them, building a complete ERPNext general ledger back to ~2009.
- **IS NOT** the Jan-1 go-live. Per the Final Review's Open Question #10, these are **two events**. This one runs first.
- **Supersedes [WI-028](../../work-items/WI-028-draft-mirror-quarantine.md)** (draft-mirror delete) — **cancelled** by the OD-6 reversal.
- **Subsumes the opening-balance work.** Posting the full history *through 2026-12-31* produces the opening position directly, so [WI-032/WI-033](../../work-items/WI-032-opening-trial-balance-je.md) opening-balance JEs are **not** separately posted; [WI-035](../../work-items/WI-035-opening-reconciliation-signoff.md)'s tie-out becomes "posted-history balances == QBO close." The [Jan-1 cutover runbook (WI-051)](wi051-cutover-runbook.md) then shrinks to go-live config flips + QBO disconnect **on top of** this already-posted ledger.

### Current population (live-verified 2026-08-31)

| Draft doctype | count | value | note |
|---|---|---|---|
| Sales Invoice | 1,599 | ~$7.55M grand_total | 1,449 pre-2026 / 150 in 2026 |
| Journal Entry | 13,068 | ~$14.39M total_debit | 11,084 pre-2026 / 1,984 in 2026 |
| Payment Entry | 1,441 | ~$5.18M paid | 1,314 pre-2026 / 127 in 2026 |
| **Total** | **~16,100** | | (Aug-5 baseline was SI 1,594 / JE 12,762 / PE 1,428 — drift from ongoing sync) |

---

## 1. HARD GATE — backup & rollback capability (blocker B3)

**No document is submitted until every line here is green.** Cancellation is **not** rollback on this site: `enable_immutable_ledger=0`, so cancel *inserts reversing rows* (GL would go 4 → ~68,000 and never return clean), and `default_amend_naming='Amend Counter'` renames documents on retry, stranding the ~19,000 Sync Mapping rows keyed on `erpnext_name`. **Restore-from-backup is the only recovery.**

| # | Step | Owner | Machine check / evidence |
|---|---|---|---|
| 1.1 | Escrow `backup_encryption_key` **off the VM, in two places**. **Never paste it into a chat or an agent session.** | Impl. lead (human) | Key present in two off-box secret stores; VM is no longer the only copy. |
| 1.2 | `bench --site erp.sapphirefountains.com backup --with-files`. | Impl. lead | Backup set produced (~209 MB db + files). |
| 1.3 | Copy the set **off-box immediately** — the on-box copy is swept ~23 h after creation. | Impl. lead | Off-box copy confirmed before the sweep window. |
| 1.4 | **Test-restore** it on the test VM. | Impl. lead | Restored site shows the §0 baseline counts (SI ~1,599 / JE ~13,068 / PE ~1,441 drafts; GL 4 rows). *An untested encrypted backup is not a backup.* |
| 1.5 | **Rehearse the full submit** on the restored site; record wall-clock. | Impl. lead | Rehearsal completes; wall-clock for ~16,100 submissions recorded (determines whether the 6-hourly backup cron collides mid-run). |

> This runbook (an agent) will **not** handle the encryption key and will not execute the submit. §1 is human/ops work; the rest of this document is the ordered procedure it unlocks.

---

## 2. Code preconditions (verified against `main` 2026-08-31)

| Guard | State | Consequence for this event |
|---|---|---|
| `enqueue_after_commit=True` on the Stripe auto-charge enqueue | ✅ **Shipped** (`stripe_payments/core/saved_methods.py:305`) | A rolled-back submit no longer charges cards. Good. |
| Posting-date guard on `auto_charge_on_invoice_submit` | 🟡 **Built v1.358.3** (`AUTO_CHARGE_MAX_AGE_DAYS=365`; bench-free test) — **pending merge/deploy to prod.** | Once deployed, a historical invoice is structurally never charged. Until then the **only** protection while posting ~1,534 chargeable historical SIs is the global `Stripe Payments Settings.enabled=0` + 0 enrolled customers (§3, B4). |
| Docstatus guard in `upsert_entity` (return `unchanged` on a submitted doc; flag via `frappe.db.set_value` when it advances so `owned_fields` survives) | 🟡 **Built v1.358.3** (`mapping.py` already-linked path; bench-free test) — **pending merge/deploy to prod.** | This is the precondition for turning `sync_enabled` back on after posting. Until it is **deployed**, `sync_enabled` MUST stay 0 post-posting, or an unfiltered Import All newly parks ~15,700 submitted docs and destroys the conflict baseline. |

---

## 3. Data preconditions (live-verified 2026-08-31; refreshed from the Aug-5 Final Review)

| Ref | Precondition | Aug-5 | Now | Fix / rule |
|---|---|---|---|---|
| **B1** | `tabPayment Entry Reference` empty → $5.18M payments unallocated | 0 rows | **0 rows** | Cause is by design (`_payment_references` only allocates at `docstatus=1`). **Fix is the §5 order**, not a data patch: submit invoices → Payment-only resync → then submit payments. `Payment Entry.references` is `allow_on_submit=0`, so after submission only Payment Reconciliation can fix it (a 1,441×1,599 manual job). |
| **B2** | 2026-window JEs post to group accounts → hard submit failure | 315 lines / 193 JEs / 15 accts | **148 lines / 120 JEs / 15 accts** | Run [WI-068](../../work-items/WI-068-group-account-remap.md) **2026 window** before §5-D: `group_account_remap.remap_group_account_lines(window='2026', apply=True)` (the code has the `window` arg; `CUTOFF_DATE` stays untouched so the pre-2026 run reproduces). Confirm the `52000`/`20000` edge accounts (no `- General` child) are handled; re-measure to **0** group-account lines before submitting JEs. |
| **B4** | Stripe auto-charge armed against a Live account | `enabled=1`, Live | **`enabled=0`**, Live; 0 autopay customers; 0 Stripe Payment rows; **1 consent row** | Blast radius is **$0** today. Keep `enabled=0` for the whole window; **do not complete `STR-CONSENT-2026-00001`**; confirm `dunning_enabled` off (no row = off). Record prior values before flipping anything. |
| **B5** | Out-of-range posting dates block submit | 1 (2029) | **1** future-dated JE draft | Correct `ACC-JV-2026-24993` (dated 2029-06-01, $5.00) against QuickBooks. **Do not** create FY 2027–2029 as a workaround. Re-scan for out-of-range dates after the v1.248.0 resync. |
| **B5b** | Disabled-party stoppers | 2 | re-check | `ACC-SINV-2026-01210` + `ACC-PAY-2026-01410` (customer *Action Consulting Engineers llc*, disabled) raise at `validate()`. Re-enable for the window or exclude; re-check after the resync. |
| **B7** | Data that submits cleanly but is wrong | 60 zero-total SI; 18 dup groups | **58 zero-total SI**; dup groups re-check | Triage the 58 zero-total invoices and the duplicate groups against `QuickBooks Raw Payload` **before** submit; **double-imports must be deleted, not cancelled**. Accept or fix per-invoice. |

**Also (not blockers, re-verify at run time):** 0 Webhooks; the finance-doctype Notifications/Server Scripts/Assignment Rules are inert; the wildcard `after_save`→`global_triton_sync` hook is retired (v1.341.1) and was dead anyway — **do not "fix" it**, rewiring to `on_update` would arm ~34,000 outbound jobs on the submit run. **Bulk-write rule (WI-050, verified 2026-09-01):** follow [WI-051 Appendix A](wi051-cutover-runbook.md) — the submit loop is an ORM `submit()` per document (unavoidable), so run it off-hours in committed batches and watch the default RQ queue; any party created during triage uses a `frappe.db`-level insert or runs with `create_customer_folders=0`, because Customer **and Supplier** `after_insert` each enqueue a Drive folder per row.

---

## 4. Pre-posting checklist (ordered; each → owner + machine check)

1. §1 backup gate green (test-restored, rehearsed). — Impl. lead.
2. Ship (recommended) the auto-charge posting-date guard; ship (required-before-resync-on) the `upsert_entity` docstatus guard, with bench-free tests in the correct CI step. — Impl. lead.
3. `Stripe Payments Settings.enabled=0` confirmed; `STR-CONSENT-2026-00001` not completed; `dunning_enabled` off. — Impl. lead. → §3 B4 checks.
4. `sync_enabled=0`; **0 Failed logs below the retry ceiling**; 0 Running/Queued. — Sync. *(The retry trap: a retry-eligible Failed log escalates to a full `import_all()` not gated by `sync_enabled`.)*
5. Run the **v1.248.0 resync now, while everything is still draft** (`preview_resync(['Invoice','SalesReceipt'])` → read preview → `run_resync`); re-count Pending Review (expect ~95). — Sync. → WI-067 primary check: for every pre-2026 SI, `base_grand_total == QBO TotalAmt` (~1,373/1,416 reconcile on the guard; the rest park for accounting).
6. Run WI-068 **2026-window** remap (§3 B2); re-measure group-account lines to **0**. — Impl. lead.
7. Fix B5 (2029 JE), B5b (disabled-party docs); re-check after the resync. — Accounts.
8. Triage B7 (58 zero-total, duplicate groups) against Raw Payload; delete double-imports. — Accounts.
9. Freeze automation config; announce a **no-merge window** (a deploy FLUSHDBs the queue Redis and kills in-flight jobs). — Impl. lead.
10. **Final named backup** immediately before the first submit; write its filename into this runbook. — Impl. lead.

---

## 5. The submit sequence (load-bearing order)

**Sales Invoices → Payment-only resync → Payment Entries → Journal Entries.** Submit via an **error-tolerant console loop that commits per document and records failures** — *not* the Desk bulk action (500-doc cap; the 20–500 path enqueues on the short queue with a 1000 s timeout). **Stop and verify after every stage; a non-empty failure list means a precondition was missed — do not continue.**

| Stage | Action | Owner | Machine check |
|---|---|---|---|
| **S-A** | Submit the ~1,599 **Sales Invoices**. Must precede the resync or `_payment_references` drops every reference. | Accounts | GL grows 4 → ~3,550; submitted SI count == expected; failure list empty. |
| **S-B** | **Payment-only resync** while PEs are still drafts: `preview_resync(entity_types='Payment')` → `run_resync`. Recovers ~$4.9M of `LinkedTxn` allocations into `Payment Entry Reference`. | Sync | `tabPayment Entry Reference` now non-empty; allocations match cached QBO `LinkedTxn`. *(After PE submit, only Payment Reconciliation can fix references — `allow_on_submit=0`.)* |
| **S-C** | Submit the ~1,441 **Payment Entries**. Must precede the Deposit JEs or Undeposited Funds goes transiently negative. | Accounts | Submitted PE count == expected; **A/R after allocation reads near $2.5M, not $7.55M** (if $7.55M, allocation did not land — stop). |
| **S-D** | Submit the ~13,068 **Journal Entries** (only after §3 B2 group-account lines == 0). | Accounts | GL grows to ~34,000; submitted JE count == expected; failure list empty. |

Expected GL growth: **4 → ~3,550 (after SIs) → ~34,000 (after JEs).**

---

## 6. Rollback rule

**If submitted counts do not match expectation after any batch, STOP and restore from the pre-submit backup. Do not attempt to cancel forward.** Capture before starting: the off-VM key, the test-restored `--with-files` backup, the final pre-submit backup filename, the prior value of every flipped switch (`Stripe enabled`, `sync_enabled`), and baseline counts (SI 1,599 / JE 13,068 / PE 1,441 draft; GL 4; Payment Entry Reference 0; Stripe Payment 0). If a Stripe charge fires despite the gates: `refund_payment`, then **hand-write the reversing JE** (GL reversal is not automated) and expect to eat 2.9%+$0.30 permanently; ACH is far harder to unwind. Take a fresh backup immediately after a successful run, before the 23-h sweep.

---

## 7. After posting

- **Keep `sync_enabled=0`** until the §2 `upsert_entity` docstatus guard ships — otherwise re-sync churns submitted docs into manual-review and destroys `owned_fields`.
- **Undeposited Funds reconciliation** (512 PEs debit `13800` ~$965k vs 462 JEs credit it ~$941k; ~$24k residual) — a separate job from A/R.
- **A/P per-bill matching decision:** unfixable after submit (`Journal Entry.accounts` is `allow_on_submit=0`, no `reference_type`). Either ship mapper code emitting `reference_type` **before** this event, or accept supplier-level netting — record the decision.
- **Monday-after:** verify A/R ≈ $2.5M; confirm the customer payment portal is not exposing settled 2009–2020 invoices (double-pay risk); start Undeposited Funds reconciliation; then this posted ledger is the base the **[Jan-1 cutover (WI-051)](wi051-cutover-runbook.md)** goes live on (its S4 delete and S7/S8 opening-balance steps are now N/A; S9 tie-out and S10 disconnect still apply).

---

## Open questions carried from the Final Review (business decisions, still unresolved)

These need answers before or during execution — none are agent-decidable:

1. Are the 58 zero-total invoices genuinely $0 in QuickBooks, or is the mapper still dropping lines? (Compare 5 vs raw payloads after the resync.)
2. Are the 18 duplicate invoice groups real repeat billings or double-imports? (Double-imports → **delete**, not cancel.)
3. Submit the ~95 Pending-Review invoices this weekend at all? Holding them protects allocation quality but strands ~206 payments (~$1.46M) for a second wave.
4. Does the business need per-bill A/P aging? (Unfixable after submit — new mapper code before the event, or accept supplier-level netting.)
5. Is cash double-counted across bank accounts? (PEs debit banks ~$5.18M while draft JEs separately debit ~$4.13M — spot check was coincidence, larger accounts not exhaustively checked.)
6. The `STR-CONSENT-2026-00001` consent row already breaches WI-039's "zero consents" acceptance — confirm it stays incomplete until after cutover.
7. The 276 Failed CDC logs set to `retry_count=99` — is that deliberate load-bearing state keeping the hourly escalation disarmed? Document it.
8. Actual wall-clock to submit ~16,100 docs here (from the §1.5 rehearsal) — determines backup-cron collision risk.
9. Is the JE side scoped correctly? (Only 8 JEs touch Debtors / 250 touch Income → the 13,068 JEs look like the expense/bank/AP side and don't duplicate the invoices — one bookkeeper confirmation.)
