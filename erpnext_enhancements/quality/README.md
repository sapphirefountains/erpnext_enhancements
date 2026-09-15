# Quality

Project inspections, non-conformance and corrective action — the module that makes a quality
failure traceable to a contracted, measurable standard.

Programme: [WI-075](../../work-items/WI-075-quality-and-inspections.md).
Decision record: [ADR-0012](../../decisions/adr/0012-project-inspections-do-not-use-quality-inspection.md).

**Status: sub-phases A through I — the loop closes and somebody is told it has.** Scope locks,
an inspection generates frozen from the template plus that project's contracted criteria, a
failed check raises a Non-Conformance and a corrective action, an unverified fix is carried into
the next inspection and re-checked before it may close, a Critical failure pages three named
people and records who saw it, there is a field tool a person can hold, and a daily sweep tells
each project manager which milestones have come round. That re-check step is the one the whole
design rests on: a self-reported fix and a re-inspected fix are different levels of confidence.

Generation is still a **deliberate act**. The sweep reports that a milestone is due; it never
creates an inspection. And `quality_enabled` is **off** — nothing in this module acts until it
is ticked.

Master checklists now exist for every milestone, but **as strawmen seeded `Draft`** — drafts to
be corrected, which generate nothing until a person reads one and sets it Active. Until then
every milestone but Build commissioning still reports *due and blocked*. See the strawman section
below for what each set was drawn from.

Still to come: the front-end chain — Change Orders, MSA rates, budget categories and the
subcontractor scorecard.

## What this module is for

Scope, contract terms, pricing and inspection standards have historically lived in four separate
places, and when they disagree there is no single record either side can point to. The design
principle, from the programme's source documents: **if a criterion cannot be inspected it should
not be in the contract, and if an inspection item does not trace back to something contracted,
question why it exists.**

## File map

| Path | What it is |
|---|---|
| `module_def/quality.json` | The `Module Def`. Needed as a file so the module has an identity of its own rather than appearing only as a side effect of a DocType import |
| `workspace/quality_control/` | The `Quality Control` workspace — **not** `Quality`; see below |
| `doctype/quality_settings/` | The Single holding every master switch. Dormant by default |
| `catalog.py` | The milestone catalog and the Commissioning checks, as data. Frappe-free so CI can read it and sub-phase D can reuse it |
| `lifecycle.py` | The NCR and Quality Action state machines, defined once so the Property Setter fixture and the code that writes a status cannot drift |
| `overrides/quality_action.py` | Replaces core's one-line `validate`. **Inseparable from the status Property Setter** |
| `routing.py` | A failed check becomes an NCR and a Quality Action; and on submit, carried fixes get their verdict |
| `carry_forward.py` | Claiming an unverified fix into the next inspection, and what a re-verification does to it. **The punch list is this, plus a flag** |
| `merge.py` | **The centre of the module.** Merging a master template with a project's contracted criteria, the content hash that freezes the result, and what counts as a failure. Frappe-free, so the freeze is asserted on every push |
| `doctype/project_quality_inspection/` + `inspection_result/` | The generated inspection. Its rows are copies, never links |
| `stable_keys.py` | Row identity — the `*_key` that criteria, checks and (in D) results all join on. Minted once, never regenerated |
| `doctype/inspection_milestone/` | Where in a project's life an inspection is due |
| `doctype/inspection_section/` + `inspection_section_item/` | A reusable block of checks. Top-level rather than a table on the template, because **Frappe has no grandchild tables** |
| `doctype/project_inspection_template/` + `inspection_template_section/` | The Master: the company's standard of care for one milestone |
| `scope_criteria.py` | Acceptance-criterion identity — the `criterion_key` every downstream record joins on. Imports no `frappe`, so it is testable without a bench; consumed by `Project Scope of Work` over in `project_enhancements` |
| `alerting.py` | Who a Critical NCR reaches and when it reaches them again. Frappe-free, so the one decision that must not be wrong — whether somebody was actually told — is asserted on every push |
| `critical_alerts.py` | The database and mail half of that: the dispatch worker, the hourly re-drive sweep, and the acknowledgement write |
| `doctype/ncr_acknowledgment/` | One row per person the alert reached. Every field read-only — an acknowledgement anybody could type into a grid is not evidence |
| `page/inspection_wizard/` | The field tool: one frozen section at a time, every answer a tap. Talks only to `api/quality_wizard.py`, which is where the freeze is enforced |
| `qualification.py` | Whether the person holding the clipboard is the one the template asked for. Frappe-free — and it has to be, because nothing downstream ever fails when this is wrong |
| `inspector_advisory.py` | The frappe half of that: inline warning, timeline comment, manager email. Gate, then swallow — **never** throws |
| `due.py` | Whether a milestone has come round, and why not. Frappe-free. The rule is **reached or passed**, never equality |
| `scheduling.py` | The daily sweep that tells each project manager what is ready. It notices; it never generates |
| `draft_catalog.py` | The **strawman** checklists, as data. Drafts to be corrected — seeded `Draft`, and a Draft template generates nothing |
| `goals.py` | What a period review is allowed to conclude. Frappe-free. Reads a `Data` target, knows which way each metric runs, and returns **Open** whenever it cannot decide |
| `reviews.py` | The half that queries: the seven metrics, the floor rule, the Annual cadence ERPNext cannot run, and the meeting agenda |
| `change_orders.py` | Change-order numbering, the derived status, and the sign of the money. Frappe-free; its DocType lives in `project_enhancements`, which cannot host a frappe-free module |

Registered in [`../modules.txt`](../modules.txt), tiled from
[`../setup/desktop_icon_map.py`](../setup/desktop_icon_map.py), and given a sidebar by
[`../workspace_sidebar/quality_control.json`](../workspace_sidebar/quality_control.json).

## Two names that must not change, and why

### The workspace is `Quality Control`, never `Quality`

ERPNext's own `Quality Management` module ships a public workspace whose **name and label are
both `Quality`**, along with the `Desktop Icon` that goes with it.

A Workspace's `name` *is* its `label`, so a file at `workspace/quality/quality.json` would sit at
ERPNext's exact docname. Workspaces are **timestamp-gated** by `import_file_by_path` — unlike
DocTypes, which are hash-gated — so whichever file carries the newer `modified` rewrites that row
on every migrate, **including its `module`**. The result is not a merge conflict; it is ERPNext's
Quality workspace silently re-homed into this module, changing which DocPerms gate it. That is
the Plaid Settings shape, and this repo has already written the verdict down: *a `modified` bump
can never fix a name clash; only distinct names do.*

The same trap sits one level along. `setup/desktop_icons.py` keys `Desktop Icon` rows by
**workspace label** and derives their roles from the same-named `Workspace`, so a `TILES` key of
`"Quality"` would stamp this module's artwork onto ERPNext's tile and inherit ERPNext's roles.
The key is `"Quality Control"` for that reason.

### No Desk Page here may be named `quality` or `quality-control`

`frappe.router` resolves the first path segment against workspaces *before* pages, so a Page
sharing a workspace's slug never renders — and it fails per-user, because the workspace list is
permission-filtered, which makes it look like a permissions bug. `tests/test_workspaces.py`
fails the build on it. The inspection wizard is `inspection-wizard` for exactly that reason.

## The module gate — why `Quality Settings` grants read so widely

`Workspace.__init__` raises `PermissionError` when its module is not in the caller's
`allowed_modules`, and `get_workspace_sidebar_items` **swallows that exception**. So a workspace
does not fail loudly for someone who cannot see its module; it simply is not there.

`allowed_modules` is built only from DocPerms on **non-child** DocTypes. In sub-phase A this
module has exactly one non-child DocType, so `Quality Settings` is the only thing standing
between the `Quality Control` workspace and vanishing for everybody but System Manager.

That is why its `permissions` block grants `read` to the `* Team` roles and `Project Manager` as
well as write to System Manager and Quality Manager. **Do not narrow it without moving the grant
to another non-child DocType in this module first.** `Position` does the same job for
`hr_enhancements`, for the same reason.

## The freeze, and why it is a test rather than a comment

An inspection's rows are **copied** from the template at generation and never re-read from it.
A later edit to a master template — or to a reusable section inside one — cannot reach an
inspection that already happened.

There is **no `fetch_from` on a single result field**, and that absence is load-bearing. One
would silently un-freeze the snapshot the moment somebody edited the source, and it would look
entirely ordinary in a diff.

The reason this is enforced by `tests/test_inspection_merge.py` rather than by a note is that
the failure is invisible. An implementation that re-reads the master at render populates the
form correctly and shows the right checks; nothing is wrong until somebody tightens a tolerance
and last year's passed inspections quietly become failures — or, worse, its failures quietly
become passes. Nothing logs it, and nobody notices until an insurer or a subcontractor asks what
was actually checked. So the suite mutates the source after generation and demands the rows and
their hash are untouched.

`snapshot_hash` is the authoritative provenance, and `Project Inspection Template.revision`
deliberately is not: the revision cannot see a change made inside a referenced section, while the
hash is taken over the rows that actually landed.

## Core's Quality DocTypes: seven reused, one rejected

The `Quality Control` workspace links seven DocTypes this module does not own —
Non Conformance, Quality Action, Quality Goal, Quality Review, Quality Meeting, Quality Feedback
and Quality Procedure. They are erpnext's, they are kept, and later sub-phases extend them with
Custom Fields and Property Setters.

Core's own **`Quality Inspection` is not used and must not be**. It lives in the Stock module and
is an incoming-materials record: `reference_type` is required and limited to stock documents,
`item_code` and `sample_size` are required, and its readings are numeric parameters. It has no
project, no milestone, no pass/fail and no photo. `frappe.get_doc("Quality Inspection", …)`
anywhere in this app is a mistake. ADR-0012 has the full reasoning.

Two of the seven carry controller logic that later sub-phases have to work *with* rather than
around, and both are recorded here so nobody rediscovers them the hard way:

- **`Quality Action.validate` is one line** that writes `status = "Completed"` whenever the
  `resolutions` table is empty — which is exactly what a punch-list item is. It also writes the
  literal `"Completed"`, so the moment a Property Setter replaces that field's options, *every*
  save of the doctype raises. The Property Setter and an `override_doctype_class` are therefore
  **one indivisible change**.
- **`Quality Review.goal` must stay required.** Core's validate calls
  `frappe.get_doc("Quality Goal", self.goal)` whenever `reviews` is empty, so relaxing `reqd`
  turns an ordinary save into a lookup of the empty string.

## Extending core's Quality Action: why two changes are one change

`Quality Action`'s status Property Setter and `override_doctype_class["Quality Action"]` ship
together and must never be separated. ERPNext's entire controller for that doctype is one line:

    def validate(self):
        self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"

`any([])` is `False`, so an action with **no resolution rows** saves as `Completed` — and an
action with no resolution rows is exactly what a punch-list item is. Every punch item would be
born closed, which is the opposite of the two-step closure this module exists to provide.

Worse, once the Property Setter replaces `Open / Completed` with the five-state lifecycle, that
same line writes a literal the field no longer offers. `_validate_selects` raises, and **every**
save of the doctype fails — including saves that have nothing to do with this app.

So the decision lives in [`lifecycle.py`](lifecycle.py), which imports no `frappe` and is
asserted on every push, and the override is a thin class that calls it. `derive_action_status`
differs from core in three deliberate ways: a terminal status is never moved by a child-table
edit; an empty resolutions table means *no information*, not *done*; and nothing outside the
option list is ever written, including core's own `"Completed"`, which is mapped to `Closed` so
a row arriving with it becomes saveable rather than raising forever.

`Non Conformance` needed no override — core ships it with no controller logic at all.

## The two-step closure, and why the claim is a stamp

An action reaching `PM Resolved` is **not** closed. The companion paper gives the reason and the
constraint together: *PM sign-off should not be blocked from moving a project forward — if a fix
is made, the team should keep working. But a self-reported fix and a re-inspected fix are
different levels of confidence, and only the second one should be allowed to permanently close
the record.*

So the action is claimed by the next inspection generated for its project, appears there as a
row, and closes only on a Pass. A Fail reopens it to `In Progress` (not `Open` — somebody has
already worked on it), escalates one step, counts the reopen, and releases the claim so it is
carried again.

**The claim is written in the same transaction as the generation that carried it.** Without
that, two inspections generated the same morning both carry the same item, both answer it, and
the second one submitted silently overwrites the first one's verdict. It is also why claiming is
synchronous rather than enqueued: a prod deploy `FLUSHDB`s the queue redis and destroys every
pending job.

The escalation happens **once** per verification. A Fail releases the claim, and the verifier
only acts on an action this inspection still holds the claim for, so cancelling and re-submitting
an inspection is a no-op rather than a second ratchet.

An open punch-list item is structurally the same thing — raised against a standard, fixed by
somebody, not actually done until it has been looked at again — so it is a Quality Action with
`custom_punch_list` ticked and gets all of this for free.

## What a period review is allowed to conclude

ERPNext generates a `Quality Review` on a cadence and copies the goal's objectives into it, and
then stops: the review arrives with targets and **blank actuals**, and somebody types a verdict.
A metric nobody computes is a metric nobody trusts, so sub-phase J computes it — and most of the
work is in refusing to compute it wrongly, because all three ways of getting this wrong report
good news.

**An empty period divides by zero and rounds up to perfect.** First-pass yield over a quarter
with no inspections is *undefined*. The obvious implementation returns 100%, and that number is
then the one on the wall. So every metric returns a **sample** alongside its value, and a sample
of zero makes the verdict `Open` rather than a score.

For a count metric the sample is the **activity level**, not the count. Zero non-conformances
across fifty inspections is genuinely good news; zero across zero inspections is no news at all,
and the two must not produce the same green tick. `open_punch_items` is the exception and carries
no sample deliberately — it is a point-in-time count, and zero open items is meaningful whether or
not the period was busy.

**Half the metrics are better when smaller, and core's objective row carries no direction.**
Compare actual against target the obvious way and "NCRs raised: target 2" reads as *failed* every
time the company does well. So direction is not a per-row field anybody can mis-set: it comes from
the metric definition in `goals.METRICS`, where it can be got right once. An unknown metric has no
direction and is **not** defaulted — a default is the silent inversion.

**`target` is a `Data` field.** Somebody will type `95%`, `<= 2`, `2 per project` or `two`. A
target that cannot be read is not a target of zero, and treating it as zero would mark a
lower-is-better goal Passed forever. `parse_target` returns `None`, the verdict is `Open`, and
saving the goal says so out loud — otherwise its reviews would simply keep arriving Open with
nobody told why.

### The floor rule

*A project goal may only meet or exceed the company-wide target for the same measure.* Matched on
**metric**, never on the objective text — two people writing "first pass yield" and "First-Pass
Yield" is not a disagreement about the standard. A metric the company has said nothing about is
not a violation; inventing a floor from silence would block goals nobody objected to.
`Quality Settings.company_floor_enforcement` is Off / Warn / Block and ships on **Warn**.

No class override was needed here, unlike `Quality Action`: core's `QualityGoal.validate` is
literally `pass`.

### Why this app owns the Annual cadence, and only that one

ERPNext's daily `quality_review.review()` branches on Daily, Weekly, Monthly and Quarterly and has
**no Annual branch** — so adding `Annual` by Property Setter produces a goal that generates
nothing, forever, with no error. `reviews.generate_annual_reviews` handles that one cadence.

It handles **only** that one, and `goals.review_due` **raises** if asked about any of the other
four rather than returning `False`. A `False` would be a correct-looking answer to a question this
module must not be asked, and answering it is how a second review would come to sit beside every
one core made, on the same goal, the same day. It also dedupes per goal per day, which core's own
`create_review` does not.

Two smaller things fixed while the tables were still empty: `Quality Meeting`'s autoname was
`format:QA-MEET-{YY}-{MM}-{DD}` — **one meeting per calendar day, site-wide** — and now carries a
counter; and `Quality Action` gained `custom_closed_on`, stamped on the transition that actually
closes it, because days-to-close could otherwise only be guessed from `modified`, which any later
edit moves.

## The strawman checklists, and the one field that makes them safe

Sub-phases C and I both declined to seed checklists for anything but Build commissioning, and the
reasoning has not changed: **a checklist carries the authority of the company that issued it**, an
inspector works through it assuming somebody chose those items on purpose, and an invented one is
indistinguishable from a real one right up until it fails to catch something.

What changed is that a blank page turned out to be a worse starting point than a draft to argue
with. The compromise is the `status` field that already existed:

**Every strawman template is seeded `Draft`, and a Draft template generates nothing.**
`generate_inspection` refuses a non-Active template; `scheduling._active_template_keys` counts
only Active ones. So a milestone with only a strawman behind it keeps reporting *due and blocked*
exactly as it did before. **Setting a template Active is the act of adopting it** — a deliberate
act, by a named person who has read it. Nobody can be handed one of these by accident, and
`tests/test_draft_templates.py` fails the build if the seed ever creates one Active.

### Three of the four sets are not invented

Each item records where it came from in `reference_standard`:

| Set | Source |
|---|---|
| **Service** | Sapphire's own `Sapphire Maintenance Section` records, live on production since June. The chemistry ranges are theirs verbatim — pH 7.2–7.8, free chlorine 1.0–3.0 ppm, ORP 650–750 mV, alkalinity 80–120 ppm — and "GFCI protection verified" is mandatory here because it is mandatory there |
| **Design** | The `Water Feature Design` model in `water_engineering`: status ladder, `blocker_count`, `issue_acks`, computed turnover against the code maximum, TDH against the selected pump. Every gate asks whether that record already says what it needs to say |
| **Products** | The `Control Panel Design` model: NEMA rating, controller hardware, fuse and interlock schedules, control voltages, `safe_state_on_power_up` |
| **Events** | **Nothing.** Read this set hardest |

The four chemistry ranges are pinned by a test, so a later tidy-up cannot quietly turn a sourced
draft into an invented one. And a test asserts that **no Events item carries a
`reference_standard`** — a citation on an invented item would be a fabricated source, which is
worse than no source at all.

`Build — Pre-Final (Systems Startup)` is deliberately not re-seeded. It is Active and it is not a
strawman: its six commissioning checks came from `docs/KPI_DASHBOARD_DESIGN.md`, written by
somebody who knew the trade.

## When a milestone comes round, and the equality bug that would have eaten inspections

Sub-phase C seeded seventeen milestones each carrying a `trigger_basis`, and until sub-phase I
**nothing read it**. A Build project could reach QA and sit there, and the pre-final commissioning
check — the one `docs/KPI_DASHBOARD_DESIGN.md` calls the biggest fountain-specific gap — would come
round only if somebody happened to remember.

**The rule is "reached or passed", not equality, and that is not a refinement.**
`Project.custom_build_status` is a Select somebody types into, not a workflow. A project can go
from `Procurement` straight to `Ready for Install` in one save, and a trigger written as
`current == "QA"` was never true at any moment a sweep looked. The commissioning check simply
never comes up, and the record afterwards is indistinguishable from a project that has not got
there yet.

So a milestone is due once the project is at or beyond its trigger, and stays due until an
inspection exists. **A project that skips a stage does not skip its inspection; it acquires an
overdue one.** That also makes the answer computable at any time from current state, rather than
depending on having observed a transition — which matters here, because a deploy `FLUSHDB`s the
queue and any design that watched for transitions would lose the ones that happened during it.

**A status that cannot be placed on the scale is reported, not swallowed.** Blank, renamed,
legacy — answering "not due" would make the sweep report clean forever, on every project, with
nothing to investigate. It comes back `unknown` and is surfaced alongside the due list.

Three more things it says out loud that a tidier implementation would hide:

- **A due milestone with no checklist is still reported**, flagged `blocked`. Only the Build
  commissioning list has ever been written down; the rest are Sapphire's standard of care and live
  in people's heads. A list that quietly omitted them would turn a gap in what the company has
  recorded into a gap nobody can see.
- **A calendar check that has never run is marked `first_time`.** It is genuinely due — nobody has
  ever inspected it — but "we have never done this" is a different conversation from "this one is
  overdue", and folding them together would page somebody about every Service project at once.
- **A multi-day-only check on a project with no dates says so.** "Why is my mid-event check not
  showing" deserves an answer, and "nobody recorded how long this event runs" is a different
  problem from "the check does not apply".

**It notices; it never acts.** The sweep reports that an inspection is due and does not generate
one, for the same reason severity is never guessed: a generated inspection reads as though a
person decided to inspect, and one that appeared on its own would be a draft nobody owns, aging in
a list, looking like work in progress.

## The field wizard, and where the freeze is actually enforced

`/app/inspection-wizard?inspection=QIR-…` walks a generated inspection one frozen section at a
time, on a phone, in front of the fountain. Without the argument it lists the drafts assigned to
whoever is signed in. It is modelled directly on `sapphire_maintenance`'s Visit Wizard — bootstrap
once, autosave a field-allowlisted patch with optimistic locking, submit server-side — because a
second half-different convention for the same job is how one of them ends up unmaintained.

**The allowlist in `api/quality_wizard.py` is the freeze's last line of defence, and it is short
on purpose.** An `Inspection Result` row carries two kinds of field: the *answers* — outcome,
measurement, notes, photo — and the *frozen* ones copied from the master template at generation,
which are the standard being inspected against. Only the first kind is writable. A wizard that
could write `min_value` could turn a failing measurement into a passing one from a phone, on
site, with nothing in the diff to see. `tests/test_inspection_wizard.py` asserts the allowlist is
exactly those four fields and that no frozen field has leaked into it, and was negative-tested by
adding `min_value` and confirming the build fails.

There is also **no append path** for result rows. Every row was frozen at generation; one that
could be added could be a check nobody contracted for, or a quiet replacement for one the
inspector could not answer.

Three smaller things the wizard does on purpose:

- **The contracted range is printed beside every measurement.** An inspector who cannot see the
  bound is guessing at what "passes" means, which is the whole gap this programme exists to close.
- **`out_of_range` is displayed, never re-derived.** The controller computes it in `validate`;
  two implementations of the same arithmetic is one more than can stay correct.
- **Required and photo-required rows are chipped before submit.** Both reach the `before_submit`
  gate whether or not anybody saw them, and a checklist that only says what it wanted once you
  try to finish is a checklist that lied.

## The inspector qualification check, and the comparison it must never make

A master template may name a `required_position` and a `required_course`. When the inspector has
neither, they get an inline warning, the inspection gets a timeline comment, and their manager
gets an email — and **the save goes through**. The decision is in the work item: two Senior
Technicians, no Masters, one Project Manager, so a hard gate would routinely stop an inspection
being *recorded* rather than stop unqualified work being done. An inspection that happened and was
never written down is worse than one written down by the wrong person, because the second at
least leaves a trail somebody can question.

`Position` is a tree carrying `job_family` and an integer `tier`, so seniority is already modelled
and is not reinvented here: **within one job family a higher tier satisfies a lower one.** Across
families it means nothing — tier 3 Designer and tier 3 Technician are both threes and nothing
follows from that. So the family gate runs *before* the tier comparison, and note the direction
the missing gate fails in: a bare `tier >= tier` **passes** a Designer as a qualified Technician,
and a check that passes is a check nobody investigates.

Two things it deliberately does not say. A template with no requirements produces no finding,
ever — absence of a requirement is not a failed requirement, and warning on every unconfigured
template is the fastest way to teach people to dismiss the warning. And an inspector with no
Employee record is reported as *unknown*, not as unqualified; those are different claims and only
one of them is true.

## The Critical alert, and why "we sent it" is not the claim being made

`Critical` is the one severity that pages people: the PM, the Production Manager and the
President, each with their own acknowledgement row. The build spec asks for it per recipient,
and the distinction is the whole feature — *the alert was sent* is a fact about a mail queue,
while *the President has seen this* is a fact about the company, and only the second is worth
having.

Three failure modes shape the design, and all three look like success from outside:

**An alert nobody received.** Merging to `main` `FLUSHDB`s the queue redis and destroys every
pending background job, silently. So an enqueue is never evidence. The dispatch worker writes
its acknowledgement rows and stamps the document *before* a single email goes out, and stamps
`notified_on` on a row only after that row's send succeeds — so a worker killed halfway leaves
rows that openly say nobody was reached. `renag_due` treats a row with no `notified_on` as due
**immediately**, which is what makes the hourly sweep a re-drive rather than a nag. The sweep
also re-dispatches a Critical NCR that was never stamped at all, which is the case where the
enqueue died before the worker ever ran. Neither pass depends on the other having worked.

**One person, two emails.** On a site with nineteen enabled users, one person holding two of
the three roles is the expected case, not an edge case. `dedupe_recipients` gives them one row
labelled with the first reason they qualified, because two rows mean two emails and an
acknowledgement that can be half-done.

**A nag that becomes noise.** A recipient is chased at most once per calendar day, matching the
hand-off escalation's by-date dedupe. An hourly re-send trains people to filter exactly the
message that must not be filtered, and a filtered Critical alert is worse than no alert.

Two more choices worth knowing. **Acknowledging happens in the Desk, never from a link in the
email** — a tokenised link can be fetched by a mail scanner or a link preview, and the resulting
timestamp would claim the President read this when a security appliance opened it. And
**somebody who was never notified is refused**, rather than recorded: an acknowledgement from a
person nobody told is a green tick with nothing behind it, on the one record here whose entire
purpose is to be evidence.

When no recipient resolves at all — nobody holds the roles, the project has no owner — the
document is stamped anyway and a comment says so on the record. The alternative is an hourly
retry that can never succeed, burying a real problem under its own noise. A Critical
non-conformance that can reach nobody is a fact about the role assignments, and it belongs where
whoever opens the NCR will see it.

## One thing this module cost us on its first two deploys

`Project Scope of Work` was created by model sync and then **force-deleted in the same
migrate** — twice, on v1.452.1 and again on v1.452.2 — because its controller class was named
`ProjectScopeOfWork` and Frappe was looking for `ProjectScopeofWork`.

Frappe resolves a controller with `doctype.replace(" ", "").replace("-", "")`. It strips
spaces; it does not title-case. "of" was lower case in the DocType name, so it stays lower case
in the class name. `get_controller` raises `ImportError` when the class is missing, and
`remove_orphan_doctypes()` passes anything that raises to `frappe.delete_doc(..., force=True)`.

Neither deploy failed. Nothing reached the Error Log. The table survived both times — MariaDB
DDL auto-commits — so production held a 26-column `tabProject Scope of Work` with no DocType
row, and `Project.custom_scope_of_work` pointing at a Link target that did not exist.

The first fix attempt was wrong: the cross-module import in that controller was blamed, made
lazy, and the DocType was deleted again on the very next deploy. The check that "proved" the
import was fine had asked for the class name *we* chose rather than the one Frappe derives —
which is the whole lesson, and is what `tests/test_doctype_controller_names.py` now asserts for
every DocType in the app.

## Settings

Every dial in `Quality Settings` ships off or permissive, and `quality_enabled` gates the lot.

A `default` on a new field of a Single **never reaches the row that already exists**, so the
settings ship with [`backfill_quality_settings_defaults`](../patches/backfill_quality_settings_defaults.py)
in the same release, and `QualitySettings.validate` repairs a missing dial in place as a second
defence. Both fill only where `tabSingles` has **no row** — never over a stored falsy value,
because an unticked box and a deliberate `0` are not the same fact.

Read `tabSingles` with `get_single_value`, or with `order_by=None`. It has three columns and no
`creation`, so `frappe.db.get_value("Singles", …)` compiles to a query ending `ORDER BY creation`
and raises on every site, every time.

## Roles

[`seed_quality_roles`](../patches/seed_quality_roles.py) creates President, Production Manager,
Account Executive, Quality Inspector and Controller — by patch rather than `fixtures/role.json`,
because fixture files import in alphabetical filename order and `custom_docperm.json` lands
before `role.json`.

**Grant them through a Role Profile, never directly.** `populate_role_profile_roles` rebuilds a
profiled user's roles from the union of their profiles on every User save, so a direct grant is
wiped the next time anybody edits that user.

One live consequence is recorded in that patch's docstring and repeated here because it is easy
to lose: creating `Account Executive` **arms a code path that currently skips**.
`crm_enhancements/handoff.py:224` documents that the role is a Select value rather than a real
Role on this site, and `_role_holder_emails` skips a Role that does not exist. Verified on
production 2026-09-14 that this is **latent, not live** — all three configured
`Hand-Off Attendee Role` rows use explicit group addresses with `role` null — but it becomes live
the first time somebody sets `role` on one of those rows.
