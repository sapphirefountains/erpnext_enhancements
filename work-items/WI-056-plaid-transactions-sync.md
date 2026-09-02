# WI-056: Plaid bank feeds — native transaction sync + the balances widget on one link per bank
**Phase:** 2 (pulled forward: the engineering shrank to a collision fix + configuration)   **Type:** CONFIG + APP_CODE (small)   **Size:** M
**Blocked by:** WI-042 (done); Plaid **production** approval (external, the long pole); WI-043 stays the fallback whenever a link pauses   **Blocks:** nothing

> **Rewritten 2026-09-02.** The original item proposed hand-rolling `/transactions/sync` in
> `plaid_banking/` and asserted "this build ships no native Plaid connector". That was wrong:
> **ERPNext v16 ships a native Plaid integration**, installed and wired on this site. Building
> our own would reimplement a native feature, which this plan's native-first rule calls a
> defect. What actually stood between us and bank feeds was a DocType-name collision (below).

## Why
Manual CSV import (WI-043) works but is operator toil and lags days. Plaid gives (a) posted
transactions per account, landing as native **Bank Transaction** rows that the Bank
Reconciliation Tool matches against Payment Entries / Journal Entries — "match them to the
banks we use" is that tool working against the 8 masters from WI-042; and (b) on-demand
balances for the Bank Balances dashboard widget. Transactions are never real-time (posted
items arrive ~T+1; pending ones are skipped by design); balances are real-time per call, so
the widget caches them and offers *Refresh now*.

## Native-first check — corrected
`erpnext/erpnext_integrations/doctype/plaid_settings/` (verified in `origin/version-16`):
- Single **`Plaid Settings`** — `enabled`, `automatic_sync`, `plaid_client_id`, `plaid_secret`
  (Password), `plaid_env` (sandbox/development/production), `enable_european_access`.
- `plaid_connector.py` on `plaid-python~=7.2.1` — an erpnext dependency, so it is on the host
  without a pip install (the "no SDK" rule is about *our* modules; erpnext's own dependency is
  erpnext's business).
- **Link** UI on the `Bank` form (`bank.js` loads Plaid Link) and on the native settings form:
  `add_institution` stores the access token on **`Bank.plaid_access_token`** (a plain Data
  field), then `add_bank_accounts` creates/updates `Bank Account` rows named
  `<Plaid account name> - <Bank>` with `integration_id` + `mask` — and **creates a new GL
  Account for any name it does not find**.
- `automatic_synchronization` (scheduler, `hourly_maintenance`) → `sync_transactions` per
  linked account → submitted Bank Transactions, deduped on `transaction_id`, pending skipped,
  first pull = **12 months** unless `Bank Account.last_integration_date` is set.

Verdict: native for transactions; the custom module keeps only the balances widget, as a thin
layer over the native settings and the native per-Bank tokens, so each institution is linked
**once** (one Plaid Item per bank shared by balances and transactions — one consent, one bill).

## The collision (prod, 2026-09-02)
Our `plaid_banking` module also defined a DocType named **`Plaid Settings`**. `sync_all`
imports a DocType JSON whenever its `migration_hash` differs from the stored one (the
`modified` stamp only gates non-DocType records — `frappe/modules/import_file.py`, v16), so
on every migrate erpnext's JSON was imported and then ours, in app-install order, and the
later-installed app — this one — won: `tabDocType` "Plaid Settings" belongs to module *Plaid
Banking* today, while the Single's stored rows in `tabSingles` are still the native fields
(`enabled=0`, `plaid_env=sandbox`, no keys). Bumping either JSON's `modified` would change
nothing; only distinct names fix it. Native code reads `plaid_env`, which our schema
lacks → Link/sync would crash the moment `enabled` was switched on; our module reads
`plaid_enabled`, which never got a row → the widget shows "disabled". Nothing is linked: all 8
Bank Accounts have `integration_id` NULL, no Bank holds a token.

## Preconditions
1. **Plaid production access** — apply in the Plaid Dashboard *now*; approval takes days and is
   the long pole. Checklist: company profile + use case ("reconcile our own business bank
   accounts in our ERP"); **Transactions** product (grants Balance); country US; Link
   customisation (client name "Sapphire Fountains ERP"); **OAuth registration** for the
   production app — Key Bank, US Bank and America First route through Plaid's OAuth flows,
   which need production status and a registered redirect URI (`https://erp.sapphirefountains.com/app/bank`
   is the natural one; record what is registered); pricing is per linked account per month
   (8 accounts — modest); production keys land in the native `Plaid Settings` only.
2. The collision fix deployed (release below).
3. WI-043 runbook remains the fallback: a link that pauses (`ITEM_LOGIN_REQUIRED` and kin)
   is re-linked natively on the Bank form; until then that account is reconciled from CSV.

## Scope
**APP_CODE — the collision fix (one release):**
- Rename our Single to **`Plaid Banking Settings`** (pre-model-sync patch modelled on
  `rename_poseidon_settings_doctype`: rename, move the native field rows back under
  `Plaid Settings`, delete any stale `plaid_access_token` `__Auth` row, `reload_doc` the native
  DocType from erpnext) and keep on it only: widget on/off, poll throttle, status, pause.
  Credentials, environment and links come from the native `Plaid Settings` / `Bank`.
- Balances widget iterates every `Bank` with a `plaid_access_token`, grouped by bank, with
  per-bank *Reconnect Required* isolation (one dead link does not blank the others).
- **Mapping helper** so native Link never strands the 8 masters: `map_plaid_account`
  stamps `integration_id`/`mask`/`last_integration_date` onto an *existing* Bank Account;
  `absorb_native_duplicate` folds a Bank Account that native Link auto-created into our
  master and removes the auto-created GL Account only when it is unused; a "Map Plaid
  accounts" dialog on our settings form drives it.
- Property Setters pinning `Bank.plaid_access_token` hidden + read-only (erpnext's own JSON
  already sets both; the pin stops Customize Form or an upstream change surfacing it). The
  value is plaintext at rest in erpnext core — accepted knowingly: out of the box only
  System Manager can read `Bank` (the Accounts roles read `Bank Account`, not `Bank`), and
  widening Bank permissions widens token exposure; we never log or return it.
- Bench-free tests for the module, the patch branches and the helper; own CI step.

**CONFIG — after production approval (no code):**
1. Native `Plaid Settings`: production keys, `plaid_env = production`, `enabled = 1`
   (leave `automatic_sync` off until step 4).
2. **Rename each `Bank` to Plaid's exact institution name first** ("KeyBank", "U.S. Bank",
   "America First Credit Union" — confirm in Link; `Bank` allows rename and the masters'
   links follow), so the token lands on the existing record. Then link each bank on the
   native `Plaid Settings` form → **Link a new bank account** (the Bank form only offers
   **Refresh Plaid Link** once a token exists). One Item per institution. Expect native Link
   to create `<Plaid name> - <Bank>` Bank Accounts and GL Accounts you did not want.
3. Run **Map Plaid accounts** on Plaid Banking Settings: each Plaid account → its existing
   master, `last_integration_date` = the go-live date (bounds the first pull; the ledger
   before it is the posted QBO history). Absorb any duplicate native Link created
   (`absorb_native_duplicate`); after any later re-link, `prune_link_created_gl_accounts`.
4. `automatic_sync = 1`. Watch the first hourly run: Bank Transactions appear on the right
   masters; no Error Log "Plaid transactions sync error".
5. Reconcile per the WI-043 runbook minus §3–§4 (the CSV import); `Plaid Banking Settings.plaid_enabled = 1`
   for the widget.

## Acceptance criteria
- Migrate: `SELECT module FROM tabDocType WHERE name='Plaid Settings'` = `ERPNext Integrations`;
  `SELECT COUNT(*) FROM tabDocType WHERE name='Plaid Banking Settings'` = 1; `tabSingles`
  rows for `enabled`/`plaid_env`/`automatic_sync` sit under `Plaid Settings`; the native
  settings form opens and saves.
- Linking: `SELECT name FROM tabBank WHERE IFNULL(plaid_access_token,'')<>''` = the 3 banks;
  `SELECT COUNT(*) FROM \`tabBank Account\` WHERE is_company_account=1 AND IFNULL(integration_id,'')<>''`
  = 8; **no** Bank Account or GL Account created by the link survives unabsorbed.
- Feeds: `SELECT COUNT(*) FROM \`tabBank Transaction\`` grows without operator action after
  a known movement; `SELECT transaction_id, COUNT(*) ... GROUP BY transaction_id HAVING COUNT(*)>1`
  is empty; the widget shows all 8 accounts grouped by bank with a fresh timestamp.
- One month reconciled with no CSV import (WI-043 §9 criteria 2–4 met from Plaid rows).

## Rollback
Native `Plaid Settings.enabled = 0` (stops Link and sync); imported Bank Transactions remain
valid; WI-043 CSV path resumes. The DocType rename is not rolled back — it is the correct
state regardless.

## Explicitly NOT in this work item
Auto-reconciliation beyond the native tool; credit-card categorisation; any transaction sync
code of our own (deleted from scope on purpose); the Plaid production application itself
(finance/ops file it; engineering supplies the checklist above).
