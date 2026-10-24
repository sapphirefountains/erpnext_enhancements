# Planner UX redesign: the Project and Maintenance Planners as construction PM tools

Status: **approved by Nik on 2026-10-21 ("Approve as is")**. This is R1 of the Planner UX rethink (TASK-2026-02828 /
TASK-2026-02829 on PRJ-00580). **Built.** All twelve PRs of the build plan ([section 7](#7-build-plan)) were written
between 2026-10-21 and 2026-10-22, P12 (help, legend, tips and the first-visit tour) last, in v1.716.0. The table in
section 7 says which PR built each row; [Built, and where it differs](#built-and-where-it-differs) lists what the
build does differently from the text below. The text itself is left as it was approved, so it reads as the design
and the section 7 table and that list read as what shipped.
Written 2026-10-21 from a production usage audit, a benchmark of construction and field-service
scheduling tools, the planner code at v1.699.0, and five rounds of questions answered by Nik Bradshaw.
This document changes no code.

Scope, as Nik set it: **rework the UX only.** The data model and back end stay. That covers Planner
Resource and its work patterns, the availability engine (`crew_availability.py`), Planner Draft Change,
Planner Block, Planner Day Note, routing and drive time, calendar sync, digests and the APIs. The few
back-end additions the new views need are listed in [What the back end needs](#what-the-back-end-needs).
They are small, additive, and change no existing behavior.

## 1. Why

Nik, 2026-10-21: "Ideally this tool would mimic behavior of other Construction Project Management
tools out there." He also said there is too much to learn, and the planners don't fit how Sapphire plans.

The planners were built in about ten rounds, v1.576.0 to v1.687.0, from 2026-10-07 to 2026-10-14.
Each round added a feature family: routes, crews, conflicts, draft mode, peeks, menus, Google Calendar
sync and task build-out. Each family was right on its own. Together they make about 70 controls
and a view model of week, month, crew, heatmap, utilization, route and my-week. No construction PM
would recognise that model on day one.

## 2. Findings

### 2.1 Who uses the planners (production, read-only, 2026-10-07 → 10-21)

| Measure | Number | What it says |
|---|---|---|
| Project Planner page views (Route History) | 104: Nikolas 91 over 5 days, Clegg 10, James 2, Korben 1 | Nik is about 88% of the use. Clegg is the only other real user. |
| Maintenance Planner page views | 38: Nikolas 24 (last on 10-12), Clegg 13 (one session on 10-20), Austin 1 | Nik's use stopped on 10-12. |
| Lisa Symanski | 0 page views | She is one of the two people who set maintenance dates, and she doesn't use the planner. |
| Views used (Project Planner) | week 43, route 22, month 13, my-week 9, heatmap 9, crew 7, utilization 1 | The week calendar is the work view. Utilization is untouched. |
| Views used (Maintenance Planner) | week 17, crew 13, month 8 | |
| Weekly trend (project / maintenance) | 32/17 → 48/8 → 24/13 (the last week is partial) | Flat to falling, not growing. |
| Planner Draft Change | **0 rows** | Draft and publish has never been used. |
| Planner Block, Calendar Time Off Review | **0 / 0** | Personal blocks have never been used. |
| Named saved views | **0** (9 people have only the automatic "last used") | Saved views are unused. |
| Planner Day Note, Planner Conflict Ack | 1 / 1 | Effectively unused. Conflicts are seen, and one was kept on purpose. |
| Digests, change alerts, leave-by pings | 0 log rows | Switched off in Settings. |
| Task date changes in `tabVersion` since launch | 19, all Nik's, all on 10-12 within two hours, then reversed | Scripted bulk edits, not planning. **No interactive rescheduling shows in the data.** |

Route History is flushed in batches and doesn't log Form routes, so the counts are relative, not
absolute. The full queries are in the session notes.

### 2.2 What the data looks like

| Measure | Number | Consequence for design |
|---|---|---|
| Open tasks | 1,764 (1,005 of them in status "Overdue") | Status is stale. Its colors can't carry meaning yet. |
| … with a start date | 1,193 (68%) | |
| … with hours (`expected_time > 0`) | **96 (5%)** | An hours-based board has nothing to draw. Work has to be sized in **days**. |
| … with a time slot | 4 | Time-of-day scheduling is a project edge case, though it matters for maintenance. |
| … with dependencies | 359 | Mostly from templates and the 10/1 schedule import. |
| … with a crew row / an assignee | 39 / 158 | Crewing is rare. |
| Customer projects' template tasks | About 40 "Design Project Template" tasks per job (Stage 1–4, Billing, "Add to Accounting", approvals), **all dated on the project's start day** | The calendar fills with admin checklists stacked on one day. |
| Planner Resources | 8 people, one work pattern (Austin, Mon–Wed) | The resource model is barely configured. |
| Maintenance Records | 29. Technician and date are always set; `planned_hours` is set on 0 | |

### 2.3 How Sapphire plans today (Nik, interview round 1)

- **Where a PM's plan takes shape:** "Head / whiteboard / paper", "Spreadsheet or estimate", and
  "Google Calendar / texts". Not the Project Planner.
- **The horizon schedulers think in:** all four. The next 2–3 weeks, the whole project from start to finish,
  today and tomorrow, and the month at a glance.
- **Who sets the maintenance schedule week to week:** Lisa, and Austin himself. Neither uses the
  Maintenance Planner. People drag the hand-made Google Calendar events instead (noted 2026-10-12).

### 2.4 What construction and field-service tools do (benchmark)

The products benchmarked were Procore, Buildertrend, Autodesk Build, Fieldwire, JobTread, Smartsheet,
MS Project, ServiceTitan, Jobber and Housecall Pro. The full report with sources is in the session notes.
Several vendor help pages could not be read directly, and the report marks each claim it could not
verify. The conventions a construction PM expects on day one:

1. **A Gantt with draggable bars.** Drag the middle to move, an edge to resize, and a dot onto a bar to link them.
2. **Linked tasks shift together** (finish-to-start plus lag), with a way to opt out. Procore uses Ctrl-drag for this.
3. **Collapsible phase rows** that roll up their tasks.
4. **One dataset seen several ways**, as tabs: Gantt, Calendar (day/week/month) and List.
5. **An assignee or trade on each item**, with filters and colors.
6. **A grey "ghost" baseline bar** behind the live bar.
7. **Notify on change, plus a draft or "don't notify yet" option.** JobTread keeps a schedule private until
   it is published. Jobber and Housecall Pro ask "Notify the client?" when something is rescheduled.
8. **A short field window on the phone**: an agenda or "today" list, with one-way calendar sync.
9. **An unscheduled list beside the calendar** to drag from.

Construction scheduling and field-service dispatch differ. Construction works in multi-day tasks in
phases with dependencies and a baseline, and lays tasks out as rows. Dispatch works in hour slots, and
lays technicians out as rows, with an unassigned queue and a map. Sapphire needs both: construction for
projects, dispatch-like for maintenance.

**What the app already has.** `public/js/gantt_widget/` embeds **DHTMLX Gantt 10 Standard** (MIT) with
`api/gantt.py`. It already runs the Project form's Schedule tab and the Projects Dashboard portfolio
Gantt, with export and an edit opt-in. Standard has no critical path, no auto-scheduling and no
resource panel; those are PRO features. Auto-shift therefore stays the planner's own
`shift_successors`, and nothing here depends on PRO features.

### 2.5 Walkthrough

See [Appendix A](#appendix-a-walkthrough). The live screenshots were taken read-only. Nothing was
dragged, saved or published.

## 3. Nik's decisions (2026-10-21)

Quoted from his answers. Where an answer picked one of several options, the option's own text follows the quote.

| # | Question | Nik's answer |
|---|---|---|
| D1 | Which construction behaviors first | **All four:** "Gantt: phases + deps", "2–3 week lookahead", "Crew/resource board", "Phone 'My schedule'" |
| D2 | The screen a PM lands on | **"Lookahead (all jobs)"**: rows are projects, then phases, then tasks; the next 3 weeks run across, with crew initials on the bars |
| D3 | James's big picture | **"All jobs on a timeline", "Exceptions only", "A weekly summary"**. Not crew capacity |
| D4 | The phone | **"Today + tomorrow list", "Today's route map", "Block my own time"**. Not the whole week as the main screen |
| D5 | Baseline | **"Yes, set on publish"**, with "a 'reset baseline' button for re-plans" |
| D6 | Which tasks the schedule shows | **"Phases + crew work only"**: tasks under Phase 100–700 and anything with a crew or assignee. Admin and billing checklist tasks stay on the project, off the schedule, behind a "show all" switch |
| D7 | Sizing | **"Days, hours optional"**: a task is N working days for its crew and books their whole day, unless hours or a time slot say otherwise |
| D8 | Publishing | **"Per-project publish"**: a new project's schedule is private until "Publish schedule". Publishing sets the baseline, notifies the crew and syncs Google. After that, edits are live with a "notify crew?" prompt |
| D9 | Maintenance Planner | **"Keep as is, simplify"**: the same month, week and crew views, with the shared new toolbar and the cleanup |
| D10 | Dependencies | **"Auto-shift, opt out"**: successors shift by the same number of working days as the drag; hold Ctrl/⌘ to move only the one task. Overbooking still only warns |
| D11 | Change notices after publish | **"'Notify crew?' prompt"**: every save that changes someone's dates asks Notify / Don't notify. Notify is the default for changes in the next 2 weeks |
| D12 | Off the main toolbar (kept, never deleted) | **"Heatmap + Utilization"** (merged into one Capacity view), **"Color-by + saved views"** (default colors by job type), **"Copy week, Split, Move-by switch"** (the right-click menu and the panel), **"Print + crew sheet"** (into More) |
| D13 | Project work on the Maintenance Planner | **"Busy strip per day"**: each tech-day shows a thin grey "Project: 8h" bar (hover for details); full project cards via More ▸ Display |
| D14 | The old week/month calendar | **"Keep Calendar tab"**: five tabs, Lookahead, Gantt, Crew, Calendar, Overview. "Nothing familiar disappears" |
| D15 | A task moved *earlier* | **"Yes, both ways"**: linked tasks move together in either direction (Ctrl/⌘ still opts out) |
| D16 | How the weekly summary reaches James | **"Print + optional email"**: printable from Overview; a Monday email to chosen managers behind a Settings switch, OFF until Nik turns it on |

**Standing rules that still hold:**
- Overbooking warns and never blocks.
- The customer confirmation email stays OFF.
- The week starts on Sunday on prod.
- Every screen gets a browser history entry.
- No feature silently loses fidelity ("We shouldn't have lost any fidelity").

**Still declined; not re-proposed:**
- "suggest a crew" auto-fill
- the phone ICS feed
- the change log + revert panel
- the "running late" / "can't make it" field buttons
- a fixed two-row toolbar
- two-way Google sync

## 4. The new information architecture

### 4.1 Project Planner: five tabs over one dataset

```
┌ Project Planner ─────────────────────────────────────────────────────────────────────────────┐
│ ‹ Today ›  Oct 19 – Nov 8   [Lookahead] Gantt  Crew  Calendar  Overview   PM▾ Job▾ Crew▾ 🔍  │
│                                                   ⚠ 3 conflicts  ⏱ 2 over   + Add ▾  More ▾ ? │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Tab | Route | Who it's for | Replaces |
|---|---|---|---|
| **Lookahead** (home) | `/project-planner/lookahead/<date>` | PMs and schedulers: the weekly coordination view across all jobs | The week view as the landing screen, plus the Needs tasks and Unscheduled trays |
| **Gantt** | `/project-planner/gantt/<project>` | A PM planning one job from start to finish | Nothing in the planner; reuses the app's DHTMLX widget, adds editing |
| **Crew** | `/project-planner/crew/<date>` | Schedulers: who is where, overbooking | The crew view; Heatmap and Utilization move inside it as **Capacity** |
| **Calendar** | `/project-planner/calendar/<week\|month>/<date>` | People used to today's week and month grid | The week and month views, unchanged in behavior |
| **Overview** | `/project-planner/overview/<date>` | James and the approvers | The portfolio Gantt on the Projects Dashboard, plus exceptions and the weekly summary |

Two screens sit outside the tabs, as they do today:

- **Today**, `/project-planner/today/<date>`, is the phone view for everyone (§4.4). It replaces My week as the landing screen on a phone. My week stays one tap away.
- **Route**, `/project-planner/route/<person>/<date>`, is unchanged.

**Old addresses keep working.** `/week/<date>` and `/month/<date>` redirect with `replace_route` to
`/calendar/week|month/<date>`, and `/heatmap/<date>` and `/utilization/<date>` to `/crew/<date>?capacity=…`,
so bookmarks, the Project form's "Open in Project Planner" link and the digest links all land on
the same content. A bare `/project-planner` opens the person's last-used tab. With none, it opens
**Lookahead** on a desktop, **Today** on a phone for someone who is not a scheduler, and **Overview** for the
Executive Team.

**One toolbar** (D12, and the 2026-10-14 rule against a fixed two-row layout): ‹ Today ›, the range, the
five tabs, the three filters (PM, Job, Crew group), search, the alert chips (shown only above zero), **+ Add ▾**
and **More ▾**, then ?. The phone keeps the bottom sheet from v1.687.0.

| More ▾ section | Items |
|---|---|
| Display | Color by (default **Job type**, D12), Show maintenance / rentals / travel, **Show all tasks** (D6), Highlight a project |
| Views | Save view as…, Manage views…, My week |
| Tools | Copy week…, Block time…, Conflicts…, Tap to move, Hold all my changes (the old global Draft mode) |
| Print | Print this view, Print selection, Crew sheet, **Weekly summary** |

**+ Add ▾** holds Quick add task, Build out tasks… (grid, paste, copy from project, templates, AI draft) and Day note…

### 4.2 Lookahead (home)

```
 Lookahead · next 3 weeks            Sun 19  Mon 20  Tue 21 … Sat 25 │ Sun 26 … │ Sun 2 … Sat 8
 ─────────────────────────────────────────────────────────────────────┼──────────┼──────────────
 ▾ PRJ-00597 Riverwalk Plaza  · PM Clegg      ● Published Oct 21      │          │
   ▾ Phase 300 — Rough-in                                             │          │
       Excavate basin          ░░░████████ KJ JG ░░░                  │          │       ← ghost = baseline
       Set forms                           ─────▶██████ KJ            │          │       ← arrow = depends on
       Pour slab ⚠ conflict                               ████ KJ AH  │          │
   ▸ Phase 400 — Mechanical (6 tasks, 2 unscheduled)                  │  ██████  │  ████
   ⋯ Not scheduled: Pull wire · Set vault                [drag onto a day]
 ▾ PRJ-00754 Tanner Park  · PM Nik            ◐ Draft schedule  [Publish…]
   ▾ Phase 100 — Design         (pencil = hatched)  ▨▨▨▨▨▨ JP          │          │
 ▸ PRJ-00699 Myers-Roy  · No tasks yet                     [Build out]
```

- **Rows are projects, then their phases (group Tasks), then tasks.** Only phase and crew work shows (D6).
  The schedulable-task rule is in the [back end section](#what-the-back-end-needs). *More ▸ Show all tasks*
  brings back the template admin tasks.
- **Bars span working days.** A bar shows crew initials and its size in days (D7). Pencil bars are hatched.
  The baseline is a grey ghost bar behind the live bar (D5), and a slipped task shows the gap as a thin red tail.
  A conflict, a running-over task, a weather flag and a missing qualification each show as a chip on the
  bar, the same chips the cards carry today.
- **Dependencies** show as a short arrow from a predecessor's end into its successor. With hundreds of
  links, the arrows are drawn only for the hovered or selected task's chain.
- **Dragging a bar** moves it, and its successors shift with it by the same number of working days,
  later or earlier (D10, D15; `shift_successors` with a positive or negative count; one Undo). Ctrl/⌘-drag moves only that bar. Dragging an edge resizes it.
  Every write goes through the page's `send()`, so the reason prompt, draft handling, Undo and the
  conflict check apply unchanged.
- **The project row carries the schedule state** (D8): ◐ *Draft schedule* with **Publish…**, or ● *Published
  <date>*, or *No tasks yet* with **Build out** (this replaces the Needs tasks tray). Clicking the
  project name opens the existing project peek. *Open Gantt* is in its menu.
- **Unscheduled work** is a collapsible *Not scheduled* line inside each project, and dragging from it
  onto a day works as dragging from today's Unscheduled tray does. Tasks that need crew carry a
  *Needs crew* chip. A *Needs crew* filter chip in the toolbar narrows the board to them, which replaces
  the Needs crew tray.
- **The window** is 3 weeks by default (2 / 3 / 4 / 6 in the range picker) and starts on the site's first
  weekday (Sunday on prod). ‹ › step one week.
- **Clicking a bar** opens the task in the side panel (the 6A panel, unchanged). Right-click opens the 6B menu,
  plus Split, Duplicate to…, Copy to another week (D12).
- **On a tablet:** Tap to move works as it does today, and rows stay 40 px tall. **On a phone** the
  Lookahead becomes a list grouped by day, because a 21-column grid doesn't fit.

### 4.3 Gantt (one project)

```
 PRJ-00597 Riverwalk Plaza   ● Published Oct 21 · baseline Oct 21   [Reset baseline] [Build out ▾]   Day Week [Month]
 ┌───────────────────────────┬────────────────────────────────────────────────────────────────────┐
 │ Task              Days Crew│ Oct            Nov                  Dec                             │
 │ ▾ Phase 100 Design   10    │ ██████████                                                         │
 │ ▾ Phase 300 Rough-in 18    │        ░░░░░░░░░░░░░░░░                                            │
 │     Excavate basin    3 KJ │        ███──┐                                                      │
 │     Set forms         2 KJ │             └▶██──┐                                                │
 │     Pour slab         1 KJ │                   └▶█                                              │
 │ ▸ Phase 400 Mechanical 12  │                        ████████████                                │
 │ ▸ Phase 500 ∥ 600          │                                     ██████████                     │
 └───────────────────────────┴────────────────────────────────────────────────────────────────────┘
```

- This tab reuses `erpnext_enhancements.gantt.mount` (DHTMLX Standard) with the planner's own task feed,
  so the planner and the Project form's Schedule tab draw the same thing.
- Phases collapse. Bars move and resize, and dragging from a bar's end dot onto another bar creates a
  finish-to-start dependency. Successors auto-shift (D10).
- The baseline is a grey layer under each bar (D5). **Reset baseline** asks for confirmation and copies today's dates.
- Every edit goes through the planner's write path, not `update_gantt_row`, so conflicts, the reason prompt,
  drafts, Undo and the notify prompt behave exactly as on the other tabs.
- The Build out grid (T1–T4: grid, paste, copy from project, templates, AI draft) opens in the side panel
  from **Build out ▾**. The rows it creates appear in the chart straight away (undated, under *Not scheduled*).
- Zoom is Day / Week / Month. The widget's export (print, PNG, SVG, CSV, Excel, date range) is kept.

### 4.4 Today (phone)

```
 ┌──────────────────────────┐
 │ ‹  Tue Oct 21  ›     ☰   │
 │ TODAY · 3 stops · 1h 40m │
 │ ┌──────────────────────┐ │
 │ │ [ map: S─1─2─3─S ]   │ │  ← today's route; tap = Google Maps
 │ └──────────────────────┘ │
 │ 1  7:35 leave · 8:10     │
 │    Riverwalk Plaza       │
 │    Excavate basin · day 2/3
 │    123 Main St  ↗  👥 JG │
 │    🗒 Shop meeting 7 am   │
 │ 2  Sequoia fountain (visit, 2h)
 │ ─────────────────────────│
 │ TOMORROW · Wed Oct 22    │
 │ 1  Riverwalk Plaza …     │
 │ [ Block my time ]  [ This week ] │
 └──────────────────────────┘
```

- Today and tomorrow, each a list of the person's stops in route order. Each stop shows its leave-by and arrival times,
  the site, what the work is (task, or "visit, 2h"), the address (tap for Maps), crewmates, equipment, the day's notes
  and weather (D4).
- The route map is inline and comes from the existing `get_route`. *Full route* opens the route view.
- **Block my time** opens today's `self_only` block form. **This week** opens My week as it is today (no fidelity lost).
- Projects and maintenance work both appear, because the engine is shared, so a tech has one place to look.
  It is read-only apart from blocking time. The "running late" style buttons stay out (declined).
- It is the default landing on a phone for anyone who is not a scheduler. Schedulers on a phone land on
  the Lookahead list and reach Today from *More*.

### 4.5 Crew (people board) and Capacity

- People run down the side and days across, the current crew view simplified. A task is a bar spanning its days in each
  crew member's row, labeled in days. A day cell shows *Full*, *3h free* or *Off* (blocks read "Unavailable",
  with the note only for those allowed to see it).
- The side tray holds **Not scheduled** and **Needs crew**, which are today's trays, to drag from. Dragging a bar onto
  another row hands the task over (`swap_crew`).
- The team capacity strip, day notes, personal blocks, peeks, *Assign to…* and *Pick someone who's free* stay as they
  are (Nik's rule: nobody is picked for anyone).
- **Capacity** (D12) is a switch in the Crew tab's header: *Board | Capacity*. Capacity shows the 8-week heatmap on top
  and the Utilization table under it (the Crew Utilization report's rows, report-gated as today), so the two
  barely used views become one.

### 4.6 Calendar

This is today's week and month grid with its behavior intact: cards per day, drag and drop, resize, quick add by
double-click, multi-select, and tap to move. It is kept because it is the most-used view today (week: 43 visits) and because
"no fidelity lost" applies to habits too. It gets the new toolbar, the D6 filter, day-based labels (D7) and
the job-type default color.

### 4.7 Overview (managers)

```
 Overview · Oct – Mar                                         [Print weekly summary]
 ┌ Needs attention (5) ────────────────────────────────────────────────────────────┐
 │ ▲ Riverwalk Plaza: Phase 300 is 4 working days behind its baseline               │
 │ ⏱ Tanner Park: "Set vault" is running over (14h of 8h)                           │
 │ ⚠ 3 conflicts next week (Austin double-booked Thu)    ⌛ 6 overdue tasks          │
 └──────────────────────────────────────────────────────────────────────────────────┘
  Job                     PM     Oct   Nov   Dec   Jan   Feb   Mar
  Riverwalk Plaza         Clegg  ▓▓▓▓██████▒▒▒▒░░          ◆ hand-off
  Tanner Park ◐ draft     Nik         ▨▨▨▨▨▨▨▨▨▨
  Sanctuary               Clegg  ████████████████████████████
```

- **All jobs on one timeline** (D3). There is one row per active customer job with a bar per phase, colored by phase
  and drawn over its baseline ghost. Milestones are diamonds. A draft schedule is hatched. Clicking a row opens that
  job's Gantt. This uses the same DHTMLX widget as the Projects Dashboard portfolio Gantt.
- **Exceptions only** (D3) is a list of what needs a manager:
  - phases or tasks behind their baseline by more than N working days
  - running over (Phase 4's `over_plan`)
  - overdue work
  - unresolved conflicts in the next 2 weeks

  Each line opens its source. When nothing is wrong, the list says so in one line.
- **Weekly summary** (D3) is a one-page printable (the print design system's chrome) showing next week by job and by
  person, what changed since last week's publish, and the exception list. *Print weekly summary* is on screen. A
  Monday email is offered behind a Settings switch, **OFF by default**, like every other planner message.

### 4.8 Publish, baseline and notices (D5, D8, D11)

- **A project's schedule state** is *Draft* or *Published*.
  - A project with no published schedule is *Draft*: every write to its tasks from any planner tab is
    drafted automatically (`draft=1`), with no global toggle. Nothing notifies, assigns or reaches Google.
  - **Publish…** on the project row (Lookahead, Gantt, Overview) runs the existing publish path for that
    project's drafts, with the conflict check and one reason for the batch. It then **sets the baseline**
    (each task's start and end copied once), marks the project *Published*, notifies each affected
    person once, and lets calendar sync pick the tasks up.
- **After publish, edits are live.** When a save changes anyone's dates or crew, the page asks
  **Notify crew? [Notify] [Don't notify]**. The default is Notify when the change falls within the next 14
  days, and Don't notify beyond that. Notify sends the existing publish notice to the people affected,
  never to the person editing.
- **Reset baseline** (Gantt, project menu) re-copies today's dates for a re-plan, after a confirmation that names
  how many tasks move.
- **Existing projects** become *Published* without a baseline when they already have dated work with a crew, and stay
  *Draft* otherwise. The first **Set baseline** gives them one. The old board-wide Draft mode stays available
  under *More ▸ Tools ▸ Hold all my changes*, unchanged, for someone who wants to rehearse a big reshuffle.

### 4.9 Maintenance Planner (D9: keep, simplify)

- Same views (**Month, Week, Crew**), same cards, same projected and seasonal visits, same *Add visit here*,
  same draft and pencil support.
- Gets the shared toolbar: tabs for its three views, the technician filter, search, the alert chips (Conflicts,
  Overdue), **+ Add ▾** (Add visit…, Day note…), **More ▾**. *More* holds Display (Color by, default *Visit type*; Projected
  visits; Project work), Views (saved views), Tools (Block time…, Conflicts…, Tap to move, Draft mode) and
  Print (this view, selection, Day Board).
- **Project work is a busy strip** (D13). Each technician-day shows one thin grey bar ("Project: 8h", with the
  task list on hover) instead of the read-only project cards that fill the October grid today. *More ▸ Display ▸
  Project work as cards* brings the cards back. Free hours count the project work either way.
- **Resources available** is collapsed by default to one summary line ("Austin: 3h free Tue · Off Thu–Fri"), and
  opens on a click. The visit-kind legend lives only in "?".
- **Techs use Today** (§4.4), which already lists their visits. Lisa keeps the desktop week view with
  *Unscheduled* and *Overdue* in view. No dispatch board or merged crew board (Nik declined both).

## 5. Feature-by-feature map

Every current feature, and where it goes. **Kept**: same place and behavior. **Moved**: same behavior, new place.
**Merged**: folded into another surface with nothing lost. **Hidden**: off the main screen, one step away. Nothing is deleted.

### 5.1 Project Planner views and screens

| Feature (today) | Where it goes | Status |
|---|---|---|
| Week view (default) | Calendar tab ▸ Week | Moved (no longer the landing screen) |
| Month view | Calendar tab ▸ Month | Moved |
| Crew view | Crew tab ▸ Board | Kept, labels in days |
| Heatmap (8 weeks) | Crew tab ▸ Capacity (top) | Merged |
| Utilization view | Crew tab ▸ Capacity (table under the heatmap) | Merged |
| Route view | Unchanged route; linked from Today, peeks and the Crew board | Kept |
| My week (phone) | Today ▸ *This week*; *More ▸ Views ▸ My week* | Moved (Today is the new phone landing screen) |
| Resources available panel | Crew tab header; collapsible summary on Calendar | Moved |
| Unscheduled tray | *Not scheduled* row per project (Lookahead), side tray (Crew), tray (Calendar) | Merged |
| Needs crew tray | *Needs crew* chip + filter (Lookahead), side tray (Crew) | Merged |
| Overdue tray (bulk reschedule / done / cancel) | Overdue chip ▸ the same tray in a drawer; Overview exceptions | Kept |
| Needs tasks tray | *No tasks yet · Build out* project rows (Lookahead) | Merged |
| Old URLs (`/week`, `/month`, `/heatmap`, `/utilization`) | Redirect (replace) to their new homes | Kept |

### 5.2 Scheduling actions

| Feature | Where it goes | Status |
|---|---|---|
| Drag to move | Every tab (Lookahead bars, Gantt bars, Crew bars, Calendar cards) | Kept |
| Drag a person onto a task / add to crew | Crew board, task panel, *Assign to…* | Kept |
| Resize | Lookahead, Gantt, Crew, Calendar week | Kept |
| Reason prompt on a new conflict | Every write | Kept |
| Successor offer after a later move | Replaced by auto-shift with Ctrl/⌘ opt-out (D10) | Merged |
| Quick add (double-click, rich fields) | Double-click on any tab; *+ Add ▸ Quick add task* | Kept |
| Build out grid, Paste a list, Copy from project, Templates + bundles, AI draft | *+ Add ▸ Build out*; Gantt *Build out ▾*; *Build out* on a no-task project row | Moved |
| Right-click menu (Edit, Assign to…, Next free day, Pencil/Firm, Duplicate, Duplicate to…, Split, Project at a glance, Open project) | Same menu on every tab | Kept |
| Split | Right-click and panel only | Hidden (D12) |
| Copy week | *More ▸ Tools ▸ Copy week…*, right-click *Copy to another week* | Hidden (D12) |
| Multi-select move + selection bar | Every tab with bars or cards | Kept |
| Move-by: Calendar / Working days switch | Inside the *Move…* panel of the selection bar | Hidden (D12) |
| Tap to move | *More ▸ Tools*; on by default with a coarse pointer | Moved |
| Drag out of a drawer | Unchanged | Kept |
| Undo toast + Ctrl/⌘Z + undo icon | Unchanged | Kept |
| Suggest dates (route-aware, "when", never "who") | Task panel and right-click | Kept |
| Who is free / Assign to… | Right-click, task panel, Conflict center | Kept |
| Pencil (tentative) | Right-click, panel; hatched bars | Kept |
| Board-wide Draft mode + Publish/Discard strip | *More ▸ Tools ▸ Hold all my changes*; the strip still shows while it is on | Hidden |
| Per-project publish, baseline, Notify crew? prompt | Project row, Gantt header, save prompt | **New** (D5, D8, D11) |

### 5.3 Looking things up

| Feature | Where it goes | Status |
|---|---|---|
| Person peek (week, route, call/text/email) | Click any name, every tab | Kept |
| Day peek (everyone's day) | Click any date head | Kept |
| Project peek (timeline, actuals, labor forecast) | Click a project name; *Open Gantt* added to it | Kept |
| Side-panel editor | Unchanged | Kept |
| Search (`/`) | Toolbar | Kept |
| Keyboard shortcuts | Kept. `1` `2` `3` `4` `5` now pick the five tabs, and the old week/month/crew keys keep working in Calendar | Kept |
| Legend / help / first-time tips | "?" | Kept, rewritten for the new tabs |
| Hover glow | Every tab | Kept |
| Sticky headers, edge scroll | Lookahead, Crew, Calendar | Kept |
| Filters in the address (`?project=&pm=&group=`) | Every tab, same rules | Kept |
| Open in Project Planner (from a Project) | Opens the **Gantt** for that project; the Lookahead filtered to it is one tab away | Moved |

### 5.4 Big picture, print and preferences

| Feature | Where it goes | Status |
|---|---|---|
| Color by (Task color, Person, Job type, Project, PM, Status) | *More ▸ Display*; **default Job type** | Hidden (D12) |
| Highlight a project | Project name click, project peek, *More ▸ Display*, Esc clears | Kept |
| Saved views + "last used" | *More ▸ Views*; "last used" now also remembers the tab | Hidden (D12) |
| Team capacity strip | Crew board and Calendar week | Kept |
| Print this view / selection | *More ▸ Print* | Hidden (D12) |
| Weekly crew sheet | *More ▸ Print ▸ Crew sheet* | Hidden (D12) |
| Projects Dashboard portfolio Gantt | Unchanged on the dashboard; the Overview tab reuses it | Kept |
| Weekly summary | Overview, *More ▸ Print*; Monday email behind a switch, OFF | **New** (D3) |

### 5.5 Conflicts, people and time

| Feature | Where it goes | Status |
|---|---|---|
| Conflict center (next free day, someone free, pencil, keep with reason, Show kept) | Conflicts chip; *More ▸ Tools ▸ Conflicts…*; Overview exceptions | Kept |
| Card conflict markers | Bars and cards on every tab | Kept |
| Personal blocks (schedulers block anyone; techs block themselves) | Crew board, Calendar, person peek, Today *Block my time* | Kept |
| Day notes | Day heads on Lookahead, Crew and Calendar; day peek; Today; digest | Kept |
| Qualification gaps, equipment conflicts, weather flags | Chips on bars and cards | Kept |
| Planned vs actual, *running over* chip | Bars, Running-over chip, Overview exceptions | Kept |
| Labor forecast (cost roles only) | Project peek | Kept |
| Work patterns, holidays, time off, travel days | Engine; shown as Off / Holiday / Unavailable | Kept |
| Drive time, long-drive flag, route order | Crew board day cells, route view, Today | Kept |
| Show maintenance / rentals / travel (read-only) | *More ▸ Display* | Moved |

### 5.6 Messages and sync (unchanged unless noted)

| Feature | Status |
|---|---|
| Combined 6 AM digest, change alerts, leave-by pings (each behind its Settings switch) | Kept |
| Publish notices | Kept. They now also fire on per-project publish and on a "Notify" answer |
| Google Calendar one-way sync + snap-back + time off read-in | Kept. A Draft project's tasks are drafts, so they don't sync until published |
| Customer date confirmation | Kept, **switch stays OFF** |
| AI tools (`crew_schedule_suggestions`, `crew_day_route`, `crew_who_is_free`, `crew_conflicts`) | Kept |

### 5.7 Maintenance Planner

| Feature | Where it goes | Status |
|---|---|---|
| Month / Week / Crew views | Tabs | Kept |
| Visit cards, projected visits, seasonal visits, Next visit drag | Unchanged | Kept |
| Unscheduled tray | Unchanged | Kept |
| Resources available, drag a technician onto a visit | Collapsed summary line that opens to the panel | Kept (collapsed) |
| Days off on the calendar, Day off chip | Unchanged | Kept |
| Multi-person visits, crew editor, Full day, Planned hours | Visit panel | Kept |
| Add visit here (double-click, right-click) | Also *+ Add ▸ Add visit…* | Kept |
| Duplicate open visit warning | Unchanged | Kept |
| Draft mode + Publish, Pencil visits | *More ▸ Tools* (draft); right-click and panel (pencil) | Hidden / kept |
| Overdue visits panel | Overdue chip | Kept |
| Site peek, person peek, day peek, side panel | Unchanged | Kept |
| Color by (Visit type, Technician, Site, Status, Contract), Highlight a site | *More ▸ Display* | Hidden |
| Saved views | *More ▸ Views* | Hidden |
| Print view / selection | *More ▸ Print* | Hidden |
| Day Board link, route icon per technician-day | *More ▸ Print/Tools*; route icon kept | Moved / kept |
| Visit-kind legend row | "?" only | Moved |
| Project work (read-only) | *More ▸ Display* | Moved |
| Technician filter in the address | Unchanged | Kept |

## 6. What the back end needs

The design keeps every endpoint and doctype. These additions are **new and additive**: no existing call changes its answer
unless the new argument is passed.

| Addition | Where | Why |
|---|---|---|
| **Schedulable-task rule**: a task shows on the schedule views when it is a group Task whose subject has a three-digit phase number (`planner_templates.phase_number`), a descendant of one, or has a crew row or an open assignee who is a Planner Resource. An `all_tasks=1` argument shows everything | `api/project_planner.get_planner` (+ the new `get_lookahead`) and the Unscheduled read; **display only**, the engine still counts every booking | D6 |
| `get_lookahead(start, weeks, project, pm, group, all_tasks, draft)`: the project → phase → task tree with bars, crew, baseline, chips, and the projects with no tasks | `api/project_planner.py` (or a new `api/planner_lookahead.py`), reusing `read_tasks`, `build_card` and the engine with `google=False` | §4.2 |
| Schedule state and baseline fields: Project `custom_schedule_status` (Draft / Published), `custom_schedule_published_on`, `custom_baseline_set_on`; Task `custom_baseline_start`, `custom_baseline_end` (Date) | Fixtures + one patch that marks existing projects (Published when they have dated, crewed work) | D5, D8 |
| `publish_drafts(project=…)` and `discard_drafts(project=…)` filters; `publish_project_schedule(project, reason)` = publish that project's drafts, then set its baseline and status; `reset_baseline(project)` | `api/project_planner.py` | D5, D8 |
| **Shared drafts for a Draft project.** Today a draft row belongs to its author, so a schedule Nik drafts is invisible to Clegg. For a project in *Draft*, every scheduler should see and publish its rows | `_draft_rows` / `_draft_overlay` scoped by project for Draft projects | D8. This is the one behavioral change to an existing path. It applies only to projects in *Draft*, which none are until the patch runs |
| `notify` argument on the live writes (`save_task`, `add_crew`, `swap_crew`, `move_many`, `shift_successors`, `move_visit`) → `planner_notices.send_publish_notices` for the affected people | `api/project_planner.py`, `api/maintenance_planner.py` | D11 |
| `add_dependency` / `remove_dependency(task, depends_on, modified)` through the same save path (conflict and "starts before" checks) | `api/planner_actions.py` | §4.3 link-by-drag |
| A write hook on the Gantt widget: `config.on_write(row, changes) → Promise` replaces `update_gantt_row` for an embed that supplies it | `public/js/gantt_widget/gantt_widget.js` | §4.3: planner writes keep their checks |
| `get_overview(start, months)`: jobs × phases with baseline, plus the exception list; `weekly_summary_html(start)`; an optional Monday job behind a Settings switch (OFF) | `api/project_planner.py`, `planner_digest.py`-style job | D3 |

## 7. Build plan

Each PR is independent once its dependencies have merged. Each one ships with the version bump,
changelog entry, README updates and tests the repo requires. Model sizes follow Nik's rule: Haiku for
mechanical work, Sonnet for medium, Opus for cross-cutting.

| # | PR | Scope | Main files | Depends on | Model | Built |
|---|---|---|---|---|---|---|
| P1 | **Shared toolbar v2 + declutter** | Tabs control, **+ Add ▾**, **More ▾** with Display / Views / Tools / Print, alert chips, the phone sheet. Moves Color by, saved views, Copy week, Split, the Move-by switch and Print off the bar (D12). Default color Job type. Both planners use it; no new views yet | `public/js/planner_kit/toolbar.js`, `project_planner.js` (PPTB), `maintenance_planner.js` (MPTB), both CSS files, `tests/test_planner_toolbar.py` + its node script | — | Sonnet | Built in #1319 (v1.701.0). |
| P2 | **Schedulable-task filter** | The D6 rule server-side (display only), `all_tasks` argument, *Show all tasks* switch in More ▸ Display, the Unscheduled tray and search follow it | `api/project_planner.py`, `crew_availability.py` (helper only), `project_planner.js`, new bench-free test | P1 | Sonnet | Built in #1322 (v1.704.0). |
| P3 | **Schedule state, baseline, per-project publish (back end)** | The fields and patch, `publish_project_schedule`, `reset_baseline`, project filters on publish/discard, shared drafts for Draft projects, the `notify` argument on live writes | fixtures (custom fields), `patches/`, `api/project_planner.py`, `api/maintenance_planner.py`, `planner_notices.py`, `doctype/planner_draft_change`, tests | — (parallel with P1/P2) | Opus | Built in #1320 (v1.702.0). |
| P4 | **Auto-shift + Notify prompt (page side)** | Drags move successors by working days with Ctrl/⌘ opt-out (one Undo), replacing the offer. A Draft project's writes auto-draft. The *Notify crew?* prompt after publish. Applies to Calendar and Crew today and becomes the shared write path for P5/P6 | `project_planner.js` (commit/send, 3A successor block), `maintenance_planner.js` (notify only), tests | P1, P3 | Opus | Built in #1323 (v1.705.0). |
| P5 | **Lookahead tab (new home)** | `get_lookahead` + the tab: project/phase/task rows, bars in days, baseline ghost, chips, *Not scheduled* rows, *No tasks yet · Build out* rows, project schedule chips with Publish…, drag/resize via P4, phone list. Landing-screen rules and old-URL redirects | `api/project_planner.py` (or `api/planner_lookahead.py`), `project_planner.js` (new PPL block), CSS, tests | P2, P3, P4 | Opus | Built in #1327 (v1.708.0). |
| P6 | **Gantt tab (one project)** | Widget write hook, planner task feed, link-by-drag (`add_dependency`/`remove_dependency`), baseline layer, Reset baseline, Build out ▾ docked, Publish. "Open in Project Planner" lands here | `public/js/gantt_widget/gantt_widget.js`, `api/gantt.py` (feed shape only if needed), `api/planner_actions.py`, `project_planner.js` (new PPG block), `project_planner_link.js`, `tests/test_gantt_api.py` + new tests | P3, P4 | Opus | Built in #1330 (v1.709.0). |
| P7 | **Crew board in days + Capacity** | Crew bars in days, side tray (Not scheduled, Needs crew), *Board / Capacity* switch merging Heatmap + Utilization, redirects from `/heatmap` and `/utilization` | `project_planner.js` (crew, PP3A heatmap, PPU blocks), CSS, `tests/test_planner_url_filters.py` | P1, P2 | Sonnet | Built in #1325 (v1.713.0). |
| P8 | **Today (phone)** | Today + tomorrow list, inline route map, Block my time, This week link, landing rule on phones; covers project tasks and visits | `api/project_planner.get_my_week` (read the same shape; maybe a `get_today`), `project_planner.js` (PP3B my-week block), CSS, tests | P1 | Sonnet | Built in #1326 (v1.707.0). |
| P9 | **Overview + weekly summary** | Portfolio timeline (widget, read-only), exception list, weekly summary print, Monday email behind an OFF switch, landing rule for Executive Team | `api/project_planner.py` (`get_overview`, `weekly_summary_html`), `project_planner_settings` (one Check + backfill patch, per the Singles rule), `project_planner.js` (PPO block), tests | P3, P6 (widget hook not needed, shared feed helpers) | Sonnet | Built in #1332 (v1.715.0), stacked: merges before P12. |
| P10 | **Maintenance Planner simplify** | P1's toolbar applied fully, project work as a per-day busy strip with cards under More ▸ Display (D13), Resources available collapsed to a summary, legend row into "?", the visit-kind defaults, Today link for techs | `maintenance_planner.js`, `maintenance_planner.css`, `tests/test_planner_toolbar.py` | P1 (P8 for the Today link) | Sonnet | Built in #1331 (v1.714.0), stacked: merges before P12. |
| P11 | **Calendar labels in days** | Week/month cards and the panel say "3 days · 2 people" (hours shown only when set); duration field in days | `project_planner.js` (card/panel builders), CSS | P1 | Haiku | Built in #1324 (v1.706.0). |
| P12 | **Help, legend, tips, docs** | "?" sections for the new tabs, first-run tips, README sections for both modules, this doc marked as built | `project_planner.js` / `maintenance_planner.js` legend providers, `project_enhancements/README.md`, `sapphire_maintenance/README.md` | all | Haiku | Built in #1333 (v1.716.0). |

**Merge order:** P1 → P3 → P2 → P4 → P5 → P6 → P7 → P8 → P10 → P11 → P9 → P12.

**Parallel lanes:**
- P1 and P3 can be built at the same time; they touch different files.
- P7, P8, P10 and P11 can be built in parallel once P1 is in. P7, P8 and P11 all edit `project_planner.js` in different blocks, so expect the usual rebase, plus the version and changelog renumbering the manager already handles.
- P5 and P6 should go one after the other: both add a tab and both use P4's write path.

**What makes each PR safe to ship alone:**
- Until P5 lands, the landing screen stays the week view.
- Until P4 lands, the successor offer stays.
- P3's fields do nothing until a page uses them.
- Every new view arrives with the toolbar tab already wired.
- Old routes keep working throughout.

## 8. Open points for the build sessions

- **Earlier moves (D15).** ERPNext's own `Task.on_update → reschedule_dependent_tasks` only pushes later, so a move earlier
  is entirely the planner's `shift_successors` with a negative count. P4 must verify the negative path end to end:
  working-day stepping backwards, the all-or-nothing check, and the successors ERPNext already pushed.
- **The "Overdue" status** sits on 1,005 open tasks, so status color and the Overdue chip would be noisy. Fixing the
  data is out of scope here (UX only). Overview counts *overdue* by end date, not by the status value.
- **DHTMLX Standard and the baseline layer.** P6 must verify `addTaskLayer` exists in the Standard build in the browser
  harness before relying on it. The fallback is a second thin bar drawn by a task template.
- **Shared drafts** (P3) are the only behavior change to an existing path. They are limited to projects in *Draft*.

## Built, and where it differs

All twelve rows of the [build plan](#7-build-plan) are built; the last column of that table says which PR did
each, and the PR's CHANGELOG entry says why anything below differs. What the build does differently from the
text above:

- **Where the new endpoints live.** `get_lookahead` is in `api/planner_lookahead.py` (P5), the Gantt's feed in
  `api/planner_gantt.py` (P6), and `get_overview` and `weekly_summary_html` in `api/planner_overview.py` (P9),
  not in `api/project_planner.py`, which is 6,000 lines. The spec's other additions (publish, baseline, the
  `notify` argument) are where §6 put them.
- **The number keys.** §5.3 has `1` to `5` pick the five tabs. They don't yet: `1 2 3` still pick Week, Month
  and Crew, as the "?" help's Keyboard section says. The tab buttons are a tablist, so the arrow keys, Home and
  End work on them.
- **The Crew board is not yet one true bar.** A multi-day task is a chip per day, drawn joined and labeled
  *Day 2 of 5* (P7). The chip is what is dragged, resized and tapped; a single spanning bar is a possible follow-up.
- **Split, Copy week and the Overdue tray on a Draft schedule** (v1.740.0, after P4).
  - A copy is a drafted new task.
  - A split is refused until the schedule is published, since no draft can hold one.
  - A Reschedule is drafted. It moves a follower only when the follower would otherwise start by
    the task's new end, to the working day after (Nik, 2026-10-23). This is unlike a drag (D10),
    because an overdue task is weeks late.
  - *Mark done* and *Cancel* stay live (Nik, 2026-10-23), and warn first about a drafted reschedule.
- **Draft hardening** (v1.742.0).
  - Every planner write treats a missing draft flag as "auto", so §4.8's "every write to its tasks is
    drafted" holds for any client, not only the page.
  - Every Task save widens its project's derived expected window first (a `before_validate` hook),
    the Desk form included. So moving the last task later, an overdue reschedule or a Copy week
    ahead is no longer refused, live or at Publish.
  - ERPNext's own date checks run on a draft, as warnings, rather than only failing at Publish.
    Since v1.743.0 that is the installed controller's `validate_dates` on an unsaved copy, so the
    warnings follow whatever ERPNext release prod runs.
  - A dependency loop among an overdue task's followers is left where it is and named.
- **The Overdue tray is not filtered by D6** (§6 names the schedule and the Unscheduled tray), and the **print
  sheet** still labels cards with hours (P2, P11).
- **The Overview** draws a job's milestones as diamonds when the job is expanded, not on its collapsed bar (P9).
- **Today's help.** Today has no toolbar, so it has no "?"; P12 gave it a "?" button beside *Planner*, so the
  phone's help and tour are not two screens away.

What P12 added beyond the table's scope, because Nik asked on 2026-10-21 ("When we are close to finishing, I'd
like a brief and simple tutorial on the planner for every first time visitor"): the **first-visit tour** for each
planner. Four to six short steps, a desktop's and a phone's apart; shown once per person, with the flag in the
person's own `__UserSettings` row for that planner (never frappe's Redis-only user_settings); skippable at every
step; **"?" ▸ Take the tour** shows it again; the Overview step is for the Executive Team only. It takes no
history entry. See "The first-visit tour and the help" in `project_enhancements/README.md` and
`sapphire_maintenance/README.md`.

## Appendix A: Walkthrough

This was done on production on 2026-10-21, signed in as Nik, at 1440, 834 and 390 px. It was look-only:
scrolling, view tabs and screenshots. Nothing was dragged, saved or published. Opening a view does call
`save_last_view`, which records the "last used" view; that is the planner's own automatic behavior.

**Project Planner, desktop (crew view, as Nik's last-used view opened it):**
- **The calendar starts about three screens down.** First come the *Overdue (200)* banner, then *Needs crew (15)* as a grid of
  cards, then *Unscheduled (109)*, then *Needs tasks (3)*. Only after those does the crew board begin.
- Most of the 109 unscheduled cards are the cost-worksheet lines of one job ("Audio and Video", "Cast-in-Place Concrete",
  "Check Valves" …), or billing and admin template tasks ("Bill For 2025 Spring Startup", "Progress Payment",
  "Quarterly Maintenance Contract").
- Every card carries a *Suggest dates* button. This is the D6 problem in pixels.
- **A multi-day task is drawn as a separate card on each day** of each crew member's row ("Precast rings" four
  times in Jesse's row). It is not one bar, so the crew view does not read as a schedule.
- The toolbar after the v1.687.0 cleanup is tidy (Views ▾, Display ▾, Edit ▾, Print ▾, ?, ↻ and the alert chips), but
  its five view tabs (Month, Week, Crew, Heatmap, Utilization) are the old model.
- **Bug found:** with *Color by = Person* (Nik's saved choice) the planner loads **blank**. The v1.687.0 toolbar builds
  the person color key from `this.data.resources` before the data has loaded. That throws inside `handle_route`,
  so `get_planner` is never called. It reproduces on a direct `/week/<date>` link and then on every open. It is
  reported as a separate hotfix, not part of this redesign.

**Maintenance Planner, desktop (month):**
- *Resources available* opens as a full-width technician × day strip over the calendar.
- In October the month grid is mostly **project work** (read-only "Travel: Kapture Events", "Underground rough-in",
  "Rough-in before…"), so the maintenance visits are a minority of the cards on the Maintenance Planner. *Project
  work* is on by default.

**Tablet (834 px, Maintenance week):**
- The Desk sidebar stays open and takes about a quarter of the width.
- Visit cards truncate to "4th …", "San…", "Pre…", and the Resources table scrolls sideways. The help text runs under the grid.

**Phone (390 px):**
- On the Maintenance week, the first visit is about one and a half screens down. Above it come the Resources table and the
  *Unscheduled* tray, and both scroll sideways.
- On the Project Planner's *My week*, the whole planner toolbar (five view tabs, Filters and tools) sits on top of a
  person's week. For someone who is not a Planner Resource, it shows only a "not on the planner's list" note.

**What this adds to the design:**
- The trays move into the board (§4.2, §4.5).
- Bars span days (§4.2, §4.5).
- The phone opens on Today with no planner toolbar (§4.4).
- The Maintenance Planner collapses Resources to one line, and *Project work* moves under *More ▸ Display* (§4.9).

## Appendix B: Sources

- Usage audit (queries and raw numbers): session notes `planner-ux-audit-usage.md`.
- Benchmark (with source links and verification marks): session notes `planner-ux-benchmark.md`.
- Earlier decisions: ClaudeBrain inbox notes of 2026-10-08, 10-09 and 10-12, and the 2026-10-14 toolbar picks;
  the module READMEs (`project_enhancements/README.md` § Project Planner, `sapphire_maintenance/README.md` § Maintenance Planner).
