# `www/` — standalone web pages (Time Kiosk, Wall Display, traveler itinerary, feedback)

Several standalone web pages live here, separate from the heavy desk app:

- the **Time Kiosk** at **`/kiosk`** — installable PWA for technicians, chrome-free (most of this README);
- the **Wall Display** at **`/wall`** — read-only project/TV dashboard, chrome-free (see below);
- the **traveler itinerary** at **`/itinerary`** — chrome-free (see [its section below](#itinerary--traveler-itinerary-page));
- the **travel guidelines** at **`/travel_guidelines`** — the company travel policy document, login-gated, standard website chrome (`travel_guidelines.py` + `.html`; static content with "In the system" callouts mapping each policy rule to the Travel Management flows). Linked from the Travel workspace shortcut, the `/itinerary` footer, and the trip-booked/traveler-added emails.
- the **fountain move intake form** at **`/fountain-move`** — the public, guest-accessible Cactus & Tropicals intake form (`fountain_move.py` + `fountain-move.html`; note the underscored controller — see [Controller filenames](#controller-filenames-hyphens-are-silently-fatal)). See [its section below](#fountain-move--public-intake-form).
- the **feedback SPA** at **`/feedback`** — employee bug/feature intake and the reviewer's
  queue ([ADR 0010](../../decisions/adr/0010-employee-feedback-to-tasks.md)), chrome-free and
  login-gated (`feedback.py` + `feedback.html`; front end in
  [`public/js/feedback/`](../public/README.md), server surface in
  [`api/feedback.py`](../api/README.md)).

  **It is the only page here that serves a whole URL subtree.** `hooks.py` carries
  `website_route_rules = [{"from_route": "/feedback/<path:feedback_path>", "to_route": "feedback"}]`,
  so a hard refresh at `/feedback/request/ER-YYYY-NNNNN` — the URL every notification this
  feature sends links to — renders this same shell and the bundle routes itself from
  `location`. The server does not parse the path: a server that parses it is a second router
  to keep in step with the first. (The rule list held a second entry, `/chat/<path:chat_path>`,
  until [ADR 0011](../../decisions/adr/0011-retire-google-chat-and-coworker-chat.md) retired
  the chat SPA in v1.426.0; both notes here were learned on it.)

  **The `website_404` trap comes with that rule.** Loading a deep link *before* the rule
  shipped caches that URL in Frappe's `website_404` cache until Redis is flushed. A full
  deploy FLUSHDBs Redis and clears it; a hotfix without a restart does not. Do not advertise
  the route before the deploy carrying it has landed.

  **There is deliberately no feature gate in the controller.** It ships live: anybody who can
  sign in can file. `Product Feedback Settings.paused` stops new *submissions* and is enforced
  in `api.feedback.submit_request`, not here — pausing intake should still let people read what
  they already filed and let a reviewer finish the queue, and a page that 404'd on pause would
  take both away.

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
www/kiosk.py                 Frappe web-page controller — auth gate + boot payload
www/kiosk.html               chrome-free app shell — inline theme script, manifest, css, the six scripts
www/kiosk-sw.js              service worker (root scope) — precache, offline, IndexedDB geo queue, Background Sync, stats
www/kiosk-manifest.json      PWA web manifest — name, icons, minimal-ui/standalone display, light-palette colours

public/css/kiosk/kiosk.css   the whole visual layer: --tk-* tokens (light + two identical dark blocks), hero, tabs, sheets
public/js/kiosk/ui.js        KioskUI — theme, bottom sheets (ask / askText), toasts, node builder, formatting
public/js/kiosk/geo.js       KioskGeo — watchPosition + heartbeat + anchorFix, fix_source, diagnostics, SW stats
public/js/kiosk/myday.js     My Day tab — 14-day strip, the day's intervals, correction requests
public/js/kiosk/map.js       Map tab — own trail on Leaflet (lazy, frappe's vendored copy), theme-aware tiles
public/js/kiosk/settings.js  Settings tab — theme control, tracking diagnostics + fix-it guide, install, refresh
public/js/kiosk/app.js       state machine, the Clock tab, every clock-event sheet, photo queue, SW loop, tab bar
api/time_kiosk.py            get_kiosk_bootstrap, log_time, get_my_day, get_shift_summary, log_geolocation_batch, …
```

- **`kiosk.py`** — `get_context()` is an auth gate (guests redirect to `/login?redirect-to=/kiosk`); for signed-in users it calls `api.time_kiosk.get_kiosk_bootstrap()` and passes `boot_json` (employee / current interval / settings / CSRF), `csrf_token`, and `deploy_version` (the per-deploy cache-bust token — mtime of `sites/assets/assets.json`, i.e. new on every `bench build`; app-version fallback) to the template. `no_cache = 1` forces a fresh per-user render.
- **`kiosk.html`** — extends `templates/web.html` but renders chrome-free. The first thing in `head_include` is an inline theme script (below), **before** the stylesheet; then the manifest, theme-color / Apple / standalone meta, touch icon, `css/kiosk/kiosk.css`, and at the end of the body the six scripts in order `ui.js`, `geo.js`, `myday.js`, `map.js`, `settings.js`, `app.js` — every mutable asset URL carries `?v={{ deploy_version }}` because raw `/assets` are served with a 1-year immutable Cache-Control (icons stay unversioned: content never changes); injects the boot payload as `window.KIOSK_BOOT` / `window.KIOSK_CSRF` / `window.KIOSK_BUILD`. `app.js` renders into `#kiosk-root` and registers the service worker as `/kiosk-sw.js?v=<KIOSK_BUILD>`. Every js/css URL here must also be in the worker's `PRECACHE` — `tests/test_kiosk_frontend.py` checks both directions, and that every `api.time_kiosk.<name>` the scripts dial is a whitelisted def.
- **`kiosk-sw.js`** — root-scope service worker:
  - **install** → precache the app shell (`PRECACHE`, with `cache: 'reload'` + the `?v=` suffix so the immutable HTTP cache can't feed stale bytes) + `skipWaiting`.
  - **activate** → delete every cache whose name ≠ `CACHE` + `clients.claim`.
  - **fetch** → network-first for `/kiosk` navigations (cached-shell fallback); network-first with last-good fallback for the kiosk's own GET APIs; cache-first-with-background-refresh for **exactly the `PRECACHE` list** (the kiosk's shell + the manifest) and nothing else — it is a root-scope worker, and `tests/test_kiosk_service_worker.py` pins that it never answers for the app's asset root. Leaflet is deliberately not precached (see Map below).
  - **geo queue** → the page posts `config` / `enqueue` / `flush` messages; points persist in IndexedDB (`TimeKioskDB` v2, store `GeoQueue` keyed by `client_id`, plus a `Meta` kv store for `csrf_token` / `max_batch_size`), then upload in batches (default 50) to `api.time_kiosk.log_geolocation_batch`. Each point carries `fix_source` (`Watch` / `Heartbeat` / `Catch-up`). The server echoes `accepted` / `rejected[].client_id`; accepted and permanently-rejected (`invalid_coords`, `low_accuracy`) points are deleted, transient ones stay queued. A failed flush registers a **Background Sync** (`flush-geo`) that re-runs on reconnect.
  - **stats** → a `{type:'stats'}` message with a `MessageChannel` port is answered with `{queued: n}` (how many points are still in IndexedDB) — the Settings tab's "Queued points" line, via `KioskGeo.queuedCount()`.
- **`kiosk-manifest.json`** (JSON, no comments) — `name`/`short_name` "Time Kiosk", `id`/`start_url`/`scope` = `/kiosk`, `display: standalone` with `display_override: ["minimal-ui"]` (Chromium installs get native back/forward/refresh chrome; on iOS the app runs standalone — there is no in-app back/forward bar any more, the app is single-page with a tab bar, and **Refresh app** lives in Settings), `orientation: portrait-primary`, `theme_color` and `background_color` = the light `--tk-bg` (`tests/test_kiosk_theme.py` pins them to the stylesheet). Icons list **PNGs first** (192, 512, maskable-512) then SVGs — the PNGs satisfy Chrome/Edge installability (see CHANGELOG 0.3.0).

## Theme — light / dark / system

Three modes, **system** the default, chosen in the Settings tab and stored in `localStorage.tk_theme`. The mode is expressed as `<html data-theme="light|dark">`; the attribute is **absent** for system, and `kiosk.css`'s dark palette then follows `prefers-color-scheme`. Mechanics:

- The inline `<script>` at the top of `kiosk.html`'s `head_include` reads the key in a `try/catch` (storage throws in some embedded contexts) and sets or removes the attribute **before the stylesheet is parsed**, so the first paint is already the right palette.
- `kiosk.css` declares every `--tk-*` token in `:root` (light), then again in `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }` and in `:root[data-theme="dark"] {…}` — two **identical** dark blocks, because a media block and a plain rule cannot share one declaration list. `body` has an explicit background. Same three-way mechanism as `css/training/player.css` (its header explains why honouring only the media query is a bug).
- `KioskUI.theme` (`ui.js`) owns every later change and rewrites `<meta name="theme-color">` from the computed `--tk-bg` on each change **and** on a `matchMedia('(prefers-color-scheme: dark)')` change while in system mode, so the browser chrome never disagrees with the page. The Map tab listens to the same event to swap tile layers.
- `tests/test_kiosk_theme.py` fails the build if the two dark blocks drift, if a light token has no dark twin (and is not on its allow-list of non-colour tokens), if the theme script does not precede the stylesheet, or if the manifest colours stop matching the palette.

## Views, sheets, diagnostics

Bottom tab bar **Clock · My Day · Map · Settings**; everything is one page, and every interruption is a `KioskUI` bottom sheet (focus-trapped, `aria-modal`, Escape / backdrop close) — there is no `window.confirm` / `prompt` / `alert` anywhere under `public/js/kiosk/` (`tests/test_kiosk_frontend.py`).

- **Clock** — a state-coloured hero (idle / working green / break amber / day complete blue) with the wall clock, elapsed time, project, activity pill, photo count and a tracking chip whose text is reason-specific (`off | ready | on | denied | unavailable | insecure | hidden`). Idle: project picker as a full-screen sheet with **Recent** (`recent_projects`), **Nearest** (by the device's last fix, distance badge) and **All** (search by title or PRJ-#); task picker; activity chips; note; the nearby-visit suggestion card and Today's Visits as before. **Clock In** takes an anchor fix (`KioskGeo.anchorFix` — high accuracy, up to 3 attempts inside ~15 s, never fails) and, when the project has site coordinates, `radius_m > 0`, the fix is outside it and `Time Kiosk Settings.offsite_warn` is on, shows the off-site sheet ("You're 3.2 km from …") before posting `log_time` with `offsite_acknowledged: 1`. **Pause** opens the break sheet (15 / 30 / 45 / 60 / custom / no timer → `break_minutes`); the Break view counts down from `planned_break_minutes` and vibrates/beeps once at zero (in-app only). **Switch** → attachments nudge → picker → confirm sheet → photo gate → maintenance warning (only when leaving the project) → anchor + off-site check → `log_time Switch`. **Clock Out** → `get_shift_summary` review sheet → Confirm → maintenance warning → photo gate (+ skip reason) → attachments nudge → anchor → `log_time Stop` → the **Day complete** screen with "Start another job". Photos/attachments and the maintenance-form link work exactly as before (see `app.js` for the register-before-upload photo queue).
- **My Day** — `get_my_history` (14-day strip, zero days included) and `get_my_day` (totals, one row per interval with tracking-health / off-site / auto-closed / corrected / break / photo badges). A row opens a detail sheet with **Request a correction** (Adjust Times / Change Project / Missed Clock-Out); "Missed an entry?" files a Missed Entry; both go to `submit_correction_request`. The employee's requests (`get_my_correction_requests`) list with status, cancellable while Requested (`cancel_correction_request`).
- **Map** — own trail for a chosen day (`get_my_trail`): one polyline per interval, fixes as circles (Low Accuracy hollow), start/end rings, site geofence circles. Leaflet is loaded **lazily** from frappe's vendored `/assets/frappe/js/lib/leaflet/leaflet.js` + `.css` — not precached, because a root-scope worker may only answer for its own shell and a vendored library never changes. Offline the tab says "The map needs a connection". Tiles: OpenStreetMap in light, CARTO `dark_all` in dark, swapped live on theme change.
- **Settings** — theme segmented control; tracking diagnostics from `KioskGeo.getDiagnostics()` (status with reason, permission, last fix age + accuracy, wake lock, secure context, installed vs browser tab, online) refreshed every 5 s, queued points (SW `stats`) and queued photos (the localStorage photo queue), a per-platform **fix-it guide** sheet (iOS: Settings → Privacy & Security → Location Services → Safari Websites → While Using + Precise; keep the app on screen; Low Power Mode pauses GPS. Android Chrome: site permissions → Location → Allow + Precise; battery → Unrestricted; keep the screen on), Re-check permission, Send queued now; install (`beforeinstallprompt` where offered, Share → Add to Home Screen on iOS); **Refresh app** (SW update check + reload); version (`KIOSK_BUILD`); the consent text.

## Cache versioning — automatic per deploy

The asset cache **versions itself**; only the IndexedDB schema is still bumped by hand:

- `CACHE = 'time-kiosk-' + VERSION` — `VERSION` is the `?v=` query of the worker's own registration URL, which `app.js` sets to `window.KIOSK_BUILD` (= `kiosk.py::get_deploy_version`, the mtime of `sites/assets/assets.json` — new on every `bench build`). A deploy is therefore a new SW script URL → the browser installs the new worker → `activate` deletes every other cache. The same `?v=` token is appended to the shell's asset URLs (busting the 1-year-immutable `/assets` HTTP cache) and precaching fetches with `cache: 'reload'`. **No manual bump needed** for shell/asset/manifest changes — deploying is the bump. `app.js` additionally calls `registration.update()` on foreground + hourly, and reloads the page once (deferred until the app is hidden) when an updated worker takes control, so even a never-relaunched kiosk converges on the current deploy; Settings → **Refresh app** does the same on demand. A new script under `public/js/kiosk/` has to be added to `PRECACHE` **and** loaded by `kiosk.html` — `tests/test_kiosk_frontend.py` refuses either half on its own.
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

## The two training routes are redirects, not pages

`training.py` and `training_analytics.py` render nothing. The learner player is a Desk
Page (`training/page/learn/`) and the manager dashboard is another
(`training/page/training_insights/`); both website routes survive only so that existing
links keep working.

`/training` is the one that matters: six senders have emailed it since v1.208.0 —
assignment and due/escalation digests, answered questions, sign-off requests, graded
submissions, evaluation invites — and those messages are still in inboxes. Deleting the
route would 404 all of them.

Neither redirects unconditionally. A user with no desk access sent to `/desk` gets a login
page, which is a worse answer than a sentence, so both render one paragraph for that case.

**`/training_certificate` is not one of these and must not become one.** It is the only
guest-reachable training surface: an external auditor scans the code printed on a
certificate, and it deliberately prints initials and dates only.
`tests/test_training_certificates.py` fails the build if that route learns to redirect
into the Desk, or if the code branch stops being answered before the Guest check.
