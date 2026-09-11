# WI-073 — Safety records, and making the competency ladder do something

**Status:** in progress
**Branch:** `claude/hr-safety-and-ladder` (stacked on WI-072 / PR #954)
**Tracked:** PRJ-00616, TASK-2026-01953 with a child per deliverable (A–H)
**Follows:** [WI-072](WI-072-hr-module-and-training-redesign.md), which built the module this sits in

## Why

WI-072 built an HR module, a `Position` ladder with Junior/Senior/Master tiers, tiered
sign-off, a credentials register with expiry, a skills matrix and a dispatch advisory. Asked
what else the module should have, we surveyed the top HR SaaS products and the Frappe HRMS
source — 230 agents across six clusters, then a scoring pass against what already exists and
a completeness critic. **186 of the 227 candidates were dropped**, and the drops are as
informative as the keeps.

Two results shaped this work item.

**The clusters that paid off were the two nobody calls HR software.** Field-service platforms
(ServiceTitan, Jobber, Housecall Pro, Workiz, FieldEdge) and EHS (SafetyCulture, KPA,
Assignar, Procore, Avetta) produced almost everything worth building. The generic HRIS
category — BambooHR, Personio, Namely, Zoho People — produced almost nothing this company
does not already have, and the payroll-led and engagement categories produced nothing at all
that survives contact with a 16-person shop whose pay runs through QuickBooks. That is the
right answer for a business whose technicians drive to sites and work with water, chemicals,
pumps and live electrical, and it is worth writing down because the instinct would have been
to start at BambooHR.

**The ladder WI-072 built has a single point of failure, today.** Prod carries 4 Junior
Technicians and exactly one Senior — Jesse Griffin. Sign-off authority is strictly-higher
tier within the same job family, so Jesse is the only person in the company who can attest
for any of the four, and the only fallback is blanket `HR Manager` authority. WI-072 shipped
the gate and not the route through it: nothing says what a Junior must hold to become a
Senior, and nothing produces evidence for the promotion. Deliverable A is that route.

## Decisions (do not re-litigate)

1. **Nik selected all eight themes.** Scope is not in question; sequencing is.
2. **`hrms` is still not installed and still cannot be.** It collides with the `HR` module
   name and six `Training *` doctype names. Anything taken from it is a re-implementation of
   the *record*, never of the cycle/template/KRA machinery.
3. **No payroll, no pay rate, no pay field, anywhere.** QuickBooks Online plus an outside
   bureau own pay. A tier review must never grow a compensation field; the moment it does it
   stops being evidence for a permission grant and becomes a salary negotiation.
4. **No second time clock.** QuickBooks Time already does it.
5. **Safety records are legally shaped, so the law's shape wins over ours.** The OSHA 300 /
   301 / 300A trio, the six privacy-case categories, Utah's 8-hour UOSH clock — these are not
   design choices and must not be simplified into something tidier.
6. **A restriction is a scheduling fact; a diagnosis is not the company's business.**
   Restricted duty records what somebody may not do and until when. The medical reason goes
   nowhere near the record.
7. **A log a manager can suppress at intake is not a log.** An injury report exists the
   moment the technician submits it, whether or not anybody agrees with it.
8. **Derived lists, never hand-maintained ones**, wherever the data already exists: the
   chemicals in a van come from stock, a rung's requirements come from the assignment rules,
   an offboarding checklist comes from what that person actually holds.

## Deliverables

| | | Task |
|---|---|---|
| **A** | Rung requirements, tier review, supervised-only sign-off | TASK-2026-01954 |
| **B** | Who can I send: time off vs dispatch, restricted duty, coverage, truck licences | TASK-2026-01955 |
| **C** | Injury and incident log (OSHA 300 / 301 / 300A) | TASK-2026-01956 |
| **D** | Confined space register + entry permit, lockout/tagout, lone-worker check-in | TASK-2026-01957 |
| **E** | Lock down the Employee record, HR Case Record, signed policy acknowledgement | TASK-2026-01958 |
| **F** | Offboarding checklist, 30/60/90 check-ins | TASK-2026-01959 |
| **G** | Issued kit register, company obligations register, subcontractor COIs | TASK-2026-01960 |
| **H** | SDS register on the visit safety gate, PPE assessment, field hazard report | TASK-2026-01961 |

Sequenced A → B → C → D → E → F → G → H. A is first because it is foundational (B's coverage
view and D's entry authority both read its requirements) and because it fixes a live bug. C
and D share a record shape and should be designed together even though they ship separately.

## Native-first check (ADR-0002)

Before each deliverable, confirm against `git show origin/version-16:` — **never** the
sibling `develop` checkouts:

- **A** — core Frappe ships nothing appraisal-shaped; ERPNext core does not either (Appraisal,
  Appraisal Cycle, KRA, Goal all live in `hrms`). Reuse core `Workflow` for the two-stage
  routing and core `Version` for the audit trail on the `custom_position` change.
- **B** — `timeoff.who_is_out` already exists. Fleet module already models vehicles.
- **G** — ERPNext core `Asset` has custodian tracking, and `fleet_maintenance` exists. Check
  both before adding a register.
- **H** — the visit wizard (`sapphire_maintenance/page/visit_wizard/visit_wizard.js`) already
  has the red banner and the safety tick. Build onto it; do not make a second safety screen.

## Guardrails

- **The dangerous failure direction here is "passes quietly".** A tier review with no derived
  lines renders empty and everyone signs it; an SDS list that is stale reads as authoritative;
  a coverage report with no requirements configured says everyone is fine. Every one of these
  must **refuse to render** rather than render blank — the same lesson as the dispatch
  advisory that never fired for anybody and the whitespace checks that reported clean on
  broken data.
- **Reachability is the half that gets forgotten.** This app has now shipped three correct
  server sides with no caller: `record_signoff` (v1.334.0), the visual editor's entry point,
  and the entire time-off approval flow. Every deliverable here needs a test asserting its
  endpoints are reached from somewhere.
- **Absence assertions must strip comments and docstrings first.** Six occurrences in WI-072.
- `frappe.db.has_column` takes a **doctype** and **raises** on an unknown table.
- Fixtures sync *after* post-model-sync patches, and *after* `after_install` on a fresh site.
