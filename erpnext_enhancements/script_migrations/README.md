# `script_migrations/` — Ported Client/Server Scripts

This package holds **Python ports of legacy Frappe Client Scripts / Server Scripts** that used to live in the database. Porting them into app code makes them version-controlled, reviewable, and deployable with the rest of the app. As each port lands, the original DB-stored script is disabled.

Almost everything here is wired through `doc_events` / `scheduler_events` in [`hooks.py`](../hooks.py). The companion *client-side* ports live in `public/js/*_migrated_scripts.js`.

## Function → hook map

| Function | `hooks.py` wiring | What it does |
|---|---|---|
| `task.calculate_project_elapsed_time` | `Task` `before_save` | When a task is Completed/Cancelled and it is the last open task on its project, marks the Project **Completed** and stamps `custom_total_time_elapsed`. |
| `task.sync_task_to_google_calendar` | `Task` `after_insert` | On task creation, creates an all-day Frappe **`Event`** linked to the hard-coded shared Google Calendar, which Frappe's own `Event` `after_insert` hook then pushes to Google; adds a success/failure comment. Skips silently when that calendar is missing or not pushing. |
| `task.sync_project_dates_from_tasks` | `Task` `on_update` **and** `on_trash` | Recomputes Project `expected_start_date` / `expected_end_date` as the min/max of its tasks' dates; writes via `db_set` only when changed. |
| `project.remove_open_status` | `Project` `before_save` | Coerces Project status `Open` → `Active` (sets `doc.status` directly, not `db_set`) and msgprints. |
| `project.update_elapsed_time_daily` | `scheduler_events.daily` | Bulk-refreshes `custom_total_time_elapsed` for all non-closed Projects; commits. |
| `opportunity.stamp_won_date` | `Opportunity` `before_save` | On `Closed Won`, stamps `custom_date_closed_won` if unset. |
| `opportunity.validate_ranks_on_won` | `Opportunity` `before_save` | On `Closed Won`, throws unless Scope/Schedule/Budget ranks are each 1/2/3. |
| `opportunity.update_lead_status` | `Opportunity` `before_save` | When a new Opportunity comes from a Lead, marks the Lead **Converted**. |
| `customer.set_last_activity` | `Customer` `before_save` | Stamps `custom_last_activity_date = today` on every save. |
| `customer.customer_inactivity_reminder` | `scheduler_events.daily` | Creates Open follow-up ToDos for **Prospect** customers (`custom_prospect` checked) past their reminder window, allocated to the account's `custom_reminder_assignee` (the account executive) or the document owner when unset — per-customer `custom_reminder_days`, falling back to the global `inactivity_threshold` from the **Sales Activity Settings** Single; `-1` opts a customer out; skips non-prospects and customers with an existing Open ToDo; commits. |
| `address.set_full_address` | `Address` `before_save` | Builds comma-joined `custom_full_address` from line1/line2/city/state/pincode. |
| `address.validate_coordinates` | `Address` `before_save` | Gates the user-editable `custom_latitude`/`custom_longitude`. Throws on half a pair (every map reads a zero axis as *no point*, so it would save cleanly and locate nothing) and on out-of-range values (almost always a transposed pair). Trims to 6 dp and stamps a blank provenance as `Manual`, so a point written by an import or the API is not discarded by the next address edit. |
| `debug.run_debug_query` | none (whitelisted endpoint) | Developer helper returning DocLink rows pointing at a given Customer. |

> The fifth `Opportunity` `before_save` entry, `crm_enhancements.api.sync_opportunity_tags`, lives in the [CRM Enhancements](../crm_enhancements/README.md) module, not here.

## Gotchas

- **`opportunity.update_lead_status` — `lead` vs `party_name`** (CHANGELOG 0.2.8): the Opportunity doctype has no `lead` field — the Lead is referenced via `party_name` when `opportunity_from == "Lead"`. Guarding on `doc.lead` raised `AttributeError` on *every* Opportunity save; the guard now checks `opportunity_from == "Lead" and party_name`.
- **`project.remove_open_status`** intentionally sets `doc.status` directly rather than `db_set`, because in `before_save` an ORM save would overwrite a `db_set`.
- **`task.sync_task_to_google_calendar`** hard-codes the sync user email and shared calendar ID as module constants (environment-specific to Sapphire Fountains).
- **`task.sync_task_to_google_calendar` — there is no `insert_event` in Frappe v16** (CHANGELOG 1.262.0). It used to call `frappe.integrations.doctype.google_calendar.google_calendar.insert_event` with a `{"doctype": "Google Calendar Event", ...}` dict. Neither exists: v16's module-level event functions are `insert_event_to_calendar` (Google → Frappe) and `insert_event_in_google_calendar` (Frappe → Google), and the carrier doctype is `Event` — "Google Calendar Event" never was a doctype. Every Task created between 2026-06-08 and the fix raised `AttributeError` into the throttled log (547 rows, invisible to users). The supported outbound path is a real `Event` with `sync_with_google_calendar` set and `google_calendar` linked to a **`Google Calendar` record name**, not a raw `c_…@group.calendar.google.com` id — passing the id fails Frappe's own `frappe.db.exists` guard and returns without pushing *or* erroring.
- **A dormant calendar is a silent skip, by design.** If the `Google Calendar` record is missing, or `enable`/`push_to_google_calendar` are off, the sync returns without a log row and without a comment: that is site configuration, not a per-Task failure, and one row per Task created site-wide is the exact noise `log_error_throttled` exists to stop. The **"Google Calendar (Task sync)"** tile in [`api/integrations_health.py`](../api/integrations_health.py) is what reports it — it counts Tasks created against Events pushed in the same 24 h window, which is the number that would have caught the two-month outage. Keep the Error Log title `"Google Calendar Sync Failed"` stable; that tile matches on it.
- **Fresh-DB safety:** related contact-sync code (`sync_contact.py`) uses `getattr(...)`/`has_column(...)` guards because these `doc_events` fire during ERPNext's test bootstrap before the app's custom fields exist. Keep that pattern when extending these scripts.
