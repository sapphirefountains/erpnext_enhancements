# CRM Enhancements

Customizes the **Opportunity** doctype (Opportunity→Project conversion + tag sync) and ships the realtime **Sales Pipeline** board. Converting an Opportunity to a Project also triggers a Google Drive folder tree — that machinery now lives in the [Google Drive module](../google_drive/README.md) (`crm_enhancements.api` calls `google_drive.drive_utils`).

## File map

| File | Purpose | Key functions | Wiring |
|---|---|---|---|
| `api.py` | Opportunity→Project conversion + tag sync | `enqueue_project_creation` (whitelisted), `create_project_from_opportunity_background` (provisions the Drive tree via `google_drive.drive_utils`), `sync_opportunity_tags`, `sync_opportunity_tags_for_existing` (whitelisted) | `sync_opportunity_tags` → `Opportunity` `before_save` |
| `handoff.py` | The Closed-Won hand-off gate (PRO-0204, v1.250.0): a Project cannot be created from a Closed-Won Opportunity until a hand-off meeting is **booked** (v1.263.0; it was "recorded"), and the buttons that book it — a real calendar `Event` and an emailed invite | `handoff_block_reason` / `throw_if_handoff_missing` (the gate predicate), `schedule_handoff_meeting`, `mark_handoff_complete`, `skip_handoff` (System Manager + mandatory reason), `resolve_attendees` / `attendee_options`, `schedule_project_meeting`, `handoff_state` / `project_handoff_context` (whitelisted); `billing_terms` / `billing_recipients` / `billing_notice_context` (the step-4 note to Billing, v1.327.0); `stamp_handoff_gate`, `launch_deadline_for_project` | `stamp_handoff_gate` → `Opportunity` `before_save` (after `stamp_won_date`); enforced by `process_steps.enforce_handoff_gate` on `Project` `before_insert`; see below |
| `doctype/accounts_lead`, `accounts_opportunity`, `accounts_project`, `lead_source`, `opportunity_contributor`, `value_stream`, `value_streams` | CRM child tables / masters ported from DB-only custom DocTypes (v0.7.0) so fresh installs can import the Custom Field fixtures that reference them | stub controllers | synced on migrate |
| `doctype/sales_activity_settings/…py` | Single: global `inactivity_threshold` (days) — fallback reminder window for `script_migrations.customer.customer_inactivity_reminder` (ported v0.8.0); and `stage_probabilities`, the forecast probability per Opportunity stage (form kit P5.5, v1.627.0) | `SalesActivitySettings.validate`: one row per stage, 0–100 | synced on migrate |
| `doctype/opportunity_stage_probability/…py` | Child table of Sales Activity Settings: a stage and its default probability (v1.627.0) | `OpportunityStageProbability` (pass) | synced on migrate |
| `project_copy.py` | What a new Project copies from its Opportunity beyond the hand-off mapping (v1.633.0, Nik's field review): Primary Address (the job site), Billing Address (ERPNext's `customer_address`, relabeled) to `custom_billing_address`, and the budget fields. Never overwrites, never copies a blank | `SITE_AND_BUDGET`, `copy_site_and_budget` | called by `api.create_project_from_opportunity_background` and `opportunity_enhancements.make_project` |
| `opportunity_probability.py` | Form kit P5.5 (v1.627.0): a deal entering a stage takes that stage's default probability unless it was set by hand; typing a probability ticks *Probability Set by Hand* (`custom_probability_by_hand`, created in code); unticking returns to the default. On Hold has no default (out of the forecast) | `apply_stage_probability` | `doc_events` Opportunity `validate` |
| `party_link_cleanup.py` | **One-off** (v1.575.0): removes the Contact/Address links the directory's old Link Existing fanned out, as reviewed on prod on 2026-10-06 (`WRONG_LINKS`), plus the test/placeholder links on Nikolas Bradshaw's own staff contact. Dry run by default; removes a link only from the row the review saw (matched by value and audited row name), and any record that no longer matches the review stops the whole run before the first save; prints what only the office can decide (`FOR_THE_OFFICE`), to act on only after the cleanup has been applied. Rehearse on the mirror: `bench --site erp.local execute erpnext_enhancements.crm_enhancements.party_link_cleanup.run` | `run` | `bench execute` only |
| `party_mirrors.py` | Form kit P3.2 (v1.669.0): keeps Opportunity's hidden `contact_person` (what ERPNext's *Create > Quotation* and the Prospect row copy) equal to the visible Primary Contact. A new record takes the visible field, or copies a hidden-only value up. On a saved record the field changed in the save wins (an emptied Primary Contact empties both, though ERPNext's `map_fields` usually refills it from a Customer's own Primary Contact first) and a hidden field changed by code to a value is copied up; **a save that changes neither leaves the pair as it is**, so rows waiting for the approved fix are not rewritten by routine saves. Changing Contact Person also refreshes `contact_display` / `contact_mobile` / `contact_email` from the new Contact (v16 fetched the email from the old one in `_validate_links`), or empties them. Copies only to a Contact that exists; skips fields the meta lacks; never raises (traceback to the Error Log against the record). Not the address pair: `customer_address` is the Billing Address since v1.633.0, a different fact from the job site. Which pairs: the Party block's `mirrors` in `form_kit/blocks.py` | `decide`, `plan_stored`, `keep_in_step` | `keep_in_step` → `Opportunity` `before_save`, first, ahead of `sync_contact.refresh_primary_contact_copies`; `report/party_mirror_preview/` |
| `report/party_mirror_preview/` | **Party Mirror Preview** (v1.669.0): the dry run for the rows saved before the rule, or written past `save()`: each Opportunity whose two contact fields disagree, both values, and what `plan_stored` would make them. Writes nothing and has no apply button; compares in Python, because MariaDB's PAD SPACE makes a SQL `<>` blind to a trailing space. On 2026-10-14 production had 9 pairs set to different contacts, 20 with only the hidden one set and 11 with only the visible one. **The fix waits for Nik's approval of this list**, then ships as a patch running the same rule | `execute`, `collect` | System Manager, Sales Manager |
| `party_naming_rules.py` | What a party-linked record should be called — Project, Opportunity, Address. **No Frappe, no I/O**; unit-tested in CI against verbatim production records | `check`, `audit`, `in_scope`, `party_names`, `party_matches`, `matches_any`, `separator_form`, `collision_groups`, `summarise` | none — advisory, no validate hook |
| `party_naming.py` | The reads behind it, plus the whitelisted entry point the three forms call | `read_rows`, `attach_customer_labels`, `attach_address_parties`, `audit_doctype`, `check_record` (whitelisted) | `public/js/party_naming_advisor.js` on all three forms; `report/party_naming_audit/`; `assistant_tools/party_naming_check.py`; `project_naming_compliance_pct` (Operations) and `opportunity_naming_compliance_pct` / `address_naming_compliance_pct` (Sales) on the nightly snapshot |
| `lead_triage.py` | Inbound Lead ownership and the speed-to-lead SLA (v1.502.0, TASK-2026-01473): named owner or `Sales Team` rotation, a working-time first-response deadline, a ToDo, remind-then-escalate once each, and the one "has this Lead been answered" rule the Speed-to-Lead widget shares. See [`docs/lead-triage-runbook.md`](../../docs/lead-triage-runbook.md) | `prepare_inbound_lead` / `assign_inbound_lead` (called by `web_lead.py`), `stamp_first_response`, `sweep_first_response_sla`, `backfill_first_responses`, `RESPONSE_EXISTS_SQL` | `Communication` `on_update`; `scheduler_events.cron` `*/10`; `after_migrate` |
| `website_cleanup.py` | Accept a bare domain in any URL field (v1.324.0) — prefixes `https://` before frappe's URL validation reads it. Shared with the QuickBooks sync, which met the same defect first | `normalize_website` (the whole rule, pure), `heal_url_fields`, `add_missing_scheme` | `before_validate` on Lead / Customer / Opportunity / **Supplier / Company** (the last two are not CRM — they are here because they carry the same Property Setter); `quickbooks_online.core.mapping._heal_invalid_urls` delegates to it |
| `historical.py` | Historical Opportunities (v1.629.0, WI-024): created before 2025-08-01 and nobody's activity in 90 days, stored as `Opportunity.custom_is_historical`, and left out of every won-without-project warning and the Closed-Won KPIs | `refresh_historical_flags`, `active_recently`, `exclude_filters` / `exclude_filter_dict` / `exclude_sql`, `is_historical`, `clear_on_person_status_change` | `scheduler_events.daily`; `clear_on_person_status_change` → `Opportunity` `before_save`; see below |
| `pay_period_reports.py` | Semi-monthly (1st–15th, 16th–EOM) delivery of the "Brian's Closed Won" commission report — moves the report's saved date window and emails the closed period on the 1st and the 16th (v1.232.0) | `run_pay_period_cycle`; `pay_period_bounds` / `previous_pay_period` (generic, reusable) | `hooks.py` `scheduler_events.cron` `"0 7 * * *"`; see below |
| `page/sales_pipeline/*` | TV-friendly realtime funnel board (`/desk/sales-pipeline`, v1.2.0) | `get_pipeline_data`, `check_permission` (whitelisted); `stamp_stage_change`, `publish_pipeline_update` | hooks → `Opportunity` `before_save` / `on_update`; see below |
| `page/opportunity_board/*` | The new Opportunity board (`/desk/opportunity-board`, form kit P6, v1.654.0): stage columns, cards with age, value-stream band, amount, close date and completeness, rows by value stream, the forecast strip, drag (long-press on touch) and the phone's tap-to-move; part B (v1.663.0) the table, timeline and needs-attention views under one filter bar, On Hold folded, snooze, quick filters and saved views; part C (v1.672.0) the side panel (tap to edit, a closing drop's required fields), quick add per column and bulk actions. Pure decisions in `public/js/opportunity_board/board.js` (bundle `opportunity_board.bundle.js` / `.css`) | `get_board` (whitelisted, GET); `snooze` (whitelisted, POST: write permission, the `modified` lock, `db_set` of `custom_snoozed_until` / `custom_snooze_reason`); `bulk_update` (whitelisted, POST: each deal through `form_kit.saver`, its own lock and savepoint, answered deal by deal) | CRM sidebar (`setup/workspace_tweaks.py`); moves save through `form_kit.api.save_step`; see below |

Related client-side code lives in `public/js/crm_enhancements/` (`opportunity.js`, `opportunity_list.js`, `opportunity_kanban_totals.js`, `opportunity_migrated_scripts.js`, `fountain_move_request*.js`, `fountain_move_invite.js`) — see the [public README](../public/README.md#crm-enhancements).

## Commission report pay periods (`pay_period_reports.py`, v1.232.0)

Commission runs on semi-monthly pay periods — **1st–15th** and **16th–end of
month** — and the "Brian Commissions Report" Auto Email Report has described
that rule in its own body text since it was written. Nothing implemented it:
`Brian's Closed Won` is a Report Builder report whose saved filters held a
hand-retyped `custom_date_closed_won > <date>`, and the email went weekly on
Mondays.

**Why this is a job and not configuration.** All three native routes are closed:

- `Auto Email Report`'s dynamic date filters (`from_date_field` / `to_date_field`
  / `dynamic_date_period`) are applied **only when the report type is not Report
  Builder** — `get_report_content` guards them with
  `if self.report_type != "Report Builder"`. They are inert here.
- `Auto Email Report.filters` are **appended** to the report's saved filters, not
  substituted (`Report.run_standard_report`), so the email can never override or
  widen a date already baked into the report.
- `frequency` offers only Daily / Weekdays / Weekly / Monthly. Nothing in that
  vocabulary means "the 1st and the 16th".

So the window lives in the report's saved `json`, which is also what makes the
desk view right: opening the report shows **the period currently in progress**.

**The borrow/restore dance.** The email needs the *finished* period while the
desk view wants the *running* one, and a Report Builder report holds one filter
set. The send briefly points the report at the closed period, renders through
the Auto Email Report doc, and `run_pay_period_cycle` re-applies the running
window immediately afterwards — on every path, including a send that raised.
The cron is **daily** rather than `1,16` for the same reason: a missed tick or a
dead send self-heals next morning instead of stranding the desk report for up to
sixteen days.

**The Auto Email Report doc is deliberately left disabled.** That is what stops
Frappe's `send_weekly` from also firing it; the doc still owns the recipient
list (editable in the desk, no code change), the description and the rendering,
and this module drives it. Do not delete it.

Two behaviours worth knowing: the window is `between`, **inclusive at both ends**
— the `>` it replaced silently dropped deals closed *on* a boundary day, which
is how a $500 opportunity dated 2026-07-16 ended up in neither period — and only
the date rows are rewritten, so an owner/status filter changed in the desk
survives the next tick. Re-runs are guarded by a `DefaultValue` key holding the
last period actually emailed, so re-running the job by hand cannot produce a
second statement for a period already paid.

Ad hoc run: `bench --site <site> execute
erpnext_enhancements.crm_enhancements.pay_period_reports.run_pay_period_cycle`.

## Fountain Move intake (`fountain_move/`, v1.160.0)

Public intake form for the **Cactus & Tropicals** partnership. A customer buys a
fountain at C&T, C&T recommends us to move it, and the customer fills in
[`/fountain-move`](../www/README.md#controller-filenames-hyphens-are-silently-fatal).
The submission lands as a **Fountain Move Request** and converts, in a background
job, into a linked **Customer → Address → Contact → Lead → Opportunity** set.

| File | Purpose |
|---|---|
| `__init__.py` | constants: `INTAKE_FIELD_MAP` (the guest allowlist), `CT_LOCATIONS`, `HONEYPOT_FIELD_NAME`, store-location lookup |
| `intake.py` | the three guest endpoints — `begin_intake`, `upload_intake_photo`, `submit_intake` — plus `gc_orphan_intake_files` |
| `matching.py` | duplicate resolution: which existing party does this belong to? |
| `conversion.py` | the staging-row → five-records engine |
| `photos.py` | File fan-out onto Lead/Customer/Opportunity + Drive mirroring |
| `notify.py` | new / failed / duplicate-review alerts + the daily stuck digest |
| `invites.py` | the desk "Send Intake Link" flow and token attribution |
| `api.py` | desk triage RPC (retry, mark spam, not spam) |

**Why a staging doctype rather than writing the five records inline:** spam never
reaches CRM; a partial failure is resumable rather than duplicating master data
(ERPNext names Customers by `customer_name`, so a rolled-back retry would create
"Jane Doe Residence - 2"); and the original payload survives for audit.

### Ordering constraints in the conversion (all load-bearing)

1. **Customer first** — everything links to it, inserted with
   `flags.ignore_mandatory` because Selling Settings carries no group/territory
   default on this site.
2. **Address before Contact.** Never set `custom_full_address` — the
   `before_save` hook recomputes it, and it is the `title_field`. `country` is
   mandatory with no default.
3. **The Contact's Customer Dynamic Link must be appended BEFORE `insert()`** —
   naming runs before validate, and `custom_full_name_and_role` is built from
   `links[0]`. Use `contacts_ux._insert_customer_link_first`.
4. **Lead after Contact**, carrying `utm_source = "Existing Customer"` *and*
   `customer`, or erpnext's `Lead.before_insert` mints a second, stray Contact
   (`lead.py:103-115`).
5. **Opportunity last, with `opportunity_from = "Customer"` — never `"Lead"`.**
   `api.py:238` maps `party_name` straight into `Project.customer` on the
   Closed-Won hand-off and inserts with `ignore_validate`, which skips
   `validate()` but *not* `_validate_links()`; a Lead id raises inside a
   `try/except log_error` and silently kills the hand-off. Drive provisioning
   likewise only fires for Customer-party opportunities.

### The hand-off gate (`handoff.py`, v1.250.0)

A Project cannot be created from a Closed-Won Opportunity until a hand-off meeting has been
**booked**. Four things about it are easy to get wrong:

1. **It opens on "booked", not "held" — changed in v1.263.0.** The meeting is routinely two
   days out while the project needs to start today, so gating on the completion stamp was
   inviting exactly the falsification the audit was about: tick "held" for a meeting still
   in the diary. Booking cannot be faked into existence without inviting three functions
   and putting it on a calendar, so it is the better thing to gate on. Recording the
   meeting (`mark_handoff_complete`) still closes step 2 and still feeds the SLA report —
   it just no longer blocks anything, and it now has to reach across to a Project that
   probably already exists (`process_steps.record_handoff_on_project`).

2. **The enforcement is on `Project.before_insert`, not `validate`.** `api.py` sets
   `flags.ignore_validate` before inserting (see the ordering constraints above), and
   Frappe's `run_before_save_methods()` returns early on that flag — so a `validate` gate
   would pass review and then silently never fire on the path that creates most projects.
   `before_insert` survives both `ignore_validate` and `ignore_permissions`. `make_project`
   and the background creator refuse as well, but only so the message is readable; the
   `before_insert` hook is the authority.
3. **The gate keys on `custom_handoff_gate_applies`, not on a date.** That flag is set only
   on a genuine *transition* into Closed Won. A date comparison would have been unsafe:
   `script_migrations.opportunity.stamp_won_date` fills a blank `custom_date_closed_won` on
   **any** Closed-Won save, so merely re-saving one of the 227 already-won deals would have
   dragged it into the gate. `patches.ensure_handoff_gate_fields` writes an explicit `0`
   across that backlog so "exempt" is a stored fact, not an absence.
4. **Skipping is allowed; silence is not.** `skip_handoff` is System Manager only and
   requires a written reason, which lands on the record and on the Project's step 2 row
   prefixed `[SKIPPED]` so it reads as a skip in the tracker and the compliance report
   rather than as a completion.

Attendees come from **ERPNext Enhancements Settings → Hand-Off Attendee Roles** (an explicit
email wins, else the holders of a Role; Sales additionally picks up the deal owner) —
configuration, so changing who attends is never a deploy. That resolution is only the
*prefill*: the dialog's three attendee fields are `MultiSelect`, autocompleting over
`attendee_options` (every desk user plus every configured address) while still accepting
anything typed, so a one-off attendee needs no configuration change. `_dedupe()` on the server
drops anything that is not an email address, so the dialog flags what the browser's regex can
before booking, and both schedule endpoints return the rest as `not_invited`, which the dialog
shows after booking. Note `Event
Participants` marks both reference fields `reqd`, so a group address like `production@`
cannot be a participant row at all; those attendees get the invite email, which is the part
that matters.

The buttons live on the Opportunity's **Hand-Off Process** tab, rendered into the
`custom_process_progress` HTML field rather than added to the form toolbar
(`public/js/crm_enhancements/opportunity_handoff.js`) — they are steps in a process, and
they read as one next to the step they advance. Since v1.286.3 that is literal: each button
renders **inside its step's box** in the bar — the meeting buttons in step 2, *Create
Project in PM System* in step 3 — mapped by step *number*, so the mapping still holds when
the bar swaps the derived three steps for the linked Project's real tracker rows. A button
whose step is missing from those rows falls back to a row beneath the bar rather than
disappearing. The Closed-Won prompt
(`create_project_prompt.js`, global) asks for whichever of those steps is next, and shares
the booking dialog with the tab; that is why `handoff_meeting_dialog.js` loads from
`erpnext_enhancements.bundle.js` rather than as a `doctype_js` — the prompt also fires on
the Kanban board and the list view, where no form script exists.

### Attribution, and a schema trap

`Lead.source` and `Opportunity.source` **do not exist** — erpnext v15 renamed the
field to `utm_source`, which points at the separate `UTM Source` taxonomy. Three
Property Setters had been enforcing `reqd` on the missing field since, silently
doing nothing; `patches.drop_orphan_source_property_setters` removes them.
Attribution lives in `custom_lead_source` (Link → `Lead Source`) on Customer,
Lead and Opportunity. `utm_source` is spent solely on the stray-Contact
suppression above.

### Before enabling the public form

It is the app's only unauthenticated write path, so the pre-flight is not
optional:

1. `Lead Source: Cactus & Tropicals` and `UTM Source: Existing Customer` exist
   (seeded by patch).
2. Turnstile site key **and** secret set — the Settings controller refuses to
   publish the form without the secret.
3. Maps key (optional) restricted by HTTP referrer. Blank ships manual address
   fields, which is a perfectly good state.
4. `fmr_default_owner` set. Deliberately not guessed by the seed patch — a wrong
   guess routes real customers to the wrong person, so conversion fails loudly
   instead.
5. **Confirm the client address is real.** `auth.py:62-70` takes the first
   `X-Forwarded-For` entry unconditionally, so every IP-keyed rate limit is only as
   good as the edge. The chain is GCLB → nginx → bench, and nginx's realip file
   (`infra/configs/nginx-realip.conf`, on the VM since 2026-08-03) makes it the real
   caller, spoof-proof. `bench --site <site> execute
   erpnext_enhancements.utils.client_ip.check_client_ip_derivation` should report
   `"proxy": 0`; a daily run of the same check writes an Error Log row if it stops.

## Sales Pipeline page (`/desk/sales-pipeline`)

The wall-TV funnel board from the Jun 9 process meeting. Columns mirror the live
`Opportunity.status` options (meta-driven — a stage rename reshapes the board without a
deploy), plus a green **Won — awaiting project** column (Closed Won with empty
`custom_created_project`, the PRO-0204 Step 1→2 gap) and a muted **On Hold** column.
Cards age by `custom_stage_changed_on` (stamped on every status change; backfilled from
`modified` by the `backfill_stage_changed_on` patch) and "light up" amber/red past the
thresholds in **ERPNext Enhancements Settings → Sales Pipeline Dashboard**. When a field is
unset, `_thresholds` falls back to 45 / 90 days (recalibrated from the original 7 / 14 in
v1.421.0, which put 34 of 39 cards straight to red); a deliberate 0 switches that tier off. The
Opportunity Board reads the same live settings through the same `_thresholds`. The won column
runs a tighter 1/3-day clock to match the unconverted nag.
Refreshes via the `sales_pipeline_updated` realtime event on every Opportunity save,
with a 5-minute poll as kiosk fallback. **TV mode** (`/desk/sales-pipeline/tv`, or the
header button) hides desk chrome and scales type — point the Raspberry Pi at the `/tv`
route. The route *is* the mode: the button routes to `/tv` and `on_page_show` sets TV mode
from the route on every show, so Back from TV mode returns to the board with its navbar and
Forward goes back in. It used to be `tv || state.tv_mode`, which kept the chrome-less view,
with no button to leave it, on the plain board after Back until a reload. Escape out of the
fullscreen the button asked for steps back the same way, and leaving the page ends that
fullscreen. Cards and hand-off chips link with `get_form_link` (/desk paths); an /app href
is a full page load on v16. Access is page-level (shared portfolio display, like the Project Dashboard): a
`Custom Role` record for page `sales-pipeline` wins if present, else any staff role in
`DEFAULT_ROLES`; data is then fetched permission-free so User Permissions can't
silently empty the board.

## The reports' face (form kit P3.3, v1.657.0)

Every script report in this module shows its phones, parties, value streams, money and dates
through the shared report face (`public/js/report_face/`, [docs/app-design-system.md, "The report
face"](../../docs/app-design-system.md#the-report-face)): a phone reads `(801) 555-0100`, a date
`10/14/2026`, a value stream is a chip in its pillar's colors, and an account or a party is its
human name linked with `get_form_link`. Each report's formatter calls
`erpnext_enhancements.report_face.format` first and keeps only its own coloring on top (the legacy
tint, the red gap, the orange untracked hand-off, the severity colors). The columns carry the
markers (`"sf_face": "party", "sf_label": "customer_name"` and so on), so a report's Python says
what each column is.

Two reports' party columns gained a link and kept their value. **Hand-Off Process Coverage**'s
"Customer / Party" still holds `customer_name` (else `party_name`) and links through the row's
`party_id` / `party_type` (`party_name` / `opportunity_from`); an Opportunity with no `party_name`
shows its `customer_name` unlinked. **Party Naming Audit**'s "Party it belongs to" still holds the
title and links through `party_id` / `party_doctype`, which come from
`party_naming_rules.party_record`, beside `party_of` (Project: its Customer; Opportunity:
`opportunity_from` + `party_name`, read for this; Address: the Dynamic Link it is named after).
Both are Data columns of the face's second party shape, so export, sorting and the column search
stay on the name; a Link column would have put the docname in all three.

`tests/test_report_hand_formatting.py` fails the build if any of these reports formats a phone or
a date by hand again, stops calling the face, or leaves a phone, party or value-stream column
unmarked.

## Opportunity Board page (`/desk/opportunity-board`)

Form kit P6 (ERPNext TASK-2026-02774): the board that is to replace the patched stock Kanban.
This first part builds P6.1 (board and cards), P6.2 (rows by value stream), P6.5 (the forecast
strip) and P6.9 (the phone layout). **The stock Kanban, its patches (`ee_kanban.bundle.js`) and
`opportunity_list.js`'s redirect to it are untouched** until P6.10, and the `/sales-pipeline` TV
board stays as it is. Roles: Sales User, Sales Manager, System Manager. It is in the CRM sidebar
under Guided Entry.

- **Data.** One GET, `opportunity_board.get_board`: the open deals (any status but Closed Won,
  Lost, Closed and Converted, the guided start screen's own rule), historical deals left out,
  through `frappe.get_list` so the caller's own permissions decide. Unlike the TV board, which is
  a shared wall display and reads permission-free, this is where a person works their deals.
  Each deal carries its value streams (one bound query on the `Value Stream` child table per 500
  names) and its stage completeness (`form_kit.completeness.completeness_for`, the P4.6 meter).
- **Stages** are the Sales Pipeline page's own (`sales_pipeline._stage_columns`: the live status
  options), then On Hold; Closed Won and Lost are drop targets along the bottom while a card is
  dragged, and choices in the move panel. An open status the options no longer list gets a column
  of its own, so no deal falls out of the totals.
- **Cards** show the value-stream band (one segment per stream, in the pillar's closing stop, from
  `print_style.STREAM_PILLAR` by way of `form_kit/summary.js`), the stream chips, the amount or a
  plain "No amount", the expected close date, days in stage (from `custom_stage_changed_on`,
  counted on the site's date) and the completeness meter. Past the **Sales Pipeline Dashboard**
  thresholds (ERPNext Enhancements Settings, the same ones the TV board reads, 45 / 90 days when
  unset) a card takes an amber and then a red border, and its age chip says "slowing" or
  "stalled". On Hold never glows, as on the TV board.
- **Rows by value stream** (P6.2): Design, Build, Service, Events, Products, Delivery, then any
  other stream, then "No value stream". A deal with several streams is in each of its rows,
  marked "Also in ...". Each row's total counts it in full; the column heads and the overall
  total count it once (Nik, 2026-10-13), so they match the plain board's.
- **Forecast strip** (P6.5): per stage, the deals, their value, and their value weighted by each
  deal's own `probability` (P5.5's stage default or Sales' override); On Hold is left out of the
  weighted figure ("Not forecast"). Then the deals expected to close this month and next, by
  `expected_closing`: their count and value include On Hold deals, their weighted value does not.
  The board caps a probability at 100; a deal with no probability adds nothing to the weighted
  figure.
- **Moving a card.** Drag with a mouse, or with a 300 ms long press on a touch screen (the
  planners' numbers). On a phone (under 768px) the board shows one stage at a time under a row of
  stage tabs, and a tap on a card opens the move panel: tap the stage it goes to. Every card also
  has a **Move** button that opens the same panel, the keyboard's way in. A move between open
  stages or to On Hold saves the status with `form_kit.api.save_step`, carrying the card's
  `modified`; a conflict puts the card back, names who changed the deal and reloads the board, and
  nothing is overwritten. A drop on **Closed Won or Lost saves nothing by itself**: those need the
  ranks or the lost reasons, which the side panel collects first (part C, below). Since part C a
  tap on a card opens the deal in the side panel, on a phone too; the Move button opens the move
  panel.
- **History.** Each view is a route: `/desk/opportunity-board/<stages|streams>`, the phone's
  stage tab as a further segment, and the move panel as `.../move/<name>`, pushed over the view it
  came from, so Back closes the panel before it changes the view.
- **Realtime.** The board refreshes on `sales_pipeline_updated` (published on every Opportunity
  save), debounced, and never while a card is being dragged or saved.

### Forecast check, per stage

The strip's per-stage figures, as SQL. `tests/test_opportunity_board.py` runs this exact query on
an SQLite copy of its test deals and holds the board's figures to it; run it on the site to check
the live board (the board caps a probability above 100, which this query does not).

```sql
select status,
	count(*) as deals,
	sum(case when opportunity_amount > 0 then opportunity_amount else 0 end) as total,
	sum(case when status = 'On Hold' or not opportunity_amount > 0 then 0
		else opportunity_amount * ifnull(probability, 0) / 100 end) as weighted
from `tabOpportunity`
where status not in ('Closed Won', 'Lost', 'Closed', 'Converted')
	and ifnull(custom_is_historical, 0) = 0
group by status
```

### Forecast check, by month

This month and next, by expected close date; put the two months in place of `2026-10` and
`2026-11`.

```sql
select substr(expected_closing, 1, 7) as month,
	count(*) as deals,
	sum(case when opportunity_amount > 0 then opportunity_amount else 0 end) as total,
	sum(case when status = 'On Hold' or not opportunity_amount > 0 then 0
		else opportunity_amount * ifnull(probability, 0) / 100 end) as weighted
from `tabOpportunity`
where status not in ('Closed Won', 'Lost', 'Closed', 'Converted')
	and ifnull(custom_is_historical, 0) = 0
	and substr(expected_closing, 1, 7) in ('2026-10', '2026-11')
group by substr(expected_closing, 1, 7)
```

### Part B: views, needs attention, focus (v1.663.0)

Form kit P6.3, P6.4 and P6.8. The side panel (P6.6), quick add and bulk (P6.7) are part C, below.
Out of scope still: retiring the stock Kanban's patches and the list's redirect (P6.10, waiting on
Nik's sign-off on the board).

- **Three views and a list under one filter bar** (P6.3). The views are the board, a sortable table,
  and a timeline of expected close dates by month (past months marked; deals with no date last).
  The list is Needs attention. Each is its own route: `/desk/opportunity-board/<stages|streams>`,
  `/table`, `/timeline` and `/attention`, with the deal panel at `…/move/<name>` over any of them.
  The filters are page state, not part of the address, so switching views keeps them, and Back
  returns to the view before.
- **Needs attention** (P6.4) lists every open deal that is any of the following:
  - **stale**: past the glow thresholds; On Hold never glows, so it is never stale;
  - **past its expected close date**;
  - **missing an amount**;
  - **missing what its stage asks for**: the P4.6 completeness, the form kit's own lists in
    `form_kit/config/opportunity.py`.

  The list is sorted by value, largest first, with no amount last. Each chip says why the deal is
  there. A snoozed deal is not on it until its date comes. On Hold deals are on it for the last
  three rules: being parked is no reason to have no amount.

  A deal opens in the side panel (since part C).
- **On Hold folded** (P6.8). The column folds into a count by default: 36 of 74 open deals sat
  there on 2026-10-14. "Show them" unfolds it and "Fold" folds it again. A folded column is still a
  drop target, and its deals still count in every total.
- **Snooze** (P6.8) is in the deal panel: "Hide it until" a date, and "Why" if the person wants to
  say.
  - A snoozed deal is hidden from every view and from the attention list until its date. On that
    date it comes back marked **Back from snooze**, until someone wakes it ("Clear the mark") or
    snoozes it again.
  - "Show snoozed (N)" shows the snoozed deals, dimmed and marked.
  - The fields are `custom_snoozed_until` (Date) and `custom_snooze_reason` (Small Text). Both are
    hidden on the form and `no_copy`, in `fixtures/custom_field.json`. Hidden fields are not in
    the guided flow, so the save goes through its own endpoint, `opportunity_board.snooze`
    (POST). That endpoint checks write permission on the deal, takes the same `modified` lock and
    conflict shape as `save_step`, and refuses a date that is not after today or is more than a
    year out.
  - It writes only the two fields, with `db_set`. A focus tool must not be stopped by an unrelated
    validation, nor re-run the stage clock or probability hooks. It adds an Info comment to the
    deal's timeline and sends the board's realtime event.
- **Quick filters** (P6.8): My deals (Opportunity Owner is you), an owner, a value stream (or none),
  "Over $" an amount, and closing this month.
  - The forecast strip, the column totals and the count in the head all reflect the deals the
    filters show. With no filter and Show snoozed on, they are the whole board, which is what the
    SQL checks above measure.
- **Saved views** (P6.8) use the planners' own store, `api/planner_views_prefs.py`, under the
  planner key `board`. That means the caller's `__UserSettings` row, written straight to the table,
  so a deploy's Redis flush cannot lose a view.
  - A view holds the filters, the view, the board's layout and the table's sort.
  - "Last used" is saved a moment after anything changes, and a bare `/desk/opportunity-board` opens
    it. Any other address wins.
  - Named views come from "Save this view" and the Saved views list. Up to 20 per person, and saving
    a name again replaces it.
  - The board's views are gated by read permission on Opportunity, not a planner role.

### Part C: the side panel, quick add, bulk (v1.672.0)

Form kit P6.6 and P6.7, on P4.4's side panel (`public/js/form_kit/ui/side_panel.js`: the guided
page's own record editor in the planner kit's drawer; see [`form_kit/README.md`](../form_kit/README.md#one-editor-two-shells-p44)).
The panel's files load the first time one opens (`OB_PANEL_ASSETS`); if they never arrive, a deal
opens the normal way and a closing drop opens the guided Close step, as before part C.

- **Every panel is a route over any view**, pushed and tagged in `history.state` (`ob_side`), so
  Back closes it (the router's own popstate; the drawer pushes nothing of its own), Forward reopens
  it, and a reload on its address reopens it:

  | Address | Panel |
  |---|---|
  | `…/edit/<name>[/<step>]` | the deal, one step at a time (a step change replaces the entry) |
  | `…/closing/<name>/<stage key>` | a drop on Closed Won or Lost: that stage's required fields |
  | `…/new/<stage key>` | quick add in that column's stage |
  | `…/bulk/<stage\|owner\|close_date\|snooze>` | a bulk action on the selected deals |

  A panel for a deal that has left the board (a closing move), a stage that is gone, or a bulk
  action with nothing selected puts the view back in its place, as the move panel does.
- **Tap to edit** (P6.6): a card, or a row of the table, the timeline or the attention list, opens
  the deal for editing. Closing an edit that saved something offers Undo.
- **Required fields on a drop** (P6.6): Closed Won asks for the three ranks, Lost for the lost
  reasons, competitors and detail (the Set as Lost dialog's fields). The move is saved only when the
  person commits, together with those fields and locked on the card's `modified`; until then the
  card stays put, marked "Moving to …". Cancel, Esc or Back drop what was typed. A refusal keeps the
  panel open with the reason; a conflict asks, with the guided page's chooser. The Closed-Won
  project prompt and the hand-off gate's stamps come from the ordinary save, as for any move into
  Closed Won.
- **Quick add** (P6.7): the "+" on each open column and On Hold asks for the account, a name, the
  value streams and a summary (the config's `quick`), creates through `form_kit.api.create_record`
  with its `client_id`, in that stage, and then shows the new deal in the panel.
- **Bulk** (P6.7): "Select" shows a box on every deal and a bar along the bottom. **Change stage**
  (a closing stage asks for its required fields first; the ranks given fill only a deal's blank
  ranks), **Reassign**, **Set close date** and **Snooze** go through `opportunity_board.bulk_update`
  (POST, up to 100 deals): every deal through the path a single change takes
  (`form_kit.saver.save_step`, `saver.declare_lost`, or `snooze`), with write permission and the
  `modified` lock checked per deal and a savepoint per deal, so one refusal never undoes the others.
  The answer is deal by deal; what did not change stays selected, with its reason. The selection
  is kept for the tab (`sessionStorage`, guarded), so a reload on a bulk address finds it.
  - A bulk move into Closed Won sends each deal's Closed-Won project prompt, one per deal, as
    single moves do.

### Needs attention check

The list as SQL, one row per deal. The test suite runs this exact query on an SQLite copy of its
test deals, child tables included, and holds the page's list to it, deal for deal and in order of
value.

To run it on the site, put today's date in place of `2026-10-14`, and the stale cutoff in place of
`2026-10-07`. The cutoff is today minus the Sales Pipeline Dashboard's amber days, or minus its red
days when amber is 0; drop that line when both are 0.

Two things differ from the page, and neither can happen through the Desk:
- MariaDB's `trim` strips spaces only, where the page treats any whitespace as blank.
- Under PAD SPACE, a value stream with a trailing space would still match its name. A Value Stream
  is a Link, so its value is always a real name.

```sql
select o.name, o.opportunity_amount
from `tabOpportunity` o
where o.status not in ('Closed Won', 'Lost', 'Closed', 'Converted')
	and ifnull(o.custom_is_historical, 0) = 0
	and (o.custom_snoozed_until is null or o.custom_snoozed_until <= '2026-10-14')
	and (
		(o.status <> 'On Hold' and date(coalesce(o.custom_stage_changed_on, o.modified)) <= '2026-10-07')
		or o.expected_closing < '2026-10-14'
		or not ifnull(o.opportunity_amount, 0) > 0
		or trim(ifnull(o.party_name, '')) = ''
		or trim(ifnull(o.custom_opportunity_name, '')) = ''
		or trim(ifnull(o.opportunity_owner, '')) = ''
		or trim(ifnull(o.custom_opportunity_summary, '')) = ''
		or (trim(ifnull(o.primary_contact, '')) = '' and trim(ifnull(o.contact_person, '')) = '')
		or not exists (select 1 from `tabValue Stream` v where v.parenttype = 'Opportunity'
			and v.parentfield = 'custom_value_stream' and v.parent = o.name
			and trim(ifnull(v.value_stream, '')) <> '')
		or (o.status in ('Needs Analysis', 'Proposal/Price Quote', 'Negotiation/Review') and (
			trim(ifnull(o.custom_lead_source, '')) = ''
			or o.expected_closing is null
			or not exists (select 1 from `tabValue Stream` v where v.parenttype = 'Opportunity'
				and v.parentfield = 'custom_value_stream' and v.parent = o.name and (
					(v.value_stream = 'Design' and exists (select 1 from `tabDesign Customer Requests` r
						where r.parenttype = 'Opportunity' and r.parentfield = 'custom_design_customer_requests'
						and r.parent = o.name and trim(ifnull(r.design_customer_requests, '')) <> ''))
					or (v.value_stream = 'Build' and exists (select 1 from `tabBuild Customer Requests` r
						where r.parenttype = 'Opportunity' and r.parentfield = 'custom_build_customer_requests'
						and r.parent = o.name and trim(ifnull(r.build_customer_requests, '')) <> ''))
					or (v.value_stream = 'Service' and exists (select 1 from `tabService Customer Requests` r
						where r.parenttype = 'Opportunity' and r.parentfield = 'custom_service_customer_requests'
						and r.parent = o.name and trim(ifnull(r.service_customer_requests, '')) <> ''))
					or (v.value_stream = 'Events' and exists (select 1 from `tabRent Customer Requests` r
						where r.parenttype = 'Opportunity' and r.parentfield = 'custom_rent_customer_requests'
						and r.parent = o.name and trim(ifnull(r.rent_customer_requests, '')) <> ''))))))
		or (o.status in ('Proposal/Price Quote', 'Negotiation/Review')
			and trim(ifnull(o.primary_address, '')) = '')
	)
order by case when ifnull(o.opportunity_amount, 0) > 0 then 0 else 1 end, o.opportunity_amount desc, o.name
```

## Gotchas

- **`website` is a URL field because *we* made it one, and that froze 408 records.** Stock ERPNext ships `website` as a plain `Data` field on Lead / Customer / Opportunity / Supplier / Company; five Property Setter fixtures (`*-website-options`) set `options = "URL"`, which switches on frappe's `validate_url`. It arrived after the data did. frappe re-validates **every** URL field on **every** save, so a stored `2xlimaging.com` rejected an edit to the phone number, the customer group, or anything a background job touched — 281 of 739 Customers, 80 of 506 Opportunities, 24 of 277 Suppliers, 14 of 35 Leads and 9 of 16 Lead `custom_account_website` values. `website_cleanup.add_missing_scheme` fixes it on the way in, on all five doctypes, and `patches.backfill_website_scheme` repaired the stored values once. **Both lists are "every doctype with a URL Property Setter", not "the CRM ones"** — a test asserts they agree, because a doctype in one and not the other fails silently in either direction. If you are tempted to drop the Property Setters instead: that also works, and loses the validation — the fix here keeps it and stops it being a trap.
- `sync_opportunity_tags` is one of several `Opportunity` `before_save` handlers; the others are Python ports in [`script_migrations/opportunity.py`](../script_migrations/README.md).
- Converting an Opportunity to a Project provisions a Drive folder tree, but that's **non-fatal** and lives in the [Google Drive module](../google_drive/README.md) — the Project is created even if Drive fails.
- **The hand-off carries `primary_contact` across (v1.320.0), but not the three fields beside it.** `primary_contact` is doc-local on every party — a Project's primary contact is a fact about that Project and never re-points the Customer's account-wide `is_primary_contact` (see `sync_contact.GLOBAL_PRIMARY_PARTY_DOCTYPES`). The read-through phone / email / job title are re-derived from the Contact by `sync_contact.apply_primary_contact_details`, *not* added to the field-mapping table: Project `on_update` pushes those same three back **down** onto the Contact and the job-title branch guards on `is not None`, so copying a blank across from an Opportunity that never had them filled would erase `Contact.custom_title`.


## Lead attribution (WP-1, v1.241.0)

`attribution.py` + `web_lead.py`. The problem it addresses, measured on 2026-08-04: of 815
Opportunities, **814 had no `utm_source` and 809 had no `custom_lead_source`**. The
"45% missing" figure from the marketing review was measured against `tabOpportunity.source`
— a column that still physically exists but has had **no DocField behind it since erpnext
v15 renamed the field to `utm_source`**. Nothing reads or writes it. Live coverage was not
55%; it was approximately zero.

### The schema decision

erpnext v16 ships `utm_source` / `utm_medium` / `utm_campaign` / `utm_content` on Lead and
Opportunity. We deliberately do **not** write real campaign data into them:

1. **`utm_source` is load-bearing elsewhere.** `Lead.before_insert` mints a stray second
   Contact unless `utm_source == "Existing Customer"` and `customer` is set — the
   suppression the fountain-move conversion depends on (see above). Writing a campaign name
   there would silently start duplicating Contacts.
2. **`utm_medium` and `utm_campaign` are Links.** Raw capture must accept whatever string is
   in the URL; a Link either rejects it or spawns junk taxonomy rows.

So raw values live in our own `custom_utm_*` **Data** fields in a collapsed "Attribution"
section on Lead, Opportunity **and** Customer, and `custom_lead_source` (Link → `Lead
Source`) stays the single human-facing channel. The accepted cost is that we own the mapping
from a raw source/medium pair to a Lead Source value — `attribution.derive_lead_source`,
deliberately small, and consulted only to fill a **blank**.

Fields we ship that erpnext has no equivalent for: `custom_utm_id` (the ad platform's
campaign ID — the spend-to-lead join key, TASK-2026-01570), `custom_utm_term`, `custom_gclid`,
`custom_landing_page`, `custom_first_referrer`, `custom_attribution_captured_on`.

Because those four erpnext fields are never written, showing them alongside ours was an
invitation to type into them — and typing into `utm_medium`/`utm_campaign` is exactly the
junk-taxonomy hazard above. On **Opportunity** the `utm_analytics_section` and its four
fields are therefore hidden by property setter (v1.250.0), parked in the field order
immediately after our own Attribution section so unhiding one puts it back in a sensible
place. The fields and their data path are untouched.

### First touch wins

`attribution._fill_blanks` is the **only** function that writes attribution onto a document,
so there is exactly one place to audit the rule. Propagation runs on `validate`:
Lead → Opportunity, Lead → Customer, and Opportunity → Customer (the last via `db.set_value`
in `on_update`, because re-saving the Customer would re-enter Drive provisioning and contact
sync for a metadata-only copy).

### The source gate is a hook, not `reqd`

`reqd = 1` would break every API-created record and retroactively invalidate the backlog.
`attribution.enforce_source` runs on `validate`, applies to **new records only**, exempts
bulk contexts (import/migrate/patch/install/test), and is gated by
`require_lead_source_on_lead` / `require_lead_source_on_opportunity` in ERPNext Enhancements
Settings so it can be switched off from the UI in seconds. Historical blanks were bucketed to
`Unknown (pre-Aug 2026)` by `patches.backfill_unknown_lead_source` — a value that stays
visibly a gap rather than being laundered into a real channel. The **Attribution Gaps**
report separates that historical debt from live process failures.

### The website ingress

`web_lead.submit_web_lead` is a machine-to-machine POST endpoint gated by a shared secret in
an **`X-Web-Lead-Secret`** header (`web_lead_shared_secret`, at least 32 characters, fails
closed when unset), rate limited, with a field allowlist rather than a payload splat. **Never
`Authorization: Bearer`**: Frappe v16 rejects any `Authorization` header it cannot authenticate
itself, with a 401, before the endpoint runs — which is what the original v1.241.0 contract
asked for.

**The public site is WordPress on WP Engine behind Cloudflare** — a different host from
ERPNext. The capture script that reads `utm_*`/`gclid`/referrer runs on the WordPress side;
its source is kept in [`docs/website-capture/`](../../docs/website-capture/) (tested by
`scripts/test_sf_attribution.js`). The full payload contract is in
[`docs/attribution-runbook.md`](../../docs/attribution-runbook.md).


## `Value Stream` vs `Value Streams` — investigated, not changed

Two doctypes with near-identical names sit in this module and it reads as cruft. It is not.
Both are in daily use, and the names are simply **inverted from frappe convention**:

| DocType | What it is | Rows (2026-08-04) |
|---|---|---|
| `Value Streams` (**plural**) | The **master list** — Design, Build, Service, Events, Delivery, Products | 6 |
| `Value Stream` (**singular**) | The **child table** behind the `custom_value_stream` Table MultiSelect on Customer, Opportunity and Project | 1,460 |

Normally the plural would be the child. Here it is the other way round, so every query
against them reads backwards. That is the trap; write it down rather than "fixing" it.

**Two genuinely dead fields were found:**

1. `Value Stream` (child) declares **two** Link fields — `value_stream` *and* `value_streams`
   — **both pointing at `Value Streams`**. A Table MultiSelect binds one `link_fieldname`, so
   the spare is inert. Almost certainly a copy-paste artifact.
2. `Value Streams` (master) declares `value_stream_link`, a **Link → `Value Stream`** — i.e.
   the master pointing at the child table. You cannot link to a child doctype in frappe; the
   field does nothing.

**Recommendation (not executed — needs sign-off).** Keep both doctypes and both names.
Renaming a child doctype with 1,460 rows referenced by three Table MultiSelect fields is a
`bench migrate`-breaking operation for a cosmetic gain. Instead: confirm which Link field the
MultiSelects actually bind to, then remove the unused one and `value_stream_link` — which is
**two steps**, a fixture removal *and* a `frappe.delete_doc` patch, because removing a record
from `fixtures/*.json` only stops managing it (see
[`fixtures/README.md`](../fixtures/README.md)).


## Account data hygiene (WP-5, v1.242.0)

`data_quality.py` + the **Account Data Quality** report. The gaps it exists to
close, measured 2026-08-04 across 1,621 Customers: **1,292 no industry, 1,160 no
customer group, 1,118 no value stream.**

### `customer_type` is the commercial/residential axis, and it was half-migrated

This site extended erpnext's stock `customer_type` (Company / Individual /
Partnership) with **Commercial** and **Residential**. Useful — the industry rule
has a real field to key on. But 364 rows were left on the legacy `Company` value
and are **100% missing both industry and customer group**, where Commercial is
70%/67%. That is the signature of an un-migrated import, not a classification.
`patches.retype_legacy_company_customers` moves them, scoped to that full
signature so a genuine `Company` carrying real data is untouched, and logs every
affected id to the Error Log first because the two values are indistinguishable
afterwards.

### Industry is required by a hook, NOT by `reqd`

Asked for as "make industry required", and it is — but the declarative form takes
the QuickBooks sync down. `reqd` and `mandatory_depends_on` are evaluated by
`_validate_mandatory` on **every** save by **every** caller, and
`quickbooks_online/core/mapping.py` inserts Customers with `ignore_permissions=True`
but **not** `ignore_mandatory`. QBO payloads carry no industry, so a declarative
rule would fail validation on every synced customer and park it in manual review —
on the system that is the book of record until the January go-live.

`data_quality.enforce_industry` runs on `validate` and can tell the cases apart:
it honours `doc.flags.ignore_mandatory` (which `api/telephony.py` and
`fountain_move/conversion.py` already set), exempts background jobs (`frappe.request`
is None — a scheduled QBO poll is not a person who can be asked for a value),
exempts bulk contexts, and skips residential accounts entirely.

| Setting | Default | Effect |
|---|---|---|
| `require_industry_on_commercial` | **on** | Block a NEW commercial account with no industry |
| `require_industry_on_edit` | off | Also block edits — turn on **after** the 732-row backlog is cleared |

Because the rule is a hook, no form knows about it on its own — which is how the
Customer quick-entry dialog came to have no Industry field while Save demanded one. `industry_rule_for_client()` ships the new-record flag and
`COMMERCIAL_TYPES` to the desk as `frappe.boot.ee_industry_rule`, and
`public/js/global_enhancements/party_quick_entry.js` shows and requires Industry
under exactly that (v1.499.0). Change the rule here and the dialog follows; the
hook stays the authority either way.

### Assisted, never automatic

`bulk_assign` / `assign_value_streams` apply a value **a human picked** to rows **a
human selected**, skip rows that already carry a value rather than overwriting,
and are role-gated to System Manager / Sales Manager. Nothing infers an industry
from a company name — a wrong industry is invisible once written and every
downstream report silently inherits it.

The keep/merge/retire proposal for the 89 Industry Type values (47 in use) is
[`docs/industry-type-proposal.md`](../../docs/industry-type-proposal.md). It is a
proposal; nothing has been executed.

## Pipeline reconciliation (WP-7, v1.242.0)

`reconciliation.py` + the **Closed Won Reconciliation** report. 200 Opportunities
are Closed Won with no linked Project; 199 of them are `opportunity_from =
"Customer"` so `party_name` is a real Customer id, and 138 belong to a customer who
already owns at least one Project.

Candidates are ranked within a customer by date proximity (50), value proximity
(30) and whether the project is already spoken for (20). Customer identity is a
**precondition, not a score component** — a project for a different customer is not
a weaker match, it is not a match.

**Nothing links automatically, and that is the whole design.** A wrong link
corrupts revenue attribution, which is the exact defect WP-1 was commissioned to
fix, so an auto-linker would manufacture the problem the programme is trying to
remove. `score_candidates` is read-only and every candidate carries its `reasons`
so a reviewer can disagree on sight; `link_opportunity_to_project` writes exactly
one link, refuses to overwrite an existing one, and guards both directions so two
Projects cannot claim one Opportunity.

**Which link counts** (WI-024). Three fields link a deal and its Project:
`Opportunity.custom_created_project` is the canonical forward link (the hand-off engine's
idempotency field, and the only one the won-without-project warnings read),
`Project.custom_opportunity` the canonical reverse link, and `Opportunity.custom_project` a
hidden legacy field that nothing should write. `patches/backfill_opportunity_project_links.py`
(v1.636.0) filled the canonical pair on every Closed Won deal from the other two, with the same
one-Project-one-deal guard as the linker; what it could not prove it listed for review instead.

**Named Account Targets** builds an outreach list from the same data, sorted by
staleness rather than alphabetically. It has **no scale filter** — the
event-planner scale taxonomy is undecided, and a filter built against a guessed
field would quietly match nothing.

## Historical opportunities (`historical.py`, v1.629.0)

Decided by Nik on 2026-10-13 (TASK-2026-00917, WI-024): most of the "Closed Won but no
linked Project" backlog is Zoho history, imported 2025-07-14 and delivered before ERPNext
existed. Those deals no longer raise hand-off warnings and no longer count in the KPIs.

**The rule.** An Opportunity is historical when it was created before **2025-08-01** (which
covers every Zoho import, whenever it was won) **and** no person has done anything on it in
the last **90 days**. Activity is any of these, created in the window (the Comment, ToDo and
Version sources only when the owner is not Administrator or Guest):

- a Comment of type **Comment** or **Attachment** (not Info, Edit, Like or the assignment
  types: code writes those as a side effect, and an assignment counts through its ToDo);
- a Communication on it (email, call), through `reference_doctype`/`reference_name` **or** a
  `Communication Link` timeline row, **whoever owns it**: an email about a deal is somebody
  working it even when the system logged it as Administrator (4 of prod's 392 Opportunity
  Communications are). Only `communication_type` **Communication**, on both paths: an
  Automated Message (a v16 Notification email, such as the won-deal alert) does not revive
  a deal. The window is the Communication's own date, not the link row's;
- a ToDo referencing it (an assignment);
- a Version recording a **status** change that was not part of a Data Import. The JSON is
  parsed in Python: every v16 Version carries `"data_import": null`, so a SQL
  `NOT LIKE '%data_import%'` would match nothing.

**The field.** `Opportunity.custom_is_historical` ("Historical (pre-ERPNext)") is a stored,
read-only Check in the list's standard filters. It is a column rather than a Python rule
because Number Cards, Dashboard Charts, Report Builder and the list view can only filter on a
stored column. Nobody sets it by hand.

**The nightly job.** `refresh_historical_flags` (daily) recomputes every deal created before
the cutoff with one `IN`-list query per source per 500 deals, writes only the rows that change
(`update_modified=False`), clears the flag on anything newer, logs its counts and never
raises. It is not ordered against the won-deal nag (each daily job is its own RQ job), so a
deal that flips can be off by one day in the nag. A person changing the status of a
historical deal un-flags it in that same save (`clear_on_person_status_change`); a comment
or email un-flags it on the next run; and ninety quiet days later it is historical again.
Any other save puts the stored flag back, so a stale open form (the Desk sends read-only
fields too) can never write an old value over the job's.

**Where it is applied** (always through `exclude_filters` / `exclude_filter_dict` /
`exclude_sql` / `is_historical`, each of which excludes nothing until the column exists):

- warnings and queues: the daily won-deal nag and its list link (`status_alerts.py`), the
  daily overdue hand-off-meeting emails (`process_steps.escalate_overdue_handoffs`), the
  **Closed Won Reconciliation** report, the Sales Dashboard's hand-off backlog and stalled
  deals widgets, the `handoff_backlog` KPI, the Sales Pipeline's "Won — awaiting project"
  column, the Opportunity form's create-project prompt and its Hand-Off Process tab (a quiet
  "Historical (pre-ERPNext) — excluded from hand-off warnings" line, no Ready step, no
  OVERDUE banner, and every button kept but none primary, so a deal behind a closed gate can
  still book or skip the meeting and create its project), and the
  `closed_won_handoff_status` AI tool, which returns `historical_excluded` so an answer can
  say how many it left out;
- measurements: the Closed-Won KPIs (`win_rate_90` on both legs, `won_count_30`,
  `won_value_30`, `avg_deal_size`, `marketing_won_30`), the open-pipeline KPIs
  (`open_pipeline_value`, `open_opportunities`, `stalled_opportunities`), the marketing
  funnel's won count, the Value Stream Performance report's opportunity counts and won
  amounts, the morning briefing's open-pipeline list, the **Closed-Won Opportunities**,
  **Open Opportunities** and **Open Pipeline Value** Number Cards and the **Opportunities by
  Status** / **Opportunity Value by Status** charts.

**Deliberately not applied** where the rows are deals that already have a project or revenue:
the **Hand-Off Process Coverage** report (it lists only deals with a project, so excluding
historical ones would hide live projects) and Value Stream Performance's billed-revenue
rollup (real money, whatever the deal's age). The TV pipeline board's open columns also keep
historical deals, so people can still see and act on them.

**To see historical deals in the reconciliation report**, tick **Include historical** (off by
default). In the Opportunity list, filter on *Historical (pre-ERPNext)*.

Alongside it, `stamp_won_date` became transition-only: it used to stamp today on any save of a
won deal with a blank date, which is how a 2026-06-30 re-import dated twelve old wins
06-30-2026 (`patches/restore_opportunity_won_dates.py` restores eleven; the twelfth really
was won that day).
