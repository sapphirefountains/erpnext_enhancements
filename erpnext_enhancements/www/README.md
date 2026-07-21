# `www/` — standalone web pages (Time Kiosk, Wall Display, traveler itinerary)

Four standalone web pages live here, separate from the heavy desk app:

- the **Time Kiosk** at **`/kiosk`** — installable PWA for technicians, chrome-free (most of this README);
- the **Wall Display** at **`/wall`** — read-only project/TV dashboard, chrome-free (see below);
- the **traveler itinerary** at **`/itinerary`** — chrome-free (see [its section below](#itinerary--traveler-itinerary-page));
- the **travel guidelines** at **`/travel_guidelines`** — the company travel policy document, login-gated, standard website chrome (`travel_guidelines.py` + `.html`; static content with "In the system" callouts mapping each policy rule to the Travel Management flows). Linked from the Travel workspace shortcut, the `/itinerary` footer, and the trip-booked/traveler-added emails.
- the **fountain move intake form** at **`/fountain-move`** — the public, guest-accessible Cactus & Tropicals intake form (`fountain_move.py` + `fountain-move.html`; note the underscored controller — see [Controller filenames](#controller-filenames-hyphens-are-silently-fatal)). See [its section below](#fountain-move--public-intake-form).

This folder is each app's *shell* (page controller, HTML, service worker where applicable); front-end logic lives in [`public/js/kiosk/`](../public/README.md#kiosk-pwa-front-end) / `public/js/wall/` / `public/js/travel/` and the server endpoints in [`api/`](../api/README.md).

## Controller filenames: hyphens are silently fatal

Frappe locates a page's controller from the **template's** basename, replacing
hyphens with underscores (`frappe/website/page_renderers/template_page.py`):

```
www/stripe-return.html   ->  frappe imports  www/stripe_return.py
```

A controller named `stripe-return.py` is therefore **never imported**, and its
`get_context()` never runs. Nothing raises. The template still renders — just with
every context variable undefined, silently taking whichever branch that implies.

**This is not hypothetical: `www/stripe-return.py` never executed until v1.159.10.**
Every Stripe Checkout return, including cancellations, rendered "Thank you! Your
payment is being processed", because `outcome` was undefined so `outcome == "cancel"`
was false. A customer who deliberately cancelled was told their payment was going
through.

The **route** comes from the template, so a hyphenated URL is perfectly fine — the
public path stayed `/stripe-return` through the fix. Only the `.py` needs
underscores. An earlier revision of this README claimed the opposite (that a
hyphenated route required a hyphenated filename); that was wrong, and it is what
let the bug survive review.

`scripts/check_www_controllers.py` enforces this in CI so it cannot regress.

## Wall Display (`/wall`)

A 24/7 wall/TV portfolio display ported from Triton's DashboardView: morning briefing band (today's tasks / overdue / today's schedule), auto-rotating per-project task-completion carousel (top-10 ranked projects, SVG donut), Open-Meteo weather chip. Dark, flat, perf-lite by construction (no backdrop-filter/animations — Pi-friendly).

```
www/wall.py             controller — guest→login redirect, staff-role gate, boot payload
www/wall.html           chrome-free shell — injects WALL_BOOT/WALL_BUILD, loads wall.css/app.js (?v= busted)
www/wall-sw.js          service worker — kiosk-sw minus the geo queue: offline shell + last-good data
public/js/wall/app.js   vanilla renderer: band, carousel, donut, weather, clock
public/css/wall/wall.css
api/task_dashboard.py    get_wall_dashboard_data (task-dashboard payload + task_stats + settings + deploy_version)
```

- **Auth**: sign the TV in once with a dedicated user holding only the **Wall Display** role (`patches/seed_wall_display_role`, `desk_access = 0`). The data endpoint role-gates then fetches permission-free, exactly like the Task Dashboard block. A 401/403 on refresh reloads the page, which bounces through `/login?redirect-to=/wall`. Raise `session_expiry` in System Settings so the Pi isn't re-logging in weekly.
- **Deploy pickup, two belts**: (1) the SW is registered as `/wall-sw.js?v=<deploy token>` and re-checked every 60s; a new worker taking control reloads the page immediately (nothing to protect on a display). (2) Every data refresh carries the server's `deploy_version`; a mismatch with `WALL_BUILD` reloads even if the SW never installed.
- **Settings** (ERPNext Enhancements Settings → Wall / TV Display): rotation seconds, data refresh seconds, weather toggle + coordinates/label (defaults: Bountiful UT).
- **Donut semantics**: `Completed` + `Invoiced` count as done; `Canceled`/`Cancelled`/`Template` are in neither slice.

---

The rest of this README covers the **Time Kiosk**.

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

## `/itinerary` — traveler itinerary page

Mobile-friendly, day-by-day trip view for travelers (flights with tap-to-copy PNRs, hotel confirmations, agenda stops with POI maps and "Open in Maps" deep links). Follows the kiosk shell pattern **without** the PWA/service-worker layer (no offline queueing needs):

```
www/itinerary.py               controller — guest → /login redirect; boot payload (employee, trips, csrf)
www/itinerary.html             chrome-free shell — every asset URL carries ?v={{ deploy_version }}
public/js/travel/itinerary.js  vanilla-JS UI (trip switcher, day chips, typed cards, lazy Leaflet maps)
public/css/travel/itinerary.css --ti-* palette + prefers-color-scheme dark (no desk data-theme on web pages)
api/travel.py                  get_itinerary_bootstrap, get_my_trips, get_trip_itinerary
```

Security model: the employee is derived from the session (never client-supplied); `get_trip_itinerary` is permission-gated and the Travel Trip hooks scope it to owner/crew/coordinators. Segments pinned to a different single traveler are filtered out of a traveler's view. `itinerary.py` imports `get_deploy_version` from `kiosk.py` — same cache-bust token, do not duplicate it.

## `sync_time_kiosk.py`

A **standalone** async tool (at the repo root, `../../sync_time_kiosk.py`) that consolidates Time Kiosk **Job Intervals → Timesheets** over the ERPNext REST API (httpx). Per batch: fetch ≤100 Completed/Pending intervals → aggregate by **(employee, project, date)** summing `end − start − total_paused_seconds` (clamped ≥0) into hours → append a `time_log` to that employee's existing **Draft** Timesheet for the date (idempotent dup-check) or create one → rebuild the Timesheet `note` from the day's descriptions → mark sources `Synced`, or bump `sync_attempts` and set `Failed` after 3 tries. Concurrency is bounded (`Semaphore(5)`); transient errors / HTTP 503 are retried with exponential backoff.

**Invocation: manual / external, NOT scheduled.** It is **not** referenced in `hooks.py`; it talks REST (not the ORM) and reads `sync_status` / `sync_attempts` on Job Interval. Run one batch with `python sync_time_kiosk.py`; schedule externally (cron) to run repeatedly. Config via env: `ERPNEXT_URL`, `API_KEY`, `API_SECRET`. Tested by `../../test_sync_time_kiosk.py` (34 tests, `httpx` mocked).

> ERPNext also ships an in-app Timesheet sync (the README's "Timesheet Sync" feature) — `sync_time_kiosk.py` is the out-of-process alternative for environments that prefer an external cron.

## Gotchas

- **Background Sync is unsupported on iOS** — the worker degrades to page-driven flush (on every `enqueue`/`flush`/app-resume); `ensureSync()` swallows the unsupported case.
- Reliable tracking requires the app **in the foreground** (browsers suspend timers and revoke geolocation when backgrounded) and the site served over **HTTPS** (localhost exempt).
