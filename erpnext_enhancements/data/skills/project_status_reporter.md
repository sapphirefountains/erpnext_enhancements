# Project Status Reporter Workflow

Use this workflow when the user asks for a project status report, a portfolio
overview, "how is project X doing", or "what are we blocked on".

## Step sequence

1. **Portfolio scan** — call `project_status_overview` (default
   `scope: "portfolio"`, optionally `{"is_active": "Yes"}`). Each row has task
   counts, assignees, priorities, and dollar amounts. Flag projects whose
   `completed_tasks`/`total_tasks` ratio lags their timeline.

2. **Drill into a project** — call `project_status_overview` with
   `{"scope": "project", "project": "<name>", "include_tasks": true}`:
   - `health` — `schedule_health` is the % of tasks not overdue;
     `high_priority_overdue` > 0 is the strongest red flag.
   - `process_steps` — the 7-step opportunity→project hand-off tracker. The
     first **Pending** step is the current step; `due_by` is its SLA deadline
     and `responsible_role` says who owns it (Account Executive / Accounts
     Receivable / Project Manager). A past-due `due_by` on a Pending step is a
     process blocker worth surfacing.
   - `tasks` — Gantt rows with `dependencies`, `assigned_to`, and
     `custom_class: "bar-overdue"` marking overdue bars.
   - For programs, use `{"scope": "master_project", "master_project": "<name>"}`
     to get member projects with completion rollups.

3. **Procurement blockers** — call `project_procurement_status` with the
   project. The default `stage_summary` view groups items by the **latest**
   stage their chain reached (Material Request → RFQ → Supplier Quotation →
   Purchase Order → Purchase Receipt/Stock Entry → Purchase Invoice;
   subcontracted receipts show as "Subcontracting Receipt"). Items stuck at
   Material Request / RFQ are unordered; `completion_percentage` < 100 at
   Purchase Order means goods not yet received. Use `{"view": "documents"}`
   when the user wants actual document numbers to chase.

4. **Compose the report** — structure as: overall health (one line), schedule
   (overdue tasks, milestones at risk), process state (current hand-off step +
   SLA), procurement (top blockers with document names), then asks/decisions.
   Offer `create_dashboard_chart` or `generate_report` for visuals.

## Pitfalls

- Portfolio scope is gated by the Project Dashboard **page role**, not
  per-project permissions — by design it shows the whole shared portfolio. If
  the user lacks the page role, fall back to `list_documents` on Project.
- Project scope checks read permission on that specific project.
- `percent_complete` on Project is task-weighted; for revenue-weighted views
  use `custom_project_dollar_amount` from the portfolio rows.
