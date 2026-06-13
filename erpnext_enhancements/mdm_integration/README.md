# MDM Integration (MDM/EMM Phase 2: provider layer)

Makes the Phase-1 [`device_management`](../device_management/README.md) registry
**live**: syncs real device state from two providers and runs governed remote
actions. Two-provider split, routed by device class:

- **Miradore = mobile MDM** (Phone/Tablet). API v2, `X-API-Key` + `X-Instance-Name`.
  list/get + **lock / wipe / locate**.
- **Action1 = computer RMM** (Laptop/Desktop). REST `api/3.0`, OAuth2
  client-credentials. list/get + **reboot / run-script / deploy-patch** (no wipe).

## Mock-first

`MDM Settings.provider_mode` defaults to **Mock**: both adapters return canned
devices and record actions, so the whole pipeline (sync → reconcile → action →
audit) runs and is testable **with no credentials**. Flip to **Live** and fill in
each provider's section to hit the real APIs.

> The Live adapters are scaffolded against the providers' *documented* REST
> shapes. Confirm exact JSON field names against each vendor's Swagger
> (`online.miradore.com/swagger`, `app.action1.com/apidocs`) when you add real
> keys — parsing is defensive (`client._first`) to tolerate that.

## What's here

| Path | What |
|---|---|
| `routing.py` | **Frappe-free**: device→provider router, per-provider capability map, and the **BYOD wipe guard** (`resolve_wipe_mode`). Unit-tested bench-free (`tests/test_mdm_integration.py`). |
| `client.py` | `MDMProvider` base → normalized `ProviderDevice`; `MiradoreProvider`, `Action1Provider`, `MockProvider`; `get_provider` / `get_provider_for`. |
| `mapping.py` | `upsert_device` — match provider-id→serial→IMEI; overwrite compliance (`compliance_source="Provider"`); provider-only → **Discovered**; never deletes. |
| `sync.py` | Per-provider pull → MDM Sync Log + MDM Raw Payload; `safe_upsert`; flags **Unmanaged**; cursor advances only on a clean run. |
| `tasks.py` | Hourly `sync_devices` (throttled), `refresh_action1_token`, `retry_failed_syncs`. |
| `actions.py` | `execute_device_action` — the one guarded executor: route + capability check + BYOD guard + dispatch + immutable **Device Action Log** + notify. |
| `webhooks.py` | Guest endpoint per provider; Bearer-secret verified before parsing; enqueues a resync. |
| `api.py` | Whitelisted `test_connection`, `trigger_sync`, `remote_action` (manager-UI path). |
| `doctype/` | `MDM Settings` (single, both providers' creds), `MDM Sync Log`, `MDM Raw Payload`, `Device Action Log` (immutable). |

AI tools live in `../assistant_tools/` (`remote_lock_device`, `remote_wipe_device`,
`locate_device`, `reboot_device`, `run_device_script`, `deploy_device_patch`).
The Integrations Health tiles live in `../api/integrations_health.py`.

## Remote actions & gating

Two paths converge on `actions.execute_device_action`:
1. **Manager UI** — buttons on the Managed Device form (`mdm_integration.api.remote_action`).
2. **AI assistant** — the six gated tools. They're in the write-gate's
   `APP_MUTATING`; **wipe / lock / run-script are HIGH risk**. With AI write
   gating on, nothing runs until a human clicks Confirm & Execute (the gate
   re-runs the tool as that user). See `../assistant_tools/_gate.py`.

The executor **always**: rejects an action the provider can't do (`supports()`),
forces a **selective** wipe for BYOD (never full), and writes a Device Action Log
row (success or failure).

## Post-deploy (going Live)

1. `bench migrate` (creates the doctypes; `provider_mode` starts Mock).
2. In **MDM Settings**: set `provider_mode = Live`; enable + fill **Miradore**
   (instance + API key) and/or **Action1** (org id + client id/secret); set a
   `webhook_secret` if you'll use webhooks; review the BYOD/full-wipe safety flags.
3. Use **Test Connection** / **Trigger Sync** (or wait for the hourly job).
   Confirm/assign any **Discovered** devices.
