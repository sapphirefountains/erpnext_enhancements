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

- **Shared secret, not a signature.** Unlike the QBO webhook, this path does not
  verify an Intuit signature. It requires `qb_time_webhook_secret` from
  site_config, sent in the `X-QB-Time-Webhook-Secret` header or as `?token=` on
  the webhook URL, and refuses every request (fixed 401 body, no Error Log row)
  while that key is unset. WI-046 retires the endpoint at cutover.
- The payload shape in the handler is an **example**; adjust it to the actual
  QuickBooks Time payload.
