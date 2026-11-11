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
              Retries   ───────────┘   (hourly, a failed CDC poll only)
```

1. **OAuth2** — `api.start_oauth` mints a one-time CSRF `state` (cached 10 min) and returns Intuit's consent URL; `api.oauth_callback` (guest) validates the state, exchanges the code, and stores tokens. `client.QuickBooksClient` owns the token lifecycle and transparently refreshes on a 401.
2. **Client** — authenticated REST helpers (`request`, `query`, `get_entity`, `cdc`) against the Sandbox/Production base URL with a pinned `minorversion`.
3. **Mapping** — `map_qbo_to_erpnext` transforms a QBO payload to an ERPNext DocType + values; `upsert_entity` decides idempotently: already linked → compare and flag, **never save** (see [The sync links, it never overwrites](#the-sync-links-it-never-overwrites-v18000)) → auto-link by fuzzy match → create → defer to manual review.
4. **Sync** — `sync.py` orchestrates `import_all`, `preview_resync`/`run_resync`, `sync_entity`, `run_cdc`, `retry_failed`. Each run opens a Sync Log, archives every payload as a Raw Payload, and routes writes through `safe_upsert`.
5. **Webhooks** — `webhooks.handle_webhook` verifies the Intuit HMAC signature, archives the notification, and enqueues a background `sync_entity` per changed entity.
6. **CDC poll** — `tasks.cdc_poll` throttles by `cdc_poll_minutes`; `run_cdc` pulls all changes since the `last_cdc_sync` cursor and advances it only on a clean run.
7. **Retries** — `tasks.retry_failed_syncs` fails orphaned runs and re-runs a failed **CDC** poll, up to `retry_limit`. It never starts an Import All or a Run Resync; a person restarts those from the dashboard.

## File map

| File | Purpose | Key functions / classes |
|---|---|---|
| `api.py` (module root) | Re-exports the QBO whitelisted endpoints (browser + Intuit webhook URL) | re-exports from `core/api.py` |
| `core/api.py` | Whitelisted RPC surface (browser + Intuit) | `start_oauth`, `oauth_callback`, `disconnect`, `disconnect_callback`, `import_all`, `preview_resync`, `run_resync`, `sync_entity`, `retry_failed`, `preview_existing_matches`, `link_existing_record`, `compare_account_balances`, `reconcile_transactions`, `sync_opening_balances`, `quickbooks_webhook`, `get_dashboard_status`, `get_sync_log_summary`, `get_match_queue`, `get_parked_transactions`, `decide_match`, `decide_matches`, `confirm_match`, `confirm_matches`, `keep_erpnext`, `use_quickbooks_values`, `refresh_drafts`, `start_matching_export`, `get_matching_export_status`, `preview_matching_import`, `apply_matching_import` |
| `core/matching.py` | The Record Matching page's engine (v1.474.0): the master-record review queue with candidates, the parked-transactions list, and the link/merge/confirm decisions, and (v1.800.0) the "QuickBooks changed" view and its three answers -- see [Record matching review](#record-matching-review-v14740) | `master_queue`, `parked_transactions`, `decide`, `decide_many`, `confirm`, `confirm_many`, `merge_plan`, `similar_records`, `latest_payloads`, `change_view`, `keep_erpnext`, `use_quickbooks_values`, `refresh_draft`, `review_many` |
| `core/matching_excel.py` | The Record Matching page's Excel round trip (v1.721.0): the export workbook (background job), the import preview and the re-checked apply -- see [Excel round trip](#excel-round-trip-v17210) | `start_export`, `run_export`, `export_status`, `build_workbook`, `parse_workbook`, `classify_row`, `classify`, `preview`, `apply`, `row_version` |
| `core/client.py` | OAuth2 + REST transport | `QuickBooksClient` (`build_authorization_url`, `exchange_code`, `refresh_access_token`, `revoke_tokens`, `request`, `query`, `get_entity`, `cdc`, `report`, `download_attachable`), `QuickBooksAPIError`, `QuickBooksDownloadTicketError` |
| `core/attachments.py` | Mirror QBO `Attachable` files onto their ERPNext docs as private Files (WI-071); self-protecting daily pass, fresh-ticket batching | `sync_attachments`, `reset_attachable`, `DOWNLOAD_URI_BATCH`, `MAX_ATTEMPTS`, `STALE_ATTEMPT_SECONDS`, `SAVE_TIMEOUT_SECONDS` |
| `core/constants.py` | Endpoints, entity catalogue, DocType map | `ENTITY_DOCTYPE_MAP`, `*_ENTITIES`, `ENVIRONMENT_BASE_URLS`, `OAUTH_SCOPE`, `MINOR_VERSION` |
| `core/mapping.py` | Transform / match / idempotent upsert; the compare-and-flag path for linked records and the three review writes | `map_qbo_to_erpnext`, `upsert_entity`, `_review_linked_record`, `keep_erpnext_values`, `use_qbo_values`, `refresh_linked_draft`, `find_existing_match`, `detect_conflicts`, `save_mapping`, `link_existing_record`, `_map_*`, `_match_*` |
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
| `page/quickbooks_record_matching/*.py` / `*.js` | The accountant's matching queue (Masters + Parked transactions tabs) | `get_context`; renders `get_match_queue` / `get_parked_transactions`, dials `decide_match` / `decide_matches` / `confirm_match` / `confirm_matches` / `sync_entity` / `keep_erpnext` / `use_quickbooks_values` / `refresh_drafts`; tick boxes, 50–500 rows per page; the "QuickBooks changed" filter on each tab; Export to Excel / Import from Excel |

## Doctypes

- **QuickBooks Online Settings** (Single) — credentials (`client_id`, encrypted `client_secret`, `webhook_verifier_token`, `redirect_uri`), OAuth state (encrypted `access_token`/`refresh_token`, `realm_id`, `token_expires_at`), cursors (`last_full_import`, `last_cdc_sync`, `last_webhook_at`), `status`/`status_message`, and tuning (`environment`, `company`, `sync_enabled`, `cdc_poll_minutes`, `retry_limit`).
- **QuickBooks Sync Mapping** — the link ledger keyed on (`qbo_entity_type`, `qbo_id`); stores `erpnext_doctype`/`erpnext_name`, `sync_token`, `last_qbo_updated_at`, `deleted`, `conflict_status` (`Clean` / `QuickBooks Changed` / `Ignored` / `Pending Review`, plus the pre-v1.800.0 `Conflict`), `qbo_changes` (JSON: what QuickBooks now says beside what ERPNext held, written when the sync flags the row) and `settled_qbo_values` (JSON: the QuickBooks value last settled per field, applied or declined; the reference the next comparison reads), `match_status`/`match_rule`/`match_confidence`, `owned_fields` (JSON snapshot of what the last sync stored), and `reviewed_by`/`reviewed_on` (stamped by the Record Matching page; NULL until a person decides, and deliberately not a `match_status` option because `Created` already says something). `qbo_id` is indexed.
- **QuickBooks Sync Log** — one per run; `sync_type`, `status`, lifecycle timestamps, per-action counters, `retry_count`, `preview_payload`, `error_message`.
- **QuickBooks Raw Payload** — append-only audit of every fetched/received payload; `source`, entity type/id, `realm_id`, `sync_log` link, `received_at`, verbatim `payload`. ~433k rows on production; `qbo_id` is indexed (v1.474.0) because every newest-payload lookup was a full scan without it.

## The sync links, it never overwrites (v1.800.0)

Nik's decision of 2026-10-06 ("Party links ask instead of guessing; QuickBooks sync links,
never overwrites"). Two behaviors went away.

**1. No self-starting imports.** `sync.retry_failed` used to end by running a full Import All
whenever a failed Import All or Run Resync log existed. The scheduler calls it hourly on the
300-second default queue, where a full import cannot finish, so each run failed and earned the
next: 2 to 6 full imports a day. Now `retry_failed` retries **CDC only**, once per call. A failed
Import All / Run Resync / Preview Resync (`sync.RESTART_BY_HAND_TYPES`) is left as it is and named
in the answer; the dashboard's *Retry Failed* says to start it again, which is one click on the
same page and runs on the long queue. `api.import_all` reaps stale runs first, so an orphaned
Running log cannot answer "already running" to the one button that starts an import.

**2. A linked record is never saved by a sync.** Not by the hourly CDC, a webhook, Import All or
Run Resync, for any doctype, drafts included, whether the import matched the record or created it.
`upsert_entity` hands a linked record to `mapping._review_linked_record`, which only compares:

| QuickBooks since the mapping's last sync | What QuickBooks changed vs the record | Result | Written |
|---|---|---|---|
| has not moved | (not compared) | `unchanged` | nothing |
| moved | nothing it changed differs | `unchanged` | the mapping's cursor (`sync_token`, `last_qbo_updated_at`) |
| moved | a changed field differs | `flagged` | the mapping: `conflict_status = QuickBooks Changed` and `qbo_changes` |
| any, record is submitted | (the older guard) | `unchanged` / `manual_review` | as before v1.800.0 |

**What counts as a difference** (`_linked_differences`). Each mapped field is first held against
its *reference*, the last thing QuickBooks is known to have said: the settled value in
`settled_qbo_values`, else what the last sync stored in `owned_fields`.

| The field | Result |
|---|---|
| has a reference, and QuickBooks still says the same | skipped, however far ERPNext has moved (an accountant's edit to a draft, a customer renamed here) |
| has a reference, and QuickBooks changed it | compared with the record; flagged if they differ |
| has a reference, and QuickBooks cleared it (a mapped `None`: a term removed, a reference that no longer resolves) | flagged `cleared` if the record still holds a value |
| has **no** reference (it differed on the day the record was first linked, so the import never wrote it) | compared with the record directly, so it flags on the first pass that looks at it, whichever side changed. Marked `no_baseline`; the page says "never synced before" and leaves it unticked |
| is a mapped `None` and never had a value | skipped |
| is a mapped `None`, or a `None` cell in a child table, **and the payload failed the preflight** | flagged `unresolved` wherever the record holds a value and the field has no reference, or the reference held a value. The None may be a value ERPNext could not resolve (a ledger or term not imported yet), and the preflight issues ride on the flag. *Use QuickBooks values* is refused; *Keep ERPNext* settles it |

So "flags only what QuickBooks changed" holds for every field with a reference, which is every
field of a record the import created and every field that agreed when a record was linked. A field
with no reference is the exception, once: its first answer settles it.

**The reference follows QuickBooks.** Every field a pass looked at and did not flag is settled at
what QuickBooks says now (`_settle_unflagged`), in the same write that advances the cursor or raises
the flag. Without that a real change was lost: ERPNext edited to Net 45, QuickBooks moves to Net 45
(they agree), QuickBooks moves back to Net 30, which equalled the stale reference and read as "not
changed".

The comparison (`_same_for_review`) forgives what ERPNext does to a value on save: blank is blank,
edge whitespace and markup go, a website gains its scheme. **Numbers are compared at the precision
the field stores** (`number_precisions`, from the DocType meta: a Currency field that keeps 2 decimals
holds 12.35 for a mapped 12.3456, the same value), child-table cells at the child field's precision,
and **exactly when the field's type is not known**. There is no blanket tolerance: the first version
called any two numbers of 1 or more within half a cent equal, so an exchange rate moving from 1.32 to
1.3245 read as unchanged.

**A stale payload does nothing.** A payload older than the newest one the mapping has seen (its
cursor, or the token stored on its flag) returns `unchanged` with `stale: true`: it never flags,
never clears a flag and never moves the cursor. Run Resync replays what an earlier Preview Resync
stored, and a webhook can arrive late; without the check a token-3 replay cleared a flag raised at
token 4. Every write of a mapping's cursor is forward-only (`_forward_cursor`, `_stamp_cursor`), the
`save_*` helpers of the unlinked paths included. When the payload fails the preflight and nothing
flags, nothing is written at all: the cursor stays and a parked row keeps its status.

A flag is written with `frappe.db.set_value`, never `save_mapping`, so `owned_fields` survives; the
cursor is held back so a replay re-derives the same flag, and an identical replay writes nothing.

**What ERPNext owns on a linked record is dropped before the comparison**
(`_strip_values_erpnext_owns`), so it is never flagged and can never be applied:

| Record | Never flagged, never applied | Why |
|---|---|---|
| Project (from a QuickBooks job) | `project_name` (when set), `status`, `company` | the title is curated; status and company are constants the mapper supplies for a create. Only `customer` can flag |
| Customer / Supplier | `customer_group`, `territory`, `supplier_group`, `customer_type`, `supplier_type` | QuickBooks has none of these; the type is a guess from whether `CompanyName` is filled |
| Item | `item_code`, `item_group`, `stock_uom`, `is_stock_item` | the code is the record's name; the rest are constants ("All Item Groups", "Nos", 0) |
| Account (from a QuickBooks Account) | `company`; a self-parent; `is_group` and `account_type` when ERPNext has children under it | `is_group` is only known on a full query, so every CDC payload of a parent reads as a leaf |
| Account (from a TaxCode) | everything but `account_name` | the mapper invents a Tax ledger under the Liability root |
| Cost Center (from a Class) | `company`; `is_group` when ERPNext has children under it | as for Account |
| Payment Terms Template, Mode of Payment | `template_name`; `mode_of_payment`, `enabled` | the record's own name; a constant |

Transactions are not in the list: each one the import created has a full baseline, so its constants
only read as changed if the constant itself changes, and *Refresh draft* rebuilds the whole draft.
A QuickBooks **job rename** is therefore dropped, with the cursor advanced and no flag: titles are
ERPNext's.

Three things this table costs, on purpose:

- **`customer_type` / `supplier_type` and `item_code` are partly QuickBooks-derived** (from whether
  `CompanyName` is filled, and from the `Sku`). Stripping them hides a QuickBooks customer changing
  from a person to a company, and a SKU change, on a linked record. The alternative was a guess and
  a rename-that-renames-nothing offered for one-click application.
- **An Account's `is_group` and `account_type` are not stripped** (only for a group that has children
  in ERPNext). They are real QuickBooks facts on a full query and unreliable on a CDC payload, which
  never knows whether an account has children, so a parent Account with no children in ERPNext can
  flag `is_group` / `account_type` as noise. One Keep ERPNext settles it.
- **Use QuickBooks values on a subset settles the unticked fields as declined.** They stay silent
  afterward, until QuickBooks changes them again.

**The two side doors are shut too.** A sync could also save a linked record that is *not* the one it
is syncing. A new sub-account under a ledger parent promoted that parent to a group
(`_ensure_group_parent`: `is_group` set, `account_type` cleared, parent saved): where a mapping links
the parent, the child is parked instead ("make it a group by hand"). And the auto-link path filled
the blank fields of whatever record the matcher found: where another mapping already links that
record (a customer re-created in QuickBooks, two jobs with one PRJ number), the new one is parked
for a person, with no save and no second link. `other_mapping_linking` answers both, and counts a
mapping marked `deleted` (QuickBooks removed its record; the ERPNext record is still one the sync
linked).

**One named exception to "never saves a linked record": the consolidated job.** A QuickBooks job
with no matching Project is filed under its parent Customer by a mapping row with
`match_rule = job_merge_no_project`. That row is not the Customer's own link. So a Customer whose
*only* mapping rows are consolidated jobs still takes its own QuickBooks customer as a first link
through the auto-link path, with its blank fields filled and the record saved, exactly as for a
Customer nothing links. Without the exception every Customer that has consolidated jobs would refuse
its own QuickBooks record.
`preview=True` reports `would_flag` and writes nothing. A person-started Preview Resync / Run Resync
passes `recheck=True` and compares even a record QuickBooks has not moved: that is how a standing
difference (a mapper fix that changes what a draft should hold, a field that has differed since the
record was first linked) is brought up for review. It still only flags.
`overwrite` is accepted by `upsert_entity` and ignored.

**Review is on the Record Matching page** (System Manager, Accounts Manager): the *QuickBooks
changed* status filter on Masters, and the *QuickBooks changed* choice in the transactions tab's
select. Each flagged row shows "ERPNext has" (read from the record when the page loads) beside
"QuickBooks has", field by field. Three POST-only endpoints, each for one row or the ticked rows
(at most `matching.MAX_REVIEW_ITEMS`, 50, per request, which also caps `decide_matches`; the page
sends 20), none of which calls QuickBooks or queues a job:

| Action | Endpoint | What it does |
|---|---|---|
| **Keep ERPNext** | `keep_erpnext` | Clears the flag, advances the cursor to what the reviewer saw, records the declined values in `settled_qbo_values`. Writes nothing to the record. Also accepted for a row still carrying the pre-v1.800.0 `Conflict` (the newest stored payload is compared with the record and what differs is settled as declined, without being shown: the page says so before it does it) and for a **submitted** document the sync parked because QuickBooks changed it (the row is un-parked; a posted document is never updated in place). |
| **Use QuickBooks values** | `use_quickbooks_values` | Writes the ticked fields to that one record and saves it; a field QuickBooks cleared is blanked when ticked. With no list it takes what the page ticks by default: not a "never synced before" field and not a cleared one. Refused for a submitted or cancelled document, for a flag whose QuickBooks data failed the preflight, and for a stored name that is not a field of the DocType. An unticked field keeps what ERPNext has and is settled too. |
| **Refresh draft** | `refresh_drafts` | Rebuilds one docstatus 0 transaction from the newest stored payload. Also offered on a parked transaction that already has a draft, where a sync used to rebuild it. |

**A write checks its own result** (`_differences_after_write`). *Refresh draft* and *Use
QuickBooks values* used to set the values, save, and mark the mapping Clean. But a save is not a
copy: ERPNext's controllers run in between. So after the save the record is read back and held
against the same QuickBooks values, field by field and row by row, with no reference to excuse
anything.

| The saved record | The mapping | The result |
|---|---|---|
| holds every value | Clean, a fresh snapshot, the values settled | `action: refreshed` / `applied` |
| still differs somewhere | `QuickBooks Changed`; `qbo_changes` holds what remains, with `after_write` (`refresh` / `use`) | `action: still_differs`, the fields in `still_differs` and `still_labels` |

The save is kept in both cases; only the claim changes. A field that still differs is also dropped
from `owned_fields` and `settled_qbo_values`. That is what keeps the flag honest later: a reference
saying "QuickBooks said 0, and that was dealt with" would let the next sync skip the table and clear
the flag while the draft still holds 250. With no reference the field is compared with the record
on every pass that looks, until the two agree or a person chooses Keep ERPNext (which settles what
QuickBooks said). Left out of the check, by name (`REWRITTEN_ON_SAVE`): a Payment Entry's
`remarks`, which ERPNext composes itself on every save.

**Why it exists: ERPNext reprices a line that carries no `price_list_rate`.** On every save
`accounts_controller.set_missing_item_details` copies the Item's list price onto a line whose
`price_list_rate` is None, and `taxes_and_totals.calculate_item_rate` then runs
`if has_pricing_rules or not item.rate: item.rate = calculated_rate` (erpnext v16.54.0). A line
QuickBooks has at 0 came out at list price, on a create as much as on a rebuild, and a rate below
list as list price less an invented discount. On a buying document it needs no Item Price: with
none, ERPNext uses the Item's last purchase rate. So every line `_sales_items` and
`_purchase_items` build carries `price_list_rate` equal to its `rate` (`_priced_line`), and the
four documents built from item lines set `ignore_pricing_rule`. Both are instructions to ERPNext,
not QuickBooks data, and are never compared, flagged or settled (`BUILD_ONLY_ROW_FIELDS`,
`BUILD_ONLY_FIELDS`): a draft holding the Item's list price beside the right rate is not something
QuickBooks changed, and every snapshot stored before v1.800.0 lacks the key. A bench-free test
cannot see ERPNext reprice anything, so the check after the write is the one that runs against the
real controller. **Drafts created at the wrong rate before v1.800.0 are not found by a sync** while
QuickBooks leaves their lines alone: their reference says "0, as QuickBooks said".

Every answer records the QuickBooks value as settled, so a field is not flagged again while
QuickBooks still says the same thing: a standing difference is answered once. The Sync Log counts
a flag under `conflict_count`; the *QuickBooks Open Conflicts* number card and the `qbo_conflicts`
KPI count `QuickBooks Changed` and the older `Conflict` together. Tests:
`tests/test_quickbooks_link_only.py` (its own pytest step in CI). Besides the single cases it holds a seeded random walk
(`test_random_walk_keeps_the_link_only_invariant`, 40 seeds of 250 steps) over the real
`upsert_entity`, `keep_erpnext_values` and `use_qbo_values`: QuickBooks edits, ERPNext edits, syncs,
replays of older payloads, previews, Keep, Use on a random subset. After every step: no sync wrote
the record; after a sync that compares, each mapped field equals the record, or is in the flag note,
or QuickBooks still says what was last accounted for; a stale payload changed nothing; the cursor
never moved backwards.

**RQ's job timeout is no longer swallowed.** `safe_upsert` caught `JobTimeoutException` like any
bad record; the run then had no time limit and its log sat in Running until the 11-hour reaper.
It is re-raised now (`utils.is_rq_job_timeout`), so a timed-out run ends as Failed at once.

## Record matching review (v1.474.0)

The **QuickBooks Record Matching** desk page (`page/quickbooks_record_matching/`, shortcut on
the Finance Hub and on this workspace, roles System Manager + Accounts Manager — the same gate
as the endpoints) is the accountant's queue for deciding which QBO records link to which
ERPNext records. Logic in `core/matching.py`; the whitelisted surface is `get_match_queue`,
`get_parked_transactions`, `decide_match`, `decide_matches`, `confirm_match`, `confirm_matches`
in `core/api.py`.

**The tab is in the route**, so the browser's Back and Forward move between the two tabs.
Masters is the bare route, which the dashboard and the workspace tiles open, and Parked
transactions is `/desk/quickbooks-record-matching/transactions`. Frappe v16's router owns
`popstate` on the Desk, so a tab that changed nothing but a hidden attribute had no entry,
and Back left the page. A tab click only routes. `on_page_show` shows the tab the route
names and never refetches rows already on screen, because that would drop unsaved picks and
ticks. Filters and pages of rows stay out of history, as they do in Frappe's own list view.
Only the newest parked-transactions reply paints.

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
| **Link** | `decide_match` | `link_existing_record` to the picked record (optionally filling its blank fields from QBO), stamps `reviewed_by`/`reviewed_on`, then applies the merge policy below. POST only, like `decide_matches` and `link_existing_record` (v1.800.0). *Fill blank fields* is refused when another QuickBooks record already links the picked record; the link itself is still allowed (two QuickBooks records on one ERPNext record is a warning, not a refusal: Nik, 2026-10-22) |
| **Keep** | `confirm_match` | stamps the row reviewed; changes no status (a `Pending Review` row stays pending until its cause is fixed and it is retried) |
| **Retry** | `sync_entity` | re-syncs a parked row from QBO |
| **Accept suggestions on this page** | `decide_matches` | Link for every row whose best suggestion clears the threshold; one failure never stops the rest |
| **Link selected** (v1.518.0) | `decide_matches` | Link for every ticked row, each to the record in its *own* picker with its *own* "Fill blank fields" box (a decision's `fill_blanks` wins over the call-level one) |
| **Keep selected** (v1.518.0) | `confirm_matches` | Keep for every ticked row; a row with no mapping is reported, not raised |
| **Retry selected** (v1.518.0) | `sync_entity` | Retry for every ticked parked row, one request after another |

**Ticking rows (v1.518.0).** Every row on both tabs has a tick box, the header ticks the page,
and Shift-click ticks a range. Choosing a record in a row's picker, or clicking one of its
suggestions, ticks the row. The bulk buttons live in a bar under the table that pins to the bottom
of the window, so they stay in reach on a long page. A reload keeps the ticks, picks and fill
boxes of the rows still on screen and drops the rest, so a bulk action only ever reaches rows the
accountant can see. Mind the pre-fill: it is the best *other* record (the current link is never
its own suggestion), so linking a ticked row that is already right re-points it. The *Link
selected* confirmation counts those rows first, in bold, and *Keep selected* is the action for
them. Pages hold 50, 100, 200 or 500 rows (`MAX_PAGE_LENGTH` is 500, and a test holds the page's
`PAGE_LENGTHS` to it). The pager moves by the `page_length` the server reports serving. Bulk
links go to `decide_matches` 20 at a time (`LINK_CHUNK` in the page script), because a batch of
`rename_doc` merges in one request would outrun the gateway timeout.

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
stored preflight `issues`, the draft document if one exists, and Retry (per row, for the ticked
rows, or the whole page, one request after another).

**Performance.** The newest payload for a page of rows comes from one query per entity type
(`latest_payloads`), never one per row: `tabQuickBooks Raw Payload` holds ~433k rows and until
v1.474.0 had no index on `qbo_id` (it does now — `search_index` on the doctype). Candidate
lookups and title lookups are likewise batched per doctype.

## Excel round trip (v1.721.0)

The page's **Export to Excel** and **Import from Excel** buttons let someone work the queue in a
spreadsheet (Meg, for Nik, 2026-10-22) and bring the decisions back. Logic in
`core/matching_excel.py`; endpoints `start_matching_export`, `get_matching_export_status`,
`preview_matching_import`, `apply_matching_import`, behind the same operator gate.

- **Export** is a background job (`queue="long"`); the page polls the token. The workbook has an
  Instructions sheet, a sheet per master type with the Masters tab's rows (read once with
  `matching.master_mapping_rows` and built by the page's own `build_master_rows`; offset paging
  would skip rows that people decide on the page while the job runs), and a Parked transactions sheet. Yellow columns:
  **Decision** (Link / Keep / Retry, the page's three; blank = leave it), **Link to** (a per-row
  dropdown of the row's suggestion IDs, pre-filled with the best), **Fill blank fields from QBO**,
  **Notes**. Hidden: Row ID, Row version, `modified`, the exported link and status, the suggestion
  IDs the dropdown reads. Unmapped payloads are not exported: they have no Row ID to come back by.
- **Import** reads the uploaded private File, finds columns by header text (Meg may sort, reorder
  and add sheets), and previews each row as *change*, *unchanged* (already done), *stale* (changed
  in ERPNext since the export) or *invalid*. **Nothing is written until Apply**, which sends
  `APPLY_CHUNK` rows per request (a Retry alone, as the page's own Retry); each row is classified
  again immediately before it runs -- a Link earlier in the same request can merge a copy and
  re-point other mappings -- and only a row still a *change* runs, through `matching.decide` / `matching.confirm` / `sync.sync_entity`.
- **Idempotent.** "Already done" is checked before "stale", so a second import of the same file
  finds every row unchanged; a note is already saved when an existing Comment on the mapping
  contains its text.
- **The stale check is a fingerprint** (`row_version`) of the link, `match_status`,
  `conflict_status` and `reviewed_on` -- not `modified`, which moves on every CDC touch. A moved
  `modified` is a warning only.
- **Two rows linking one ERPNext record** is a warning on both and both apply (Nik, 2026-10-22);
  notes are saved as a Comment on the Sync Mapping row.
- **Duplicate Row IDs** (a copied sheet) that disagree are a conflict and neither applies; copies
  that agree apply once. A note is "already saved" only when the import's own comment carries its
  `[Excel note <hash>]` marker.
- **Hardening:** every string cell is written as text (no formulas from QBO data); Link and Keep only
  on master QBO types, whatever headers a sheet has; key columns locked by sheet protection (no
  password, filtering and widths allowed); sheet dimensions reset before reading; a 100 MB
  expanded-size cap. The page settles every request on the `frappe.call` promise.
- **Files:** exports and uploads live in `Home/QuickBooks Matching Excel`, tagged
  `attached_to_field = "qbo_matching_excel"`. The daily `matching_excel.delete_old_files` deletes only
  tagged, unattached Files older than 7 days, never a File that merely sits in the folder. Preview
  accepts only the caller's own private, unattached upload, since accepting one tags it for deletion.

## Scheduler / webhook entry points

- `tasks.refresh_token_if_needed` (hourly) — refresh the access token if expiring within 10 min (no-op when disconnected). On `invalid_grant` (refresh token revoked/expired — e.g. the user disconnected the app from Intuit's side) it clears the dead tokens and marks the connection Not Connected instead of erroring every run.
- `tasks.cdc_poll` (hourly) — run CDC if `cdc_poll_minutes` elapsed since `last_cdc_sync`.
- `tasks.retry_failed_syncs` (hourly, :40) — fail orphaned Running/Queued logs, then re-run a failed CDC poll once, capped by `retry_limit`. Never an Import All or a Run Resync (v1.800.0).
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

- **Idempotency** hinges on the (entity_type, qbo_id) Sync Mapping; re-running import/webhook/CDC is safe, and since v1.800.0 a re-run never saves a record that is already linked. Transactions are never fuzzy-matched (always created); only master entities (Account/Customer/Vendor/Item/TaxCode/Term/PaymentMethod/Class) auto-link.
- **The Record Matching page's Link action can delete a record, and the rule for when is in `matching.merge_plan`, not in the UI.** Only a record whose mapping said `Created` (the import made it) is ever merged into the chosen record, and only into the same doctype; Projects never. A refused merge leaves the link in place and the duplicate in the queue -- it is reported, not raised. Do not widen the policy from the page.
- **`preview_existing_matches` lists only QBO records with NO mapping row**, which after Import All is none of them. That is why the dashboard's old "Link Existing Records" dialog read empty on production, and why the Record Matching page exists; the old population is its *Unmapped payloads* filter.
- **Reconciliation is read-only.** `compare_account_balances` (Trial Balance vs GL) and `reconcile_transactions` (payload total vs document total) never write — they surface discrepancies for you to act on. Run the **QuickBooks Balance Comparison** report after an import.
- **Opening balances are a draft by default.** `sync_opening_balances` creates one balanced Opening Entry; review it before submitting (pass `auto_submit` to post it). A/R and A/P are broken out per party from QBO's *current* open balances (correct for a present-day cut-over; for a historical cutoff, check the draft against QBO's aging). Stock accounts are excluded — post opening stock via a Stock Reconciliation — and any residual squares off against the company's **Temporary Opening** account.
- **CDC cursor** advances only on a clean run, so failures reprocess the same window. The first run looks back 24h. `TaxCode` is excluded from CDC (Term/PaymentMethod/Class are included).
- **There is no overwrite.** A sync never saves over a linked record, and `run_resync` is no exception: it creates, links and flags. QuickBooks values reach a linked record only through *Use QuickBooks values* or *Refresh draft* on the Record Matching page. `Conflict` is the status the sync wrote before v1.800.0; a row that still carries it is compared again on its next sync and becomes `QuickBooks Changed` or `Clean`.
- **Retry on a parked transaction creates, it does not rebuild.** `sync_entity` on a transaction that already has an ERPNext record compares and flags like any other sync. The page offers *Refresh draft* on those rows and *Retry* only where no record exists yet.
- **A mapper fix does not reach existing drafts by itself.** Before v1.800.0 the next Import All or a Run Resync re-saved every draft with the new mapping. Now: Preview Resync, Run Resync (it flags the drafts that differ), then *Refresh drafts* for the ticked rows on the transactions tab.
- **A party's group / territory is ERPNext's; the importer files a new party into NO group and never writes one on update.** QuickBooks has no supplier group, customer group or territory. Until v1.496.0 `_map_supplier` / `_map_customer` invented one with `frappe.db.get_value(doctype, {"is_group": 0}, "name")` -- and a dict-filtered `get_value` on Frappe 16 orders by `creation` **descending**, so "any leaf" meant "the leaf somebody created most recently"; because the update path re-applied every mapped value on each re-sync, every Supplier Group anyone added became the group of all 911 QBO-linked Suppliers on the next scheduled run (five sweeps between 2026-06-18 and 2026-09-16), and 466 Customers landed in "Government" -- invisibly, since `customer_group` is hidden on the Customer form. Now `constants.DEFAULT_PARTY_GROUP` is `None` (a wrong group is worse than no group -- Nik, 2026-09-22; if a named default is ever wanted it goes there as a NAME, resolved with `frappe.db.exists`), `_drop_party_groups_on_update` strips the three fields from every update whatever the record holds, and `ERPNEXT_OWNED_PARTY_FIELDS` are excluded from the `owned_fields` snapshot and from `detect_conflicts`, so re-grouping or clearing a Supplier is never a conflict and is never undone. The swept Suppliers were restored or cleared by `patches/restore_supplier_groups_after_qbo_sweep` and the swept Customers -- group *and* territory -- by `patches/restore_customer_groups_after_qbo_sweep` (engine: `core/party_group_remediation.py`, dry-run by default; `MIGRATION_NOTES.md` §7). Never resolve a default with an unordered `get_value` -- name it.
- **Per-record resilience:** batch ops use `safe_upsert`, so one bad record can't abort a run; inline failure notes are capped at 20 (full tracebacks go to the Frappe Error Log). RQ's job timeout is the one exception it lets through, so a timed-out run fails instead of running on with no limit.
- **No rate-limit/backoff handling:** QBO 429/throttling responses aren't specifically handled — any ≥400 (other than 401) raises `QuickBooksAPIError`.
- **The daily attachment job runs on the `default` queue (300 s RQ timeout), not `long`.** `hooks.py` lists it under `daily`, and v16's `ScheduledJobType.get_queue_name` sends only `*Long`/`Maintenance` frequencies to `long` (1500 s). RQ therefore kills the run after 300 s however many files remain; the attempt marker tolerates that (a young in-flight marker is released, not poisoned) and the next day resumes. Moving it to `daily_long` is the knob if the daily backlog ever outgrows the budget.
- **`QuickBooks Sync Mapping` rows with `qbo_entity_type = Attachable` are attempt markers, not links** (see [Attachment mirroring](#attachment-mirroring-wi-071)). Deleting one re-enables a file the mirror gave up on — which is the documented reset, so do it knowingly, not as clean-up.
- Sandbox vs Production is chosen via `environment`; only the base URL differs (OAuth endpoints are shared).
