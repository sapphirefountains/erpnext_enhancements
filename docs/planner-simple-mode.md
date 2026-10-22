# Planner Simple mode: every job, done and coming up, in one quiet list

Nik, 2026-10-22: "Add an easy view that is simple and doesn't overwhelm the user, but enables them to
quickly and easily see all the data in a simple view, both historical data and future data."

This came the day after the planner UX redesign (`docs/planner-ux-redesign.md`) shipped its twelve PRs.
That redesign's main complaint was "too much to learn", and the planners still carry five tabs, filters,
alert chips, + Add and More. Simple mode is the opposite: one switch, one search box, and a card per job.

## Nik's decisions (2026-10-22)

| # | Question | Nik's answer |
|---|---|---|
| S1 | Where it lives | **"Simple mode switch"**: a toggle on the planner that swaps the whole toolbar for a stripped-down view. Not a sixth tab, not a new page, not the Project form |
| S2 | Who it is for | **"Managers / office", "PMs"**: people who look, more than people who schedule |
| S3 | What "all the data" is | **"Tasks + phases", "Maintenance visits", "Who did / will do it"**. Not money or documents |
| S4 | How far back and ahead | **"Whole job, start to finish"** |
| S5 | Which planners | **"Both planners"**: the Project Planner shows jobs, the Maintenance Planner shows visits |
| S6 | One row is | **"One row per job"** |
| S7 | Who starts in Simple mode | **"Remember per device"**, like the last-used tab: a phone and a desktop each keep their own |
| S8 | Screens | **"Desktop and phone"** |
| S9 | Layout | **"B: Done \| Coming up card"**: each job a card split in two, the latest done items on the left (grey), the next items on the right (bold), Today between them |
| S10 | Which jobs | **"All jobs ever, searchable"** |
| S11 | Which items | **"Phases + crew work"**: the planner's own display rule (D6); the ~40 admin and billing template tasks per job stay out |
| S12 | Maintenance Planner row | **"One row per site"**: per contract project, past visits grey with who did them, the next visit bold |
| S13 | Old jobs (no Phase 100–700) | **"Their own top-level groups"**: an old job's groups (Project Launch, Mechanical, Electrical, Service Visit(s)…) stand in for phases |
| S14 | A site's past visits | **"Visits + old service tasks"**: Maintenance Records, plus the completed tasks on the site's contract project |

## What the data looks like (production, read-only, 2026-10-22)

| Measure | Number | Consequence |
|---|---|---|
| Customer jobs (Internal, Overhead and Group Projects left out) | 640: Active 58, Completed 565, Canceled 15, Paid 2 | "All jobs ever" needs search and paging, not one long page |
| Tasks on finished jobs | 5,497, **1** with an assignee, none under a Phase 100–700 group | The D6 rule alone would leave every old card empty, hence S13 |
| Top-level groups on finished jobs | 771: Project Launch 93, Billing 89, Service Visit(s) 31, Construction Plan 30, Electrical 29, Mechanical 29… | What an old card lists |
| Completed tasks | 6,370, dated by `completed_on` from 2009 to yesterday | History is there, by completion date |
| Maintenance Records | 29 (Oct 6 – Oct 30 2026), every one with a project | A site is the record's project; Records alone are three weeks of history, hence S14 |
| Timesheet rows | 13 | Hours worked are not worth showing yet |

## The design

```
 Project Planner                                      🔍 Search jobs…            [ Simple ● ]   ?
 ┌ Sonnenburg Reflection Pool · PM Clegg · Oct 1 2026 – Apr 28 2027                     Active ┐
 │ DONE (14)                          │ COMING UP (25)                                          │
 │ ✓ Oct 20  Excavate basin       KJ  │ ! Oct 21  Survey re-check (late)     NC                 │
 │ ✓ Oct 14  Survey site          NC  │   Oct 24  Set forms                  KJ                 │
 │ ✓ Oct 1   Mobilize                 │   Oct 28  Pour slab                  KJ AH              │
 │   Show all 14                      │   Show all 25 · 3 not scheduled                         │
 └──────────────────────────────────── today Thu Oct 22 ──────────────────────────────────────┘
 ┌ Hess Construction – Colony 256 · PM Nik · Aug 10 2026 – Jun 2 2027                   Active ┐
 …
                                      [ Show more jobs ]
```

**The switch.** *Simple* is a switch at the start of each planner's toolbar (and the first row of the phone's
sheet). Turning it on opens `/project-planner/simple` or `/maintenance-planner/simple`, a screen with its own
history entry, so Back returns to where you were. In Simple mode the toolbar is only the search box, the
switch and "?". Turning it off opens the full planner on the tab this device used last. Simple is remembered
per device like any tab (S7): a device whose last planner screen was Simple opens Simple at a bare address.

**A card** is one job (Project Planner) or one site (Maintenance Planner):

- The header: the job's name, its PM, its first and last day, and its status (Active, Completed, Canceled).
  The name opens the Project.
- **Done**, on the left, grey with a ✓: the three most recent done items, newest first.
- **Coming up**, on the right, bold: late items first (open, their last day already past, marked *late*), then
  the next three by date. *N not scheduled* counts open work with no date.
- An item is a date, what it is, and who (crew and assignees, as initials with the full names on hover).
  Clicking an item opens its Task or Maintenance Record.
- *Show all N* opens that column in place to the whole job, start to finish (S4). It is not a screen.
- On a phone the two halves stack, **Coming up first**.

**What an item is.**

- *Project Planner, a job with phase or crew work:* the planner's display rule (D6): a task under a Phase
  100–700 group or with a crew or assignee. Group rows are not items; the item says which phase it is in.
- *Project Planner, a job with none (S13):* its top-level groups, and any top-level task outside a group. A
  group's date is its work's last completion date when all of it is done, otherwise its span; it says
  "N of M done".
- *Maintenance Planner (S12, S14):* each Maintenance Record is a visit (dated by its visit date, else its
  scheduled date; done once the visit is done; who is the technician), and each completed task on the site's
  project is a past item.
- *Done* means the task is Completed (dated by `completed_on`, else its end date) or the visit is done. On a
  finished or canceled job, every item is history: nothing on it is late.

**Which cards, in what order.** Every customer job (S10) by the planner's own rule for what a customer job
is; on the Maintenance Planner every project with a maintenance contract or a Maintenance Record. Cards with something
coming up come first, soonest first; then the rest by their last activity, newest first. Twenty cards at a
time, then *Show more jobs*. The search box matches the job's name, number, customer and PM across all of
them, from the server.

**What Simple mode does not do.** It writes nothing: no drag, no edit, no publish. Every schedule change is
still the full planner's, one switch away. It has no filters other than the search, no colors other than
done/late, and no hours or money (S3).

## Built, and where it differs

Built in v1.720.0 (`api/planner_simple.py`, `public/js/planner_kit/simple.js`, the PPS and MPS blocks of the two page
scripts). Where it differs from the text above, and why:

- **A site is a project with a contract or a record,** not "a Maintenance Record or of type Service": Service is
  also the type of one-off repair jobs, which are the Project Planner's cards.
- **A site's Coming up includes its contract's projected visits** for the next 120 days, marked *planned*. Records
  are drafted only a week ahead, so without them almost every site would look finished.
- **The order is judged from all of a job's open dated work** in one SQL read, while the card lists only what the
  display rule shows. A job whose only future work is an admin task can sort among the jobs with work coming up.
- **The tour.** Its steps are capped at six and the Executive Team's already has six, so the switch is a sentence
  in the last step ("Help is one click away") rather than a step of its own.
- **Switching off on the Project Planner** goes to the tab the device showed when Simple was switched on. On the
  Maintenance Planner, which keeps its last view per person rather than per device, it goes to that view.
