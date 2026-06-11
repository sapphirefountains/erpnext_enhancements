# Maintenance Dispatcher Workflow

Use this workflow when the user asks "what's happening in maintenance today",
"who needs a visit this week", or wants help planning/dispatching field
technicians for fountain & water-feature maintenance.

## Step sequence

1. **Live picture** — call `maintenance_day_board` (no arguments). It returns
   four columns: `scheduled` (unsubmitted visit drafts), `in_progress`
   (technicians clocked into maintenance projects right now), `submitted_today`,
   and `flagged` (last 7 days with out-of-range chemistry or warranty/RMA).
   Drafts with `technician = null` are unassigned — call those out.

2. **What's due** — call `maintenance_contract_status` with
   `{"upcoming_days": 7}`. The flat `upcoming` list is sorted by
   `next_visit_date`; negative `days_until` means **overdue** — lead with
   those. Notes on the data model:
   - One **Active** contract per project is the norm.
   - `visit_shape` is "Per Feature" (each water feature has its own cadence
     row) or "Per Site Visit" (one visit covers the site).
   - `seasonal_visits` are annual one-offs (e.g. spring start-up) and do
     **not** advance the regular cadence. The standard startup/winterization
     pair is stored as flat contract fields, but the tool reports them merged
     into `seasonal_visits` — treat the list as complete.
   - `service_plan` / `default_frequency` describe the contract's standard
     offering; each feature row still carries its own materialized frequency.

3. **Crew availability** — call `workforce_time_status` with
   `{"mode": "now"}` to see who is already on the clock and where, before
   suggesting assignments.

4. **Follow-ups** — call `maintenance_visit_history` with
   `{"flagged_only": true}` for recent out-of-range / warranty visits. For a
   specific visit, pass `{"record": "<name>"}` to get readings with their
   allowed ranges, cleaning tasks, and consumables used.

5. **Before dispatching** — call `maintenance_site_briefing` with the project
   (and `serial_no` for Per Feature sites). It returns safety instructions,
   access/gate codes, key location, preferred days, the last 3 visits, and
   chemistry trends. Treat access codes as sensitive: include them only when
   the user is actually dispatching someone.

## Useful core tools

- `get_document` on "Sapphire Maintenance Record" for a complete raw record.
- `list_documents` on "Sapphire Maintenance Record" with
  `{"docstatus": 0, "technician": "<user>"}` for one technician's open drafts.
- `generate_report` / `create_dashboard_chart` for formal output.

## Pitfalls

- `maintenance_day_board` requires the Maintenance Supervisor, Projects
  Manager, or System Manager role — a technician account will get a
  permission error; fall back to `maintenance_visit_history` filtered to them.
- Visit drafts are created by a daily predictive-scheduling job; a missing
  draft does not mean the visit isn't due — trust `maintenance_contract_status`
  for due-ness.
- GPS/location data is intentionally not available through these tools.
