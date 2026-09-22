# MDM Integration (MDM/EMM Phase 2: provider layer)

Makes the Phase-1 [`device_management`](../device_management/README.md) registry
**live**: syncs real device state from two providers and runs governed remote
actions. Two-provider split, routed by device class:

- **Miradore = mobile MDM** (Phone/Tablet). Reads through **API v1** (XML, key
  in the `auth` query parameter); acts through **API v2** (`X-API-Key` +
  `X-Instance-Name`). list/get + **lock / wipe / locate**.
- **Action1 = computer RMM** (Laptop/Desktop). REST `api/3.0`, OAuth2
  client-credentials. list/get + **reboot / run-script / deploy-patch** (no wipe),
  each run as an Action1 automation.

## Mock-first

`MDM Settings.provider_mode` defaults to **Mock**: both adapters return canned
devices and record actions, so the whole pipeline (sync → reconcile → action →
audit) runs and is testable **with no credentials**. Flip to **Live** and fill in
each provider's section to hit the real APIs.

> **Until v1.506.0 the Live adapters had never matched either vendor.** They were
> scaffolded against guessed endpoints. Miradore sync called `GET /api/v2/devices`,
> which does not exist (12,803 failed syncs from 2026-06-15). Every remote action
> for both providers also called a path that does not exist. The clients are now
> written from the vendors' own specs, listed in [Provider API reference](#provider-api-reference),
> and `tests/test_mdm_provider_clients.py` pins every request against them.

## What's here

| Path | What |
|---|---|
| `routing.py` | **Frappe-free**: device→provider router, per-provider capability map, the **BYOD wipe guard** (`resolve_wipe_mode`), the **Miradore selective-wipe guard** (`miradore_selective_wipe_refusal`) and Action1's script platform/language. Unit-tested bench-free (`tests/test_mdm_integration.py`). |
| `client.py` | `MDMProvider` base → normalized `ProviderDevice`; `MiradoreProvider`, `Action1Provider`, `MockProvider`; `get_provider` / `get_provider_for`. Requests pinned by `tests/test_mdm_provider_clients.py`. |
| `mapping.py` | `upsert_device`: matches on provider ID, then serial, then IMEI, and overwrites compliance (`compliance_source="Provider"`). A provider-only device becomes **Discovered** and **stays Discovered until a person confirms it**. Never deletes. |
| `sync.py` | Per-provider pull → MDM Sync Log + MDM Raw Payload; `safe_upsert`; flags **Managed** devices the feed dropped as **Unmanaged** (a Discovered one stays Discovered); cursor advances only on a clean run. |
| `tasks.py` | Hourly `sync_devices` (throttled), `refresh_action1_token`, `retry_failed_syncs`. |
| `actions.py` | `execute_device_action` — the one guarded executor: route + capability check + BYOD guard + **no full wipe of an unconfirmed Discovered device** + dispatch + immutable **Device Action Log** + notify. |
| `webhooks.py` | One guest endpoint, `?provider=` in the query string; `X-MDM-Webhook-Secret` verified before parsing; archives the body, enqueues one deduplicated resync (skipped for a disabled or auth-paused provider). **Neither provider can call it**; see [Inbound webhook](#inbound-webhook). |
| `api.py` | Whitelisted `test_connection` and `trigger_sync` (the **Test** / **Sync Now** buttons on MDM Settings), `remote_action` (manager-UI path), `confirm_device` (Discovered → Managed, with ownership). |
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

The executor **always**:
- rejects an action the provider can't do (`supports()`);
- forces a **selective** wipe for BYOD (never full);
- refuses a **full** wipe of a **Discovered** device;
- writes a Device Action Log row, whether the action succeeds or fails.

### Wipe, precisely

Miradore has **no selective-wipe option**. Its Wipe takes no such field, and what
it does depends on how the device is enrolled, not on anything we send:

| Enrollment (`Client.ManagementType`) | **full** = Miradore Wipe | **selective** = Miradore Retire |
|---|---|---|
| Work-profile Android (`AndroidProfileOwner`) | removes the work profile only | removes company data, unenrolls |
| Fully managed Android (`AndroidDeviceOwner`) | factory reset | **refused**: Retire factory-resets these too |
| iPhone / unsupervised iPad (`iOSUnsupervised`) | erases the device | removes company data, unenrolls |
| Supervised iPhone (`iOSSupervised`) | erases the device | removes company data, unenrolls |
| Supervised iPad | erases the device | **refused**: may be a Shared iPad, which Retire resets |
| Anything else or unknown | per Miradore | **refused** (fails closed) |

The enrollment is read fresh from Miradore at wipe time. The Device Action Log
records what actually happened (`effect`), since a "full" wipe of a work-profile
phone is not a factory reset. **A selective wipe unenrolls the device**: it can no
longer be locked or located, and the next sync marks it Unmanaged. Sources:
Miradore KB "Retire a device" and "Wipe for Android devices", and
`WipeConfiguration` in the v2 spec.

### Discovered devices must be confirmed

The sync creates any device it cannot match as **Discovered**, with ownership
defaulting to **Company**. The BYOD guard is only as good as that field, so a
full wipe is refused until a Device Manager clicks **Confirm Discovered Device**
on the form and says who owns it (`api.confirm_device`, recorded on the timeline).
Until v1.506.0 the next hourly sync promoted every Discovered device to Managed
by itself. All four devices on production were promoted that way, and nobody ever
confirmed one.

## Post-deploy (going Live)

1. `bench migrate` (creates the doctypes; `provider_mode` starts Mock).
2. In **MDM Settings**: set `provider_mode = Live`; enable + fill **Miradore**
   (instance + API key) and/or **Action1** (org id + client id/secret); review
   the BYOD/full-wipe safety flags. Leave `webhook_secret` empty unless something
   of ours will call the webhook (below) — unset, the endpoint refuses everything.
3. Save, then use **Test Connection → Test Miradore / Test Action1** on the
   form. A passing test lifts a provider's auth pause. **Sync Now** runs one sync
   and reports its counts; otherwise wait for the hourly job.
4. On each **Discovered** device, click **Confirm Discovered Device** and set who
   owns it.

Credentials, per provider:
- **Miradore**: create a key under **System → Infrastructure diagram → API → Create
  key**. It is shown once, and the same key serves API v1 and v2. **Instance
  Name** is the site name from `online.miradore.com/<site>`. The API may need a
  plan that includes it; if there is no API icon, that is why.
- **Action1**: create API credentials in the Action1 console (Client ID + Client
  Secret, shown once). Sync only needs read access. Reboot / run script / deploy
  patch run as automations and need `run_automations`. The adapter uses the North
  America host `app.action1.com`. Action1 also runs `app.eu.`, `app.uk.`,
  `app.au.` and `app.na-2.`; an organization on another region needs `BASE`
  changed.

## Inbound webhook

**Neither provider can send one.** Checked 2026-09-22: Miradore's public API v2
spec (`online.miradore.com/swagger/v2/swagger.json`, 29 paths) has no webhook,
callback or subscription endpoint, and its console notifications go to the
notification center and email only. Action1's alerts are email-only, to a single
Action1 user
([docs](https://www.action1.com/documentation/alerts/)); its API has no webhook
either. So there is nothing to configure in either vendor console, and the hourly
poll (`tasks.sync_devices`) is the only way device state arrives. The endpoint is
a hook for **our own** tooling. For example, an Action1 automation script can run
`Invoke-RestMethod` at the end of a patch run to ask for a resync now, rather than
waiting up to an hour. The secret then sits in that script, where every Action1
admin can read it, and runs on every endpoint it targets. A leak costs little,
because all the endpoint can do is archive a body and queue one resync, at most
120 times an hour from any one address. Production
has never received a webhook: no
`webhook_secret` has ever been set, and no MDM Raw Payload row has
`source = "Webhook"`.

The contract, for anything that does call it:

```
POST https://erp.sapphirefountains.com/api/method/erpnext_enhancements.mdm_integration.webhooks.handle_webhook?provider=Action1
X-MDM-Webhook-Secret: <MDM Settings.webhook_secret>
Content-Type: application/json

{"any": "json"}            → 200 {"message": {"status": "ok"}}
```

- `provider` is `Miradore` or `Action1`, and it is read **only from the query
  string**. On a JSON POST, Frappe v16 builds the request arguments from the body
  *instead of* the query string. Until 1.502.2 that made every JSON call to this
  endpoint die with a `TypeError` 500 before our code ran (verified on prod).
- POST only, rate-limited to 120 requests an hour per caller address.
- The body is archived verbatim as an **MDM Raw Payload** (`source = "Webhook"`),
  then a resync of that provider is queued. Bursts collapse into one queued job.
  A provider that is disabled, or paused after an auth failure, is not resynced.
  That is the same gate the hourly job applies, minus its throttle.
- Generate the secret with
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. A secret shorter
  than 32 characters is treated as unset, and that is logged to the Error Log at
  most once a day.

Test it (the second call must be Frappe's 401, **not** ours):

```bash
curl -si -X POST -H "Content-Type: application/json" -H "X-MDM-Webhook-Secret: $SECRET" -d '{}' \
  "https://erp.sapphirefountains.com/api/method/erpnext_enhancements.mdm_integration.webhooks.handle_webhook?provider=Action1"
curl -si -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $SECRET" -d '{}' \
  "https://erp.sapphirefountains.com/api/method/erpnext_enhancements.mdm_integration.webhooks.handle_webhook?provider=Action1"
```

### Why a custom header, and not `Authorization`, a query string or an auth hook

- **`Authorization: Bearer <secret>`** was the original contract, and it **cannot
  work on Frappe v16**. `frappe.auth.validate_auth` runs before every handler. It
  treats any two-part `Authorization` header as a credential and tries it as an
  OAuth bearer token, then as an API key. If neither yields a user, it raises
  `AuthenticationError`: HTTP 401, body `{"exc_type":"AuthenticationError"}`.
  Verified against this endpoint on prod 2026-09-22. The website lead ingress had
  the same defect and got the same fix (`X-Web-Lead-Secret`, 1.501.0).
- **A secret in the query string** (`?secret=`) would get past Frappe. But the
  full URL is written to the nginx access log and to the Google load balancer's
  request logging, so the secret would sit in both in plain text. We would only
  accept that if the caller could not set a header, and every possible caller
  here is something we write.
- **An `auth_hooks` entry in `hooks.py`** could recognize `Bearer <mdm secret>` on
  this path and set a user before Frappe's Guest check. But that hook runs on
  **every** request to the site. It would also turn a webhook secret into a login
  for the service user, one mistake in its path check away from authenticating
  arbitrary API calls. It solves a problem no caller of ours has.

## Provider API reference

Checked 2026-09-22. Keep these in sync with `client.py` and the tests.

**Miradore** — [API v1.19 spec (PDF)](https://www.miradore.com/wp-content/uploads/2025/12/api-specification-119.pdf),
[v2 Swagger](https://online.miradore.com/swagger/v2/swagger.json).
- List / read: `GET https://online.miradore.com/<site>/API/Device[/<id>]?auth=<key>&select=<attrs>&options=rows=100,page=N`.
  Returns XML under `Content/Items` with a `count`. **API v2 cannot list or read
  devices**: `/api/v2/Device` is POST-only (create an "Other Device"), and
  Miradore's own docs send you to v1 for device IDs.
- Act (v2, `X-API-Key` + `X-Instance-Name`): `POST /api/v2/Device/{id}/Lock`,
  `GET /api/v2/Device/{id}/Location`, `POST /api/v2/Device/{id}/Wipe` (body `{}` =
  Miradore's defaults), `DELETE /api/v2/Device/{id}` (Retire). `{id}` is an int32.
  v2 also has Reboot and LostMode, which are not wired up.
- The v1 key travels in the URL, so every error message is redacted and transport
  errors are re-raised `from None`. A `requests` exception's text includes the
  full URL, and the sync stores error text on the Sync Log and in the Error Log.

**Action1** — OpenAPI 3.1 behind [app.action1.com/apidocs](https://app.action1.com/apidocs),
and [PSAction1](https://github.com/Action1Corp/PSAction1) for known-good payloads.
- List: `GET /endpoints/managed/{org}?limit=200&from=N`. The real field names
  are `id`, `name`, `serial`, `OS`, `platform`, `MAC`, `manufacturer`, `user`
  and `last_seen`.
- Actions: `POST /automations/instances/{org}` with
  `endpoints: [{"id": <uuid>, "type": "Endpoint"}]` and one action:
  - `reboot`: user warned, 10-minute grace. `retry_minutes` 60.
  - `run_script`: PowerShell on Windows, Bash on Mac/Linux. `retry_minutes` 60.
  - `deploy_update`: `scope: "Specified"`, `packages: [{<package id>: <version>}]`,
    no automatic reboot. The version is the one Action1 lists in the endpoint's
    `/missing-updates`; a package that is not missing there is refused.
    `retry_minutes` 1440.
- Endpoint IDs must be UUIDs. `{"id": "all"}` means every endpoint in the
  organization, so it can never be sent.
