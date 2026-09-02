# QuickBooks Online

This module is the **QuickBooks Online (QBO) accounting integration** (OAuth2, REST client, entity mapping, idempotent sync, audit log, CDC polling, webhooks, retries, balance reconciliation, opening-balance import).

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
| `core/api.py` | Whitelisted RPC surface (browser + Intuit) | `start_oauth`, `oauth_callback`, `disconnect`, `disconnect_callback`, `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `retry_failed`, `preview_existing_matches`, `link_existing_record`, `compare_account_balances`, `reconcile_transactions`, `sync_opening_balances`, `quickbooks_webhook`, `get_dashboard_status` |
| `core/client.py` | OAuth2 + REST transport | `QuickBooksClient` (`build_authorization_url`, `exchange_code`, `refresh_access_token`, `revoke_tokens`, `request`, `query`, `get_entity`, `cdc`, `report`, `download_attachable`), `QuickBooksAPIError`, `QuickBooksDownloadTicketError` |
| `core/attachments.py` | Mirror QBO `Attachable` files onto their ERPNext docs as private Files (WI-071); self-protecting daily pass, fresh-ticket batching | `sync_attachments`, `reset_attachable`, `DOWNLOAD_URI_BATCH`, `MAX_ATTEMPTS`, `STALE_ATTEMPT_SECONDS`, `SAVE_TIMEOUT_SECONDS` |
| `core/constants.py` | Endpoints, entity catalogue, DocType map | `ENTITY_DOCTYPE_MAP`, `*_ENTITIES`, `ENVIRONMENT_BASE_URLS`, `OAUTH_SCOPE`, `MINOR_VERSION` |
| `core/mapping.py` | Transform / match / idempotent upsert | `map_qbo_to_erpnext`, `upsert_entity`, `find_existing_match`, `detect_conflicts`, `save_mapping`, `link_existing_record`, `_map_*`, `_match_*` |
| `core/sync.py` | Sync orchestration + logging | `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `run_cdc`, `retry_failed`, `query_all`, `store_raw_payload`, `start`/`finish`/`fail_log` |
| `core/reconcile.py` | Read-only balance/transaction reconciliation (Reports API) | `compare_account_balances`, `reconcile_transactions`, `_parse_trial_balance` |
| `core/opening_balances.py` | Build a balanced opening Journal Entry from QBO balances | `sync_opening_balances`, `_opening_account_line`, `_party_opening_line`, `_plug_line` |
| `core/group_account_remap.py` | One-off (WI-068): move draft JE lines off group accounts onto `- General` ledgers, one **window** at a time (`pre-2026` applied; `2026` outstanding for TASK-2026-01236). Dry-run by default, **never wired to migrate/scheduler** | `remap_group_account_lines`, `WINDOWS`, `NEW_LEDGER_CHILDREN`, `MERGE_INTO_EXISTING` |
| `core/tasks.py` | Scheduler hooks (hourly + the daily attachment pass) | `refresh_token_if_needed`, `cdc_poll`, `retry_failed_syncs`, `sync_attachments_scheduled` |
| `report/quickbooks_balance_comparison/*` | QBO vs ERPNext account-balance report | `execute` (filters: as-of date, tolerance, only-discrepancies) |
| `core/utils.py` | Shared helpers | `get_settings`, `get_secret`/`set_secret`, `clear_oauth_tokens`, `json_dumps`/`loads`, `parse_qbo_datetime`, `is_token_expiring`, `verify_intuit_signature`, `update_settings_status` |
| `core/webhooks.py` | Inbound webhook handling | `handle_webhook`, `_iter_events` |
| `doctype/*/*.py` | Doctype controllers | `QuickBooksOnlineSettings` (has `validate`), `QuickBooksRawPayload`, `QuickBooksSyncLog`, `QuickBooksSyncMapping` |
| `page/quickbooks_online_dashboard/*.py` / `*.js` | Status dashboard page | `get_context`; render/refresh/match-dialog |

## Doctypes

- **QuickBooks Online Settings** (Single) — credentials (`client_id`, encrypted `client_secret`, `webhook_verifier_token`, `redirect_uri`), OAuth state (encrypted `access_token`/`refresh_token`, `realm_id`, `token_expires_at`), cursors (`last_full_import`, `last_cdc_sync`, `last_webhook_at`), `status`/`status_message`, and tuning (`environment`, `company`, `sync_enabled`, `cdc_poll_minutes`, `retry_limit`).
- **QuickBooks Sync Mapping** — the link ledger keyed on (`qbo_entity_type`, `qbo_id`); stores `erpnext_doctype`/`erpnext_name`, `sync_token`, `last_qbo_updated_at`, `deleted`, `conflict_status`, `match_status`/`match_rule`/`match_confidence`, and `owned_fields` (JSON of QBO-owned values, for conflict detection).
- **QuickBooks Sync Log** — one per run; `sync_type`, `status`, lifecycle timestamps, per-action counters, `retry_count`, `preview_payload`, `error_message`.
- **QuickBooks Raw Payload** — append-only audit of every fetched/received payload; `source`, entity type/id, `realm_id`, `sync_log` link, `received_at`, verbatim `payload`.

## Scheduler / webhook entry points

- `tasks.refresh_token_if_needed` (hourly) — refresh the access token if expiring within 10 min (no-op when disconnected). On `invalid_grant` (refresh token revoked/expired — e.g. the user disconnected the app from Intuit's side) it clears the dead tokens and marks the connection Not Connected instead of erroring every run.
- `tasks.cdc_poll` (hourly) — run CDC if `cdc_poll_minutes` elapsed since `last_cdc_sync`.
- `tasks.retry_failed_syncs` (hourly) — re-run Failed logs, capped by `retry_limit`.
- `tasks.sync_attachments_scheduled` (daily) — mirror new QBO attachments, capped by `max_new=500`; see [Attachment mirroring](#attachment-mirroring-wi-071).
- `api.quickbooks_webhook` (guest) — Intuit push → `handle_webhook` (verify signature → archive → enqueue `sync_entity`).
- `api.oauth_callback` (guest) — OAuth2 redirect target.
- `api.disconnect_callback` (**login required**, not guest) — the app's Intuit **Disconnect URL** target: clears the local tokens (Intuit has already revoked the grant) and redirects to the dashboard. Not guest, so it can't be used to force a disconnect anonymously.

## Attachment mirroring (WI-071)

`core/attachments.py` mirrors QBO `Attachable` files (receipts, bill scans) onto the ERPNext documents their transactions were imported as, as **private Files** stamped with `custom_qbo_attachable_id` — idempotent per target document, so re-runs never duplicate a file. The historical backfill is complete (v1.359.x, 5,015 files); `tasks.sync_attachments_scheduled` keeps steady state current daily, capped by `max_new=500`, inside an RQ worker. Every per-file operation is bounded, and every mirrored file is committed on its own so a killed run loses at most one file's work.

**Fresh download tickets.** A `TempDownloadUri` is a pre-signed ticket that expires within minutes, so a 1000-row page queried up front had its tail rejected (HTTP 401) by the time the downloads reached it — ~139 of them in the backfill. Each page is first reduced to the (Attachable, target document) pairs that actually need a download, then worked in batches of `DOWNLOAD_URI_BATCH` (50): the batch's URIs are re-queried (`SELECT * FROM Attachable WHERE Id IN (...)` — `Id` accepts `=` and `IN`; projections are not supported) **immediately before** its downloads, and a rejected ticket (`QuickBooksDownloadTicketError`, 401/403) is re-queried **exactly once** before counting as a failure (`url_refreshes` in the run summary). If QBO ever refused the `IN` form the code falls back to one `get_entity` per Attachable.

**Self-protection: the write-ahead attempt marker.** Frappe's synchronous JS-in-PDF scan (`pdf_contains_js` → pypdf) loops forever on a malformed PDF inside `File.insert()`. The `bench execute` backfill path catches that with a 90 s SIGALRM guard (`SAVE_TIMEOUT_SECONDS`); the daily pass runs in an RQ worker where that guard is a deliberate no-op (RQ owns SIGALRM) and only RQ's job timeout ends the hang — which kills the whole daily run and, with nothing recording which file did it, would kill it again every day. So each download+insert is bracketed by a durable marker: a **`QuickBooks Sync Mapping` row** with `qbo_entity_type = Attachable`, `qbo_id = <Id>`, `match_rule = qbo_attachment_mirror` and the state in its `owned_fields` JSON (`state`, `attempts`, `started_at`, `target`, `file_name`, `last_error`; `last_synced_at` mirrors `started_at`). It is written and **committed before** the attempt and deleted in the same transaction as the File on success. A DB row, not redis, so it survives the deploy `FLUSHDB`. No new DocType or Custom Field: these rows carry no `erpnext_doctype`/`erpnext_name`, and every other reader of the ledger filters on its own entity types or on the ERPNext side, so sync, reconcile and writeback never see them.

| Marker state | Meaning | The next run… |
|---|---|---|
| `attempting`, older than `STALE_ATTEMPT_SECONDS` (2 h), written **unguarded** (the RQ worker) | the run that wrote it never finished this file — it hung, or the worker was killed mid-file (a deploy mid-run does this to one healthy file) | settles it as `hung`: **one** Error Log titled `QBO attachment <Id> skipped: hung`, `match_status = Pending Review`, counted in `skipped_hung`, never downloaded again |
| `attempting`, older than 2 h, written **under the SIGALRM guard** (`guarded`, the `bench execute` backfill) | the guard settles a real hang itself, so this process was killed from outside — its OS `timeout`, a deploy restart — while a healthy file was in flight | rewrites it as one ordinary `failed` attempt and retries it (or gives up at 3, `Pending Review`) — never `hung` |
| `attempting`, younger than 2 h | an overlapping run (the daily job and a manual pass) may still own it | skips it without judgement (`skipped_in_flight`); so does the loser of a marker-insert race between two runs |
| `hung` | a settled hang — also set at once by the SIGALRM guard's `_AttachmentTimeout`, or by RQ's timeout landing on a file already ≥ 90 s in flight | skipped silently, counted in `skipped_hung` |
| `failed`, `attempts` < `MAX_ATTEMPTS` (3) | an ordinary exception; still retryable | retries it, carrying the count |
| `failed`, `attempts` ≥ 3 | given up on; `match_status = Pending Review` | skipped, counted in `skipped_failed` |

RQ's timeout landing on a file in flight for **less** than 90 s is the run's 300 s budget expiring on a healthy file, not a hang: its marker is released so the next day retries it. Two more things are deliberately *not* the file's fault: a dead OAuth grant mid-run (`QuickBooksDisconnectedError`) releases the marker and aborts the run with the reconnect message; and `FAILURE_STREAK_LIMIT` (10) files failing back to back is treated as an environment fault (Intuit's file host, storage, the database) — the run stops, those markers are rewound to their pre-run state instead of each being charged an attempt, and one Error Log names the streak (`aborted = "failure_streak"` in the summary). The QuickBooks Sync Mapping list filtered on `qbo_entity_type = Attachable` shows every file currently under a marker; the *QuickBooks Records Mapped* number card excludes them. To put a skipped file back in play:

```
bench execute erpnext_enhancements.quickbooks_online.core.attachments.reset_attachable --kwargs "{'att_id': '123'}"
```

(deleting the mapping row by hand is equivalent). Bench-free tests: `tests/test_quickbooks_attachments.py` (its own pytest step in CI).

## Auth & secrets

OAuth2 authorization-code flow with `client_secret_basic` token requests. Tokens, client secret, and webhook verifier are stored in **encrypted Password fields** on the Settings Single and read/written only via `utils.get_secret`/`set_secret`. `token_expires_at` is deliberately backdated 5 minutes vs QBO's `expires_in`; refresh happens proactively (scheduler, 10-min window) and reactively (401 retry). Refresh-token rotation is honored. Webhook authenticity is enforced by constant-time HMAC-SHA256 verification of the raw body against `webhook_verifier_token`. **Disconnect** (`api.disconnect`, the Settings/dashboard button) best-effort revokes the grant at Intuit (`client.revoke_tokens` → Intuit's revoke endpoint) and then forgets the stored tokens/realm via `utils.clear_oauth_tokens` (which deletes the encrypted token rows directly — `set_secret` can't clear a Password field — but keeps the client id/secret/verifier so reconnect is one click).

## Gotchas

- **Idempotency** hinges on the (entity_type, qbo_id) Sync Mapping; re-running import/webhook/CDC is safe. Transactions are never fuzzy-matched (always created); only master entities (Account/Customer/Vendor/Item/TaxCode/Term/PaymentMethod/Class) auto-link.
- **Reconciliation is read-only.** `compare_account_balances` (Trial Balance vs GL) and `reconcile_transactions` (payload total vs document total) never write — they surface discrepancies for you to act on. Run the **QuickBooks Balance Comparison** report after an import.
- **Opening balances are a draft by default.** `sync_opening_balances` creates one balanced Opening Entry; review it before submitting (pass `auto_submit` to post it). A/R and A/P are broken out per party from QBO's *current* open balances (correct for a present-day cut-over; for a historical cutoff, check the draft against QBO's aging). Stock accounts are excluded — post opening stock via a Stock Reconciliation — and any residual squares off against the company's **Temporary Opening** account.
- **CDC cursor** advances only on a clean run, so failures reprocess the same window. The first run looks back 24h. `TaxCode` is excluded from CDC (Term/PaymentMethod/Class are included).
- **Conflict policy:** user edits to QBO-owned fields are preserved unless an overwrite resync (`run_resync`) is run; a preview is required first.
- **Per-record resilience:** batch ops use `safe_upsert`, so one bad record can't abort a run; inline failure notes are capped at 20 (full tracebacks go to the Frappe Error Log).
- **No rate-limit/backoff handling:** QBO 429/throttling responses aren't specifically handled — any ≥400 (other than 401) raises `QuickBooksAPIError`.
- **The daily attachment job runs on the `default` queue (300 s RQ timeout), not `long`.** `hooks.py` lists it under `daily`, and v16's `ScheduledJobType.get_queue_name` sends only `*Long`/`Maintenance` frequencies to `long` (1500 s). RQ therefore kills the run after 300 s however many files remain; the attempt marker tolerates that (a young in-flight marker is released, not poisoned) and the next day resumes. Moving it to `daily_long` is the knob if the daily backlog ever outgrows the budget.
- **`QuickBooks Sync Mapping` rows with `qbo_entity_type = Attachable` are attempt markers, not links** (see [Attachment mirroring](#attachment-mirroring-wi-071)). Deleting one re-enables a file the mirror gave up on — which is the documented reset, so do it knowingly, not as clean-up.
- Sandbox vs Production is chosen via `environment`; only the base URL differs (OAuth endpoints are shared).
