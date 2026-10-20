# WI-081: My Work — reimagine ToDos and assigning, on top of Frappe's own assignments

**Phase:** 2   **Type:** APP_CODE   **Size:** L (three phases; Phase 1 M, shipped v1.696.0)
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

### Phase 2 — assigning and alerts (not started)

- Assign To dialog: quick dates (Today / Tomorrow / Friday / Next week) and a "What do you need?" line.
- Load-aware picker: each person's open and overdue counts, and off/in-the-field from crew availability
  and time-off.
- Daily digest folded into the morning briefing (`api/briefing.py`); single emails only for High.
- Due-date nudge to the assignee; escalation to the assigner after N days overdue.
- SMS for High/Urgent through the existing Twilio path.
- Better record sidebar: who, what was asked, due, status — not bare avatars.
- Frappe's own Assignment notification email and the Notification fixture both email the assignee on a
  new assignment; decide which one stays.

### Phase 3 — teams and structure (not started)

- Team queues on ToDo's native `role` field (unused on production: 0 open ToDos carry one, 2026-10-20).
- Team load board (manager view; drag between people).
- Recurring assignments; checklists inside an assignment; calendar view (ToDo is already
  calendar-enabled here and carries a Google event field).

## Preconditions

- `bench migrate` has run the v1.696.0 deploy (adds `ToDo.custom_triage_batch`; the triage refuses to
  apply without it and says so).

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
- **A daily auto-close sweep.** Auto-close is event-driven; the triage covers the past. A status written
  with a raw `frappe.db.set_value` (no `on_change`) is a known gap, and the next triage catches it.
- **Prospects with no reminder assignee.** The reminder now skips them instead of assigning
  Administrator; setting `custom_reminder_assignee` on those customers is a data task for sales.
