# 0013. Hold pay rates in ERPNext at permlevel 1 and stamp them onto clock-in sessions

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

The migration plan's labour-costing item, [WI-016](../../work-items/WI-016-activity-cost-labor-costing.md),
answered the question "does ERPNext need salary detail?" with a flat no. Its design was that the
payroll firm supplies a **burdened hourly costing rate** per employee, that number lands in native
`Activity Cost`, and actual pay never enters the system. That kept a hard line the plan calls rule
3: payroll is the firm's, ERPNext captures hours and exports them.

Two things changed between that plan and the kiosk's final pass.

First, the burdened rates never arrived. **Verified live on 2026-09-17:** `tabActivity Cost` has 0
rows, every `Activity Type.costing_rate` is 0, `tabTimesheet` has 0 rows, and the Project budget
category `Labor` — which already declares `Timesheets` as its actual source — has reported *Not
Tracked* on every project since the budget lines shipped. The costing path was fully wired and
carried nothing, for a year, because the one input it depended on lived outside the system and
nobody owned entering it.

Second, Nik asked, in so many words, for pay rate and position to reach finance from the kiosk
"for helping finance do payroll and job costing analysis". Position was already there:
`Employee.custom_position` on the WI-072 ladder, set on 14 of 15 active employees. Pay was not,
and there was nowhere for it to go. ERPNext core's `Employee.ctc` / `salary_mode` /
`salary_currency` exist and are already `permlevel 1` on this site, but `ctc` is a single annual
figure with no history and no burden, and a rate that changes on a pay review needs an effective
date or every historical cost figure moves with it.

The tension is real: a pay rate in a database that every employee has an account on is exactly
the class of data rule 3 was written to keep out. Frappe's permlevel mechanism is the answer the
framework offers — a field at permlevel 1 is invisible, on the form, in list views, in reports and
over `/api/resource`, to any role without a permlevel-1 DocPerm — and this app already relies on
it for `Managed Device` hardware identifiers and the Employee bank and health fields.

## Decision

Pay rates live in ERPNext, on the Employee, as an **effective-dated child table**
(`Employee Pay Rate`: effective-from date, hourly or salaried, the rate or the annual salary, a
burden percentage) whose Custom Field sits at **permlevel 1**. Read at that level is granted to
HR Manager, System Manager and Accounts Manager and to nobody else; the grant ships as an
insert-only patch keyed on (doctype, role, permlevel) because two of the three rows already
existed on the site unversioned.

Every `Job Interval` is stamped at clock-in with the employee's **position and tier**, and at
close with the **pay type, rate, burden and burdened labour cost** in force on the day it
started. The cost fields are permlevel 1 too; the position fields are not, because a position is
not secret. Labour cost is recomputed on every save from the timestamps, never stored as a second
source of truth for hours.

The native path is kept, not replaced: `Activity Cost` rows are **derived** from the pay rate
and burden and kept in step automatically, so kiosk Timesheets carry `costing_rate` and
`costing_amount`, native Project costing and the Profitability report work, and the `Labor`
budget category's actuals fill in without any change to `budget_rollup`. WI-016's mechanism
survives; only its input source changed.

The payroll workbook still emits **hours**, now split into regular and weekly-overtime hours by
FLSA workweek, and still leaves the qualified-overtime column to the firm. A second worksheet,
marked internal and never sent, carries rate and straight-time gross for reconciliation. No
premium-pay arithmetic is done anywhere in this app, and that remains rule 3's line.

## Consequences

- **The permlevel-1 grant is load-bearing.** Any new field that carries money about a person goes
  in at permlevel 1, and any endpoint that returns Job Interval rows to a non-manager must strip
  the cost fields or read through the framework's permission layer. `tests/test_workforce_costing.py`
  pins the fieldnames and their permlevel.
- **A rate change is a new row, never an edit.** Editing an existing row's rate silently re-costs
  every interval that has not yet been closed and none of those that have. The Employee validate
  hook refuses two rows on the same effective date; it cannot refuse a careless edit.
- **`Activity Cost` is now written by code.** A rate typed directly into `Activity Cost` will be
  overwritten on the next sync. Billing rates are left alone, so T&M billing can still be
  configured natively.
- **Rule 3 is narrowed, not dropped.** ERPNext holds a rate. It does not hold withholding, direct
  deposit, overtime premium, PTO balances or anything that produces a pay figure, and
  `hr_enhancements`' progression records still carry no pay field at all
  (`tests/test_hr_progression.py` asserts it), so a tier review never becomes a salary negotiation.
- **To revisit:** if the payroll firm ever offers burdened rates as a feed, the pay-rate table
  can be emptied and `Activity Cost` fed from the feed instead; nothing downstream would notice.
