# CRM Enhancements

Customizes the **Opportunity** doctype (Opportunity→Project conversion + tag sync) and ships the realtime **Sales Pipeline** board. Converting an Opportunity to a Project also triggers a Google Drive folder tree — that machinery now lives in the [Google Drive module](../google_drive/README.md) (`crm_enhancements.api` calls `google_drive.drive_utils`).

## File map

| File | Purpose | Key functions | Wiring |
|---|---|---|---|
| `api.py` | Opportunity→Project conversion + tag sync | `enqueue_project_creation` (whitelisted), `create_project_from_opportunity_background` (provisions the Drive tree via `google_drive.drive_utils`), `sync_opportunity_tags`, `sync_opportunity_tags_for_existing` (whitelisted) | `sync_opportunity_tags` → `Opportunity` `before_save` |
| `doctype/accounts_lead`, `accounts_opportunity`, `accounts_project`, `lead_source`, `opportunity_contributor`, `value_stream`, `value_streams` | CRM child tables / masters ported from DB-only custom DocTypes (v0.7.0) so fresh installs can import the Custom Field fixtures that reference them | stub controllers | synced on migrate |
| `doctype/sales_activity_settings/…py` | Single: global `inactivity_threshold` (days) — fallback reminder window for `script_migrations.customer.customer_inactivity_reminder` (ported v0.8.0) | `SalesActivitySettings` (pass) | synced on migrate |
| `page/sales_pipeline/*` | TV-friendly realtime funnel board (`/app/sales-pipeline`, v1.2.0) | `get_pipeline_data`, `check_permission` (whitelisted); `stamp_stage_change`, `publish_pipeline_update` | hooks → `Opportunity` `before_save` / `on_update`; see below |

Related client-side code lives in `public/js/crm_enhancements/` (`opportunity.js`, `opportunity_list.js`, `opportunity_kanban_totals.js`, `opportunity_migrated_scripts.js`, `fountain_move_request*.js`, `fountain_move_invite.js`) — see the [public README](../public/README.md#crm-enhancements).

## Fountain Move intake (`fountain_move/`, v1.160.0)

Public intake form for the **Cactus & Tropicals** partnership. A customer buys a
fountain at C&T, C&T recommends us to move it, and the customer fills in
[`/fountain-move`](../www/README.md#controller-filenames-hyphens-are-silently-fatal).
The submission lands as a **Fountain Move Request** and converts, in a background
job, into a linked **Customer → Address → Contact → Lead → Opportunity** set.

| File | Purpose |
|---|---|
| `__init__.py` | constants: `INTAKE_FIELD_MAP` (the guest allowlist), `CT_LOCATIONS`, `HONEYPOT_FIELD_NAME`, store-location lookup |
| `intake.py` | the three guest endpoints — `begin_intake`, `upload_intake_photo`, `submit_intake` — plus `gc_orphan_intake_files` |
| `matching.py` | duplicate resolution: which existing party does this belong to? |
| `conversion.py` | the staging-row → five-records engine |
| `photos.py` | File fan-out onto Lead/Customer/Opportunity + Drive mirroring |
| `notify.py` | new / failed / duplicate-review alerts + the daily stuck digest |
| `invites.py` | the desk "Send Intake Link" flow and token attribution |
| `api.py` | desk triage RPC (retry, mark spam, not spam) |

**Why a staging doctype rather than writing the five records inline:** spam never
reaches CRM; a partial failure is resumable rather than duplicating master data
(ERPNext names Customers by `customer_name`, so a rolled-back retry would create
"Jane Doe Residence - 2"); and the original payload survives for audit.

### Ordering constraints in the conversion (all load-bearing)

1. **Customer first** — everything links to it, inserted with
   `flags.ignore_mandatory` because Selling Settings carries no group/territory
   default on this site.
2. **Address before Contact.** Never set `custom_full_address` — the
   `before_save` hook recomputes it, and it is the `title_field`. `country` is
   mandatory with no default.
3. **The Contact's Customer Dynamic Link must be appended BEFORE `insert()`** —
   naming runs before validate, and `custom_full_name_and_role` is built from
   `links[0]`. Use `contacts_ux._insert_customer_link_first`.
4. **Lead after Contact**, carrying `utm_source = "Existing Customer"` *and*
   `customer`, or erpnext's `Lead.before_insert` mints a second, stray Contact
   (`lead.py:103-115`).
5. **Opportunity last, with `opportunity_from = "Customer"` — never `"Lead"`.**
   `api.py:238` maps `party_name` straight into `Project.customer` on the
   Closed-Won hand-off and inserts with `ignore_validate`, which skips
   `validate()` but *not* `_validate_links()`; a Lead id raises inside a
   `try/except log_error` and silently kills the hand-off. Drive provisioning
   likewise only fires for Customer-party opportunities.

### Attribution, and a schema trap

`Lead.source` and `Opportunity.source` **do not exist** — erpnext v15 renamed the
field to `utm_source`, which points at the separate `UTM Source` taxonomy. Three
Property Setters had been enforcing `reqd` on the missing field since, silently
doing nothing; `patches.drop_orphan_source_property_setters` removes them.
Attribution lives in `custom_lead_source` (Link → `Lead Source`) on Customer,
Lead and Opportunity. `utm_source` is spent solely on the stray-Contact
suppression above.

### Before enabling the public form

It is the app's only unauthenticated write path, so the pre-flight is not
optional:

1. `Lead Source: Cactus & Tropicals` and `UTM Source: Existing Customer` exist
   (seeded by patch).
2. Turnstile site key **and** secret set — the Settings controller refuses to
   publish the form without the secret.
3. Maps key (optional) restricted by HTTP referrer. Blank ships manual address
   fields, which is a perfectly good state.
4. `fmr_default_owner` set. Deliberately not guessed by the seed patch — a wrong
   guess routes real customers to the wrong person, so conversion fails loudly
   instead.
5. **Confirm the edge proxy OVERWRITES `X-Forwarded-For` rather than appending.**
   `auth.py:62-70` takes the first entry unconditionally, so an appending proxy
   makes every IP-keyed rate limit spoofable.

## Sales Pipeline page (`/app/sales-pipeline`)

The wall-TV funnel board from the Jun 9 process meeting. Columns mirror the live
`Opportunity.status` options (meta-driven — a stage rename reshapes the board without a
deploy), plus a green **Won — awaiting project** column (Closed Won with empty
`custom_created_project`, the PRO-0204 Step 1→2 gap) and a muted **On Hold** column.
Cards age by `custom_stage_changed_on` (stamped on every status change; backfilled from
`modified` by the `backfill_stage_changed_on` patch) and "light up" amber/red past the
thresholds in **ERPNext Enhancements Settings → Sales Pipeline Dashboard** (defaults
7/14 days; the won column runs a tighter 1/3-day clock to match the unconverted nag).
Refreshes via the `sales_pipeline_updated` realtime event on every Opportunity save,
with a 5-minute poll as kiosk fallback. **TV mode** (`/app/sales-pipeline/tv`, or the
header button) hides desk chrome and scales type — point the Raspberry Pi at the `/tv`
route. Access is page-level (shared portfolio display, like the Project Dashboard): a
`Custom Role` record for page `sales-pipeline` wins if present, else any staff role in
`DEFAULT_ROLES`; data is then fetched permission-free so User Permissions can't
silently empty the board.

## Gotchas

- `sync_opportunity_tags` is one of several `Opportunity` `before_save` handlers; the others are Python ports in [`script_migrations/opportunity.py`](../script_migrations/README.md).
- Converting an Opportunity to a Project provisions a Drive folder tree, but that's **non-fatal** and lives in the [Google Drive module](../google_drive/README.md) — the Project is created even if Drive fails.
