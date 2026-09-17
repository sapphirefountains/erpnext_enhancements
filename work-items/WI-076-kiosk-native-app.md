# WI-076: Time Kiosk native app (Capacitor + background geolocation)
**Phase:** 2 (deferred)   **Type:** APP_CODE   **Size:** L
**Blocked by:** a full pay period on the v1.480.0 browser kiosk and the decision gate in
[`docs/kiosk-native-app-spike.md`](../docs/kiosk-native-app-spike.md)   **Blocks:** nothing

## Why

A browser cannot read GPS with the screen off on iOS or Android. The v1.480.0 kiosk pass made
foreground tracking honest — wake lock on by default, a deliberate high-accuracy fix at every
clock event, per-interval gap accounting a manager can see — and that is the ceiling of what a
web page can do. If the business needs a continuous trail *between* clock events with the phone
in a pocket, the only route is a native shell with a background-location plugin. Nik asked on
2026-09-17 for the design to be written so the choice is a decision rather than a discovery; he
also said a browser solution is preferred where it can work. This item exists so the design is
not lost and is not built by accident.

## Native-first check

Not applicable in the ERPNext sense: no native ERPNext mechanism reads a phone's GPS. Within the
options, the spike prefers a **Capacitor shell around the existing `/kiosk` page** over any
rewrite, so the UI, the offline queue, the photo gate and every endpoint stay single-sourced on the
site. The only server change is one new `fix_source` option.

## Preconditions

- The decision gate in the spike is met: measured coverage after a full period is below the
  threshold ops sets, the gaps are attributable to locked screens (not denied permissions or dead
  zones), an owner exists for store accounts and the annual rebuild, and the privacy policy and
  staff notice cover background collection.
- Device policy decided: company Android only (sideload/managed) versus BYOD iPhones (public
  App Store, which is the cost driver).

## Scope

- Sibling repo with the Capacitor project; WebView pointed at the live `/kiosk`, no bundled assets.
- Background-location plugin (community plugin, or Transistorsoft if geofencing/motion detection
  is wanted), bridged from `public/js/kiosk/geo.js` only while an interval is Open.
- `Time Kiosk Log.fix_source` gains `Background`; `log_geolocation_batch` accepts it.
- Permission flow ("While Using" first, "Always" from an explanatory screen), foreground-service
  notification text, diagnostics in the Settings sheet reflecting native permission state.
- Android internal-testing track with the field technicians, then TestFlight, then store
  submission with the background-location justification.

## Acceptance criteria

- On a company Android handset with the screen locked for 30 minutes while clocked in, the
  interval's `tracking_coverage_pct` is ≥ 95 % and `fix_source = Background` rows exist.
- The same on an iPhone with "Always" granted and Low Power Mode off.
- Revoking the permission mid-shift degrades to the browser behaviour (foreground only) with the
  diagnostics sheet naming the cause, and nothing errors.

## Rollback

Uninstall the app; the browser kiosk is unchanged and continues to work. The `Background` Select
option is inert without a sender.

## Explicitly NOT in this work item

Any change to what is recorded (still only while clocked in and active); geofence *enforcement*;
replacing the web UI; an MDM provider integration (device_management Phase 2 is its own item).
