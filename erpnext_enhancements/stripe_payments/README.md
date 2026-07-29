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
