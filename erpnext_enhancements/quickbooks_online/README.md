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
| `core/api.py` | Whitelisted RPC surface (browser + Intuit) | `start_oauth`, `oauth_callback`, `disconnect`, `disconnect_callback`, `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `retry_failed`, `preview_existing_matches`, `link_existing_record`, `compare_account_balances`, `reconcile_transactions`, `sync_opening_balances`, `quickbooks_webhook`, `get_dashboard_status`, `get_sync_log_summary`, `get_match_queue`, `get_parked_transactions`, `decide_match`, `decide_matches`, `confirm_match` |
| `core/matching.py` | The Record Matching page's engine (v1.474.0): the master-record review queue with candidates, the parked-transactions list, and the link/merge/confirm decisions -- see [Record matching review](#record-matching-review-v14740) | `master_queue`, `parked_transactions`, `decide`, `decide_many`, `confirm`, `merge_plan`, `similar_records`, `latest_payloads` |
| `core/client.py` | OAuth2 + REST transport | `QuickBooksClient` (`build_authorization_url`, `exchange_code`, `refresh_access_token`, `revoke_tokens`, `request`, `query`, `get_entity`, `cdc`, `report`, `download_attachable`), `QuickBooksAPIError`, `QuickBooksDownloadTicketError` |
| `core/attachments.py` | Mirror QBO `Attachable` files onto their ERPNext docs as private Files (WI-071); self-protecting daily pass, fresh-ticket batching | `sync_attachments`, `reset_attachable`, `DOWNLOAD_URI_BATCH`, `MAX_ATTEMPTS`, `STALE_ATTEMPT_SECONDS`, `SAVE_TIMEOUT_SECONDS` |
| `core/constants.py` | Endpoints, entity catalogue, DocType map | `ENTITY_DOCTYPE_MAP`, `*_ENTITIES`, `ENVIRONMENT_BASE_URLS`, `OAUTH_SCOPE`, `MINOR_VERSION` |
| `core/mapping.py` | Transform / match / idempotent upsert | `map_qbo_to_erpnext`, `upsert_entity`, `find_existing_match`, `detect_conflicts`, `save_mapping`, `link_existing_record`, `_map_*`, `_match_*` |
| `core/sync.py` | Sync orchestration + logging | `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `run_cdc`, `retry_failed`, `query_all`, `store_raw_payload`, `start`/`finish`/`fail_log` |
| `core/reconcile.py` | Read-only balance/transaction reconciliation (Reports API) | `compare_account_balances`, `reconcile_transactions`, `_parse_trial_balance` |
| `core/opening_balances.py` | Build a balanced opening Journal Entry from QBO balances | `sync_opening_balances`, `_opening_account_line`, `_party_opening_line`, `_plug_line` |
| `core/group_account_remap.py` | One-off (WI-068): move draft JE lines off group accounts onto `- General` ledgers, one **window** at a time (`pre-2026` applied; `2026` outstanding for TASK-2026-01236). Dry-run by default, **never wired to migrate/scheduler** | `remap_group_account_lines`, `WINDOWS`, `NEW_LEDGER_CHILDREN`, `MERGE_INTO_EXISTING` |
| `core/party_group_remediation.py` | Remediation (v1.496.0 / v1.498.0): put each QBO-linked Supplier / Customer back in the group or territory it held before the importer's default-group sweep (from `tabVersion`) or **clear** it, plus the audit's hand-curated corrections in `core/supplier_group_corrections.json` and `core/customer_corrections.json`; Supplier search fields recomputed alongside. Dry-run by default; each side ran once from its patch (`restore_supplier_groups_after_qbo_sweep`, `restore_customer_groups_after_qbo_sweep`) -- see [Gotchas](#gotchas) and `MIGRATION_NOTES.md` §7 | `restore_party_groups`, `apply_curated_party_values`, `load_curated_corrections`, `curated_names_by_field`, `pre_sweep_group`, `restoration_target`, `SWEEP_LANDING_GROUPS` |
| `core/tasks.py` | Scheduler hooks (hourly + the daily attachment pass) | `refresh_token_if_needed`, `cdc_poll`, `retry_failed_syncs`, `sync_attachments_scheduled` |
| `report/quickbooks_balance_comparison/*` | QBO vs ERPNext account-balance report | `execute` (filters: as-of date, tolerance, only-discrepancies) |
| `core/utils.py` | Shared helpers | `get_settings`, `get_secret`/`set_secret`, `clear_oauth_tokens`, `json_dumps`/`loads`, `parse_qbo_datetime`, `is_token_expiring`, `verify_intuit_signature`, `update_settings_status` |
| `core/webhooks.py` | Inbound webhook handling | `handle_webhook`, `_iter_events` |
| `doctype/*/*.py` | Doctype controllers | `QuickBooksOnlineSettings` (has `validate`), `QuickBooksRawPayload`, `QuickBooksSyncLog`, `QuickBooksSyncMapping` |
| `page/quickbooks_online_dashboard/*.py` / `*.js` | Status dashboard page | `get_context`; render/refresh; the Record Matching button routes to the page below |
| `page/quickbooks_record_matching/*.py` / `*.js` | The accountant's matching queue (Masters + Parked transactions tabs) | `get_context`; renders `get_match_queue` / `get_parked_transactions`, dials `decide_match` / `decide_matches` / `confirm_match` / `sync_entity` |

## Doctypes

- **QuickBooks Online Settings** (Single) — credentials (`client_id`, encrypted `client_secret`, `webhook_verifier_token`, `redirect_uri`), OAuth state (encrypted `access_token`/`refresh_token`, `realm_id`, `token_expires_at`), cursors (`last_full_import`, `last_cdc_sync`, `last_webhook_at`), `status`/`status_message`, and tuning (`environment`, `company`, `sync_enabled`, `cdc_poll_minutes`, `retry_limit`).
- **QuickBooks Sync Mapping** — the link ledger keyed on (`qbo_entity_type`, `qbo_id`); stores `erpnext_doctype`/`erpnext_name`, `sync_token`, `last_qbo_updated_at`, `deleted`, `conflict_status`, `match_status`/`match_rule`/`match_confidence`, `owned_fields` (JSON of QBO-owned values, for conflict detection), and `reviewed_by`/`reviewed_on` (stamped by the Record Matching page; NULL until a person decides, and deliberately not a `match_status` option because `Created` already says something). `qbo_id` is indexed.
- **QuickBooks Sync Log** — one per run; `sync_type`, `status`, lifecycle timestamps, per-action counters, `retry_count`, `preview_payload`, `error_message`.
- **QuickBooks Raw Payload** — append-only audit of every fetched/received payload; `source`, entity type/id, `realm_id`, `sync_log` link, `received_at`, verbatim `payload`. ~433k rows on production; `qbo_id` is indexed (v1.474.0) because every newest-payload lookup was a full scan without it.

## Record matching review (v1.474.0)

The **QuickBooks Record Matching** desk page (`page/quickbooks_record_matching/`, shortcut on
the Finance Hub and on this workspace, roles System Manager + Accounts Manager — the same gate
as the endpoints) is the accountant's queue for deciding which QBO records link to which
ERPNext records. Logic in `core/matching.py`; the whitelisted surface is `get_match_queue`,
`get_parked_transactions`, `decide_match`, `decide_matches`, `confirm_match` in `core/api.py`.

**Why it replaced the dashboard's "Link Existing Records" dialog.** That dialog listed QBO
records with *no* Sync Mapping row, and after Import All that is none of them — every master
record gets a row on import (`Created`, `Auto Matched` or `Pending Review`). On production it
was empty for the person with ~2,300 master links to check. Its population survives as the
*Unmapped payloads* filter, for the pre-import flow (Preview Resync → link → Import All).

**The Masters tab** shows every master mapping with a filter by entity type and status
(*Needs decision* is the default: not yet reviewed and not already a manual match), the QBO
record's identifying facts, the current link and its status, up to five other candidates —
the sync's own matcher first (`find_existing_match`), then the stored candidates of an
ambiguous match, then name-similar records (`similar_records`: a SQL `like` on the two longest
words of the normalised name, ranked by `difflib` in Python) — a Link picker pre-filled with
the best one, and per row:

| Action | Endpoint | What it does |
|---|---|---|
| **Link** | `decide_match` | `link_existing_record` to the picked record (optionally filling its blank fields from QBO), stamps `reviewed_by`/`reviewed_on`, then applies the merge policy below |
| **Keep** | `confirm_match` | stamps the row reviewed; changes no status (a `Pending Review` row stays pending until its cause is fixed and it is retried) |
| **Retry** | `sync_entity` | re-syncs a parked row from QBO |
| **Accept suggestions on this page** | `decide_matches` | Link for every row whose best suggestion clears the threshold; one failure never stops the rest |

**Merge policy** (`matching.merge_plan`, pinned by `tests/test_quickbooks_matching.py`): when
the link moves off a record whose mapping said `Created` — i.e. the import made it — onto a
different record of the *same* doctype, the import's copy is folded into the chosen record
with the model-level `frappe.model.rename_doc.rename_doc(merge=True, force=True,
ignore_permissions=True)` (not the `frappe.rename_doc` alias, which on 16.30 has no
`ignore_permissions` — `tests/test_uom_cleanup.py` guards every call site), which re-points
every Link and Dynamic Link (so posted transactions and any other QBO id mapped to the copy
follow). Nothing else is ever merged: not a record a person made, not one the import linked
*to* (`Auto Matched`), not one already decided (`Manual Matched`), never a Customer into a
Project, and never a Project (use the Project Merge tool, which cancels rather than deletes).
Account merges are pre-checked on `is_group`/`root_type`/`company`/`account_currency`, the
four properties ERPNext's `merge_account` insists on. The link is **committed before** the
merge is attempted; a merge ERPNext refuses comes back as `merge.status == "failed"` with the
message and the decision stands. The merged record's Drive folder is not touched.

**The Parked transactions tab** lists `Pending Review` mappings of transaction types with the
stored preflight `issues`, the draft document if one exists, and Retry (per row, or the whole
page sequentially).

**Performance.** The newest payload for a page of rows comes from one query per entity type
(`latest_payloads`), never one per row: `tabQuickBooks Raw Payload` holds ~433k rows and until
v1.474.0 had no index on `qbo_id` (it does now — `search_index` on the doctype). Candidate
lookups and title lookups are likewise batched per doctype.

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
- **The Record Matching page's Link action can delete a record, and the rule for when is in `matching.merge_plan`, not in the UI.** Only a record whose mapping said `Created` (the import made it) is ever merged into the chosen record, and only into the same doctype; Projects never. A refused merge leaves the link in place and the duplicate in the queue -- it is reported, not raised. Do not widen the policy from the page.
- **`preview_existing_matches` lists only QBO records with NO mapping row**, which after Import All is none of them. That is why the dashboard's old "Link Existing Records" dialog read empty on production, and why the Record Matching page exists; the old population is its *Unmapped payloads* filter.
- **Reconciliation is read-only.** `compare_account_balances` (Trial Balance vs GL) and `reconcile_transactions` (payload total vs document total) never write — they surface discrepancies for you to act on. Run the **QuickBooks Balance Comparison** report after an import.
- **Opening balances are a draft by default.** `sync_opening_balances` creates one balanced Opening Entry; review it before submitting (pass `auto_submit` to post it). A/R and A/P are broken out per party from QBO's *current* open balances (correct for a present-day cut-over; for a historical cutoff, check the draft against QBO's aging). Stock accounts are excluded — post opening stock via a Stock Reconciliation — and any residual squares off against the company's **Temporary Opening** account.
- **CDC cursor** advances only on a clean run, so failures reprocess the same window. The first run looks back 24h. `TaxCode` is excluded from CDC (Term/PaymentMethod/Class are included).
- **Conflict policy:** user edits to QBO-owned fields are preserved unless an overwrite resync (`run_resync`) is run; a preview is required first.
- **A party's group / territory is ERPNext's; the importer files a new party into NO group and never writes one on update.** QuickBooks has no supplier group, customer group or territory. Until v1.496.0 `_map_supplier` / `_map_customer` invented one with `frappe.db.get_value(doctype, {"is_group": 0}, "name")` -- and a dict-filtered `get_value` on Frappe 16 orders by `creation` **descending**, so "any leaf" meant "the leaf somebody created most recently"; because the update path re-applied every mapped value on each re-sync, every Supplier Group anyone added became the group of all 911 QBO-linked Suppliers on the next scheduled run (five sweeps between 2026-06-18 and 2026-09-16), and 466 Customers landed in "Government" -- invisibly, since `customer_group` is hidden on the Customer form. Now `constants.DEFAULT_PARTY_GROUP` is `None` (a wrong group is worse than no group -- Nik, 2026-09-22; if a named default is ever wanted it goes there as a NAME, resolved with `frappe.db.exists`), `_drop_party_groups_on_update` strips the three fields from every update whatever the record holds, and `ERPNEXT_OWNED_PARTY_FIELDS` are excluded from the `owned_fields` snapshot and from `detect_conflicts`, so re-grouping or clearing a Supplier is never a conflict and is never undone. The swept Suppliers were restored or cleared by `patches/restore_supplier_groups_after_qbo_sweep` and the swept Customers -- group *and* territory -- by `patches/restore_customer_groups_after_qbo_sweep` (engine: `core/party_group_remediation.py`, dry-run by default; `MIGRATION_NOTES.md` §7). Never resolve a default with an unordered `get_value` -- name it.
- **Per-record resilience:** batch ops use `safe_upsert`, so one bad record can't abort a run; inline failure notes are capped at 20 (full tracebacks go to the Frappe Error Log).
- **No rate-limit/backoff handling:** QBO 429/throttling responses aren't specifically handled — any ≥400 (other than 401) raises `QuickBooksAPIError`.
- **The daily attachment job runs on the `default` queue (300 s RQ timeout), not `long`.** `hooks.py` lists it under `daily`, and v16's `ScheduledJobType.get_queue_name` sends only `*Long`/`Maintenance` frequencies to `long` (1500 s). RQ therefore kills the run after 300 s however many files remain; the attempt marker tolerates that (a young in-flight marker is released, not poisoned) and the next day resumes. Moving it to `daily_long` is the knob if the daily backlog ever outgrows the budget.
- **`QuickBooks Sync Mapping` rows with `qbo_entity_type = Attachable` are attempt markers, not links** (see [Attachment mirroring](#attachment-mirroring-wi-071)). Deleting one re-enables a file the mirror gave up on — which is the documented reset, so do it knowingly, not as clean-up.
- Sandbox vs Production is chosen via `environment`; only the base URL differs (OAuth endpoints are shared).
