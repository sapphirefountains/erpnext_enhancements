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
| `webhooks.py` | One guest endpoint, `?provider=` in the query string; `X-MDM-Webhook-Secret` verified before parsing; archives the body, enqueues one deduplicated resync (skipped for a disabled or auth-paused provider). **Neither provider can call it**; see [Inbound webhook](#inbound-webhook). |
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
   (instance + API key) and/or **Action1** (org id + client id/secret); review
   the BYOD/full-wipe safety flags. Leave `webhook_secret` empty unless something
   of ours will call the webhook (below) — unset, the endpoint refuses everything.
3. Use **Test Connection** / **Trigger Sync** (or wait for the hourly job).
   Confirm/assign any **Discovered** devices.

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
