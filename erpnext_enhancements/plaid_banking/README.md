# `plaid_banking/` — the Bank Balances widget, over ERPNext's own Plaid link

Shows current bank balances on the Finance dashboard. That is all it does now. Read-only:
this module never moves money, never writes accounting, and never links a bank.

**Native-first.** ERPNext v16 ships a complete Plaid integration — the `Plaid Settings`
Single (module ERPNext Integrations: client id, secret, environment, `enabled`,
`automatic_sync`), Plaid Link on the Bank form, a per-institution access token on
`Bank.plaid_access_token`, and an hourly transactions sync into submitted `Bank Transaction`
rows. This module is a thin layer over that: it reads the native keys and the native
per-Bank tokens, and each institution is linked **once** — one Plaid Item per bank, shared
by balances (ours) and transactions (erpnext's). The earlier plan to hand-roll
`/transactions/sync` here (WI-056) is dropped.

It is built to read like its siblings — `stripe_payments/` and `quickbooks_online/` share
the same `core/{client,api,utils,constants,tasks}.py` shape.

## Why the rename (and why it must not go back)

This module used to ship its own Single called **`Plaid Settings`** — the same name as
erpnext's. Two DocTypes with one name cannot coexist: `sync_all` imports a DocType JSON
whenever its `migration_hash` differs from the stored one (the `modified` stamp is only
consulted for non-DocType records — `frappe/modules/import_file.py`, version-16), so on
every migrate erpnext's JSON was imported and then ours, in app-install order, and the
later-installed app — this one — won. On prod the native `tabDocType` record was overwritten
with our field list while `tabSingles` kept the native rows. Erpnext's Link code then read
`plaid_env` off a schema that no longer declared it, and our widget read `plaid_enabled` off
a row that never had it. Neither side raised; the widget just said "disabled".

`patches/rename_plaid_settings_doctype.py` (pre_model_sync) renames ours to
**`Plaid Banking Settings`**, points erpnext's own "Plaid Settings" navigation (the
Invoicing workspace card and the Banking sidebar item, which `rename_dynamic_links` had
moved to the new name and which `sync_all` never re-imports) back at the native form, moves
every `tabSingles` row that is not one of our fields back under the native name (frappe's
`DocType.after_rename` moves *every* row — the six native values and the Single's own
`modified` / `owner` history go back; ours is left empty and loads with its declared
defaults), purges any `__Auth` row for our retired access-token field, and `reload_doc()`s
erpnext's JSON so the native record is back before `sync_all` runs. **A `modified` bump can
never "fix" a name clash** — the hash decides and app order breaks the tie. Only distinct
names do.

## File map

| File | Purpose |
|---|---|
| `core/constants.py` | Both settings doctype names, base URLs keyed by the **native** `plaid_env` values, endpoint paths, and the two error-code sets the balance layer branches on |
| `core/utils.py` | Our settings doc; the native settings doc; `get_credentials` / `get_environment` (read from the native Single); `linked_banks()` (the per-Bank tokens, for request bodies only); status persistence |
| `core/client.py` | The REST client on `requests`: `/accounts/balance/get`, `/accounts/get`, `/item/get` |
| `core/balances.py` | Multi-bank balance fetch, the per-bank error policy, and the durable cache |
| `core/link_accounts.py` | The mapping helper: stamp native Plaid account ids onto our existing Bank Account masters; absorb a duplicate the native Link created; prune the GL Accounts a native re-link leaves behind |
| `core/api.py` | The whitelisted endpoints and the two role gates |
| `core/tasks.py` | The hourly scheduler job (throttled, pause-aware) |
| `doctype/plaid_banking_settings/` | The widget switch, throttle and status (**no** credentials, environment or tokens) |
| `doctype/bank_balance_snapshot/` | The durable cache the widget reads |
| `../custom_html_blocks/finance_bank_balances.*` | The dashboard block, grouped by bank |

## DocTypes

| DocType | Owner | Role |
|---|---|---|
| `Plaid Banking Settings` | this app | Single — `plaid_enabled` (show the widget), `company`, `refresh_poll_minutes`, `plaid_status` / `plaid_status_message` / `plaid_last_sync`, `plaid_auth_blocked` |
| `Bank Balance Snapshot` | this app | Single — the normalised balance cache, one entry per bank |
| `Plaid Settings` | **erpnext** | Single — `plaid_client_id`, `plaid_secret`, `plaid_env` (sandbox / development / production), `enabled`, `automatic_sync`, `enable_european_access`. Read here, edited there |
| `Bank` | **erpnext** | `plaid_access_token` — the Item's token, written by native Link, one per institution |
| `Bank Account` | **erpnext** | `integration_id` (the Plaid account id, unique), `mask`, `last_integration_date` (the native sync's start date) |

## Where linking actually happens (v16, verified against `origin/version-16`)

- **`/app/plaid-settings`** (native form). With `enabled` ticked, the form shows
  **Link a new bank account**, **Reset Plaid Link** and **Sync Now**. The first two open Plaid
  Link (`link-initialize.js` from Plaid's CDN, a link token from `PlaidSettings.get_link_token`).
  On success it prompts for a **Company**, then `add_institution` looks the `Bank` up by
  **Plaid's exact institution name** ("KeyBank", "U.S. Bank", "America First Credit Union")
  — updating it with the access token when that name exists, otherwise **inserting a new
  Bank** of that name — and `add_bank_accounts` creates a `Bank Account` per shared account
  named **`"<Plaid account name> - <bank_name>"`** (`integration_id` + `mask`,
  `is_company_account`), **creating a new GL Account** (`"<Plaid name> - <institution>"`
  under the company's Bank group) for any it does not find by that exact name. Our Banks are
  named "US Bank", "Key Bank", "America First", so a plain link puts each token on a *new*
  Bank record the masters do not sit under — see the operating procedure.
- **`/app/bank/<name>`**: once the Bank holds a token, **Refresh Plaid Link** re-authenticates
  that institution (Link update mode) and re-runs `add_bank_accounts`. It resolves the Bank
  from Plaid's institution name in the response, which is why the token must stay on the
  Bank Plaid named. Once our masters hold the ids, each re-run inserts a GL Account per
  shared account *before* the Bank Account insert fails on the unique `integration_id` (the
  failure is swallowed with a msgprint) — one stray Account per account per re-auth;
  `prune_link_created_gl_accounts(bank)` removes them.
- **Hourly** (`erpnext` `hourly_maintenance`): `automatic_synchronization` runs when both
  `enabled` and `automatic_sync` are on — `sync_transactions` per linked Bank Account, deduped
  on `transaction_id`, pending skipped, inserted **and submitted**; the first pull is 12 months
  back unless `last_integration_date` is set.

## The mapping helper — why it exists

Plaid's account names ("Checking", "Business Savings") are not ours ("Key Bank Checking -
Key Bank", GL 13100). Left alone, native Link therefore creates a **second** Bank Account
per account **and a second GL Account**, while the eight masters the ledger posts to stay
unlinked. `core/link_accounts.py` closes that gap:

- `list_plaid_accounts(bank)` — the Item's accounts (`/accounts/get`; ids, names, masks, no balances).
- `map_plaid_account(bank_account, account_id, mask=None, start_date=None, bank=None)` —
  stamps `integration_id` (+ `mask`) onto an **existing** company Bank Account and sets
  `last_integration_date` to `start_date` or yesterday, so the native first pull does not
  drag in a year. Validates: the Bank Account exists, is a company account, and no *other*
  Bank Account already carries that id. The token must be on a Bank — the master's own by
  default, or `bank` when Link named the institution differently ("KeyBank" holds the
  token, the master sits under "Key Bank"): then the master's `bank` is re-pointed to the
  token-holding Bank as part of the mapping. Refused when the master's own Bank holds a
  token too — that is a second Plaid Item, not a naming mismatch.
- `absorb_native_duplicate(duplicate, into)` — moves `integration_id` / `mask` from a
  Link-created row onto our master and deletes the duplicate; when the two sit under
  different Banks the master is re-pointed to the duplicate's (token-holding) Bank, refused
  if the master's own Bank holds a token. `last_integration_date` becomes the duplicate's if
  it has one, else the master's, else yesterday — a Link-created row that has never synced
  carries NULL, and NULL means "twelve months, submitted" to the native sync. Refuses if the
  duplicate has any `Bank Transaction`. Deletes the duplicate's GL Account **only** if its
  `account_name` is the native `"<Plaid name> - <institution>"` pattern, it has no GL Entry,
  and nothing else links to it — otherwise it is left and the return value says why.
- `prune_link_created_gl_accounts(bank)` — after a native **Refresh Plaid Link**: lists the
  Item's accounts (one `/accounts/get`) and deletes each stray `"<Plaid name> - <bank>"` GL
  Account under the same guards as the absorb. Returns what it deleted and what it kept, with
  the reason.

All of them work from `bench execute` (which commits on return) and are whitelisted behind
the connect gate. The **Map Plaid accounts** button on Plaid Banking Settings drives
`map_plaid_account` through a dialog: per linked bank, each Plaid account gets a Select of
that bank's still-unmapped company Bank Accounts plus the company Bank Accounts under Banks
that hold no token (shown with their Bank; picking one moves it under the linked Bank), or
blank to skip.

## Operating procedure, end to end

1. **Keys.** `/app/plaid-settings`: Client ID, Secret, Environment (`production` for real
   data; `sandbox` for `user_good` / `pass_good`; `development` is retired by Plaid and this
   module treats it as sandbox), tick **Enabled**, save. Leave **Automatic Sync** off for now.
2. **Name the Bank records the way Plaid does, then link each bank natively.** Native Link
   stores the token on the `Bank` named exactly as Plaid's institution string, inserting a
   new Bank when that name does not exist. Our three are "US Bank", "Key Bank", "America
   First"; Plaid's are "U.S. Bank", "KeyBank", "America First Credit Union" (check the
   institution name Plaid shows in Link before trusting these). **Preferred:** rename each
   `Bank` to Plaid's exact name first (`Bank` has `allow_rename`; the masters' `bank` links
   follow, their names do not change), so the token lands on the existing record and the
   masters already sit under it. Then Link a new bank account → choose the institution →
   share the accounts → pick the Company. Expect it to create `"<Plaid name> - <Bank>"` Bank
   Accounts and GL Accounts you did not want; that is step 3. Repeat per bank: three Items,
   one token each on three `Bank` records. If you linked without renaming, the token is on a
   new Bank ("KeyBank") and the masters are under the old one ("Key Bank"): step 3 handles
   that by moving each master under the token-holding Bank as it maps it.
3. **Map.** `/app/plaid-banking-settings` → **Map Plaid accounts** → for each Plaid account
   pick the master it is (mask is the tell); masters under an un-tokened Bank are listed
   with their Bank and move under the linked one when picked. If native Link already created
   duplicates, run `absorb_native_duplicate("<Plaid name> - <Bank>", "<master>")` per pair
   instead; it moves the link (and the master, when the Banks differ) and cleans up the
   auto-created GL Account when that is safe. Do the absorbs **before** step 5: the
   duplicate's `last_integration_date` wins when it has one.
4. **Switch the widget on** (`Show the Bank Balances widget`), press **Test Connection**
   (`/item/get` per bank), then **Refresh Balances Now**.
5. **Transactions.** Check `last_integration_date` on each master is the day you want the
   feed to start (map and absorb both default it to yesterday when nothing better is known),
   then tick **Automatic Sync** on the native form. First-run expectations: one
   `Bank Transaction` per posted (not pending) transaction since that date, **submitted** on
   insert, tagged with Plaid categories; the reconciliation is then the WI-043 runbook minus
   its CSV-import step.
6. **When a bank says Reconnect Required** (widget banner, or `Plaid transactions sync error`
   in the Error Log): open that `Bank` record → **Refresh Plaid Link** → re-authenticate. The
   other banks keep refreshing meanwhile; nothing pauses. **Side effect:** the re-auth re-runs
   `add_bank_accounts`, and because the masters (not the native-named rows) hold the ids it
   inserts one unused GL Account per shared account and then msgprints "already exists" —
   the masters keep their ids and nothing else changes. Run
   `prune_link_created_gl_accounts("<Bank>")` afterwards to delete the strays.

## One fetch path

`refresh_balances` is the **only** function that calls `/accounts/balance/get`: one call per
linked bank, both from the scheduler and from "Refresh now". Results are normalised into
`Bank Balance Snapshot` as `[{bank, status, message, accounts: [...]}]`, and **the widget
reads that cache — it never calls Plaid on render**. Adding a second call site is how you get
rate-limited by a dashboard that three people have open.

## Error policy, the pause, and throttling

Per bank, because the keys are shared but the Items are not:

- an **Item-level** non-retryable code (`ITEM_LOGIN_REQUIRED`, revoked token …) marks *that*
  bank `Reconnect Required` and the loop continues — one dead link must not hide the other
  banks' numbers, and it does **not** pause the job;
- a **config-level** code (`INVALID_API_KEYS` …) sets `plaid_auth_blocked` and stops, since
  every remaining call would fail the same way. Only a human lifts it: a successful Test
  Connection or manual Refresh, or toggling the widget switch;
- anything else marks that bank `Error`, stays retryable, and the loop continues.

Settings status is `Connected` when at least one bank answered. The hourly job self-throttles
to `refresh_poll_minutes` (default 240) from the last *successful* sync and skips while
paused. A pass where no bank succeeds does not move the anchor, so it retries hourly — at
most one refused call per dead bank per hour until someone re-links it.

## Permissions

Two trust tiers, enforced at the top of every method. Whitelisted methods are callable
directly over HTTP, so **the RPC gate is the only access boundary**.

- **read** — the widget feed: System Manager, Accounts Manager, Accounts User. Returns
  balances, masks and freshness per bank; never a token, secret or client id.
- **connect** — anything that spends a Plaid call or writes a Bank Account's link (refresh,
  test, map, absorb): System Manager, Accounts Manager.

## Security note: the token is plaintext at rest

Native `Bank.plaid_access_token` is a plain **Data** field, not a Password field: the value
sits unencrypted in `tabBank`, and any role with Bank read can read it through the API even
though the form hides it. Out of the box that is **System Manager only** — erpnext's
`bank.json` grants no other role, this app ships no Custom DocPerm on `Bank`, and the
Accounts roles' read is on `Bank Account`, not `Bank` (verified on prod). Widening Bank
permissions (say, for reconciliation) also widens who can read the token; check here before
doing it. The field is `hidden` +
`read_only` in erpnext's own JSON (verified on prod and in `origin/version-16`);
`fixtures/property_setter.json` pins both properties (`Bank-plaid_access_token-hidden`,
`-read_only`) so an upstream or Customize Form change cannot surface it. This module never
logs it, never returns it from an endpoint, and reads it only into a request body. That is
a native trade-off accepted knowingly, not a fix: the alternative — a second, encrypted
copy under our control — is exactly the two-tokens-per-institution design this rewrite
removed.

## No SDK, again

ERPNext's connector uses `plaid-python` (an erpnext dependency, so it is on the host). This
module still talks to the REST API with `requests`: the widget needs three POSTs with the
keys in the body and gains nothing from a model layer; the SDK's pinned major is erpnext's
business and an upgrade that moves it must not be able to break a dashboard tile; and the
Stripe and QuickBooks siblings are hand-rolled for the same host-can't-pip-install reason
(ADR 0004). One client shape across the three is worth more than a saved hundred lines.

## Tests

`tests/test_plaid_banking.py` — bench-free, pytest-style, its own `frappe` stub, its own CI
step. Covers the native credential/environment read, the per-bank error policy (including
blank keys pausing rather than logging hourly), the snapshot shape, the mapping-helper
refusals and the token-on-a-differently-named-Bank path, the absorb's date fallback and
message-log hygiene, the re-link GL prune, and the exact SQL of the rename patch.
`tests/test_hooks_integrity.py` guards the form script against being listed in
`doctype_js` (Frappe already loads it; listing it evaluates the file twice and a top-level
`const` then breaks the whole form).
