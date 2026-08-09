# `integration_hub/` — integration health and web analytics

The desk surfaces for "are our integrations working?" and "how is the website doing?". Moved
here out of `enhancements_core` in v1.39.0 (`move_analytics_to_integrations`).

Nearly all of the logic lives in `api/integrations_health.py` and `api/analytics.py`; this
module is the desk pages, the settings doctype, and the workspace.

## Contents

| Path | Purpose |
|---|---|
| `page/integrations_health/` | Health snapshot of every external integration |
| `page/ga4_dashboard/` | Google Analytics 4 + Search Console dashboard |
| `doctype/ga4_settings/` | Single — GA4 / Search Console configuration |

## Integrations Health

System-Manager-only. It reports QuickBooks token state, CDC status and failed syncs; Google
Drive configuration and sync-log failures; the Google Calendar Task-sync push; Triton/Twilio,
Gemini, GA4/GSC; scheduler liveness; and a 24-hour Error Log digest.

Three design properties worth preserving:

- **Secrets are read only as "configured?" booleans.** The page never surfaces a credential,
  so it is safe to screenshot into a support thread.
- **It is DB-only on load.** The single live check (`run_drive_test`, which proxies
  `crm_enhancements.drive_sync.test_connection`) is **opt-in**, behind a button. A health
  page that live-checks every integration on render becomes the thing that takes them down
  when someone leaves it open.
- **A tile that only reports configuration reports nothing.** "Configured" is not "working" —
  the Google Calendar tile exists because a Task→Calendar push stayed configured, credentialled
  and completely dead for two months. So tiles carry a *liveness* metric wherever one is cheap
  to compute from the database: last sync age, failures in the window, or work-in against
  work-out (that tile counts Tasks created against Events pushed). Add one when you add a tile.

## GA4 dashboard

Reads the Google Analytics 4 Data API and Search Console. Setup instructions are in the
[Enhancements Core README](../enhancements_core/README.md#google-analytics-4--search-console-dashboard).

## Tests

```bash
python -m unittest erpnext_enhancements.tests.test_integrations_health -v
```

Bench-free and in CI — it covers the tone helpers, i.e. the mapping from raw state to the
Good/Degraded/Down wording the page shows.
