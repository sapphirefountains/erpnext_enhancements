# `plaid_banking/` — bank balances via Plaid

Pulls current bank balances through Plaid and caches them for the Bank Balances dashboard
widget. Read-only: this module never moves money and never writes accounting.

It is deliberately built to read like its siblings — `stripe_payments/` and
`quickbooks_online/` share the same `core/{client,api,utils,constants,tasks}.py` shape, so
learning one teaches you all three.

## No SDK, again

The host is a managed server where PyPI packages can't be installed, so the Plaid client is
hand-rolled on `requests`. One Plaid-specific quirk: it authenticates by placing `client_id`
and `secret` in the **JSON body** of every POST, not in a header.

## File map

| File | Purpose |
|---|---|
| `core/client.py` | The REST client on `requests` |
| `core/constants.py` | Per-environment base URLs, endpoint paths, and the error-code sets the balance layer branches on |
| `core/connect.py` | The Plaid Link flow: create a Link token, then exchange the public token for a long-lived access token and persist it. A stable, non-PII `client_user_id` identifies this deployment's linker. Not whitelisted — gating lives in `api.py` |
| `core/api.py` | The whitelisted endpoints and the permission gate |
| `core/balances.py` | Balance fetch and the durable cache |
| `core/tasks.py` | The hourly scheduler job |
| `core/utils.py` | Settings doc, encrypted secret read/write, status persistence |

## One fetch path

`refresh_balances` is the **only** function that calls `/accounts/balance/get`. Both the
scheduler and the manual "Refresh now" route through it. Results are normalised into the
durable `Bank Balance Snapshot` single doctype, and **the widget reads that cache — it never
calls Plaid on render**.

Adding a second call site is how you get rate-limited by a dashboard that three people have
open.

## Throttling and the auth-blocked flag

The job is registered on `scheduler_events["hourly"]` but self-throttles to
`refresh_poll_minutes` (default 240 — balances change slowly), and skips entirely while
`plaid_auth_blocked` is set.

That flag is the important part: a dead connection or bad keys must not produce a retry
storm. It mirrors the QuickBooks `cdc_poll` behaviour. When you see balances going stale,
check the flag before assuming the scheduler is broken — being blocked is the designed
response to an auth failure, and it clears on reconnect.

## Permissions

Two trust tiers, enforced at the top of every method. Whitelisted methods are callable
directly over HTTP, so **the RPC gate is the only access boundary** — there is no framework
permission check behind it.

- **read** — the widget feed: System Manager, Accounts Manager, and the other roles listed in
  `api.py`.
- **write/connect** — the Link flow and settings, restricted further.

Secrets (`plaid_secret`, `plaid_access_token`) are stored encrypted and never logged.

## DocTypes

| DocType | Role |
|---|---|
| `Plaid Settings` | Single — environment, encrypted credentials, poll interval, `plaid_auth_blocked` |
| `Bank Balance Snapshot` | Single — the durable normalised balance cache the widget reads |
