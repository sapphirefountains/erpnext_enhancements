# Time Kiosk native app — design spike (not a build)

**Status:** design only, decided 2026-09-17 with Nik. The browser kiosk got its reliability pass in
v1.480.0; this document is what a native wrapper would add, what it would cost, and how it would be
built if the browser version turns out not to be enough. Tracked as
[WI-076](../work-items/WI-076-kiosk-native-app.md).

## The problem a native app solves, and only that problem

A web page **cannot read GPS while the screen is off or the app is in the background** on any
phone. That is a platform rule, not a bug in our code:

- **iOS (Safari and Home-Screen web apps):** `watchPosition` stops the moment the page is hidden;
  there is no Background Sync, no Periodic Background Sync, no background geolocation for web
  content, and Low Power Mode throttles location further. A technician who locks their phone and
  puts it in a pocket sends nothing until they unlock it and the app is on screen again.
- **Android (Chrome):** the same suspension applies within roughly a minute of the page going
  hidden; an installed PWA behaves like a tab for this purpose. Periodic Background Sync exists on
  Chromium but a service worker cannot access geolocation, so it can flush a queue and nothing else.

Everything else the browser kiosk does — anchor fixes at every clock event, foreground tracking with
gap accounting, offline queueing, photo capture — is unaffected and is what v1.480.0 hardened. A
native app is worth considering **only** if the business decides the trail between clock events
needs to be continuous with the screen off. That is a policy question about how the location
data is used, and it should be answered before anything below is built.

## What "native" would mean here

**Capacitor shell around the existing `/kiosk` page.** Not a rewrite. Capacitor loads a WebView
pointed at the live site, so the UI, the offline logic, the photo queue and every endpoint stay as
they are; the shell adds native plugins the page can call through `window.Capacitor`.

- **Background location plugin.** Two credible options:
  - `@capacitor-community/background-geolocation` — free, MIT, maintained; delivers fixes while
    backgrounded on both platforms; no geofencing or motion detection; batching is ours to write.
  - Transistorsoft `react-native-background-geolocation` / `cordova-background-geolocation`
    (Capacitor-compatible) — commercial licence (per-app, one-time, order of $300–$400 for the
    Android licence at the time of writing; iOS is free), best-in-class motion detection,
    geofencing, HTTP batching with retry, and battery management. This is the one field-service
    products actually ship with.
- **The page keeps ownership of the queue.** The plugin's fixes are handed to the same IndexedDB
  queue the service worker already drains, with `fix_source = "Background"` (one new Select
  option). Nothing server-side changes except accepting that option.
- **Clock events still come from the page.** The native layer never clocks anyone in or out; it
  only fills the trail between events.

## Permissions and the honest user experience

- iOS requires **"Always"** location for background fixes, and Apple's review requires a plain
  in-app explanation of why before the prompt, plus a visible indicator while tracking. The app
  should request "While Using" first and upgrade to "Always" only from a screen that explains the
  policy (recorded only while clocked in and active — the same sentence the kiosk shows today).
- Android requires `ACCESS_BACKGROUND_LOCATION` and a **foreground service with a persistent
  notification** ("Time Kiosk is recording your location while you are clocked in"). Google Play
  reviews background-location apps by hand and asks for a video of the flow.
- Both platforms let the user revoke this at any time; the kiosk's diagnostics sheet must reflect
  the native permission state as it does the browser one.

## Distribution

The company already runs a device registry (`device_management`, no external MDM provider yet —
Phase 2 is unbuilt). Three routes, cheapest first:

1. **Android only, sideloaded/managed.** An APK distributed through the device console or, once
   an MDM provider lands, as a managed app. No store review. Covers company Android handsets only.
2. **Public stores.** Apple Developer Program ($99/yr) and Google Play ($25 one-time); review
   cycles of days; background-location apps get extra scrutiny on both. Required for BYOD iPhones.
3. **Apple Business Manager + custom app distribution.** Private distribution to enrolled devices
   without a public listing; still App Review; needs an MDM to be useful.

Sixteen active employees, mixed BYOD: option 2 is the realistic one if iPhones are in scope, and
that is the single largest cost item — not the code.

## Cost, honestly

| Item | Order of magnitude |
|---|---|
| Capacitor shell + plugin integration + permission screens + diagnostics | 1–2 weeks of engineering |
| Server: accept `Background` fix source; nothing else | hours |
| Store accounts, listings, screenshots, privacy labels, review back-and-forth | 1–2 weeks elapsed, mostly waiting |
| Ongoing: OS updates break background-location behaviour roughly yearly on each platform; a native build has to be rebuilt and re-reviewed | recurring, unbudgeted today |
| Transistorsoft licence (if chosen) | one-time, low hundreds |

Against that: the browser kiosk with wake lock on, anchor fixes at every clock event and per-interval
gap accounting already answers "were they at the site when they clocked in and out" and "how much of
the shift do we have a trail for". If the answer to the second question is consistently "most of it",
a native app buys little.

## Decision gate

Build WI-076 only when **all** of these are true, measured after a full pay period on the browser
kiosk:

1. Median `tracking_coverage_pct` on completed intervals is below a threshold ops sets (suggest
   70 %), **and** the gaps are explained by locked screens rather than by permission denials or
   dead zones (the diagnostics sheet and the timeline's gap view tell these apart).
2. Somebody owns the store accounts and the annual rebuild.
3. The privacy policy has been updated for background collection and staff have been told in
   writing what the persistent notification means.

Until then the browser kiosk is the product, and this document is the record of why.

## If it is built: sequence

1. Capacitor project in a sibling repo (`sapphire-kiosk-native`), WebView pointed at
   `https://<site>/kiosk`, no bundled web assets (the site stays the single deploy target).
2. Add `Background` to `Time Kiosk Log.fix_source` and accept it in `log_geolocation_batch`.
3. Plugin bridge in `public/js/kiosk/geo.js`: when `window.Capacitor` is present, subscribe to
   the plugin while clocked in and Open, and stop on Pause/Stop; hand fixes to the existing queue.
4. Permission screens and diagnostics in the Settings sheet.
5. Android internal-testing track first, with the four field technicians; iOS TestFlight second.
6. Store submission with the background-location justification video.
