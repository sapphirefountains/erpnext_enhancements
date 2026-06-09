# `www/` — Time Kiosk PWA shell

The **Time Kiosk** is a standalone, installable Progressive Web App served at **`/kiosk`**, separate from the heavy desk app. This folder is the PWA *shell* (page controller, HTML, service worker, manifest); the *front-end logic* lives in [`public/js/kiosk/`](../public/README.md#kiosk-pwa-front-end) and the *server endpoints* in [`api/time_kiosk.py`](../api/README.md).

## How the pieces fit

```
www/kiosk.py            Frappe web-page controller — auth gate + boot payload
www/kiosk.html          chrome-free app shell — loads manifest, css, geo.js, app.js; registers the SW
www/kiosk-sw.js         service worker (root scope) — precache, offline, IndexedDB geo queue, Background Sync
www/kiosk-manifest.json PWA web manifest — name, icons, standalone display

public/js/kiosk/app.js  UI / clock state machine
public/js/kiosk/geo.js  geolocation sampling (watchPosition + distance filter + heartbeat)
api/time_kiosk.py        get_kiosk_bootstrap, log_time, log_geolocation_batch, get_location_history, …
```

- **`kiosk.py`** — `get_context()` is an auth gate (guests redirect to `/login?redirect-to=/kiosk`); for signed-in users it calls `api.time_kiosk.get_kiosk_bootstrap()` and passes `boot_json` (employee / current interval / settings / CSRF) and `csrf_token` to the template. `no_cache = 1` forces a fresh per-user render.
- **`kiosk.html`** — extends `templates/web.html` but renders chrome-free. Declares the manifest, theme-color / Apple / standalone meta, and touch icon; loads `css/kiosk/kiosk.css`, then `js/kiosk/geo.js` + `js/kiosk/app.js`; injects the boot payload as `window.KIOSK_BOOT` / `window.KIOSK_CSRF`. `app.js` renders into `#kiosk-root` and registers the service worker.
- **`kiosk-sw.js`** — root-scope service worker:
  - **install** → precache the app shell (`PRECACHE`) + `skipWaiting`.
  - **activate** → delete every cache whose name ≠ `CACHE` + `clients.claim`.
  - **fetch** → network-first for `/kiosk` navigations (cached-shell fallback); cache-first-with-background-refresh for `/assets/...` + the manifest.
  - **geo queue** → the page posts `config` / `enqueue` / `flush` messages; points persist in IndexedDB (`TimeKioskDB` v2, store `GeoQueue` keyed by `client_id`, plus a `Meta` kv store for `csrf_token` / `max_batch_size`), then upload in batches (default 50) to `api.time_kiosk.log_geolocation_batch`. The server echoes `accepted` / `rejected[].client_id`; accepted and permanently-rejected (`invalid_coords`, `low_accuracy`) points are deleted, transient ones stay queued. A failed flush registers a **Background Sync** (`flush-geo`) that re-runs on reconnect.
- **`kiosk-manifest.json`** (JSON, no comments) — `name`/`short_name` "Time Kiosk", `id`/`start_url`/`scope` = `/kiosk`, `display: standalone`, `orientation: portrait-primary`, `theme_color: #2490ef`. Icons list **PNGs first** (192, 512, maskable-512) then SVGs — the PNGs satisfy Chrome/Edge installability (see CHANGELOG 0.3.0).

## Cache versioning — how to bump

`kiosk-sw.js` has **two independent versions** — don't conflate them:

- `const CACHE = 'time-kiosk-v2'` — the asset cache. **Bump the suffix** (`-v3`, …) whenever the precached shell/assets change; `activate` deletes the old cache, so the rename *is* the cache-bust. `kiosk-manifest.json` is precached, so manifest changes also need a bump to reach installed clients.
- `DB_VERSION` — the IndexedDB schema version (bump only on schema changes).

## `sync_time_kiosk.py`

A **standalone** async tool (at the repo root, `../../sync_time_kiosk.py`) that consolidates Time Kiosk **Job Intervals → Timesheets** over the ERPNext REST API (httpx). Per batch: fetch ≤100 Completed/Pending intervals → aggregate by **(employee, project, date)** summing `end − start − total_paused_seconds` (clamped ≥0) into hours → append a `time_log` to that employee's existing **Draft** Timesheet for the date (idempotent dup-check) or create one → rebuild the Timesheet `note` from the day's descriptions → mark sources `Synced`, or bump `sync_attempts` and set `Failed` after 3 tries. Concurrency is bounded (`Semaphore(5)`); transient errors / HTTP 503 are retried with exponential backoff.

**Invocation: manual / external, NOT scheduled.** It is **not** referenced in `hooks.py`; it talks REST (not the ORM) and reads `sync_status` / `sync_attempts` on Job Interval. Run one batch with `python sync_time_kiosk.py`; schedule externally (cron) to run repeatedly. Config via env: `ERPNEXT_URL`, `API_KEY`, `API_SECRET`. Tested by `../../test_sync_time_kiosk.py` (34 tests, `httpx` mocked).

> ERPNext also ships an in-app Timesheet sync (the README's "Timesheet Sync" feature) — `sync_time_kiosk.py` is the out-of-process alternative for environments that prefer an external cron.

## Gotchas

- **Background Sync is unsupported on iOS** — the worker degrades to page-driven flush (on every `enqueue`/`flush`/app-resume); `ensureSync()` swallows the unsupported case.
- Reliable tracking requires the app **in the foreground** (browsers suspend timers and revoke geolocation when backgrounded) and the site served over **HTTPS** (localhost exempt).
