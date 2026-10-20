# Task Enhancements

Overrides ERPNext's core **Task** controller (via `override_doctype_class`) to keep the task tree valid, and adds an in-form descendant-task table. Since v1.696.0 it is also the home of **My Work** (WI-081), the replacement for ERPNext's ToDo experience -- see below.

> The standalone **Hierarchical Task View** desk page was retired in v1.623.0: Frappe v16's TreeView builds its own page and calls `get_tree_nodes`, so the page's data feed never ran and the tree never loaded. The Project Planner covers browsing a project's tasks. `patches/custom_pages_s1_navigation.py` deletes the Page record and every workspace or sidebar row that pointed at it.

> Recurring-task generation is **not** here — it lives in the repo-root [`tasks.py`](../../erpnext_enhancements/tasks.py) (`generate_next_task`, wired via `Task` `on_update`).

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `assignments.py` | My Work's shared rules, frappe-free: when an assignment is due (a ToDo dated its own creation day has **no** due date -- `assign_to.add` stamps `nowdate()`), its bucket, what it asks, the email subject, which record states are finished (`FINISHED_STATES`), and the backlog triage's rules | `due_date`, `bucket`, `the_ask`, `paragraphs`, `email_subject`, `finished_value`, `just_finished`, `triage_rule` |
| `assignment_alerts.py` | My Work's alerts (Phase 2): the weekday 06:40 job (a bell on items due today, the assigner told once at 2 days late, the morning summary email for everyone not emailed the Morning Briefing) and the text for a High-priority assignment. Site-config kill switches per step | `daily_run`, `nudge_due_today`, `escalate`, `send_digests`, `digest_sections`, `on_todo_insert`, `send_high_priority_text` |
| `recurrence.py` | Phase 3: a ToDo with `custom_repeat` makes its next one when it changes to Closed (not Cancelled; not when auto-close or the triage closed it; not when its record is finishing or finished), due one step after its due date (`assignments.next_occurrence`) | `on_update`, `make_next`, `mark_finishing` |
| `doctype/assignment_checklist_item/` | Phase 3: **Assignment Checklist Item**, the child table behind `ToDo.custom_checklist` (step, done, done by, done on) | `AssignmentChecklistItem` |
| `auto_close.py` | Closes a record's open assignments when the record moves into a finished state (doc_events `on_change`), and cancels them when it is cancelled (`"*"` `on_cancel`). Never raises | `on_change`, `on_cancel`, `close_todo`, `close_open_assignments` |
| `backlog_triage.py` | The one-time backlog clean-up behind My Work's **Tidy the backlog**: dry run, apply (background job, each ToDo stamped `custom_triage_batch`), undo | `preview`, `apply`, `undo`, `batches` |
| `page/my_work/` | The **My Work** desk page (`/desk/my-work`, `/by-me`, `/unclaimed`, `/calendar`, `/team`, `/tidy`) and its stylesheet | `my_work.js`, `my_work.css` |
| `doctype/task/task.py` | Core Task override + child-task HTML feed | `Task(BaseTask)` (`validate`, `before_save`, `on_update`, `update_status`); whitelisted `get_child_tasks_html`; helpers `_get_all_descendants`, `_build_task_tree_html` |
| `doctype/task/task.js` | Form script: render the child-task tree on refresh | `frappe.ui.form.on("Task", {refresh})`, `add_toggle_functionality` |

## The Task override

`Task` subclasses `erpnext.projects.doctype.task.task.Task` and is registered in `hooks.py`:

```python
override_doctype_class = {"Task": "erpnext_enhancements.task_enhancements.doctype.task.task.Task"}
```

- `validate` — when the Task is being saved into a finished status, marks it as finishing for the request (`recurrence.mark_finishing`) **before** calling `super().validate()`. ERPNext's `validate_status` closes every open assignment on a Task becoming Completed, before the Task's row says so; the mark stops a repeating assignment closed that way from making a next one (v1.699.0).
- `before_save` — promotes the task's `parent_task` to a **group** (`is_group=1`, saved with `ignore_permissions`) so the NestedSet tree stays valid. It does **not** chain `super()`.
- `on_update` — simply calls `super().on_update()`.
- `update_status` — never flips a finished Task (Completed, Canceled, Cancelled, Invoiced, Template) to Overdue.

## Data flow

- **In-form child table:** `get_child_tasks_html` walks all descendants of a group task, batch-resolves assignees from ToDo rows, rebuilds the hierarchy in memory, and returns styled collapsible HTML into the form's `custom_child_tasks_table` HTML field. `task.js` renders it on refresh.

## `hooks.py` touchpoints

- `override_doctype_class` — Task (above).
- `doctype_js["Task"]` includes `task_enhancements/doctype/task/task.js` (alongside the comments + gantt scripts).
- `doc_events["Task"]` adds before_save/after_insert/on_update/on_trash handlers from `script_migrations.task` and `tasks.generate_next_task` (separate from the override).
- `fixtures`: `Task-custom_comments_tab`/`field` and `Task-custom_create_child_task_btn` Custom Fields.

## Gotchas

- `task.js` / `task.py` contain some left-in debug logging.
- Descendant collection in `_get_all_descendants` is **one query per node** (not batched) — watch performance on very deep trees.
- The `custom_child_tasks_table` HTML field is assumed to exist on Task but is not in the `fixtures` list.

## My Work (WI-081 Phase 1)

ERPNext's ToDo list shows "Assignment for Opportunity CRM-OPP-2026-00187", a notification opens the ToDo form, and the record you were actually assigned to is a link at the bottom of it. My Work replaces that experience **on top of Frappe's own assignments**: an assignment is still a ToDo made by the sidebar's Assign To, an Assignment Rule, the crew mirrors (`project_enhancements/crew_sync.py`, `sapphire_maintenance/visit_crew.py`), Triton or the AI follow-up tool, and closing one still goes through the ToDo controller, which rebuilds the record's `_assign`. Nothing is stored anywhere else; the one new field, `ToDo.custom_triage_batch`, only marks what the triage closed so it can be undone.

| Surface | Where | What it does |
|---|---|---|
| **My Work page** | `page/my_work/` -> `api/my_work.py` | Three tabs (assigned to me, assigned by me, unclaimed), rows that lead with the record, buckets Overdue / Today / No due date / This week / Later, and Done (with a note for the record's timeline; on a Task it also completes the Task), Snooze, Reassign, Reply, Take it, Nudge and Take back on each row. Drag a row to another bucket on a desktop. |
| **The banner** | `public/js/global_enhancements/my_work.js` | "Assigned to you by James · due Fri · *what they asked*" with Done, Snooze, Reassign and Reply, at the top of any record you are assigned on. On a ToDo's own form it says which record the to-do is for, with a button to open it. |
| **Sidebar count** | same file | A "My Work" row after the bell, counting what is overdue, due today or undated. Snoozing an item takes it off. |
| **The email** | `fixtures/notification.json` "New ToDo Created", `email_style.ee_assignment` | Subject "Smith Residence rebuild: send revised quote"; the button opens the record, not the ToDo form. |
| **Auto-close** | `auto_close.py` | A record moving into a finished state closes its open assignments (`assignments.FINISHED_STATES`). |
| **Assign To dialog** (Phase 2) | `public/js/global_enhancements/my_work.js` | "What do you need?" first and empty (Frappe pre-filled the record's title), quick dates, Priority from the record, and each picked person's load and time off. Subclasses Frappe's `AssignToDialog`, so the list view's bulk Assign To gets it too. |
| **Record sidebar** (Phase 2) | same file | Under the Assigned To avatars: each person, what was asked, when it is due. |
| **Morning summary, nudges, escalation, texts** (Phase 2) | `assignment_alerts.py` | See the file map. The Morning Briefing carries the same summary for whoever is emailed it (`api/briefing._user_todos`). |
| **Team queues** (Phase 3) | `api/my_work.assign`, Unclaimed & queues tab | A team is a Role named "… Team"; a queue item is a ToDo with that Role in its native `role` field and no assignee. Members get a bell; the first to Take it owns it. |
| **Team board** (Phase 3) | `/desk/my-work/team`, `api/my_work.get_team_board` | Leads (`LEAD_ROLES`): a column per person and per queue; drag a card to hand it over. |
| **Repeats and checklists** (Phase 3) | `recurrence.py`, `ToDo.custom_repeat`, `ToDo.custom_checklist` | Set in the dialog or on a row; ticked on My Work, the banner or the ToDo form. |
| **Calendar** (Phase 3) | `/desk/my-work/calendar`, `api/my_work.get_calendar` | Dated assignments only, on a month grid; drag to another day. |
| **Tidy the backlog** | `backlog_triage.py`, `/desk/my-work/tidy` | System Manager only. A dry run per rule, then Apply, then Undo per batch. |

Rules worth knowing before you change anything here:

- **A ToDo's date is usually not a due date.** On 2026-10-20, 1,141 of production's 1,310 open ToDos were dated the day they were made. `assignments.due_date` treats that stamp as no due date; anything else, including a snooze, is a real date. Reassign carries a date over only when it is a real one.
- **"Closed Won" and Task "Pending Review" are not finished.** Winning is when the next piece of work starts, and Pending Review is waiting on the reviewer.
- **Auto-close fires on the change *into* a finished state**, through `on_change`, which v16 runs after a `db_set` too (ERPNext's `set_status(update=True)` moves a PO or Material Request without any `on_update`). An assignment made on an already-finished record survives later saves.
- **Unassigned is not junk.** Triton files every call follow-up with no assignee and no record. They are on the Unclaimed tab for every System User, and the triage closes one only when nobody has touched it in 90 days.
- **The sidebar count's class is `.ee-my-work-count`, never `.notification-count`**: Frappe writes the bell's unread number into every element with that class.
- **Email policy (Phase 2):** the "New ToDo Created" Notification fires for **High** priority only, and `hooks.notification_skip_email_types` holds `"Assignment"`, so Frappe never emails an Assignment bell entry. Everything else reaches people in the bell and the next morning's summary. Before this, most assignees got two emails per assignment (231 + 78 in the 30 days to 2026-10-20).
- **Repeats never fire on the day they are made, and never already overdue**: `next_occurrence` steps whole periods from the due date to the first one *after* today. A next one dated today would equal its creation day and read as having no due date.
- **Done on a Task completes the Task** (v1.699.0), with `completed_on` today and `completed_by` whoever pressed Done; ERPNext then closes the other assignees' assignments, as it does for any completion. Not for a repeating assignment (`assignments.done_completes_task`). A Task that depends on one not yet Completed or Cancelled **refuses the Done** with the blocking Task named, before anything changes (Nik's call: 59 of 179 open Task assignments were in that state on 2026-10-20). Only ERPNext's spelling "Cancelled" lets a dependency through; this site's "Canceled" still blocks, because ERPNext's own check reads only its own spelling.
- **Escalations are said once**, marked on the row (`ToDo.custom_escalated_on`), not in redis, which every deploy flushes.
- **The customer inactivity reminder** (`script_migrations/customer.py`) counts a closed follow-up as following up, and no longer writes reminders to Administrator, Guest or a disabled user -- without both, the triage's closures would have been rewritten the next morning.

Tests: `tests/test_my_work.py` (bench-free, own CI step), and `tests/test_s3_page_css.py` holds the page's stylesheet to the design-system rules.

