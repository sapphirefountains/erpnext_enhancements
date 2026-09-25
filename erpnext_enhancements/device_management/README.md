# Device Management (MDM / EMM) — Phase 1: native registry

ERPNext as the **system of record** for the company's mixed device fleet (Android
/ iOS phones & tablets, laptops/desktops, BYOD): who holds which device, its
lifecycle, warranty, and security posture. Works standalone — **no external MDM
provider required**. This is Phase 1 of a phased plan; Phase 2 (`mdm_integration`,
not yet built) layers a real provider (Intune/Hexnode) on top for live compliance
and remote lock/wipe.

## What's here

| Path | What |
|---|---|
| `doctype/managed_device/` | The device master — identity, hardware ids (`permlevel: 1`), assignment, warranty, compliance posture. Lifecycle is a guarded `status` Select (not submittable). |
| `doctype/device_assignment_log/` | Child table — append-only custody history (open row = current holder). |
| `doctype/device_compliance_settings/` | Single — attestation cadence, warranty lead, camera-scan toggle, notify flags. `get_settings()` is the defensive reader. |
| `compliance.py` | **Frappe-free** lifecycle/compliance rules (`is_valid_transition`, `derive_compliance`). Unit-tested bench-free in `tests/test_device_management.py`. |
| `permissions.py` | `permission_query_conditions` + `has_permission` — non-managers see only the device assigned to them (BYOD privacy). |
| `tasks.py` | Daily nudges — warranty lead-time (to Device Managers) and stale attestation (to the holder). Stamp-first / at-most-once. |
| `setup.py` | `after_migrate` — adds the Employee "Assigned Devices" panel field (insert-only). |
| `page/device_console/` | Mobile-first scan → check-in/out/transfer/repair/lost, and enroll-on-unknown-scan. |
| `page/device_fleet_dashboard/` | Green/amber/red fleet snapshot (status, compliance, attestation, warranty). |

API lives at the top level alongside the other feature APIs:
- `api/device_management.py` — scan resolution + lifecycle + self-service attestation.
- `api/device_dashboard.py` — fleet health payload (reuses `api/integrations_health.py` tone helpers).

Form scripts: `doctype/managed_device/managed_device.js` (lifecycle buttons + Attest)
and `public/js/device_management/employee_devices.js` (Employee panel).

## Access

- **Device Manager** (seeded by `patches/create_device_manager_role.py`) + **System
  Manager** — full fleet; the only roles that see the `permlevel: 1` hardware
  identifiers and run the Console / Dashboard.
- **HR Manager** — read-only fleet + the Employee panel.
- **Employee** — read-only, scoped to *their own* device, with the **Attest**
  self-service action.

## Phase-2 seam (don't break these)

- `Managed Device.compliance_source` is always `"Manual"` in Phase 1; the provider
  sync flips it to `"Provider"` and overwrites `screen_lock_enabled` /
  `encryption_enabled` / `os_version` / `compliance_status`. Keep
  `compliance.derive_compliance` the single status rule so the feed can reuse/bypass it.
- `api.device_management.mark_lost` forces Non-Compliant — that is exactly the
  device a Phase-2 admin would remote-lock/wipe. The provider remote-action layer
  (gated through the existing AI-governance human-approval flow) hangs off here.

## Gotchas

- A device has a current assignee **exactly when** `status == "Assigned"`; every
  other state clears `assigned_to_*` (the history retains who held it). The
  controller enforces this on save, so drive assignment changes through the API /
  form buttons, not by hand-editing the fields.
- Hardware identifiers (`serial_number`, `imei`, `mac_address`, `phone_number`,
  `purchase_cost`) are `permlevel: 1`. Adding a new sensitive field? give it
  `permlevel: 1` and it inherits the manager-only visibility.
- The Employee panel field is provisioned in code (`setup.py`), not in
  `fixtures/custom_field.json`. If it is later exported to fixtures, the fixture
  owns it and the after-migrate hook becomes a no-op for it.
- **The Device Console's sheets are route segments, so the phone's Back button closes
  them.** Camera Scan is `device-console/camera` and Choose Employee is
  `device-console/employee`. Frappe v16's router owns `popstate` on the Desk and closes
  the open dialog on every route change, so a sheet with no entry of its own made Back
  leave the console. Now Back closes the sheet and stays, and Forward opens it again
  (Choose Employee only for the same device). Three things are deliberate. First, a
  sheet is shown only after its route has settled, because the route change would
  otherwise close it. Second, a sheet closed any other way (a read, a pick, X) steps back
  off its own entry, and the scan or pick runs only once that has landed, so the enroll
  prompt is not closed by the step. Third, a sheet's URL never opens the camera on its
  own. Only an entry the console pushed itself is a sheet's: it marks each
  one in `history.state` (no URL) as it pushes it. Frappe's Route History records every
  route with a second segment, and the awesome bar offers the most used as links, so the
  same URL can arrive from another page; the sheet would then open with that page behind
  it, and X, or a camera read's step back, would land there. An unmarked sheet URL becomes
  the console, as a pasted link's does, except when it was pushed over the console's own
  entry while that was showing (the awesome bar's link picked on the console itself): then
  the console steps back onto that entry, since replacing it would leave two console entries
  in a row and the next Back would seem to do nothing. "Showing" means no other page has
  been shown since, which the console learns from the `hide` frappe fires on the page it
  leaves. A reload on a sheet entry the console pushed (a tab discarded with the camera
  open, since the console's camera has no visibilitychange close) steps back the same way:
  `history.state` survives the reload, and the entry behind a marked one is always the
  console's own. Each replace the console asks for (`route_flags.replace_route`) is cleared
  the moment `set_route` returns: v16 reads the flag while writing the entry but clears it
  only once every request then in flight has landed, so on a first show, with the bootstrap
  call out, it turned the next tap into a replace of the console's own entry, and Back from
  the camera tapped then left the page. Back or Forward onto a sheet's entry that cannot be
  opened again steps back onto the console's own entry; it used to replace the entry with
  a second copy of the console, so the next Back seemed to do nothing. The picker cannot
  be opened again for a device scanned since, after a pick has been made (Forward used to
  offer a second Check Out, or a second Transfer), or once the device's status no longer
  allows the action (a Check Out picker for a device since marked lost). A scan is never
  a history entry. A late reply to an unknown scan puts the
  enroll prompt up only while the console itself is showing; anywhere else (another
  page, a sheet opened since) the code is only reported. The picker checks out the device
  it was opened for, not whatever a scan still in flight puts on the card. "Open full record" is a
  `get_form_link` /desk path: an /app href is a full page load on v16, and Back from it
  used to rebuild the console with the scanned device gone. Behaviour tests:
  `scripts/test_desk_page_history.js`, run by `tests/test_desk_page_history.py`.
