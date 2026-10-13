# `google_calendar/` — Google Calendar read helpers

One module: `calendar_utils.py`, providing Google Calendar v3 **read** helpers for the
Finance Calendar widget.

## Authentication

It reuses the **same service account** the Google Drive automation already uses — the JSON
key on `Project Folder Google Drive Settings` — scoped read-only to Calendar. No second
credential, no second consent flow.

The consequence to remember when it "doesn't work": the target "Finance" calendar must be
**shared with the service account's email address**. A service account sees nothing by
default, so an empty calendar widget is almost always a sharing problem on the Google side
rather than a bug here. The service-account settings themselves are documented in the
[Google Drive README](../google_drive/README.md).

## Scope

Read-only. Nothing here creates, moves or deletes events. Consumers: `api/finance_calendar.py`
and the Finance Calendar widget.

## Not here: the planner calendar sync

The **write** side, pushing committed planner work to the shared Projects, Maintenance and Company
Holidays & Vacations calendars (v1.604.0), lives in
[`project_enhancements/calendar_sync/`](../project_enhancements/calendar_sync/) and is documented in the
[Project Enhancements README](../project_enhancements/README.md#calendar-sync-c2-v16040). It does **not**
use the service account above: Workspace caps sharing outside the domain at "See all event details", so a
service account could never edit those calendars. It signs in as one Google account through OAuth instead.

## Related

- `google_drive/` — the Drive automation that owns the service-account settings
- `travel_management/` — trip scheduling, which has its own calendar concerns
