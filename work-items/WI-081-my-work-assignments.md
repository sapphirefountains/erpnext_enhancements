# WI-081: My Work — reimagine ToDos and assigning, on top of Frappe's own assignments

**Phase:** 2   **Type:** APP_CODE   **Size:** L (three phases; Phase 1 M, v1.696.0; Phase 2 M, v1.697.0; Phase 3 M, v1.698.0)
**Blocked by:** nothing
**Blocks:** nothing

## Why

Nik, 2026-10-20: "ToDo's suck in ERPNext. If I get assigned a task, I get a ToDo notification, which
takes me to a weird doctype that links to the actual thing I've been assigned to, but that is buried on
the bottom."

What production showed the same day (read-only queries, 2026-10-20, frappe/ERPNext 16.50.0, app 1.695.0):

- **1,310 open ToDos, and the list could not be trusted.** 1,141 were dated the day they were made,
  because `assign_to.add` writes `args.get("date", nowdate())` when no "Complete By" date is chosen — so
  "97% overdue" was mostly an artefact of the date, not of the work.
- **286 sat on Administrator.** The customer inactivity reminder (`script_migrations/customer.py`) fell
  back to a Customer's owner, which is Administrator for every imported customer.
- **581 "Follow up with …" Customer reminders**, almost all for customers no longer flagged as
  prospects (15 prospects remain). Closing one did nothing: the reminder checks only for an *Open*
  ToDo, and closing a ToDo never touches `custom_last_activity_date`, so it was written again the next
  morning.
- **225 open on Completed Tasks, 35 on "Canceled" ones** (the site's one-L spelling, which ERPNext's
  `Task.unassign_todo` never clears), **17 on Lost Opportunities, 16 on Completed Projects.**
- **201 with no assignee and no record, created by Triton** (the phone agent, posting as triton@) — the
  newest that morning: "Send an estimate for one Stillwater panel emergency shut-off unit to …". Live
  customer work that only a System Manager could see.
- **The assignment email** was titled "ToDo: CRM-OPP-… [For Opportunity CRM-OPP-…]", printed the
  description's HTML tags (`ee_prose` escapes what it is given), and its button opened the ToDo form.

## Decided 2026-10-20 (Nik, from the option rounds)

- **Approach: a My Work page built on native ToDo** (not "fix the ToDo form only", not "replace ToDo").
  Nik asked whether this hooks into the native Assign To or replaces it, and said using it is better than
  inventing a duplicate. It does: an assignment stays a ToDo; Assign To, Assignment Rules, the crew
  mirrors, Triton and the AI follow-up tool keep working untouched.
- **A notification opens the record**, with an "Assigned to you" banner (Done / Snooze / Reassign /
  Reply) at the top — not the inbox, not a side drawer.
- **Inbox, v1:** smart buckets, one-click actions, an "Assigned by me" tab, a team load board.
- **Backlog:** triage then archive, with a dry run shown before anything is cancelled.
- **Assigning:** quick dates + "the ask", a load-aware picker, team queues. (Accept/decline: not chosen.)
- **Alerts:** fix the email, a daily digest, nudges + escalation, a text for urgent.
- **Automation:** auto-close with the record, recurring assignments, checklists. (AI one-line brief: not
  chosen.)
- **Surfaces:** a sidebar badge, phone-friendly, a calendar view, a better record sidebar.
- **Phasing** (proposed and accepted: "Start Phase 1"): Phase 1 the daily experience; Phase 2 assigning
  and alerts; Phase 3 teams and structure.

Two departures from the options as offered, both made because the data said so and both reported to Nik:

1. **The triage does not close a ToDo for having no assignee.** The option read "everything on
   Administrator or with no assignee". Triton's unassigned ToDos are live call follow-ups, so they are
   kept and shown on a new **Unclaimed** tab; only the 90-day staleness rule reaches one.
2. **The triage is a button, not a patch.** A patch would close a thousand ToDos at deploy, before
   anyone had looked at the numbers. *Tidy the backlog* shows the dry run per rule, then applies in a
   background job, and every batch can be undone.

## Native-first check

- **ToDo + Assign To** is the native assignment model, and it stays the store. The v16.50 ToDo
  controller rebuilds the record's `_assign` on every status change (`todo.py update_in_reference`), so
  closing through it keeps the sidebar avatars right. What it lacks is a usable *view*: no record in the
  list, no due-date semantics, no "assigned by me", no inline actions. That view is this work item.
- **Notification Log** already opens the record for an `Assignment` bell entry (`notifications.js
  get_item_link`). The broken link was this app's own Notification fixture's email button.
- **`close_all_assignments`** exists, but sends every assignee "Your assignment on X has been removed" —
  noise when a Task is completed — so auto-close closes through the controller without it.
- **Assignment Rule** has close conditions, but per rule and only for rule-made assignments; it does not
  cover sidebar assignments, crew mirrors or Triton.
- **Notification Settings** stays the per-person email switch for every bell entry this sends.

## Scope

### Phase 1 — the daily experience (v1.696.0)

- **My Work page** (`/desk/my-work`, `/by-me`, `/unclaimed`), rows that lead with the record, buckets
  Overdue / Today / No due date / This week / Later, inline Done (with a note for the timeline), Snooze,
  Reassign, Reply, Take it, Nudge, Take back; drag between buckets on a desktop; phone layout.
- **The banner** on any record you are assigned on; on a ToDo's own form, a pointer to its record.
- **Sidebar "My Work" row** with a count of overdue + today + undated.
- **The email**: subject "Record title: the ask", the ask as text, the button to the record.
- **Auto-close** on the change into a finished state (`assignments.FINISHED_STATES`), and cancel with a
  cancelled document.
- **Tidy the backlog** (System Manager): dry run, apply, undo.
- **The customer reminder**: a closed follow-up restarts the window; no reminder to Administrator, Guest
  or a disabled user.

### Phase 2 — assigning and alerts (v1.697.0)

Built 2026-10-20, right after Phase 1 merged (Nik: "create the PR, merge, and then begin phase 2").
Two facts from production shaped it:

- **The Morning Briefing reaches two people** (Briefing Recipients: Nik with email, James without)
  while 19 people hold open assignments. So "fold the digest into the briefing" became: the briefing
  carries it (`api/briefing._user_todos`, on My Work's rules), and everyone *not* emailed the
  briefing gets the same summary as a short "My Work" email at 06:40. Nobody gets both.
- **Two emails per assignment.** In the 30 days to 2026-10-20 the "New ToDo Created" Notification
  sent 231 emails and Frappe's own Assignment notification 78. The Notification now fires for High
  priority only, and `notification_skip_email_types = ["Assignment"]` stops Frappe's; the bell
  entries are unchanged.

What shipped:


- **Assign To dialog** (subclassed, so the sidebar and the list view's bulk Assign To both get it):
  "What do you need?" first, as one plain line, no longer pre-filled with the record's title;
  quick dates (Today, Tomorrow, Friday, Next Monday, In a week); Priority defaulting to the
  record's (Frappe's default evaluated to `true`).
- **Load-aware picker:** under Assign To, each picked person's open, overdue and due-that-day
  counts, and — for staff — approved time off or a work restriction on the chosen day
  (`hr_enhancements.availability.reasons_unavailable`). Counts only, never titles.
  "In the field" (booked hours from the planner) is **not** shown: `crew_availability.availability`
  is the planner's whole engine and too heavy for a dialog; it is a follow-up if wanted.
- **Morning summary** (`task_enhancements/assignment_alerts.py`, weekdays 06:40): overdue, due
  today, new since the last weekday morning, and what is late with people you assigned; sent only
  when one of those is non-empty.
- **Due-date nudge:** a bell entry on the morning an assignment's real due date arrives.
- **Escalation:** the assigner is told once, by bell and in their summary, when an assignment is 2
  days past a real due date (`ToDo.custom_escalated_on` keeps it to once).
- **Text for High:** a High-priority assignment someone else made texts the assignee's Employee
  cell number through the Triton gateway (`api/telephony.send_system_sms`), after the commit.
  ToDo priorities are Low / Medium / High, so "urgent" is High.
- **Record sidebar:** under the avatars, each open assignment's person, ask, due state and assigner.
- **Kill switches** (site config, live): `my_work_digest_disabled`, `my_work_escalation_disabled`,
  `my_work_nudge_disabled`, `my_work_sms_disabled`.

### Phase 3 — teams and structure (v1.698.0)

Built 2026-10-20 after Phase 2 merged (Nik: "create the PR, merge, and begin phase 3").

- **Team queues, on native pieces only.** A team is a **Role named "… Team"** — production already
  has nine (Support 14 people, Production 13, Sales 9, Design 7, Finance 6, Operations 4, Executive 4,
  HR 3, Marketing 3; 2026-10-20). A queue item is a ToDo with that Role in ToDo's native `role` field
  (unused until now: 0 open ToDos carried one) and nobody in `allocated_to`. The Assign To dialog
  offers "Or a team's queue"; every member gets a bell and sees it on My Work's **Unclaimed & queues**
  tab, and the first to press Take it owns it. User Groups were the alternative and were passed over:
  production's eight are partial (Production 4, Sales 2 … Finance 1).
- **Team load board** (`/desk/my-work/team`), for leads — System Manager, President, Executive Team,
  Operations Manager, Production Manager (`api.my_work.LEAD_ROLES`, a constant to edit): a column per
  person with overdue / today / open counts and their assignments, a column per team queue, a team
  filter, and drag a card onto someone to hand it over (Frappe's own `assign_to.add` underneath).
  Leads may also reschedule, reassign and take back anyone's assignment, as System Managers could.
- **Repeating assignments**: `ToDo.custom_repeat` (Daily … Yearly), set in the dialog or from a row's
  Repeat menu. Marking one done makes the next — same person or queue, record, ask, priority and steps
  (unticked) — due one step after this one's due date, never already overdue and never on the day it
  is made (`task_enhancements/recurrence.py`). Cancelled, auto-closed and triaged ones do not repeat.
- **Checklists**: `ToDo.custom_checklist`, a child table (**Assignment Checklist Item**, new in this
  module). Steps go in from the dialog (one per line) or the row; they are ticked on My Work, on the
  record's banner, or in the ToDo form's grid, and a tick records who and when.
- **Calendar** (`/desk/my-work/calendar`): your assignments with a real due date on a Monday-first
  month grid; drag one to another day. Undated ones are left off on purpose — the stock ToDo calendar
  plots creation-day stamps. On a phone it is a list of the days with something on them.
- The guided page's Assign To (`form_kit/ui/record_tools.js`) no longer pre-fills the ask with the
  record's title, for the reason the sidebar's no longer does (Phase 2).

## Preconditions

- `bench migrate` has run the v1.696.0 deploy (adds `ToDo.custom_triage_batch`; the triage refuses to
  apply without it and says so).

## Acceptance criteria (Phase 3)

1. **Assign to a team only** from a record's sidebar ("Or a team's queue": Sales Team, no person): every
   Sales Team member gets a bell; it is on their Unclaimed & queues tab with a "Sales team" chip; the
   first to press Take it owns it and it leaves the others' tab. A Support Team member cannot take it.
2. **A weekly with three steps**: assign with Repeat = Weekly and three checklist lines; tick two on
   the record's banner (the ToDo form's grid shows who ticked them); mark it done; a new one appears
   for the same person, due a week after the first's due date, with three unticked steps. Withdraw
   that one: nothing new appears.
3. **The Team board** (as a lead): drag a card from one person to another; the record's Assign To
   avatars change accordingly. As a non-lead, `/desk/my-work/team` has no tab and the endpoint refuses.
4. **The Calendar** shows only dated assignments; dragging one to next Tuesday moves its due date and
   it shows under "This week" on My Work.
5. `SELECT COUNT(*) FROM tabToDo WHERE role LIKE '% Team' AND allocated_to IS NULL AND status='Open'`
   counts the open queue items the board shows.

## Acceptance criteria (Phase 2)

1. **Assign from a record's sidebar**: the dialog asks "What do you need?" first and empty; picking a
   person shows their open/overdue counts (and "Away: …" if they have approved time off that day);
   the Friday button fills Complete By with this Friday.
2. **A Medium assignment** produces a bell entry and no email; **a High one** emails (subject
   "Record: the ask") and, if the assignee's Employee has a `cell_number`, texts them within a
   minute: `SELECT COUNT(*) FROM tabEmail Queue WHERE message LIKE '%Assignment Upd%' AND
   creation > <deploy time>` = 0.
3. **The morning after**, everyone with something overdue, due today or new who is not emailed the
   briefing has one "My Work: …" email; Nik's briefing has a "My Work" section instead.
4. **Escalation**: an assignment due two weekdays ago, assigned by someone else, puts a bell on the
   assigner and `custom_escalated_on` is set; the next morning there is no second bell.
5. **The record sidebar** lists each assignee with what was asked and when it is due.

## Acceptance criteria (Phase 1)

1. **The dry run before anything changes.** As a System Manager, open `/desk/my-work/tidy`. Approximate
   counts from a read-only SQL rendering of the rules on 2026-10-20: 1,310 open; close ≈ 976
   (finished record ≈ 291 as Done, no longer a prospect ≈ 288, Administrator 286, stale ≈ 110, record
   missing 1); keep ≈ 334, of which ≈ 123 have no assignee. The page's figures are the authority.
2. **Apply, then check:** `SELECT COUNT(*) FROM tabToDo WHERE status='Open' AND allocated_to='Administrator'`
   = 0; `SELECT COUNT(*) FROM tabToDo WHERE custom_triage_batch LIKE 'triage-%'` equals the page's
   "closes" figure; no open ToDo created by triton@ in the last 90 days was closed:
   `SELECT COUNT(*) FROM tabToDo WHERE owner='triton@sapphirefountains.com' AND custom_triage_batch IS NOT NULL AND creation > NOW() - INTERVAL 90 DAY` = 0.
3. **Undo works:** Undo on the batch reopens exactly that many (the toast says how many), and a record's
   Assign To avatars come back.
4. **The reminder holds:** the morning after the triage, `SELECT COUNT(*) FROM tabToDo WHERE
   description LIKE 'Follow up with %' AND creation >= CURDATE()` is no more than the number of
   prospects whose window genuinely ran out, and none is allocated to Administrator.
5. **Assign someone from a record's sidebar** with a comment: their email's subject reads "<record
   title>: <comment>", its button opens the record, and on the record they see the banner with Done,
   Snooze, Reassign and Reply.
6. **Done with a note** closes it, the record's timeline shows the note, the assigner gets a bell entry,
   and the record's Assign To avatar for that person is gone.
7. **Complete a Task** with two open assignments, from the form: both close; set it back to Working and
   assign someone new, re-save, set Completed again: the new one closes then, not before.
8. **Mark an Opportunity "Closed Won"**: its open assignments stay open.
9. **Unclaimed**: a Triton follow-up appears for every desk user on `/desk/my-work/unclaimed`; Take it
   moves it to that person's list and off the tab.
10. **The sidebar count** equals the page's Overdue + Today + No due date for the same person; snoozing
    an item lowers it by one.
11. CI: `test_my_work` and `test_s3_page_css` pass.

## Rollback

- **Triage:** Undo per batch (`/desk/my-work/tidy`), or
  `UPDATE tabToDo SET status='Open', custom_triage_batch=NULL WHERE custom_triage_batch LIKE '<batch>|%'`
  followed by re-saving the affected records' assignments (or `ToDo.update_in_reference()` per row) to
  rebuild `_assign`.
- **Everything else:** revert the release. Nothing else stores data: the page, banner, badge, email and
  auto-close read and write ordinary ToDos. Reverting leaves `ToDo.custom_triage_batch` in place until
  a deletion patch removes it (fixtures are create/update only).

## Explicitly NOT in this work item

- **Replacing ToDo, or a parallel assignment doctype.** Rejected on 2026-10-20.
- **Accept/decline and an AI one-line brief.** Not chosen.
- **Changing Assignment Rules, the crew mirrors or Triton.** They keep writing ToDos as they do;
  Triton's missing assignee is surfaced (Unclaimed), not fixed at its source, which is outside this repo.
- **Booked hours ("in the field") in the Assign To picker.** Phase 2 shows time off and work
  restrictions only; the planner's availability engine is the source if it is wanted.
- **A daily auto-close sweep.** Auto-close is event-driven; the triage covers the past. A status written
  with a raw `frappe.db.set_value` (no `on_change`) is a known gap, and the next triage catches it.
- **Prospects with no reminder assignee.** The reminder now skips them instead of assigning
  Administrator; setting `custom_reminder_assignee` on those customers is a data task for sales.
