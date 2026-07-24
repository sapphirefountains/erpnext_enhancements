# Phase 0 — Foundation: Dependency-Ordered Execution Plan

**Window:** 2026-08-01 → 2026-11-30 (cutover prep Dec 2026, cutover Jan 2027)
**Authored:** 2026-07-24 · **Scope:** the 20 Phase-0 work items (WI-001…WI-020) of PRJ-00739 (ERPNext Accounting Migration).

This plan is dependency-ordered and topology-checked. It fixes the date inversions in the current task board (several items are dated to start before their blocker finishes) and folds in couplings the header `Blocked by:` lines miss.

## The one deadline that shapes everything

Nearly every Phase-0 item **Blocks WI-022** — the **December parallel run / UAT on TEST (starts 2026-12-01)**. So the Phase-0 bar is: *everything feeding WI-022 must land by **Nov 30**.* Two items legitimately fall outside that bar and must **not** be treated as WI-022 prerequisites:

- **WI-003 (monthly close)** — its final Dec close completes ~**2027-01-15** and feeds **Jan opening balances** (WI-032/033/034), not WI-022 directly.
- **WI-017 (payroll hours export)** — tagged Phase 0 but **blocked by WI-021** (a Phase-1 December item); its pilot-export acceptance only happens during the Dec run. It feeds WI-047 (Phase 2), not WI-022. **Defer it.**

## Done already (removed from scheduling)

WI-001 ✅ · WI-004 ✅ (merged v1.168.2) · WI-009 ✅ · WI-015 ✅ · **WI-065 ✅** (Rent→Events rename — satisfies the naming coupling in WI-004 & WI-007).

## Critical path

```
WI-010 ──▶ WI-011 ──┬─▶ WI-012 ──▶ WI-013        (purchasing controls)
                    └─▶ WI-018 ──▶ WI-019        (workspace → form fixtures)
```

Two co-critical branches of equal length share the **WI-010 → WI-011** root. **WI-011 is the single highest-leverage schedule risk** — it is gated on slow human inputs (HR 14-employee map + CEO SoD sign-off). If WI-011 slips past mid-October, **both** branches invert at once. Front-load WI-010 and request WI-011's inputs in August.

## Wave schedule (non-overlapping; intra-wave order shown where a blocker and its dependent share a wave)

| Wave | Window | Items (▶ = must precede the next within the wave) | Why here |
|---|---|---|---|
| **1** | **Aug 1–31** | **WI-002** (OAuth re-grant + supervised catch-up), **WI-003** (stand up close; run Aug close), **WI-005** (Stripe Clearing/Merchant Fees, ~Aug 28), **WI-007** (Selling Settings='No' + SOP drafts), **WI-010** (Role Profile fixtures + PO Approver — *front-loaded to finish ≤ Sep 30*), **WI-014** (clear Pending Review; visibility-only setters) | All zero-blocker. Front-load WI-010 so WI-011 can start Oct 1. |
| **2** | **Sep 1–30** | **WI-010 complete + deploy to prod** (17 profiles reconciled, PO Approver COUNT=1), **WI-008** (SI→Project decision + "Invoices without Project" filter), **WI-006** (Drive config + TEST verify; toggles stay **OFF**), **WI-016** (taxonomy + Activity Types on TEST) | WI-007 done → WI-008; WI-002 catch-up converged → WI-006 config; WI-010 must land to feed WI-011. |
| **3** | **Oct 1–20** | **WI-011** (per-user matrix + `Employee.user_id`; CEO-signed; schedule **outside** WI-003 close windows) — *complete before its dependents start* | WI-010 → WI-011, the critical-path pivot. |
| **4** | **Oct 20 – Nov 15** | **WI-012 ▶ WI-013** (treat as **one** purchasing-controls deliverable — they are mutually entangled), and in parallel **WI-018 ▶ WI-019 (begin)** | Both branches fan out from WI-011. |
| **5** | **Nov 15–30** | **WI-019 deploy** (the fixture **HUB** — physically carries WI-008's `SI.project` setter, WI-016's costing permlevel setter, WI-020's `default_print_format` setters) → **WI-020** (branded print formats + Letter Head, rides the WI-019 batch), **WI-016** (stage prod rate entry — *confidentiality perms first*), **WI-008 / WI-014** close-out, **WI-013** finalize | Everything feeding WI-022 lands by Nov 30; WI-019 is the integration hub, so it and its riders finish last. |

*Intra-wave rule:* where a blocker and dependent share a wave (WI-012▶WI-013, WI-018▶WI-019, WI-019▶riders), the ▶ order is a **hard sequence inside the window**, not a suggestion — the adversarial check flagged these as the schedule's fragile joints.

## Parallel tracks (who can work in parallel)

- **Track A — QBO book-of-record & payments:** WI-002 → (WI-005, WI-006). *WI-006 toggle-on only after every bulk import.*
- **Track B — Security / SoD / purchasing (contains the critical path):** WI-010 → WI-011 → WI-012 → WI-013.
- **Track C — Finance workspace & form/print fixtures:** WI-007 → WI-008; WI-011 → WI-018 → WI-019; WI-014; WI-020.
- **Track D — Costing & payroll:** WI-016 (external-rate-gated); **WI-017 is Dec-trapped — not a Phase-0/WI-022 gate.**
- **Rhythm (spans all tracks):** WI-003 monthly close Aug→Dec.

## Request these external inputs in August (they gate the Oct–Nov chain)

1. **WI-011:** HR-confirmed 14-employee map (23 users → employees) **+ CEO SoD sign-off** — *the #1 schedule risk; needed before Oct 1.*
2. **WI-016:** payroll-firm **burdened hourly cost** per employee — *the un-WI'd gate that can slip WI-022 even if every WI dependency is met.*
3. **WI-013:** the PO-escalation **dollar threshold** — baked into the fixture, so no placeholder can merge.
4. **WI-020:** logo / letterhead / remit-to copy + the Stripe pay-link render decision.
5. **WI-006:** Google Workspace **Content-Manager** grant for the service account + the JSON key.
6. **WI-008:** finance decision on ad-hoc / non-job revenue (Internal-projects pattern vs blank).
7. **WI-014:** accountant Branch-A/B (mandatory-vs-visible) decision + curate the 13 "Internal" projects.

## Gates, hazards & prod-write cautions

- **Monthly-close windows (WI-003):** every prod fixture/security deploy (WI-010/013/019/020) writes on `bench migrate` — **sequence them outside close-execution windows.** Each close also requires WI-005's Stripe Clearing to reconcile to **zero**.
- **WI-002 — highest-risk prod write:** OAuth re-grant + Import All mass-creates Customers, firing folder-provisioning hooks and per-doc external Triton POSTs. It **cannot be TEST-rehearsed**; guard it operationally — `create_customer_folders = 0` for the whole catch-up (coordinate WI-006), run **off-hours**, human-supervised. Its real deadline is "converged & stable by ~Nov 30" (the WI-022 baseline), **not** its misleading 07-31 header date.
- **WI-013 writes straight to prod on merge** (no site-specific staging) — do not merge before the threshold is decided.
- **WI-011 is an unstaged live-prod security mutation** (`test_first=false`) — disables users and rewrites `role_profile_name`; gate on CEO sign-off and keep it clear of WI-003 close windows.
- **Shared fixture files:** WI-009/010/013/015/019/020 all touch `property_setter.json` / `workflow.json` / the hooks print-format allowlist; `bench export-fixtures` overwrites — **serialize the export/commit order.** WI-015's repair must precede WI-044 (Phase 1) activation.
- **WI-007 forward-coupling:** its per-stream SOPs attach to **WI-051**'s cutover runbook (Phase 1). The config (`so_required='No'`) lands in Phase 0; the SOP-attachment finalizes when WI-051 exists — treat WI-007's Phase-0 bar as "settings applied + SOPs drafted."
- **Config that doesn't travel via git** (hand-recreate on prod at cutover): WI-018 (Workspace) and WI-016 (Activity Cost rates — confidential, entered by hand on prod).

## Phase-0 exit checklist (all true before WI-022, Dec 1)

- [ ] WI-002 QBO reconnected, catch-up converged, hourly CDC steady.
- [ ] WI-005 Stripe Clearing + Merchant Fees live and reconciling to zero.
- [ ] WI-006 Drive verified on TEST (toggle-on timed after all bulk imports).
- [ ] WI-007 Selling Settings = 'No'; per-stream SOPs drafted.
- [ ] WI-008 SI→Project decision recorded + saved filter live.
- [ ] WI-010 Role Profiles fixtured/reconciled; PO Approver on prod (COUNT=1).
- [ ] WI-011 per-user matrix applied; CEO-signed SoD (preparer≠approver resolved or compensating control documented).
- [ ] WI-012 MR-vs-PO split applied · WI-013 Authorization Rule on prod with the decided threshold (approver = CEO only).
- [ ] WI-014 PO/PI project-field setters deployed; Branch-A/B locked.
- [ ] WI-016 taxonomy + Activity Types validated on TEST; prod rate-entry staged (confidentiality perms first). **Highest-risk exit item — external rate gate.**
- [ ] WI-018 Accounting Workspace built + accountant sign-off (WI-017 shortcut deferred).
- [ ] WI-019 hide/mandatory setters on all 6 forms deployed (prod PS count == fixture count; Stripe/maintenance fields excluded).
- [ ] WI-020 branded Quotation/SO/SI formats + Letter Head signed off (hard 2027-01-01 customer-facing gate).
- **Excluded from Phase-0 exit:** WI-017 (Dec), WI-003 final Dec close (~Jan-15-2027).

> Note on partial acceptance: WI-008 and WI-014 acceptance queries filter `posting_date >= 2027-01-01`, so they are only fully provable in the Dec UAT. The Nov-30 bar for them is "config + SOP + fixture deployed."
