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
