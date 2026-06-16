# QuickBooks Time

A lightweight, standalone integration (independent of the QuickBooks **Online**
accounting module): an inbound **QuickBooks Time** timesheet webhook that creates
ERPNext **Time Log** documents.

## Entry point

- `api.qb_timesheet_webhook` (guest) — parses the inbound timesheet payload,
  resolves the ERPNext Employee/Project from the custom QuickBooks-id fields
  (`custom_quickbooks_user_id` on Employee, `custom_quickbooks_jobcode_id` on
  Project), converts duration (seconds) → hours, and inserts a Time Log.

  **Webhook URL:** `/api/method/erpnext_enhancements.quickbooks_time.api.qb_timesheet_webhook`

  > Previously served from the `quickbooks_time_integration` module
  > (`...quickbooks_time_integration.api.qb_timesheet_webhook`). After deploying
  > the QuickBooks module split, update the endpoint configured in QuickBooks Time.

## Gotchas

- **No signature verification** yet — unlike the QBO webhook, this path does not
  verify an Intuit signature (flagged with an inline `SECURITY:` comment). Add an
  HMAC check before treating it as production-trusted.
- The payload shape in the handler is an **example**; adjust it to the actual
  QuickBooks Time payload.
