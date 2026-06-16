# QuickBooks Online

This module is the **QuickBooks Online (QBO) accounting integration** (OAuth2, REST client, entity mapping, idempotent sync, audit log, CDC polling, webhooks, retries).

> The QBO engine lives under `core/`; `api.py` at the module root re-exports its whitelisted endpoints so the dashboard JS and the Intuit webhook URL resolve at `...quickbooks_online.api.*`. The separate **QuickBooks Time** timesheet webhook now lives in its own `quickbooks_time` module.

## Data flow

```
OAuth2  →  Client  →  Mapping  →  Sync  →  Sync Log / Raw Payload
(api)      (client)   (mapping)   (sync)    (audit doctypes)
                                    ↑
              Webhooks ────────────┤   (Intuit push, signature-verified)
              CDC poll  ───────────┤   (hourly, cursor-based)
              Retries   ───────────┘   (hourly, Failed logs)
```

1. **OAuth2** — `api.start_oauth` mints a one-time CSRF `state` (cached 10 min) and returns Intuit's consent URL; `api.oauth_callback` (guest) validates the state, exchanges the code, and stores tokens. `client.QuickBooksClient` owns the token lifecycle and transparently refreshes on a 401.
2. **Client** — authenticated REST helpers (`request`, `query`, `get_entity`, `cdc`) against the Sandbox/Production base URL with a pinned `minorversion`.
3. **Mapping** — `map_qbo_to_erpnext` transforms a QBO payload to an ERPNext DocType + values; `upsert_entity` decides idempotently: update-if-linked → auto-link by fuzzy match → create → defer to manual review. QBO-owned field values are tracked for conflict detection.
4. **Sync** — `sync.py` orchestrates `import_all`, `preview_resync`/`run_resync`, `sync_entity`, `run_cdc`, `retry_failed`. Each run opens a Sync Log, archives every payload as a Raw Payload, and routes writes through `safe_upsert`.
5. **Webhooks** — `webhooks.handle_webhook` verifies the Intuit HMAC signature, archives the notification, and enqueues a background `sync_entity` per changed entity.
6. **CDC poll** — `tasks.cdc_poll` throttles by `cdc_poll_minutes`; `run_cdc` pulls all changes since the `last_cdc_sync` cursor and advances it only on a clean run.
7. **Retries** — `tasks.retry_failed_syncs` re-runs Failed logs up to `retry_limit`.

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `api.py` (module root) | Re-exports the QBO whitelisted endpoints (browser + Intuit webhook URL) | re-exports from `core/api.py` |
| `core/api.py` | Whitelisted RPC surface (browser + Intuit) | `start_oauth`, `oauth_callback`, `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `retry_failed`, `preview_existing_matches`, `link_existing_record`, `quickbooks_webhook`, `get_dashboard_status` |
| `core/client.py` | OAuth2 + REST transport | `QuickBooksClient` (`build_authorization_url`, `exchange_code`, `refresh_access_token`, `request`, `query`, `get_entity`, `cdc`), `QuickBooksAPIError` |
| `core/constants.py` | Endpoints, entity catalogue, DocType map | `ENTITY_DOCTYPE_MAP`, `*_ENTITIES`, `ENVIRONMENT_BASE_URLS`, `OAUTH_SCOPE`, `MINOR_VERSION` |
| `core/mapping.py` | Transform / match / idempotent upsert | `map_qbo_to_erpnext`, `upsert_entity`, `find_existing_match`, `detect_conflicts`, `save_mapping`, `link_existing_record`, `_map_*`, `_match_*` |
| `core/sync.py` | Sync orchestration + logging | `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `run_cdc`, `retry_failed`, `query_all`, `store_raw_payload`, `start`/`finish`/`fail_log` |
| `core/tasks.py` | Hourly scheduler hooks | `refresh_token_if_needed`, `cdc_poll`, `retry_failed_syncs` |
| `core/utils.py` | Shared helpers | `get_settings`, `get_secret`/`set_secret`, `json_dumps`/`loads`, `parse_qbo_datetime`, `is_token_expiring`, `verify_intuit_signature`, `update_settings_status` |
| `core/webhooks.py` | Inbound webhook handling | `handle_webhook`, `_iter_events` |
| `doctype/*/*.py` | Doctype controllers | `QuickBooksOnlineSettings` (has `validate`), `QuickBooksRawPayload`, `QuickBooksSyncLog`, `QuickBooksSyncMapping` |
| `page/quickbooks_online_dashboard/*.py` / `*.js` | Status dashboard page | `get_context`; render/refresh/match-dialog |

## Doctypes

- **QuickBooks Online Settings** (Single) — credentials (`client_id`, encrypted `client_secret`, `webhook_verifier_token`, `redirect_uri`), OAuth state (encrypted `access_token`/`refresh_token`, `realm_id`, `token_expires_at`), cursors (`last_full_import`, `last_cdc_sync`, `last_webhook_at`), `status`/`status_message`, and tuning (`environment`, `company`, `sync_enabled`, `cdc_poll_minutes`, `retry_limit`).
- **QuickBooks Sync Mapping** — the link ledger keyed on (`qbo_entity_type`, `qbo_id`); stores `erpnext_doctype`/`erpnext_name`, `sync_token`, `last_qbo_updated_at`, `deleted`, `conflict_status`, `match_status`/`match_rule`/`match_confidence`, and `owned_fields` (JSON of QBO-owned values, for conflict detection).
- **QuickBooks Sync Log** — one per run; `sync_type`, `status`, lifecycle timestamps, per-action counters, `retry_count`, `preview_payload`, `error_message`.
- **QuickBooks Raw Payload** — append-only audit of every fetched/received payload; `source`, entity type/id, `realm_id`, `sync_log` link, `received_at`, verbatim `payload`.

## Scheduler / webhook entry points

- `tasks.refresh_token_if_needed` (hourly) — refresh the access token if expiring within 10 min (no-op when disconnected).
- `tasks.cdc_poll` (hourly) — run CDC if `cdc_poll_minutes` elapsed since `last_cdc_sync`.
- `tasks.retry_failed_syncs` (hourly) — re-run Failed logs, capped by `retry_limit`.
- `api.quickbooks_webhook` (guest) — Intuit push → `handle_webhook` (verify signature → archive → enqueue `sync_entity`).
- `api.oauth_callback` (guest) — OAuth2 redirect target.

## Auth & secrets

OAuth2 authorization-code flow with `client_secret_basic` token requests. Tokens, client secret, and webhook verifier are stored in **encrypted Password fields** on the Settings Single and read/written only via `utils.get_secret`/`set_secret`. `token_expires_at` is deliberately backdated 5 minutes vs QBO's `expires_in`; refresh happens proactively (scheduler, 10-min window) and reactively (401 retry). Refresh-token rotation is honored. Webhook authenticity is enforced by constant-time HMAC-SHA256 verification of the raw body against `webhook_verifier_token`.

## Gotchas

- **Idempotency** hinges on the (entity_type, qbo_id) Sync Mapping; re-running import/webhook/CDC is safe. Transactions are never fuzzy-matched (always created); only master entities (Account/Customer/Vendor/Item/TaxCode) auto-link.
- **CDC cursor** advances only on a clean run, so failures reprocess the same window. The first run looks back 24h. `TaxCode` is excluded from CDC.
- **Conflict policy:** user edits to QBO-owned fields are preserved unless an overwrite resync (`run_resync`) is run; a preview is required first.
- **Per-record resilience:** batch ops use `safe_upsert`, so one bad record can't abort a run; inline failure notes are capped at 20 (full tracebacks go to the Frappe Error Log).
- **No rate-limit/backoff handling:** QBO 429/throttling responses aren't specifically handled — any ≥400 (other than 401) raises `QuickBooksAPIError`.
- Sandbox vs Production is chosen via `environment`; only the base URL differs (OAuth endpoints are shared).
