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
| `core/link_accounts.py` | The mapping helper: put each Plaid account on the company account it really is — stamp its id on a master, fold in the duplicate a first native Link creates, or give a credit card a Bank Account on its own GL Account — and set the feed's start date; prune stray Link-created GL Accounts |
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

## Where linking actually happens (verified against the `v16.60.0` tag prod runs)

- **`/desk/plaid-settings`** (native form). With `enabled` ticked, the form shows
  **Link a new bank account**, **Reset Plaid Link** and **Sync Now**. The first two open Plaid
  Link (`link-initialize.js` from Plaid's CDN, a link token from `PlaidSettings.get_link_token`).
  On success it prompts for a **Company**, then `add_institution` looks the `Bank` up by
  **Plaid's exact institution name** ("KeyBank", "U.S. Bank", "America First Credit Union")
  — updating it with the access token when that name exists, otherwise **inserting a new
  Bank** of that name — and `add_bank_accounts` handles each shared account.
- **A first link always creates duplicates.** `add_bank_accounts` reuses a Bank Account only
  when one already carries that Plaid `integration_id`, or has the exact native name
  **`"<Plaid account name> - <bank_name>"`**. On a first link neither exists, so every shared
  account gets a new Bank Account of that name **and a new GL Account**
  (`"<Plaid name> - <institution>"`, type Bank, under the company's Bank group, 1200 here),
  credit cards included. Our Banks are named "US Bank", "Key Bank", "America First", so a
  plain link also puts each token on a *new* Bank record the masters do not sit under. The
  operating procedure below folds all of that back.
- **One token per Bank.** The token lives on the `Bank` named after the institution, so
  linking the same institution a second time overwrites the first Item's token. Link each
  institution **once**, sharing every account (checking, savings *and* that bank's card) in
  that one Link session. A card held under a separate login at the same bank cannot be linked
  natively alongside the first.
- **History.** The native link token asks for no particular history, so Plaid's default of 90
  days applies. A feed start date older than that gets whatever Plaid has.
- **`/desk/bank/<name>`**: once the Bank holds a token, **Refresh Plaid Link** re-authenticates
  that institution (Link update mode) and re-runs `add_bank_accounts`. It resolves the Bank
  from Plaid's institution name in the response, which is why the token must stay on the
  Bank Plaid named. On 16.60 the re-run finds our master by `integration_id` and **updates it
  in place**: `bank`, type, subtype and mask, and it overwrites `account_name` with Plaid's
  name ("Checking"). The master's own name and its GL Account do not change, so this is
  cosmetic. No stray GL Account is created any more; 16.50 created one per account per
  re-auth, which is what `prune_link_created_gl_accounts` was written for.
- **Hourly** (`erpnext` `hourly_maintenance`): `automatic_synchronization` runs when both
  `enabled` and `automatic_sync` are on — `sync_transactions` per linked Bank Account, deduped
  on `transaction_id`, pending skipped, inserted **and submitted**; the first pull is 12 months
  back unless `last_integration_date` is set, and each run then moves that date to the newest
  transaction it imported. The job runs every hour whether or not Plaid is enabled (it returns
  at once when not); 177 clean runs in the week before 2026-11-24.

## The mapping helper — why it exists

Plaid's account names ("Checking", "Business Savings") are not ours ("Key Bank Checking -
Key Bank", GL 13100). Left alone, native Link therefore leaves a **second** Bank Account
per account **and a second GL Account**, while the eight masters the ledger posts to stay
unlinked. `core/link_accounts.py` closes that gap. The dialog's one call is
`apply_plaid_mappings(rows, start_date)`, which runs `apply_plaid_mapping` per row:

- **Target.** Each row names a company Bank Account (a master) *or*, for a credit card, the
  card's GL Account (`card_gl_account`). Exactly one.
- **Who holds the Plaid id decides the step.** Nobody: `map_plaid_account` stamps it on the
  master. The master already: only the mask and start date are refreshed. A row native Link
  created (`is_link_created`): `absorb_native_duplicate` folds it into the master. Another
  master: refused, because moving a link between two real accounts is a person's decision.
- **`is_link_created`** looks at the GL Account, not the Bank Account's name. A master is
  named `"<account_name> - <bank>"` too, so the name proves nothing. A Link-created row's GL
  Account is named `"<its account_name> - <its bank>"`, and a master's carries our own name.
  That stays true after a re-link has overwritten the master's `account_name`.
- **Feed start.** `start_date` becomes `last_integration_date` on every account mapped, so the
  native first pull starts there rather than twelve months back. A future date is refused
  everywhere: the sync would ask Plaid for a start after its end and log an error every hour.
- **Per row, on its own.** Each row runs in a savepoint with messages muted. A refused row is
  rolled back and reported, and the rows around it commit.

The pieces, each also callable from `bench execute`:

- `list_plaid_accounts(bank)` — the Item's accounts (`/accounts/get`; ids, names, masks, no balances).
- `map_plaid_account(bank_account, account_id, mask=None, start_date=None, bank=None)` —
  stamps `integration_id` (+ `mask`) onto an **existing** company Bank Account and sets
  `last_integration_date` to `start_date` or yesterday. Validates: the Bank Account exists, is
  a company account, and no *other* Bank Account already carries that id. The token must be
  on a Bank — the master's own by default, or `bank` when Link named the institution
  differently ("KeyBank" holds the token, the master sits under "Key Bank"): then the
  master's `bank` is re-pointed to the token-holding Bank as part of the mapping. Refused when
  the master's own Bank holds a token too — that is a second Plaid Item, not a naming mismatch.
- `absorb_native_duplicate(duplicate, into, start_date=None)` — moves `integration_id` /
  `mask` from a Link-created row onto our master and deletes the duplicate; when the two sit
  under different Banks the master is re-pointed to the duplicate's (token-holding) Bank,
  refused if the master's own Bank holds a token. `last_integration_date` becomes
  `start_date` when given, else the duplicate's, else the master's, else yesterday — a
  Link-created row that has never synced carries NULL, and NULL means "twelve months,
  submitted" to the native sync. Refuses if the duplicate has any `Bank Transaction`. Deletes
  the duplicate's GL Account **only** if its `account_name` is the native
  `"<Plaid name> - <institution>"` pattern, it has no GL Entry, and nothing else links to it
  — otherwise it is left and the return value says why.
- `prune_link_created_gl_accounts(bank)` — deletes stray `"<Plaid name> - <bank>"` GL
  Accounts under the same guards as the absorb. A 16.50 re-link left one per account; 16.60
  leaves none, so expect it to find nothing. An account Plaid adds to an Item later still takes
  the create path, and this cleans that up after its absorb.

The **Map Plaid accounts** button on Plaid Banking Settings drives all of it through one
dialog: a **Feed starts on** date (default the first of the month), then per linked bank each
Plaid account with a Select of that bank's still-unmapped masters plus the masters under
Banks that hold no token (picking one moves it under the linked Bank). A credit account also
gets a Link to a Liability GL Account, pre-filled when exactly one ledger's name carries the
card's mask. Accounts a master already holds are shown as mapped, with no control.
**Map selected** first lists every change it will make, per account (the card ledger's type,
the card's new Bank Account, each Link-created duplicate deleted, the start date), and writes
nothing until that list is confirmed.

## Credit cards

Cards have no Bank Account masters, and the obvious fix does not work: ERPNext's
`Bank Account.validate_account` accepts only a GL Account that is a ledger of
`account_type = "Bank"`, while the QuickBooks sync leaves card ledgers **untyped** on purpose
(`quickbooks_online/core/mapping.py` `_account_type`: "Payable" would make ERPNext demand a
Party on every card line, and an untyped liability books freely). "Bank" demands nothing of
a journal line, is legal on a Liability root (13202 America First Auto Loan has carried it
since WI-042), and ERPNext's own matcher expects it: its comment in
`get_je_matching_query` says one bank can hold asset and liability accounts and judges debit
or credit from the transaction, not the account type.

So mapping a card to its GL Account (`_ensure_card_master`):

1. checks the ledger is an enabled, non-group **Liability** account whose type is blank or
   Bank (anything else is refused rather than overwritten);
2. reuses a Bank Account already on that ledger if there is one (ERPNext allows only one per
   GL Account), which makes a re-run harmless;
3. otherwise sets the ledger's `account_type` to Bank through a normal save, so ERPNext's
   Account checks run, and inserts the card's Bank Account under the token-holding Bank,
   named after the ledger, with **Is Credit Card** ticked (16.60; skipped on a site without the
   field), copying Plaid's type and subtype from the duplicate;
4. folds the Link-created duplicate into it, which also deletes the Asset GL Account native
   Link made for the card under 1200, when unused.

The QuickBooks sync never saves a record it has already linked (v1.800.0), so it will not put
the type back. In the feed, a card charge arrives as a **withdrawal** and a payment or refund
as a **deposit**, which is what the matcher looks for against a QuickBooks Purchase (credit to
the card) and a Credit Card Payment (debit to the card).

## QuickBooks overlap — why the feed cannot double post

- Plaid creates **Bank Transactions**, which write no GL Entry. The QuickBooks sync creates
  the Journal Entries and Payment Entries, as drafts. Reconciling links a Bank Transaction to
  one of those vouchers and stamps its clearance date; it posts nothing.
- ERPNext offers only **submitted** vouchers for matching (`docstatus = 1`, no clearance date
  yet). Until the QuickBooks drafts are posted, the feed sits Unreconciled. That is expected,
  and QuickBooks stays the reconciliation of record until then.
- The one way to double post is **Create Journal Entry / Payment Entry** on a Bank
  Transaction (the Reconciliation Tool, or the `/banking` page in 16.60) for something
  QuickBooks also imports. While QuickBooks is the book of record: **match only, never
  create**. Bank Transaction Rules only tag a suggested rule; they never create a voucher.
- Known limit: a card payment from checking is one Journal Entry but two Bank Transactions,
  one on each feed. ERPNext clears a voucher once (`clearance_date` is on the voucher), so the
  tool matches only one side.

## What Plaid bills for

Transactions is a monthly subscription charge (Plaid's `transactions-active` usage metric).
Read the rate, and whether it counts Items or accounts, on the Plaid Dashboard's billing page.
Each linked institution is one Item; the WI-056 plan links 5 Items holding 11 accounts. The
Bank Balances widget adds a Balance charge
for every `/accounts/balance/get`, which is one call per linked bank per refresh: the hourly job
self-throttles to `refresh_poll_minutes` (240 by default, so up to six refreshes a day), plus
every *Refresh now*. `/accounts/get` and `/item/get` (Map and Test Connection) are not Balance
calls. Rates are on the Plaid Dashboard's billing page; Plaid's Developer Tools connector
reports usage counts only.

## Operating procedure, end to end

1. **Keys.** `/desk/plaid-settings`: Client ID, Secret, Environment (`production` for real
   data; `sandbox` for `user_good` / `pass_good`; `development` is retired by Plaid and this
   module treats it as sandbox), tick **Enabled**, save. Leave **Automatic Sync** off until
   step 4 is done: a Link-created duplicate syncing first would pull twelve months onto
   itself, and the absorb then refuses it for having Bank Transactions.
2. **Name the Bank records the way Plaid does, then link each institution once.** Native Link
   stores the token on the `Bank` named exactly as Plaid's institution string, inserting a
   new Bank when that name does not exist. Our three are "US Bank", "Key Bank", "America
   First"; Plaid's are "U.S. Bank", "KeyBank", "America First Credit Union" (check the
   institution name Plaid shows in Link before trusting these). **Preferred:** rename each
   `Bank` to Plaid's exact name first (`Bank` has `allow_rename`; the masters' `bank` links
   follow, their names do not change), so the token lands on the existing record. Card-only
   issuers (Capital One, American Express) need no Bank beforehand; Link creates one. Then
   **Link a new bank account** → choose the institution → share **every** account at it,
   cards included → pick the Company. One Link per institution (see "One token per Bank").
   Expect a duplicate Bank Account and GL Account per account; step 3 removes them.
3. **Map.** `/desk/plaid-banking-settings` → **Map Plaid accounts**. Set **Feed starts on**
   (the cutover date), pick each bank account's master (the mask is the tell), and for each
   card pick its GL Account instead. **Map selected** reports per account what it did: mapped,
   duplicate folded in, card Bank Account created, the card ledger's type set to Bank, the
   duplicate's GL Account deleted or kept and why.
4. **Check** `last_integration_date` on each mapped Bank Account is the cutover date, and that
   no `"<Plaid name> - <Bank>"` Bank Account is left.
5. **Transactions.** Tick **Automatic Sync** on the native form. First-run expectations: one
   `Bank Transaction` per posted (not pending) transaction since that date, **submitted** on
   insert, tagged with Plaid categories, and no "Plaid transactions sync error" or "Plaid
   transaction ... could not be imported" in the Error Log. Reconcile per the WI-043 runbook
   minus its CSV-import step, and the QuickBooks section above.
6. **The widget** (optional): `Show the Bank Balances widget`, **Test Connection** (`/item/get`
   per bank), then **Refresh Balances Now**. Card balances show as owed, with the credit left.
   Every refresh is a billed Balance call per bank.
7. **When a bank says Reconnect Required** (widget banner, or "Plaid Link Refresh Required" /
   "Plaid transactions sync error" in the Error Log): open that `Bank` record → **Refresh Plaid
   Link** → re-authenticate. The other banks keep syncing meanwhile. On 16.60 the re-auth
   updates the masters in place and renames their `account_name` to Plaid's; nothing else
   changes and nothing needs pruning.

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
message-log hygiene, the re-link GL prune, the 16.60 path (telling a Link-created duplicate
from a master, folding it in, a card's ledger typed Bank and its Bank Account created, the
refusals, a future feed start refused before any write, one refused row not undoing the
batch), and the exact SQL of the rename patch.
`tests/test_hooks_integrity.py` guards the form script against being listed in
`doctype_js` (Frappe already loads it; listing it evaluates the file twice and a top-level
`const` then breaks the whole form).
