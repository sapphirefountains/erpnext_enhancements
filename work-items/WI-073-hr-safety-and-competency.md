# WI-073 — Safety records, and making the competency ladder do something

**Status:** complete — A through H shipped, v1.387.0 → v1.395.0
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

| | | Task | |
|---|---|---|---|
| **A** | Rung requirements, tier review, supervised-only sign-off | TASK-2026-01954 | ✅ v1.387.0 |
| **B** | Who can I send: time off vs dispatch, restricted duty, coverage, truck licences | TASK-2026-01955 | ✅ v1.389.0 |
| **C** | Injury and incident log (OSHA 300 / 301 / 300A) | TASK-2026-01956 | ✅ v1.390.0 |
| **D** | Confined space register + entry permit, lockout/tagout, lone-worker check-in | TASK-2026-01957 | ✅ v1.391.0 |
| **E** | Lock down the Employee record, HR Case Record, signed policy acknowledgement | TASK-2026-01958 | ✅ v1.392.0 |
| **F** | Offboarding checklist, 30/60/90 check-ins | TASK-2026-01959 | ✅ v1.393.0 |
| **G** | Company obligations register + subcontractor COIs (issued kit **not built**) | TASK-2026-01960 | ✅ v1.394.0 |
| **H** | SDS on the visit safety gate, PPE assessment, field hazard report | TASK-2026-01961 | ✅ v1.395.0 |

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


## What the work changed about the plan

Three things came out differently from the brief, and each is worth reading before the next
work item repeats them.

**Two findings that prompted deliverables turned out to be wrong, and checking first was the
whole value.** The brief for E said any of the sixteen could read a colleague's Employee
record; 19 `User Permission` rows say otherwise, and the real issue was narrower — no field
carried a permlevel at all, so the protection rested entirely on those rows. The brief for the
reimbursement work (the separate accounting fix, v1.388.0) said to match a Supplier by name;
the seven on prod use three naming shapes and two do not contain their Employee's name, so
matching would have billed somebody's receipt to a real vendor. **Both briefs said "confirm
against prod first", and both times the confirmation is what changed the design.**

**One deliverable was refused on native-first grounds, and that was the right answer.** G asked
for an issued-kit register; core ERPNext `Asset` already carries `custodian` and `location`,
and `Asset Movement` records the handover. The gap was never a missing doctype — it was that
nobody had put a flow meter into `Asset`, and nothing read the custodian where it mattered. A
test now fails the build if an `Issued Kit`-shaped doctype appears.

**The same class of bug appeared three times in one day, twice in my own new code.** A repeated
key in a Python dict literal silently replaces the earlier value: once in `hooks.py` (where it
would have disabled four chat sweeps, caught by `test_hooks_integrity`), and once in an
`or_filters` in `hazards.py` an hour later. Python warns about neither. Where a dict is built
from a list of similar-shaped entries — scheduler crons, query filters — assume the duplicate
is there and check for it.

## Deliberately not built

- **An issued-kit register** — see above. Core `Asset` is the register.
- **A "reason" field on `Work Restriction`** — a restriction is a scheduling fact and a
  diagnosis is not the company's business. A test holds it shut.
- **Any record behind the 30/60/90 check-ins** — the value is the prompt, and a form attached
  to it turns a two-minute conversation into an admin task.
- **An atmosphere override on the confined-space permit** — there is no argument to have with
  a gas reading, and a button that looks like there might be is a button somebody presses.
