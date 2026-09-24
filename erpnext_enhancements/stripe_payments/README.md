# `stripe_payments/` — card and ACH payments

Takes customer payments through Stripe and posts the resulting accounting into ERPNext:
hosted Checkout, a customer portal path, saved payment methods with off-session charging,
declined-card dunning, surcharge-aware card collection, and payout reconciliation into the
general ledger.

Every function is documented inline. This README is the map.

> **Indentation: tabs** throughout this module.

## Two constraints that shape everything here

**No Stripe SDK, on purpose.** The host is a managed server where PyPI packages cannot be
installed. Everything Stripe-facing is hand-rolled on `requests` (a Frappe dependency),
including **webhook signature verification** (the `Stripe-Signature` `t=`/`v1=` scheme),
because there is no SDK to do it. This mirrors the QuickBooks Online module, and the two are
worth reading together — conventions were deliberately kept parallel. Do not "fix" this by
adding `stripe` to `pyproject.toml`; the dependency comment there explains why.

**No card data touches this server.** Hosted Checkout and Stripe's Payment Element handle
the card; this module only ever sees ids, statuses, and amounts.

## Pipeline

```
create_payment ──> Stripe Checkout Session ──> customer pays
                                                    │
                                          Stripe webhook (signed)
                                                    │
                          handle_webhook ─> Stripe Event (name = event id)
                                                    │  enqueued
                                          reconcile.process_event
                                                    │
                                     Payment Entry (submitted)
```

Idempotency is structural rather than defensive: a `Stripe Event` is named by the Stripe
event id, so a redelivery cannot be ingested twice.

## File map

| File | Purpose |
|---|---|
| `api.py` | Public surface — re-exports the whitelisted RPCs from `core/api.py` so callers and the registered webhook URL use the stable short path `erpnext_enhancements.stripe_payments.api.*`. Same trick the QuickBooks module uses |
| `core/client.py` | The Stripe REST client on `requests`. Authenticates with the sandbox-guarded secret key; amounts always in minor units (cents). Also implements webhook signature verification. Higher layers never touch HTTP directly |
| `core/api.py` | Whitelisted RPC entry points: Settings form, dashboard, the Sales Invoice button, the customer portal, and the webhook. Thin wrappers that enforce the permission boundary, then delegate |
| `core/checkout.py` | `create_payment` — the single entry point for both initiation channels. Resolves customer and amount (from a Sales Invoice or ad hoc), records a `Stripe Payment` ledger row, creates the hosted Checkout Session whose metadata carries the reconciliation keys |
| `core/webhooks.py` | `handle_webhook` — verify signature, record the `Stripe Event`, enqueue `reconcile.process_event` so the HTTP response returns fast |
| `core/reconcile.py` | Turns verified events into accounting. `finalize_payment` builds and submits the Payment Entry exactly like the QuickBooks module's `_map_payment_entry` (Receive / Customer / deposit account / invoice allocation), guarded so a redelivered event never double-posts |
| `core/saved_methods.py` | Phase 2: `create_setup_session` saves a card or bank **with consent and no charge**; `charge_saved_method` charges off-session (e.g. a maintenance invoice) by confirming a PaymentIntent immediately |
| `core/dunning.py` | Declined-card recovery. `run_dunning_cycle` is a daily job that enrols failed auto-charges still outstanding and retries them on a schedule |
| `core/payouts.py` | Turns a `payout.paid` webhook into the Journal Entry that moves money out of clearing (see below) |
| `core/card_element.py` | Surcharge-aware card collection on our own page, via ConfirmationToken two-step confirmation (see below) |
| `core/tasks.py` | Hourly safety net: `poll_pending` reconciles payments stuck in Link Sent / Processing by re-reading Stripe; `retry_failed` re-runs events that previously errored. Both no-op when disabled |
| `core/utils.py` | Settings doc loading, encrypted secret reads, currency ↔ minor-unit conversion, the sandbox-guarded client handle |
| `setup.py` | `after_migrate` — idempotently creates the back-reference custom fields (Stripe ids on Customer / Sales Invoice / Payment Entry) and the Stripe and ACH Modes of Payment |

## Access control

Three distinct boundaries, and they are not interchangeable:

- **Desk** — payment creation and the dashboard require an accounting operator
  (`_require_stripe_operator`).
- **Customer portal** — `portal_create_payment` instead checks that the logged-in user owns
  the invoice.
- **Webhook** — the only `allow_guest` endpoint in the module, gated entirely by Stripe
  signature verification.

## ACH is a delayed-notification method

`checkout.session.completed` arrives first with `payment_status != "paid"` — the payment is
marked **Processing**, not paid — and the terminal event follows later. Treating the first
event as success would post a Payment Entry for money that has not settled. `reconcile.py`
handles the two-phase flow explicitly.

## Payout reconciliation

Stripe holds each captured charge in the deposit/clearing account (the customer Payment
Entry debits it), then pays out the accumulated balance net of fees. `payouts.py` posts:

```
Dr  Payout Bank Account     net (what actually lands in the bank)
Dr  Merchant Fees           Stripe's processing fees
    Cr  Stripe Clearing         net + fees
```

The clearing and merchant-fee accounts are set up by **WI-005**.

## Surcharging

Only **credit** cards may be surcharged, and hosted Checkout fixes its line items when the
Session is created — before the payer's card, and therefore its funding type, exists. So the
hosted path can never price a surcharge without guessing, and guessing wrong on a debit card
is a flat card-network violation.

`core/card_element.py` is the way around it: a Payment Element in *deferred intent* mode, then
ConfirmationToken two-step confirmation so the real card's funding type is known before the
amount is fixed. All GA API — no preview version, no third-party surcharge app.

**Surcharging ships off.** Per OD-7, `surcharge_enabled` stays `0` at go-live and is enabled
later only via the compliance checklist in
[`docs/stripe_surcharging_compliance.md`](../../docs/stripe_surcharging_compliance.md).

### The card page (`/pay-card`): Back, Forward, and paying twice

`www/pay-card.html` has two steps, the card and the review (the true total). **The review is
a history entry of its own**, pushed from the Continue tap with no URL (`?invoice=` never
changes), so the phone's Back returns to the card step instead of leaving the page. The entry
names its quote, and Forward shows that review again for as long as the page's memory still
holds that quote — until the card in the Payment Element changes (its `change` event) or a
new Continue starts. A quote is bound to its ConfirmationToken, which only that memory holds,
so `history.state` never supplies one and nothing is restored after a reload. A review entry
whose quote is gone is stepped back off (`history.back()`) rather than re-stamped as the card
step: re-stamping left two card entries, and a Back from the card step that did nothing
visible. The one place that still costs an extra Back is a reload on the review step, where
the entry beneath belongs to the document before the reload. The page's own "Back — use a
different payment method" shows the card step at once (so a Pay tap before the traversal
lands charges nothing) and goes through the same history. Back while a charge is in flight
(the server call, then 3-D Secure) changes nothing until the charge has an answer. **Any
failed charge spends its quote** and returns to the card step with the card still entered:
by then the server has usually created the PaymentIntent, and Pay again would resend the same
row and token — refused as "already Processing", or replayed by the `ee-element-<row>`
idempotency key, which re-stamps the invoice `Processing` around the same dead PaymentIntent.
Continue prices a fresh quote instead, and the POSTs cancel the abandoned attempt first (below).
Success goes to `/stripe-return` with `location.replace`, and a page restored from the
back-forward cache reloads. `scripts/test_web_flow_history.js` drives the page's real script
through all of this (`test_stripe_payments.py` runs it).

**A paid invoice, or one with a payment still settling, is never offered a second card
payment.** Back from `/stripe-return` downloads `/pay-card` again (no-store), and a card
payment finished through 3-D Secure stays `Processing` — outstanding unchanged — until the
webhook posts it, so the page used to show a working form for it. `card_element.invoice_payment_block`
is the invoice-level verdict, read by `www/pay_card.py` (which then renders "paid", "received"
or "being processed", with a link back to `/pay`), by `price_card_payment` before the card is
read, by `confirm_card_payment` where the money moves, and by `checkout.create_payment` — the
Bank button on `/pay` and the desk's "Pay with Stripe" — before anything exists at Stripe. It reads the
ledger as `dunning` and `saved_methods` do (a `Processing` or `Paid` row blocks), with one
difference: a Payment Element attempt (the only kind carrying a ConfirmationToken, and the only
kind whose 3-D Secure runs in our page, where a Back abandons it) is asked of Stripe, and one
whose PaymentIntent is `requires_action` / `requires_payment_method` / `requires_confirmation`
or `canceled` does not block. Without that, an abandoned 3-D Secure — which `poll_pending` never
settles — would block the invoice for good. Before anything new is charged, the POSTs cancel
such a PaymentIntent at Stripe (`POST /payment_intents/:id/cancel` through `client._request`),
fail its row and re-stamp the invoice `Unpaid`; a PaymentIntent that succeeded meanwhile
cannot be canceled, so the refusal keeps blocking and two charges can never both land. A
Stripe lookup that fails blocks too.

Two cases the ledger alone gets wrong. A `Paid` Stripe row on an invoice that still shows a
balance (Accounts canceled its Payment Entry, most often; nothing resets the row) stays
blocked, as dunning and autopay block it, but the page renders "received" and the RPCs say the
payment was received and to contact us — never that the invoice is paid. And
`confirm_card_payment` re-checks the outstanding amount against the quote, refusing when it
has changed at all: an invoice quote is always the whole outstanding (`_resolve_target`), so a
cheque or credit note posted between Continue and Pay would otherwise leave the difference as
an unallocated overpayment. A return (credit note) is "not available" on the page, as before;
its negative outstanding is not "paid".

Also, `confirm_card_payment` now commits a `succeeded` charge as `Processing` before posting its
Payment Entry. If the post failed (no deposit account, a closed period), the row used to be
rolled back to Draft with no PaymentIntent on it — invisible to every guard while the page
offered Pay again. `poll_pending` retries the post from `Processing`.

**The bank path and `/pay` use the same verdict.** Hosted Checkout used to check only the
outstanding amount, so a `/pay` tab loaded before a card payment could still open a bank
Checkout for an invoice whose card charge was settling (3-D Secure done, the webhook not yet
landed), and the payer could complete an ACH debit for an invoice the card had already paid.
`create_payment` now refuses it the way the card endpoints do — Accounts get their own wording
for a received payment, since "contact us" means them — and, like them, cancels an abandoned
card attempt before a new payment starts. `www/pay.py` decides which invoices get Card and Bank
from `invoice_payment_blocks`, the same verdict for a whole list (one ledger query, Stripe asked
only about card attempts, never releasing), instead of the invoice's
`custom_stripe_payment_status` stamp: that stamp hid both buttons for good after an abandoned or
failed 3-D Secure, which nothing un-stamps, and said nothing of a payment already received.
An invoice with a payment settling shows "Processing…", one with a payment received on a
balance still owing shows "Payment received — please contact us", and every other one both
buttons. Every path that starts a payment for an invoice runs the guard exactly once, with
release; each page render once, without; an ad hoc payment never
(`test_every_path_that_starts_a_payment_runs_the_guard_exactly_once`).

**A pending ACH payment is never canceled.** Only a row that carries a ConfirmationToken — a
Payment Element card attempt — is ever looked up at Stripe or canceled. A hosted Checkout, ACH
or off-session payment in `Processing` blocks until it settles on its own; an ACH debit waiting
on microdeposit verification sits in `requires_action` for days, which for a card attempt would
read as abandoned.

Still open, by design of this change: a hosted Checkout that is only `Link Sent` (the payer is on
Stripe's page, or has the link) is not in flight, so a card payment can start beside it — the
rule dunning and autopay use too. Closing that means expiring the Session first. The desk's
`charge_saved_method` (off-session, `saved_methods.py`) does not consult the guard either;
autopay on submit and dunning check the ledger themselves.

## DocTypes

| DocType | Role |
|---|---|
| `Stripe Payments Settings` | Single — keys, master switch, account links, feature gates |
| `Stripe Payment` | The ledger row behind each payment attempt, with its lifecycle status |
| `Stripe Event` | Ingested webhook events; **named by the Stripe event id**, which is what makes redelivery safe |
| `Stripe Autopay Consent` | Recorded consent for saved-method off-session charging |

## Tests

Bench-free pytest suite, in CI:

```bash
python -m pytest erpnext_enhancements/tests/test_stripe_payments.py -q
```

It is a **pytest** suite — if you add to it, or add a sibling, it belongs on a
`python -m pytest` step in `ci.yml`. `python -m unittest` collects pytest-style function
tests silently as nothing.

## Related

- **WI-005** — Stripe Clearing + Merchant Fees accounts and routing
- **WI-039** — production go-live
- **WI-040** — payout ingestion
- **WI-055 / OD-7** — surcharging (Phase 2)
