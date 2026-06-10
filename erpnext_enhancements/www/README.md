# `www/` — Time Kiosk PWA shell

The **Time Kiosk** is a standalone, installable Progressive Web App served at **`/kiosk`**, separate from the heavy desk app. This folder is the PWA *shell* (page controller, HTML, service worker, manifest); the *front-end logic* lives in [`public/js/kiosk/`](../public/README.md#kiosk-pwa-front-end) and the *server endpoints* in [`api/time_kiosk.py`](../api/README.md).

## How the pieces fit

```
www/kiosk.py            Frappe web-page controller — auth gate + boot payload
www/kiosk.html          chrome-free app shell — loads manifest, css, geo.js, app.js; registers the SW
www/kiosk-sw.js         service worker (root scope) — precache, offline, IndexedDB geo queue, Background Sync
www/kiosk-manifest.json PWA web manifest — name, icons, minimal-ui/standalone display

public/js/kiosk/app.js  UI / clock state machine
public/js/kiosk/geo.js  geolocation sampling (watchPosition + distance filter + heartbeat)
api/time_kiosk.py        get_kiosk_bootstrap, log_time, log_geolocation_batch, get_location_history, …
```

- **`kiosk.py`** — `get_context()` is an auth gate (guests redirect to `/login?redirect-to=/kiosk`); for signed-in users it calls `api.time_kiosk.get_kiosk_bootstrap()` and passes `boot_json` (employee / current interval / settings / CSRF), `csrf_token`, and `deploy_version` (the per-deploy cache-bust token — mtime of `sites/assets/assets.json`, i.e. new on every `bench build`; app-version fallback) to the template. `no_cache = 1` forces a fresh per-user render.
- **`kiosk.html`** — extends `templates/web.html` but renders chrome-free. Declares the manifest, theme-color / Apple / standalone meta, and touch icon; loads `css/kiosk/kiosk.css`, then `js/kiosk/geo.js` + `js/kiosk/app.js` — every mutable asset URL carries `?v={{ deploy_version }}` because raw `/assets` are served with a 1-year immutable Cache-Control (icons stay unversioned: content never changes); injects the boot payload as `window.KIOSK_BOOT` / `window.KIOSK_CSRF` / `window.KIOSK_BUILD`. `app.js` renders into `#kiosk-root` and registers the service worker as `/kiosk-sw.js?v=<KIOSK_BUILD>`.
- **`kiosk-sw.js`** — root-scope service worker:
  - **install** → precache the app shell (`PRECACHE`, with `cache: 'reload'` + the `?v=` suffix so the immutable HTTP cache can't feed stale bytes) + `skipWaiting`.
  - **activate** → delete every cache whose name ≠ `CACHE` + `clients.claim`.
  - **fetch** → network-first for `/kiosk` navigations (cached-shell fallback); cache-first-with-background-refresh for `/assets/...` + the manifest.
  - **geo queue** → the page posts `config` / `enqueue` / `flush` messages; points persist in IndexedDB (`TimeKioskDB` v2, store `GeoQueue` keyed by `client_id`, plus a `Meta` kv store for `csrf_token` / `max_batch_size`), then upload in batches (default 50) to `api.time_kiosk.log_geolocation_batch`. The server echoes `accepted` / `rejected[].client_id`; accepted and permanently-rejected (`invalid_coords`, `low_accuracy`) points are deleted, transient ones stay queued. A failed flush registers a **Background Sync** (`flush-geo`) that re-runs on reconnect.
- **`kiosk-manifest.json`** (JSON, no comments) — `name`/`short_name` "Time Kiosk", `id`/`start_url`/`scope` = `/kiosk`, `display: standalone` with `display_override: ["minimal-ui"]` (Chromium installs get native back/forward/refresh chrome; where minimal-ui is unsupported — iOS — the app runs standalone and `app.js` renders its own nav bar instead), `orientation: portrait-primary`, `theme_color: #2490ef`. Icons list **PNGs first** (192, 512, maskable-512) then SVGs — the PNGs satisfy Chrome/Edge installability (see CHANGELOG 0.3.0).

## Cache versioning — automatic per deploy

The asset cache **versions itself**; only the IndexedDB schema is still bumped by hand:

- `CACHE = 'time-kiosk-' + VERSION` — `VERSION` is the `?v=` query of the worker's own registration URL, which `app.js` sets to `window.KIOSK_BUILD` (= `kiosk.py::get_deploy_version`, the mtime of `sites/assets/assets.json` — new on every `bench build`). A deploy is therefore a new SW script URL → the browser installs the new worker → `activate` deletes every other cache. The same `?v=` token is appended to the shell's asset URLs (busting the 1-year-immutable `/assets` HTTP cache) and precaching fetches with `cache: 'reload'`. **No manual bump needed** for shell/asset/manifest changes — deploying is the bump. `app.js` additionally calls `registration.update()` on foreground + hourly, and reloads the page once (deferred until the app is hidden) when an updated worker takes control, so even a never-relaunched kiosk converges on the current deploy.
- `DB_VERSION` — the IndexedDB schema version (bump only on schema changes; it does **not** rotate per deploy).

## `sync_time_kiosk.py`

A **standalone** async tool (at the repo root, `../../sync_time_kiosk.py`) that consolidates Time Kiosk **Job Intervals → Timesheets** over the ERPNext REST API (httpx). Per batch: fetch ≤100 Completed/Pending intervals → aggregate by **(employee, project, date)** summing `end − start − total_paused_seconds` (clamped ≥0) into hours → append a `time_log` to that employee's existing **Draft** Timesheet for the date (idempotent dup-check) or create one → rebuild the Timesheet `note` from the day's descriptions → mark sources `Synced`, or bump `sync_attempts` and set `Failed` after 3 tries. Concurrency is bounded (`Semaphore(5)`); transient errors / HTTP 503 are retried with exponential backoff.

**Invocation: manual / external, NOT scheduled.** It is **not** referenced in `hooks.py`; it talks REST (not the ORM) and reads `sync_status` / `sync_attempts` on Job Interval. Run one batch with `python sync_time_kiosk.py`; schedule externally (cron) to run repeatedly. Config via env: `ERPNEXT_URL`, `API_KEY`, `API_SECRET`. Tested by `../../test_sync_time_kiosk.py` (34 tests, `httpx` mocked).

> ERPNext also ships an in-app Timesheet sync (the README's "Timesheet Sync" feature) — `sync_time_kiosk.py` is the out-of-process alternative for environments that prefer an external cron.

## Gotchas

- **Background Sync is unsupported on iOS** — the worker degrades to page-driven flush (on every `enqueue`/`flush`/app-resume); `ensureSync()` swallows the unsupported case.
- Reliable tracking requires the app **in the foreground** (browsers suspend timers and revoke geolocation when backgrounded) and the site served over **HTTPS** (localhost exempt).
