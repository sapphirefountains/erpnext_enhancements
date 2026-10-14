# Offline cold start: opening the guided page with no connection at all (P4.8 spike)

TASK-2026-02766. Read-only spike: the app repo, Frappe `origin/version-16` (16.52.0; prod runs 16.50.0), and prod's public `/assets/assets.json` and bundle files.

## Recommendation

**Use a service worker, but not one for all of Desk.** Its scope should be `/desk/guided-entry` only, and it should be built the way `/itinerary` already is (v1.549.0). On a cold start it hands back the Desk shell, the bundles and the few read calls the guided page makes. It stays out of the way whenever the server answers. Build it **after P4.7's journal** (v1.648.0): a record that opens offline is no use if the edits are lost when the phone closes the tab. That journal is in place. It keeps unsent changes and step 1 of a record not yet created in localStorage, which survives a closed tab, a reload and a crash. It replays them from the page when the signal returns. The worker never needs to read it: iOS has no Background Sync, so the page is the only place a replay can run.

- **Size:** a 1–2 day proof on a real iPhone and an Android phone, then about 6–9 days in 3 PRs:
  1. The worker and its registration, with the offline marker generalized from `ee_itinerary_key`.
  2. Read-ahead of recent records and Link options.
  3. Quieting Desk's boot noise offline, plus the node tests.
- **Gate:** the proof has to show that Desk's own boot gets to the guided page offline without errors that block it. That is the one big unknown, and nobody has tested it. If it fails, fall back to the standalone page (option B below, about 2–3 weeks).
- **Not native.** The Capacitor route in `docs/kiosk-native-app-spike.md` loads the live site in a WebView with no bundled assets, so it has exactly the same cold-start problem. It adds store accounts, review and a yearly rebuild. It is worth doing only if WI-076 (background GPS) is built anyway.

## 1. Can Desk have a service worker on v16?

Yes. Frappe v16 ships no worker and no manifest for Desk; `git grep serviceWorker` finds nothing. This app already runs three:

- `www/kiosk-sw.js` at scope `/`
- `www/wall-sw.js` at scope `/`
- `www/itinerary-sw.js` at scope `/itinerary`

All three are served by Frappe as `www/*.js` Jinja templates, so the file must contain no `{{` or `{%`, comments included.

A worker script served at `/guided-sw.js` may claim `/desk/guided-entry` without a `Service-Worker-Allowed` header. A file under `/assets/...` cannot claim it: nginx serves those files and sets no such header.

All of `/desk/<path>` renders the same `desk.html` (`website_route_rules`), so one cached shell answers every guided-entry address. Two scope facts make the narrow scope work:

- Registrations are keyed by scope, so this worker does not replace the kiosk's root worker. A navigation goes to the registration with the longest matching scope.
- Once the worker controls a Desk document, every fetch that document makes goes through it, including `/assets` and `/api/method`.

**Register it from a small `app_include_js` snippet**, and only on the guided page, as `/guided-sw.js?v=${window._version_number}`. Desk's `_version_number` comes from the mtime of `assets.json` (`get_build_version`), the same source as `utils/deploy.get_deploy_version`. Keep the page's handle to the worker that `register()` returns, never `serviceWorker.ready`. The itinerary hit exactly this trap.

A manifest would need a `<link>` that `desk.html` has no hook for. Treat the home-screen icon as an optional extra.

## 2. What it must cache, and what is dangerous

| Item | How | Risk |
|---|---|---|
| Desk HTML shell (`/desk/guided-entry*`) | Network first, using the navigation preload's response. Fall back to the kept copy only on a failed fetch, a 502/503/504, or 6 s with no answer. Keep only a 200 that was not redirected and is not a `?cmd=` request | **Dangerous.** The shell embeds the user's whole `frappe.boot` and `frappe.csrf_token`. Name the cache after the deploy and after an offline marker, not after the email. Refuse to show the kept shell when the marker is missing or different (the shared-phone rule from `/itinerary`) |
| Bundles the shell loaded | Hashed `dist` files, taken from the page's own resource list. Cache first; they are content-addressed | **Safe.** Measured on prod: desk.bundle.js is 1.34 MB raw / 0.39 MB gzipped, controls 0.92 / 0.25, desk.css 0.81 / 0.13. All 84 bundles together are 10.5 MB raw / 3.0 MB gzipped. sf_kit plus guided_entry is 36 KB |
| Page script (`frappe.desk.desk_page.getpage`) | It is a **POST**, which Cache Storage cannot key, so the worker answers it from a copy stored under a key it makes up | Medium. Desk's own `_page:` localStorage copy is wiped after 2 days, on a deploy, and on a reload within 5 s, so the worker cannot rely on it |
| `getdoctype` (GET, child-table metas) | Per-deploy cache | Low |
| `get_flow` and `my_records` (GET) | Read ahead the user's most recent open records while online (for example 20 of the 50), roughly 10–30 KB each | Medium. Shows data as of the last sync. Draw it under an "as saved <time>" banner, purge it on sign-out, and expire it after about 7 days |
| Link search (`search_link`, a POST) | Optional: a small per-field list of the user's own Customers, Leads and Contacts, filtered inside the worker | Medium. Offline, a typed value can be checked only on replay, and the existing 417 path handles a rejection |

Writes are never cached. `save_step` and `create_record` go to P4.7's durable queue.

**On reconnect, reload the shell from the network before the queue replays.** The kept CSRF token is valid only for the session it came from. After the session expires (System Settings, 240 h by default) or the user signs in again, a POST with that token fails. The guided page already shows a sign-in panel for an expired session.

**Permissions change only what is shown.** A cached record can show something the user has since lost access to, but only data the phone already held. The server checks every write again.

**The deploy's Redis `FLUSHDB` does not touch anything on the phone.** It destroys queued jobs, and replayed saves are synchronous calls, not jobs. While the bench restarts, the server answers 502/503/504, and those must count as "no answer", as they already do in `/itinerary`.

## 3. Desk's own caching and the one-year asset rule

Prod answers hashed bundles with `cache-control: max-age=31536000`. Those files are safe to keep forever, because a new build gets new names.

The danger is a **raw, unhashed path**. The icon sprites and the vendored UMD libraries are raw paths, and so was the kiosk worker's old `/assets/erpnext_enhancements/` catch-all with `ignoreSearch`. That bug served a training player four releases stale. To avoid repeating it:

- Answer only for a manifest the worker holds. Pass everything else through.
- Delete only this worker's own older caches. `test_kiosk_service_worker.py` already enforces this rule for the other workers.

A new deploy means a new `?v=`, so a new worker installs and the old shell's cache goes.

One gap remains. With one bar of signal right after a deploy, the 6 s fallback could run the old shell against the new server. Fix it the way `/wall` does: the guided page compares a `deploy_version` field in `get_flow` with its own and reloads when they differ.

## 4. The alternatives

- **A: cache all of Desk (scope `/desk`).** This has the largest blast radius: any bug serves stale Desk to every page. About 3–4 weeks. Not recommended.
- **B: a standalone `/field` page** built like `/itinerary`. It is isolated and the pattern is proven. However, the guided page draws its fields with `frappe.ui.FieldGroup` (Link, Date, Table MultiSelect), so a page outside Desk needs its own renderer. That means a second UI to keep in step. About 2–3 weeks. This is the fallback if the proof fails.
- **Native (Capacitor).** See the recommendation. It does not solve cold start.

**iOS constraints, which apply to every web option:**

- Safari has supported service workers since 11.3, including in home-screen apps.
- In a Safari tab, script-written storage (Cache Storage and IndexedDB) is deleted after 7 days of use without a visit to the site. Home-screen apps are exempt from that.
- A home-screen app keeps its own cookies and storage, separate from Safari. Technicians should use one or the other, consistently.
- There is no Background Sync and no periodic sync, so read-ahead happens only while the app is open with signal.
- Request `navigator.storage.persist()` (Safari 17+).

On Android Chrome, everything above works in a normal tab too.

## 5. Risks, and what Nik must decide

**Risks:**

- Desk's boot offline is unproven: the realtime socket, the "Connection Lost" toast, and the boot-time `xcall`s.
- iPhone home-screen behavior is untested.
- The shared-phone exposure of a kept boot.

**Decisions for Nik:**

1. Accept the narrow worker with the proof as a go/no-go gate, or go straight to B.
2. How many recent records to read ahead (suggested: 20, kept 7 days), and whether offline Link lookups are needed or "checked when back online" is enough.
3. Confirm that P4.7's journal (localStorage, replayed by the page) is enough, or whether replay must also work with the page closed. That is Android-only, needs Background Sync and IndexedDB, and is not possible on iOS.
4. Whether technicians standardize on the home-screen app on iPhones.
