# `www/` — standalone web pages (Time Kiosk, Wall Display, traveler itinerary, feedback, marketing, Stock Scan)

Several standalone web pages live here, separate from the heavy desk app:

- the **Time Kiosk** at **`/kiosk`** — installable PWA for technicians, chrome-free (most of this README);
- the **Wall Display** at **`/wall`** — read-only project/TV dashboard, chrome-free (see below);
- the **traveler itinerary** at **`/itinerary`** — chrome-free (see [its section below](#itinerary--traveler-itinerary-page));
- the **travel guidelines** at **`/travel_guidelines`** — the company travel policy document, login-gated, standard website chrome (`travel_guidelines.py` + `.html`; static content with "In the system" callouts mapping each policy rule to the Travel Management flows). Linked from the Travel workspace shortcut, the `/itinerary` footer, and the trip-booked/traveler-added emails.
- the **fountain move intake form** at **`/fountain-move`** — the public, guest-accessible Cactus & Tropicals intake form (`fountain_move.py` + `fountain-move.html`; note the underscored controller — see [Controller filenames](#controller-filenames-hyphens-are-silently-fatal)). See [its section below](#fountain-move--public-intake-form).
- the capture widget's recorder (`capture.bundle.js`, WI-079 slice 2) is included by exactly
  five templates here: `kiosk.html`, `feedback.html`, `itinerary.html`,
  `travel_guidelines.html` and `stock-scan.html`. Each sets `window.EE_CAPTURE` (surface, user,
  CSRF token, the panel's hashed URL) first in its script block. Two have no floating launcher
  (`launcher: false`) and draw their own "Report a problem" instead: the kiosk in its Settings
  tab, Stock Scan in its header (the button would sit on Scan). No other page loads any capture
  code, and `tests/test_feedback_capture_surface.py` fails the build if one does, or if a page
  without the launcher has no door of its own
  ([`public/README.md`](../public/README.md), `capture/`);
- the **feedback SPA** at **`/feedback`** — employee bug/feature intake and the reviewer's
  queue ([ADR 0010](../../decisions/adr/0010-employee-feedback-to-tasks.md)), chrome-free and
  login-gated (`feedback.py` + `feedback.html`; front end in
  [`public/js/feedback/`](../public/README.md), server surface in
  [`api/feedback.py`](../api/README.md)).

  **It serves a whole URL subtree** (so does `/marketing`, below, the same way). `hooks.py` carries
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
- the **marketing app** at **`/marketing`** (TASK-2026-01487, v1.515.0): the calendar, composer,
  media library and approval queue for social posts, chrome-free (`marketing.py` +
  `marketing.html`; front end in [`public/js/marketing/`](../public/js/marketing/README.md),
  server surface in `marketing/publish/spa.py` and `approval.py`). A second
  `website_route_rules` entry, `/marketing/<path:marketing_path>`, serves the subtree exactly as
  `/feedback`'s does, with the same `website_404` trap. **Unlike `/feedback` it is gated:** a
  signed-out visitor is sent to log in and back to the same path, and a signed-in one without
  Marketing Team, Marketing Manager or System Manager gets a 403. There is no "publishing is off"
  gate: writing and approving posts while publishing is switched off is how the team gets ready.
- the **Stock Scan page** at **`/stock-scan`** (v1.521.0): scan the QR label on a location,
  then take parts, receive stock or put it away with − / + and Save. Chrome-free, phone-first,
  gated to the stock roles (`stock_scan.py` + `stock-scan.html`; front end in
  [`public/js/stock_scan/`](../public/README.md), server surface in
  [`api/stock_scan.py`](../api/README.md)). **A query string, not a route rule** — see
  [its section below](#stock-scan--the-stock-scan-page).
- the **warehouse QR labels** at **`/warehouse-labels`** (v1.521.0): a printable sheet of those
  labels, one per stock-holding warehouse, server-rendered (`warehouse_labels.py` +
  `warehouse-labels.html`). See [its section below](#warehouse-labels--printable-qr-labels) —
  it has print settings that are not optional.

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

## `/stock-scan` — the Stock Scan page

The phone page a warehouse QR label opens: the items at that location, a − / + stepper, Save,
**Scan next**. What each save posts, and why, is in
[`inventory_enhancements/README.md`](../inventory_enhancements/README.md#the-stock-scan-page);
this section is the shell.

```
www/stock-scan.html               chrome-free shell — theme script first, bundled css, capture recorder, boot payload, bundled js
www/stock_scan.py                 controller — guest → login (query kept), role gate, boot via api.stock_scan.boot_payload
public/js/stock_scan.bundle.js    entry: mounts StockScanApp on #ee-stock-scan-root
public/js/stock_scan/             app.js (state machine), nav.js (Back/Forward, pure), scanner.js, transport.js, logic.js (pure), ui.js, dom.js, lib/jsQR
public/css/stock_scan.bundle.css  --ss-* tokens: light, then two identical dark blocks
api/stock_scan.py                 every endpoint the page calls (transport.js's M map)
```

- **The shell is the `/feedback` one.** It extends `templates/web.html` with the chrome blocks
  emptied, loads its CSS and JS through `bundled_asset()` (content-hashed — a raw `/assets` path
  is served year-immutable, so an edit never reaches a phone that already cached it), and hands
  the boot over as `window.EE_STOCK_SCAN_BOOT` (`| tojson`). `no_cache = 1`: the render carries
  the caller's name and recent saves. The theme script (`localStorage.ee_ss_theme`) runs first in
  `head_include`, before the stylesheet, as the kiosk's does. No Vue, no `frappe.*` and no
  `innerHTML` under `public/js/stock_scan/`: item, supplier and job names are data.
- **The gate.** A signed-out visitor goes to `/login?redirect-to=<full path>`, and `full_path`
  keeps the query string — a technician whose session lapsed scans a bin, logs in, and lands on
  that bin. Signed in without a stock role (`stock_scan_rules.SCAN_ROLES`) is a 403. The scanned
  location is resolved server-side into the boot (`boot_payload`), so the first screen costs no
  second round trip on a warehouse's phone signal.
- **Why a query string (`?w=`), not a path.** A path would need a `website_route_rules` entry and
  bring the `website_404` trap described under `/feedback` above — and these URLs are *printed*:
  stuck on shelves months ahead and scanned whenever, including in the minutes before a deploy
  that would have added the rule. A query string needs no rule at all. The keys (`w`, `item`,
  `loc`) stay clear of the request keys Frappe consumes before a page sees them — `sid`,
  `csrf_token`, `cmd`, `usr`, `pwd`.
- **The URL never changes while the page runs, but Back and Forward work.** iOS Safari asks for
  camera permission again when the URL changes, so a page that routed would re-prompt at every
  bin. Navigation is an in-memory back stack, and every view has a visible back link; `nav.js`
  mirrors it into browser history entries that carry **no URL** — every call is
  `pushState(state, "")` / `replaceState(state, "")`, nothing else in the client may call either,
  and `tests/test_stock_scan_surface.py` fails the build on a third argument. The rules:
  - the document's first screen is stamped onto the entry the phone opened (`replaceState`), so
    Back from it leaves the page as it always did — nothing traps Back;
  - a screen the person asked for is a new entry; one the page opened by itself (a scanned bin
    holding one item) replaces the entry it came from. Push only from a tap: Chrome's Back
    button skips an entry a page added without a user tap, and a camera read is not a tap;
  - a sheet (the camera is one) pushes one **marker** entry inside the tap that opens it, so
    the phone's Back closes the sheet instead of leaving the screen. A sheet closed from its ×
    steps back off its marker; one that closed into a navigation (a scan, a search pick) hands
    the marker's entry to the screen that lands;
  - screens live in memory, keyed by an id in `history.state`; item and supplier names never go
    into `history.state`, which browsers write to disk. An entry the page did not write (a
    reload's, the report form's) is never restored from: the page stays put and re-stamps it.

  A reload still returns to the location in the URL — the label the phone's camera app opened —
  not to the last bin scanned in the page. **The off switch is a settings box, no deploy
  needed:** Inventory Scanner Settings → "Turn Off Browser Back on Stock Scan"
  (`stock_scan_disable_browser_back`, v1.534.0). Ticked, the boot sends
  `settings.browser_history: 0`, the page writes none of its **own** entries (screens and sheet
  markers) and runs on its in-memory stack alone, as before; and the template sets
  `EE_CAPTURE.history: false`, so the report form pushes no entry either
  (`capture/panel.js` `wantsHistoryEntry`). It is the fix if an iPhone ever re-prompts for the
  camera because of an entry. `BROWSER_HISTORY` in `app.js` is the same switch for the page's
  own entries as a one-line code change. Check on a real iPhone (Safari and a home-screen
  install): open a label, scan three bins in the page, Back twice, Forward once, scan again —
  one camera prompt in total; then open "Report a problem", Back out of it, and scan again.
- **"Report a problem"** (the capture panel, WI-079) is a header button on every view, beside the
  job chip. The template sets `launcher: false` because the floating launcher would sit on Scan;
  `app.js` opens the panel through the `window.ee_capture` global as surface `web` (the bundle may
  not import the recorder) and says what to do if it does not open. The panel pushes and removes
  its **own** history entry and answers the phone's Back itself ("Discard this report?"), so the
  page adds no marker for it and ignores `popstate` while it is open. Opening it drops a lookup
  still loading, whose screen would otherwise land under the form, and from the tap until the
  form closes the page opens no sheet and starts no navigation (`reportBusy`): the form
  downloads on first use, and a camera opened under it on a slow connection would keep
  decoding, navigate under the form and take the letters typed into it. What a report carries from
  here: the path `/stock-scan` (never the `?w=` query), the recorder's scrubbed rings, and
  `registerCaptureState` codes and counts — view, warehouse, item code, stack depth, sheets open,
  whether a store run is open and how many lines it has — no names, quantities, suppliers, prices
  or jobs. The page is never sent a stock cost; since v1.535.0 it shows store-run prices the
  technician typed, and those stay out of a report.
- **"Bought on a store run"** (v1.535.0; what it posts is in
  [`inventory_enhancements/README.md`](../inventory_enhancements/README.md#bought-on-a-store-run-v15350)).
  `boot.store_run` carries the stores (one per `store_key`, the newest usable Supplier), the
  reasons, the quick-item groups and units, two permissions and the runs still open today; it is
  `None` where store runs are not set up, and then the page is exactly as before. `boot.now` is the
  site's clock: another person's run is offered only while its last line is under three hours old
  by it (`logic.runOffered`), never by the phone's own clock or zone. The non-stock item card's
  *Bought it on a store run* and search's *Not in ERPNext?* ask *Which store run?* when one is open
  (the person's current run first, then *A different store run*), so a trip is one run. A run whose
  every line was undone reopens as a new run's header, prefilled to correct; *Finish* says "Check
  the receipt total" when it is well above the lines plus tax, and a joined run's header shows the
  lines so far against the receipt. Each line
  carries the page's own day (`page_today`), so a page left open overnight is told to reload. The **run
  sheet** is a full-height sheet like Move: a new run's header (store, today or yesterday, the
  receipt photo, the total with tax, the receipt number — drawn once; a store or day tap redraws
  only its own buttons, and a photo landing only its own field), then the line (item or quick item, how
  many, price each before tax, why, the job or "No job: safety or shop"). The **receipt photo** is
  shrunk on the phone (`dom.shrinkPhoto`, 1,600 px JPEG) and sent at once through
  `transport.upload` (XHR for progress; a 403 there can only be a lapsed session and says so);
  there is no `capture` attribute, so a photo already taken can be picked. The **run id** is
  minted once per new run and kept on the app with the header typed so far until a line posts, so
  a retry or a reopened sheet is the same save. The **run bar** sits at the top of every view while
  this phone's run is open; `localStorage` (`ee-ss-store-run:<user>`) holds only which run is
  current and which were finished here — lose it and the run is still offered on the next +.
- **The camera.** `getUserMedia` needs https. The browser's `BarcodeDetector` is used only
  where `getSupportedFormats()` lists `qr_code` (Chrome on Android); everywhere else — every
  iPhone — the vendored **jsQR** decodes frames drawn to a canvas at most 480 px wide, about
  five times a second. jsQR is loaded once, by a plain `<script>`, from `decoder_url` in the
  boot — **not** bundled, because esbuild would inline 130 KB into every page load for the
  Android phones that never need it; a raw `/assets` path is safe for it because the version is
  in the filename and the file is never edited. jsQR folds the options it is given into its
  module defaults, so every call passes the same ones. Every camera track is stopped on close,
  on a hit, when the page is hidden and on `pagehide` — a live track keeps the iOS capture
  indicator lit. The **Type a code** box is also where a Bluetooth or USB scanner gun's
  keystrokes and Enter land. The Desk count page (`inventory_scanner_audit`) got the same jsQR
  fallback in the same release, through `frappe.require`.
- **A retried save does not post twice.** The page mints a `client_ref` per intended save and
  resends the same one after a network failure or timeout, a proxy 502/503/504, or a 409 (the
  same reference still being posted by an earlier attempt) — `logic.mayHaveSaved` — so the
  server returns the first save instead of making a second; any other refusal (nothing saved)
  or a change to what the save would post (`logic.saveKey`: quantity, item, location, order
  line, job) mints a fresh one. Walking to another screen and back keeps it. The server half is
  in the inventory README.
- **Tests** (bench-free): `tests/test_stock_scan_surface.py` (the endpoint surface, the shell,
  the controller, the two-argument history rule, the Back and report wiring),
  `tests/test_stock_scan_theme.py` (the three-way theme, cloned from the kiosk's) and
  `scripts/test_stock_scan_client.mjs` (the pure `logic.js`, the transport's error extraction
  against the same parse vectors as the Python rules, `nav.js` driven against a fake session
  history that refuses any call with a URL, and the real `app.js` on a small fake DOM for the
  report form's sequences: nothing opens or navigates under it while it loads or is open).

## `/warehouse-labels` — printable QR labels

A sheet of QR labels, one per warehouse that can hold stock (a leaf, not disabled, not
Transit), each encoding that warehouse's `/stock-scan?w=` URL. Server-rendered: every QR code
is an inline SVG drawn by `inventory_enhancements/qr_svg.py`, so what prints is exactly what the
preview shows and nothing needs a script to put a code on paper. The small inline script only
paginates — leaves the first `skip` cells blank on a part-used sheet, drops locations unticked
in the list — and calls `window.print()`.

- **Who and what.** Signed-out visitors go to log in and come back; signed in, the stock roles
  plus Item Manager (`stock_scan_rules.LABEL_ROLES`). Query: `under=<group warehouse>` (only the
  locations beneath it — the group Warehouse form's **Print QR Labels**), `w=<name>` repeatable
  (exactly these — a location's **QR Label** button; wins over `under`),
  `size=avery-5160|avery-5163|thermal-2x1`, `skip=<n>`.
- **Print settings are part of the product.** Print from desktop Chrome or Edge (only Chromium
  honours `@page` size reliably). **Scale 100%** ("Actual size" / Custom 100 — not "Default" or
  "Fit to printable area"), **Margins None**, headers and footers off. The sheet geometry
  already carries the label stock's own margins (`stock_scan_rules.LABEL_PRESETS`), so any
  margin the browser adds shifts every label off its die-cut. For a label printer, define a
  2 × 1 in stock in the printer driver and pick it as the paper size.
- **The Bootstrap print trap, and why "Default" scale is wrong.** Frappe's website stylesheet
  compiles the whole of Bootstrap 4.6.2 (`frappe/public/scss/website/index.scss`), and
  Bootstrap's print partial — `$enable-print-styles` defaults on and Frappe never turns it off —
  adds, inside `@media print`, `@page { size: a3 }` and `min-width: 992px !important` on both
  `body` and `.container`. The page's own `@page` comes later in the cascade and wins the paper
  size. The `min-width` does not lose on its own: a 992 px minimum on an 8.5 in (816 px) page is
  wider than the paper, and at "Default" scale Chrome shrinks the whole sheet to fit that width
  (816 / 992 — roughly 82% on Letter; worse on a 2 in thermal label), so every label misses its
  die-cut. A www page that prints exact geometry has to reset `min-width: 0 !important` on
  `body` and `.container` in its print CSS, and still says Scale 100% next to the Print button.

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
public/js/kiosk/map.js       Map tab — own trail on Google Maps (shared EEGoogleMaps loader), theme-aware
public/js/kiosk/settings.js  Settings tab — theme control, tracking diagnostics + fix-it guide, install, refresh
public/js/kiosk/app.js       state machine, the Clock tab, every clock-event sheet, photo queue, SW loop, tab bar
api/time_kiosk.py            get_kiosk_bootstrap, log_time, get_my_day, get_shift_summary, log_geolocation_batch, …
```

- **`kiosk.py`** — `get_context()` is an auth gate (guests redirect to `/login?redirect-to=/kiosk`); for signed-in users it calls `api.time_kiosk.get_kiosk_bootstrap()` and passes `boot_json` (employee / current interval / settings / CSRF), `csrf_token`, and `deploy_version` (the per-deploy cache-bust token — mtime of `sites/assets/assets.json`, i.e. new on every `bench build`; app-version fallback) to the template. `no_cache = 1` forces a fresh per-user render.
- **`kiosk.html`** — extends `templates/web.html` but renders chrome-free. The first thing in `head_include` is an inline theme script (below), **before** the stylesheet; then the manifest, theme-color / Apple / standalone meta, touch icon, `css/kiosk/kiosk.css`, and at the end of the body the six scripts in order `ui.js`, `geo.js`, `myday.js`, `map.js`, `settings.js`, `app.js` — every mutable asset URL carries `?v={{ deploy_version }}` because raw `/assets` are served with a 1-year immutable Cache-Control (icons stay unversioned: content never changes); injects the boot payload as `window.KIOSK_BOOT` / `window.KIOSK_CSRF` / `window.KIOSK_BUILD`. `app.js` renders into `#kiosk-root` and registers the service worker as `/kiosk-sw.js?v=<KIOSK_BUILD>`. Every js/css URL here must also be in the worker's `PRECACHE` — `tests/test_kiosk_frontend.py` checks both directions, and that every `api.time_kiosk.<name>` the scripts dial is a whitelisted def.
- **`kiosk-sw.js`** — root-scope service worker:
  - **install** → precache the app shell (`PRECACHE`, with `cache: 'reload'` + the `?v=` suffix so the immutable HTTP cache can't feed stale bytes) + `skipWaiting`.
  - **activate** → delete every cache whose name ≠ `CACHE` + `clients.claim`.
  - **fetch** → network-first for `/kiosk` navigations (cached-shell fallback); network-first with last-good fallback for the kiosk's own GET APIs; cache-first-with-background-refresh for **exactly the `PRECACHE` list** (the kiosk's shell + the manifest) and nothing else — it is a root-scope worker, and `tests/test_kiosk_service_worker.py` pins that it never answers for the app's asset root. The shared Google Maps loader is deliberately **not** precached (see Map below).
  - **geo queue** → the page posts `config` / `enqueue` / `flush` messages; points persist in IndexedDB (`TimeKioskDB` v2, store `GeoQueue` keyed by `client_id`, plus a `Meta` kv store for `csrf_token` / `max_batch_size`), then upload in batches (default 50) to `api.time_kiosk.log_geolocation_batch`. Each point carries `fix_source` (`Watch` / `Heartbeat` / `Catch-up`). The server echoes `accepted` / `rejected[].client_id`; accepted and permanently-rejected (`invalid_coords`, `low_accuracy`) points are deleted, transient ones stay queued. A failed flush registers a **Background Sync** (`flush-geo`) that re-runs on reconnect.
  - **stats** → a `{type:'stats'}` message with a `MessageChannel` port is answered with `{queued: n}` (how many points are still in IndexedDB) — the Settings tab's "Queued points" line, via `KioskGeo.queuedCount()`.
- **`kiosk-manifest.json`** (JSON, no comments) — `name`/`short_name` "Time Kiosk", `id`/`start_url`/`scope` = `/kiosk`, `display: standalone` with `display_override: ["minimal-ui"]` (Chromium installs get native back/forward/refresh chrome, which walks the kiosk's tabs and sheets — see Views below; on iOS the app runs standalone — there is no in-app back/forward bar any more, the app is single-page with a tab bar, and **Refresh app** lives in Settings), `orientation: portrait-primary`, `theme_color` and `background_color` = the light `--tk-bg` (`tests/test_kiosk_theme.py` pins them to the stylesheet). Icons list **PNGs first** (192, 512, maskable-512) then SVGs — the PNGs satisfy Chrome/Edge installability (see CHANGELOG 0.3.0).

## Theme — light / dark / system

Three modes, **system** the default, chosen in the Settings tab and stored in `localStorage.tk_theme`. The mode is expressed as `<html data-theme="light|dark">`; the attribute is **absent** for system, and `kiosk.css`'s dark palette then follows `prefers-color-scheme`. Mechanics:

- The inline `<script>` at the top of `kiosk.html`'s `head_include` reads the key in a `try/catch` (storage throws in some embedded contexts) and sets or removes the attribute **before the stylesheet is parsed**, so the first paint is already the right palette.
- `kiosk.css` declares every `--tk-*` token in `:root` (light), then again in `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }` and in `:root[data-theme="dark"] {…}` — two **identical** dark blocks, because a media block and a plain rule cannot share one declaration list. `body` has an explicit background. Same three-way mechanism as `css/training/player.css` (its header explains why honouring only the media query is a bug).
- `KioskUI.theme` (`ui.js`) owns every later change and rewrites `<meta name="theme-color">` from the computed `--tk-bg` on each change **and** on a `matchMedia('(prefers-color-scheme: dark)')` change while in system mode, so the browser chrome never disagrees with the page. The Map tab listens to the same event and re-applies `EEGoogleMaps.mapOptions()`.
- `tests/test_kiosk_theme.py` fails the build if the two dark blocks drift, if a light token has no dark twin (and is not on its allow-list of non-colour tokens), if the theme script does not precede the stylesheet, or if the manifest colours stop matching the palette.

## Views, sheets, diagnostics

Bottom tab bar **Clock · My Day · Map · Settings**; everything is one page, and every interruption is a `KioskUI` bottom sheet (focus-trapped, `aria-modal`, Escape / backdrop close) — there is no `window.confirm` / `prompt` / `alert` anywhere under `public/js/kiosk/` (`tests/test_kiosk_frontend.py`).

**Browser Back and Forward** walk the tabs and close sheets (`KioskUI.nav`, `ui.js` "History"), and the URL never changes. Every history call is two-argument, because iOS Safari asks for camera and location again when the URL changes and `kiosk-sw.js` serves the offline shell for the exact path `/kiosk`. So a reload lands on Clock, as before. A tab tap pushes one entry, from the tap itself. An open stack of sheets adds one more entry, and each Back closes the top sheet through `close('dismiss')`, the same path Escape takes. Every gate reads that as cancel, so Back on the review sheet, the photo gate or "Why no photo?" posts nothing. The clock state (idle / working / break / day complete) is never an entry, so Back cannot undo or repeat a clock action. The entry the page loads into is replaced, never pushed, so Back from it still leaves the page. A sheet closed from the UI, or replaced by the next one in the same burst, leaves no entry behind: its entry goes with one `history.back()`, and the `popstate` that causes is counted off rather than read as a Back. "Report a problem" pushes and answers its own entry (`capture/panel.js`), and the kiosk leaves `popstate` alone while `ee_capture.isOpen()`. Any entry that is not the kiosk's own is re-stamped with the screen as it is, never interpreted. That covers the panel's leftover entry and entries left by an earlier load of the page. It also covers a kiosk entry the person reached while the panel had the `popstate`, such as a jump several entries back with the panel open. Only a tap adds an entry. Under Chrome's history intervention each push uses up the activation of the tap before it. A push made without one marks every entry of the page skippable, and the next Back then leaves the app from wherever it is. So the kiosk pushes a tab entry only for a tab tap, and when the screen and its entry disagree for any other reason it re-stamps the entry. When Back closes the top of two stacked sheets, the marker's re-push spends the tap that stacked the second sheet. A stack three deep, a `dismissible: false` sheet refusing Back, or a sheet opened by a timer would push without a tap, and none exists today. `scripts/test_kiosk_history.js` drives all of this against the real scripts, with a fake history that models the intervention and counts every push made without a tap. `tests/test_kiosk_frontend.py` runs it. **The off switch is a settings box, no deploy needed:** Time Kiosk Settings → "Turn Off Browser Back in the Kiosk" (`disable_browser_back`, v1.534.0). Ticked, `app.js` never starts `KioskUI.nav` (no `popstate` listener, so sheets push nothing either) and `kiosk.py` hands the report panel `EE_CAPTURE.history: false`, so Back leaves the kiosk again, as before. It exists for iPhones: if one asks for location or camera permission again after a Back, tick it.

- **Clock** — a state-coloured hero (idle / working green / break amber / day complete blue) with the wall clock, elapsed time, project, activity pill, photo count and a tracking chip whose text is reason-specific (`off | ready | on | denied | unavailable | insecure | hidden`). Idle: project picker as a full-screen sheet with **Recent** (`recent_projects`), **Nearest** (by the device's last fix, distance badge) and **All** (search by title or PRJ-#); task picker; activity chips; note; the nearby-visit suggestion card and Today's Visits as before. **Clock In** takes an anchor fix (`KioskGeo.anchorFix` — high accuracy, up to 3 attempts inside ~15 s, never fails) and, when the project has site coordinates, `radius_m > 0`, the fix is outside it and `Time Kiosk Settings.offsite_warn` is on, shows the off-site sheet ("You're 3.2 km from …") before posting `log_time` with `offsite_acknowledged: 1`. **Pause** opens the break sheet (15 / 30 / 45 / 60 / custom / no timer → `break_minutes`); the Break view counts down from `planned_break_minutes` and vibrates/beeps once at zero (in-app only). **Switch** → attachments nudge → picker → confirm sheet → photo gate → maintenance warning (only when leaving the project) → anchor + off-site check → `log_time Switch`. **Clock Out** → `get_shift_summary` review sheet → Confirm → maintenance warning → photo gate (+ skip reason) → attachments nudge → anchor → `log_time Stop` → the **Day complete** screen with "Start another job". Photos/attachments and the maintenance-form link work exactly as before (see `app.js` for the register-before-upload photo queue).
- **My Day** — `get_my_history` (14-day strip, zero days included) and `get_my_day` (totals, one row per interval with tracking-health / off-site / auto-closed / corrected / break / photo badges). A row opens a detail sheet with **Request a correction** (Adjust Times / Change Project / Missed Clock-Out); "Missed an entry?" files a Missed Entry; both go to `submit_correction_request`. The employee's requests (`get_my_correction_requests`) list with status, cancellable while Requested (`cancel_correction_request`).
- **Map** — own trail for a chosen day (`get_my_trail`): one polyline per interval, fixes as symbols (Low Accuracy hollow), start/end rings, site geofence circles. **Google Maps** since v1.482.0; Leaflet is gone, with no fallback renderer. The shell loads `js/global_enhancements/google_maps_loader.js` before `map.js` — the one place this app injects the Maps API. It is the single documented exception to "every shell asset is precached": the worker is root-scope and may only answer for the kiosk's own shell, so it must not cache an asset the desk serves too. That costs nothing, because the loader's only job is to fetch the Maps API, which is third-party and uncacheable — a device offline enough to be missing it could not draw a map anyway. `tests/test_kiosk_frontend.py` holds the exemption in a named set and fails if it ever names a file the shell does not load. Offline the tab still says "The map needs a connection"; an unset API key gets its own message, because telling a technician to check their signal when the real problem is a blank setting wastes their afternoon. Light/dark come from `EEGoogleMaps.mapOptions()` — a cloud Map ID when set, a legacy `styles` array when not.
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
