# CRM Enhancements

Customizes the **Opportunity** doctype (Opportunity→Project conversion + tag sync) and ships the realtime **Sales Pipeline** board. Converting an Opportunity to a Project also triggers a Google Drive folder tree — that machinery now lives in the [Google Drive module](../google_drive/README.md) (`crm_enhancements.api` calls `google_drive.drive_utils`).

## File map

| File | Purpose | Key functions | Wiring |
|---|---|---|---|
| `api.py` | Opportunity→Project conversion + tag sync | `enqueue_project_creation` (whitelisted), `create_project_from_opportunity_background` (provisions the Drive tree via `google_drive.drive_utils`), `sync_opportunity_tags`, `sync_opportunity_tags_for_existing` (whitelisted) | `sync_opportunity_tags` → `Opportunity` `before_save` |
| `doctype/accounts_lead`, `accounts_opportunity`, `accounts_project`, `lead_source`, `opportunity_contributor`, `value_stream`, `value_streams` | CRM child tables / masters ported from DB-only custom DocTypes (v0.7.0) so fresh installs can import the Custom Field fixtures that reference them | stub controllers | synced on migrate |
| `doctype/sales_activity_settings/…py` | Single: global `inactivity_threshold` (days) — fallback reminder window for `script_migrations.customer.customer_inactivity_reminder` (ported v0.8.0) | `SalesActivitySettings` (pass) | synced on migrate |
| `pay_period_reports.py` | Semi-monthly (1st–15th, 16th–EOM) delivery of the "Brian's Closed Won" commission report — moves the report's saved date window and emails the closed period on the 1st and the 16th (v1.232.0) | `run_pay_period_cycle`; `pay_period_bounds` / `previous_pay_period` (generic, reusable) | `hooks.py` `scheduler_events.cron` `"0 7 * * *"`; see below |
| `page/sales_pipeline/*` | TV-friendly realtime funnel board (`/app/sales-pipeline`, v1.2.0) | `get_pipeline_data`, `check_permission` (whitelisted); `stamp_stage_change`, `publish_pipeline_update` | hooks → `Opportunity` `before_save` / `on_update`; see below |

Related client-side code lives in `public/js/crm_enhancements/` (`opportunity.js`, `opportunity_list.js`, `opportunity_kanban_totals.js`, `opportunity_migrated_scripts.js`, `fountain_move_request*.js`, `fountain_move_invite.js`) — see the [public README](../public/README.md#crm-enhancements).

## Commission report pay periods (`pay_period_reports.py`, v1.232.0)

Commission runs on semi-monthly pay periods — **1st–15th** and **16th–end of
month** — and the "Brian Commissions Report" Auto Email Report has described
that rule in its own body text since it was written. Nothing implemented it:
`Brian's Closed Won` is a Report Builder report whose saved filters held a
hand-retyped `custom_date_closed_won > <date>`, and the email went weekly on
Mondays.

**Why this is a job and not configuration.** All three native routes are closed:

- `Auto Email Report`'s dynamic date filters (`from_date_field` / `to_date_field`
  / `dynamic_date_period`) are applied **only when the report type is not Report
  Builder** — `get_report_content` guards them with
  `if self.report_type != "Report Builder"`. They are inert here.
- `Auto Email Report.filters` are **appended** to the report's saved filters, not
  substituted (`Report.run_standard_report`), so the email can never override or
  widen a date already baked into the report.
- `frequency` offers only Daily / Weekdays / Weekly / Monthly. Nothing in that
  vocabulary means "the 1st and the 16th".

So the window lives in the report's saved `json`, which is also what makes the
desk view right: opening the report shows **the period currently in progress**.

**The borrow/restore dance.** The email needs the *finished* period while the
desk view wants the *running* one, and a Report Builder report holds one filter
set. The send briefly points the report at the closed period, renders through
the Auto Email Report doc, and `run_pay_period_cycle` re-applies the running
window immediately afterwards — on every path, including a send that raised.
The cron is **daily** rather than `1,16` for the same reason: a missed tick or a
dead send self-heals next morning instead of stranding the desk report for up to
sixteen days.

**The Auto Email Report doc is deliberately left disabled.** That is what stops
Frappe's `send_weekly` from also firing it; the doc still owns the recipient
list (editable in the desk, no code change), the description and the rendering,
and this module drives it. Do not delete it.

Two behaviours worth knowing: the window is `between`, **inclusive at both ends**
— the `>` it replaced silently dropped deals closed *on* a boundary day, which
is how a $500 opportunity dated 2026-07-16 ended up in neither period — and only
the date rows are rewritten, so an owner/status filter changed in the desk
survives the next tick. Re-runs are guarded by a `DefaultValue` key holding the
last period actually emailed, so re-running the job by hand cannot produce a
second statement for a period already paid.

Ad hoc run: `bench --site <site> execute
erpnext_enhancements.crm_enhancements.pay_period_reports.run_pay_period_cycle`.

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


## Lead attribution (WP-1, v1.241.0)

`attribution.py` + `web_lead.py`. The problem it addresses, measured on 2026-08-04: of 815
Opportunities, **814 had no `utm_source` and 809 had no `custom_lead_source`**. The
"45% missing" figure from the marketing review was measured against `tabOpportunity.source`
— a column that still physically exists but has had **no DocField behind it since erpnext
v15 renamed the field to `utm_source`**. Nothing reads or writes it. Live coverage was not
55%; it was approximately zero.

### The schema decision

erpnext v16 ships `utm_source` / `utm_medium` / `utm_campaign` / `utm_content` on Lead and
Opportunity. We deliberately do **not** write real campaign data into them:

1. **`utm_source` is load-bearing elsewhere.** `Lead.before_insert` mints a stray second
   Contact unless `utm_source == "Existing Customer"` and `customer` is set — the
   suppression the fountain-move conversion depends on (see above). Writing a campaign name
   there would silently start duplicating Contacts.
2. **`utm_medium` and `utm_campaign` are Links.** Raw capture must accept whatever string is
   in the URL; a Link either rejects it or spawns junk taxonomy rows.

So raw values live in our own `custom_utm_*` **Data** fields in a collapsed "Attribution"
section on Lead, Opportunity **and** Customer, and `custom_lead_source` (Link → `Lead
Source`) stays the single human-facing channel. The accepted cost is that we own the mapping
from a raw source/medium pair to a Lead Source value — `attribution.derive_lead_source`,
deliberately small, and consulted only to fill a **blank**.

Fields we ship that erpnext has no equivalent for: `custom_utm_term`, `custom_gclid`,
`custom_landing_page`, `custom_first_referrer`, `custom_attribution_captured_on`.

### First touch wins

`attribution._fill_blanks` is the **only** function that writes attribution onto a document,
so there is exactly one place to audit the rule. Propagation runs on `validate`:
Lead → Opportunity, Lead → Customer, and Opportunity → Customer (the last via `db.set_value`
in `on_update`, because re-saving the Customer would re-enter Drive provisioning and contact
sync for a metadata-only copy).

### The source gate is a hook, not `reqd`

`reqd = 1` would break every API-created record and retroactively invalidate the backlog.
`attribution.enforce_source` runs on `validate`, applies to **new records only**, exempts
bulk contexts (import/migrate/patch/install/test), and is gated by
`require_lead_source_on_lead` / `require_lead_source_on_opportunity` in ERPNext Enhancements
Settings so it can be switched off from the UI in seconds. Historical blanks were bucketed to
`Unknown (pre-Aug 2026)` by `patches.backfill_unknown_lead_source` — a value that stays
visibly a gap rather than being laundered into a real channel. The **Attribution Gaps**
report separates that historical debt from live process failures.

### The website ingress

`web_lead.submit_web_lead` is a machine-to-machine POST endpoint gated by a Bearer shared
secret (`web_lead_shared_secret`, fails closed when unset), rate limited, with a field
allowlist rather than a payload splat.

**The public site is WordPress on WP Engine behind Cloudflare** — a different host from
ERPNext. The capture script that reads `utm_*`/`gclid`/referrer and forwards them lives on
the WordPress side and is **not in this repo**; only the ERPNext half is. The full payload
contract is in [`docs/attribution-runbook.md`](../../docs/attribution-runbook.md).


## `Value Stream` vs `Value Streams` — investigated, not changed

Two doctypes with near-identical names sit in this module and it reads as cruft. It is not.
Both are in daily use, and the names are simply **inverted from frappe convention**:

| DocType | What it is | Rows (2026-08-04) |
|---|---|---|
| `Value Streams` (**plural**) | The **master list** — Design, Build, Service, Events, Delivery, Products | 6 |
| `Value Stream` (**singular**) | The **child table** behind the `custom_value_stream` Table MultiSelect on Customer, Opportunity and Project | 1,460 |

Normally the plural would be the child. Here it is the other way round, so every query
against them reads backwards. That is the trap; write it down rather than "fixing" it.

**Two genuinely dead fields were found:**

1. `Value Stream` (child) declares **two** Link fields — `value_stream` *and* `value_streams`
   — **both pointing at `Value Streams`**. A Table MultiSelect binds one `link_fieldname`, so
   the spare is inert. Almost certainly a copy-paste artifact.
2. `Value Streams` (master) declares `value_stream_link`, a **Link → `Value Stream`** — i.e.
   the master pointing at the child table. You cannot link to a child doctype in frappe; the
   field does nothing.

**Recommendation (not executed — needs sign-off).** Keep both doctypes and both names.
Renaming a child doctype with 1,460 rows referenced by three Table MultiSelect fields is a
`bench migrate`-breaking operation for a cosmetic gain. Instead: confirm which Link field the
MultiSelects actually bind to, then remove the unused one and `value_stream_link` — which is
**two steps**, a fixture removal *and* a `frappe.delete_doc` patch, because removing a record
from `fixtures/*.json` only stops managing it (see
[`fixtures/README.md`](../fixtures/README.md)).


## Account data hygiene (WP-5, v1.242.0)

`data_quality.py` + the **Account Data Quality** report. The gaps it exists to
close, measured 2026-08-04 across 1,621 Customers: **1,292 no industry, 1,160 no
customer group, 1,118 no value stream.**

### `customer_type` is the commercial/residential axis, and it was half-migrated

This site extended erpnext's stock `customer_type` (Company / Individual /
Partnership) with **Commercial** and **Residential**. Useful — the industry rule
has a real field to key on. But 364 rows were left on the legacy `Company` value
and are **100% missing both industry and customer group**, where Commercial is
70%/67%. That is the signature of an un-migrated import, not a classification.
`patches.retype_legacy_company_customers` moves them, scoped to that full
signature so a genuine `Company` carrying real data is untouched, and logs every
affected id to the Error Log first because the two values are indistinguishable
afterwards.

### Industry is required by a hook, NOT by `reqd`

Asked for as "make industry required", and it is — but the declarative form takes
the QuickBooks sync down. `reqd` and `mandatory_depends_on` are evaluated by
`_validate_mandatory` on **every** save by **every** caller, and
`quickbooks_online/core/mapping.py` inserts Customers with `ignore_permissions=True`
but **not** `ignore_mandatory`. QBO payloads carry no industry, so a declarative
rule would fail validation on every synced customer and park it in manual review —
on the system that is the book of record until the January go-live.

`data_quality.enforce_industry` runs on `validate` and can tell the cases apart:
it honours `doc.flags.ignore_mandatory` (which `api/telephony.py` and
`fountain_move/conversion.py` already set), exempts background jobs (`frappe.request`
is None — a scheduled QBO poll is not a person who can be asked for a value),
exempts bulk contexts, and skips residential accounts entirely.

| Setting | Default | Effect |
|---|---|---|
| `require_industry_on_commercial` | **on** | Block a NEW commercial account with no industry |
| `require_industry_on_edit` | off | Also block edits — turn on **after** the 732-row backlog is cleared |

### Assisted, never automatic

`bulk_assign` / `assign_value_streams` apply a value **a human picked** to rows **a
human selected**, skip rows that already carry a value rather than overwriting,
and are role-gated to System Manager / Sales Manager. Nothing infers an industry
from a company name — a wrong industry is invisible once written and every
downstream report silently inherits it.

The keep/merge/retire proposal for the 89 Industry Type values (47 in use) is
[`docs/industry-type-proposal.md`](../../docs/industry-type-proposal.md). It is a
proposal; nothing has been executed.

## Pipeline reconciliation (WP-7, v1.242.0)

`reconciliation.py` + the **Closed Won Reconciliation** report. 200 Opportunities
are Closed Won with no linked Project; 199 of them are `opportunity_from =
"Customer"` so `party_name` is a real Customer id, and 138 belong to a customer who
already owns at least one Project.

Candidates are ranked within a customer by date proximity (50), value proximity
(30) and whether the project is already spoken for (20). Customer identity is a
**precondition, not a score component** — a project for a different customer is not
a weaker match, it is not a match.

**Nothing links automatically, and that is the whole design.** A wrong link
corrupts revenue attribution, which is the exact defect WP-1 was commissioned to
fix, so an auto-linker would manufacture the problem the programme is trying to
remove. `score_candidates` is read-only and every candidate carries its `reasons`
so a reviewer can disagree on sight; `link_opportunity_to_project` writes exactly
one link, refuses to overwrite an existing one, and guards both directions so two
Projects cannot claim one Opportunity.

**Named Account Targets** builds an outreach list from the same data, sorted by
staleness rather than alphabetically. It has **no scale filter** — the
event-planner scale taxonomy is undecided, and a filter built against a guessed
field would quietly match nothing.
