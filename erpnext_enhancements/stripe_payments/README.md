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
| `core/card_element.py` | Surcharge-aware card collection on our own page, via ConfirmationToken two-step confirmation; and the rules every payment path shares so one invoice is never paid twice — the invoice lock, the Sales Invoice row lock, the guard (amended-from invoices included), written-ahead charges, unknown outcomes, expiring emailed links, and the Sales Invoice `before_cancel` hook (see below) |
| `core/tasks.py` | Hourly safety net: `poll_pending` reconciles payments stuck in Link Sent / Processing by re-reading Stripe — including charges Stripe never answered, attempts that can no longer charge, rows Stripe answers 404 for, and links whose invoice was canceled or settled another way; `retry_failed` re-runs events that previously errored. Both no-op when disabled |
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

**No webhook moves a row backwards.** Stripe does not guarantee event order, and
`retry_failed` re-runs an errored or never-run event up to an hour later (or after a deploy's
FLUSHDB), so a handler can meet a row that has already moved on. Every guard in *One invoice,
one payment* reads the ledger's status, so a status that runs ahead of the money is how an
invoice gets paid twice:

- **A declined attempt inside a Checkout link is not the link's outcome.** Stripe sends
  `payment_intent.payment_failed` for every declined card inside a Checkout Session, and the
  Session stays open — payable with another card or the bank — for the rest of its 24 hours.
  That event used to mark the emailed link's row `Failed`, which hid a live link from every
  guard: a card payment on `/pay-card` went through beside it, and the customer could then pay
  the link too; `/stripe-return` told a customer who had retried successfully inside Checkout
  that nothing was charged; and the same event for an earlier declined card, processed after
  `checkout.session.completed` had made an ACH debit `Processing`, failed a debit still settling
  and unblocked its invoice. A hosted row's outcome now comes only from its Session's events
  (`completed`, `async_payment_succeeded` / `async_payment_failed`, `expired`): the payment
  event only records the decline on an open link, for Accounts, and leaves a `Processing` row
  alone. For the card page and off-session charges it is still the outcome — neither ever
  confirms the same PaymentIntent again.
- **A `Paid` or `Refunded` row is never re-opened.** `checkout.session.completed` for an ACH
  debit, retried after `async_payment_succeeded` had posted it, set it back to `Processing`.
  And a late or retried success event for a refunded row reached `finalize_payment` — neither
  Checkout handler looked for a settled row, and finalize returned early only when a Payment
  Entry existed — so a row refunded before it was ever posted (posting had failed: no deposit
  account, a closed period) got a Payment Entry for money given back and was marked `Paid`
  again. Both Checkout handlers now skip a settled row, as `payment_intent.succeeded` already
  did, and `finalize_payment` itself, under its lock, refuses a `Refunded` row, Payment Entry or
  not; one with none alerts Accounts, since the ledger never booked the receipt that refund
  gives back.
- **A debit Stripe has reported failed stays failed**: a late `checkout.session.completed` for
  it would otherwise hold the invoice "being processed" for good (nothing settles a hosted row
  whose debit is dead). The Stripe Event log decides, not the row's status: a row marked
  `Failed` by the old declined-attempt rule, whose debit is in fact on its way, is marked
  `Processing` by that event, so it blocks.

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

### The card page (`/pay-card`): Back and Forward

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
(the server call, then 3-D Secure) changes nothing until the charge has an answer.

**What the page does with the answer depends on whether it *is* one.** A definite failure —
the server refused, or Stripe declined; to `frappe.call` a 417 whose body names its
`exc_type` — charged nothing, and spends the quote: back to the card step with the card still
entered, and Continue prices a new one (the server never sends a row twice). **A refusal for
the invoice rather than the card** — `PaymentBlocked`: another payment settling or waiting on a
bank, one already received, the invoice paid or credited meanwhile, the quote already sent — is
different: a card form beside "already being processed" contradicts itself, so the page loads
`/pay-card` again and the render says why (at Continue too). An emailed link Stripe could not
be asked to close (`LinkStillOpen`), and an earlier card attempt that cannot charge whose cancel
Stripe would not confirm just now (`AttemptUnreleased`, the `Unreleased` verdict), are ordinary
refusals: the form stays and the modal says why. There is nothing to render for either — a
render never releases, so it reads that dead attempt as not blocking and used to bring the card
form back with no word of why, every tap looping until Stripe answered the cancel. Both are
`PaymentBlocked` subclasses, which dunning still reschedules on. Anything else is
**not an answer**: a dropped connection, a 5xx, a proxy timeout, or 3-D Secure ending in any
error that is not about the card or the request (`card_error`, `validation_error`,
`invalid_request_error` are the bank's or Stripe's definite no; `api_connection_error`,
`api_error`, a `rate_limit_error`, a type the page does not know say nothing about whether the
bank approved it). The card may have been charged, so the page never shows a card form then: it
holds Pay and Back, shows "Checking your payment…", and loads `/pay-card` again, which renders
what the server has on record for the invoice — "being processed", "waiting for your bank's
approval", or "we could not yet confirm whether a payment went through" — and the form only
when nothing reached it. A server answer of `Processing` — including `outcome_unknown`, when the server
itself could not learn the outcome — goes to `/stripe-return`, as a success does, with
`location.replace`. **`/stripe-return` says what the row says now**, not what the address
says: received; being processed; "we could not confirm your payment yet — please do not pay
again for now: if it did not go through, we will email you, or, if you are back at your invoices
first, you will find this one ready to pay again" for an outcome nobody knows; "did not go
through, nothing was charged" once it is `Failed` or `Expired`. "Nobody knows" is
`card_element.held_unknown` while the row is `Processing`: a charge whose PaymentIntent is still
unknown, and one `poll_pending` has found but not seen complete (`NOTE_OUTCOME_FOUND` — 3-D
Secure the payer never saw, say), which the page's own narrower test used to turn into "Thank
you! Your payment is being processed" half an hour before it was released as not charged. The
promise is kept whichever way that outcome resolves — see **5** under *One invoice, one
payment*: the payer is emailed, except when their own new payment is what released it, and they
can only have started that from the invoice shown ready to pay again. A page restored from the
back-forward cache reloads. `scripts/test_web_flow_history.js` drives the page's real script
through all of this (`test_stripe_payments.py` runs it).

## One invoice, one payment

The owner's rule: one invoice is never paid twice, through any path. Every way to take money
for an invoice runs the same checks, all in `core/card_element.py`:

| Path | Entry point | Starts |
|---|---|---|
| Card page, quote | `price_card_payment` (portal) | nothing — a Draft row |
| Card page, charge | `confirm_card_payment` (portal) | a PaymentIntent, confirmed |
| Bank button on `/pay`, desk "Pay with Stripe" | `checkout.create_payment` | a Checkout Session |
| Desk "Charge Saved Method" **with an invoice chosen**, autopay on submit, the autopay sweep, dunning | `saved_methods.charge_saved_method` | a PaymentIntent, off-session |
| Desk "Charge Saved Method" with no invoice (ad hoc) | `saved_methods.charge_saved_method` | a PaymentIntent allocated to **no** invoice — no invoice guard can cover it, so it is refused while the customer has any Stripe payment `Processing` or link `Link Sent`, and the prompt asks for confirmation |
| Desk ad hoc Checkout link for an amount, no invoice (`api.create_adhoc_payment`) | `checkout.create_payment` | a Checkout Session allocated to **no** invoice — refused by the same rule as the ad hoc charge, while the customer has any Stripe payment `Processing` or link `Link Sent` |
| Canceling a Sales Invoice | `card_element.before_invoice_cancel` (`before_cancel` doc_event) | nothing — refused while a payment is in flight; open links expired |

Every rule below reads an invoice **and every invoice it was amended from**
(`_invoice_family`): cancel-and-amend gives the copy a new name, and a payment still settling
(or received) on the original is a payment for the same bill. A `Paid` row on the original
blocks the copy as "received — please contact us": Accounts allocate that money (an ad hoc
desk link can collect a genuine difference), never a second charge.

**1. The invoice lock.** Each of those holds `invoice_lock(sales_invoice)` — the `filelock`
pattern `reconcile.finalize_payment` already uses — from its guard until the commit that makes
the new attempt visible to the next guard (`Processing` for a charge, `Link Sent` for a
Session). Two tabs holding quotes, or two portal contacts of one customer, used to pass the
guard together, because a row became `Processing` only after Stripe answered. Acquiring the
lock **commits**: under REPEATABLE READ a request that read anything before it waited would
otherwise keep reading the ledger as it stood before the previous holder committed — and pass
the very guard it waited on. A `SELECT … FOR UPDATE` would not do: the first commit on these
paths releases it. A second request that cannot get the lock within 15 s is refused
(`PaymentBlocked`) as "a payment for this invoice is being started right now" — never "it will
show as paid", since the one holding it may yet be declined. The lock is not re-entrant, so only those four functions take it,
never their callers (dunning calls `charge_saved_method`, which locks).

**1a. The Sales Invoice row lock.** Each of those paths also takes `SELECT … FOR UPDATE` on the
Sales Invoice row as its **last read before committing its attempt in flight**
(`_recheck_invoice(…, for_update=True)`), with no commit in between: the card charge after its
guard and link-closing, the off-session charge after the saved method's lookup, hosted Checkout
before its ledger row is inserted (held across the Session call, to the `Link Sent` commit — so
a cancel never waits on an uncommitted insert while that insert's transaction waits on the
cancel). A cancel holds the same row lock from `check_if_latest` to its commit, so the two
serialize at the invoice row, in the same order: a payment that waits on a cancel then reads
the invoice canceled and is refused with nothing charged, and a cancel that waits on a payment
finds its row (it reads the ledger with a locking read — see **Canceling an invoice**). After
Stripe answers, each path writes the invoice stamp before the ledger row, the order a cancel
takes its locks in. The file lock cannot do this job: a cancel may not commit halfway through
its own transaction.

**2. The guard.** `invoice_payment_block` refuses while any Stripe payment for the invoice is
`Paid` or `Processing`, as dunning and autopay always did, with one difference: a Payment
Element attempt (the only kind carrying a ConfirmationToken, and the only kind whose 3-D Secure
runs in our page, where a Back abandons it) is asked of Stripe, and stops blocking once it
provably cannot charge — its PaymentIntent is `requires_payment_method`,
`requires_confirmation` or `canceled`, or has sat in `requires_action` untouched for **30
minutes** (the owner's decision: a payer approving in their bank's app is never cut short).
Meanwhile the invoice cannot be paid again, and the customer is told the payment is waiting
for their bank's approval: `/pay-card` says it "is waiting for your bank's approval. If you did
not finish approving it, you can pay again in about N minutes", and `/pay` shows "Waiting for
bank approval…" in place of the buttons; the desk's invoice stamp reads Processing. (Not
"being processed", which the pages keep for a payment known to be settling: an abandoned
attempt never settles.) Before anything new starts, the POSTs cancel
such a PaymentIntent (`POST /payment_intents/:id/cancel` through `client._request`), fail its
row and re-stamp the invoice `Unpaid`. A cancel that raises is settled by reading the
PaymentIntent back: `canceled` releases (another request got there first); anything else keeps
blocking — as `Processing` when it reads back `succeeded` or `processing` (the payer finished
3-D Secure a moment ago — a succeeded PaymentIntent cannot be canceled, so two charges can
never both land), and otherwise as `Unreleased`: the cancel timed out and the read-back failed,
or still shows a state that cannot charge, so it will never clear and nothing is promised
about it — "could not be released just now; try again in a few minutes". A card attempt with no PaymentIntent on record
(its charge got no answer), and a Stripe lookup that fails, block too. The guard is refused as
`PaymentBlocked` (`AttemptUnreleased`, a subclass, for `Unreleased`), which dunning reschedules
on instead of counting a declined card.

The verdict says **why** it blocks, because the customer is told: `Paid` ("received — please
contact us"), `Processing` (settling — the only one promised to "show as paid once it clears"),
`Awaiting` (3-D Secure inside its window, which may never be finished: "waiting for your bank's
approval — if you did not finish approving it, you can pay again in about N minutes", with the
release time on the verdict), `Unconfirmed` (a charge Stripe never answered, or a lookup that
failed: "we could not yet confirm whether it went through — please do not pay again"),
`Unreleased` (a release above that Stripe would not confirm: "could not be released just now —
please try again in a few minutes"; the cancel hook says the same, never "wait for it to
settle") and `Verifying` (below). All six block; `/pay` shows "Payment received", "Waiting for
bank approval…" or "Processing…". A card-page attempt reaches `Unreleased` only in a POST that
releases, or the cancel hook — a page render never releases — so that refusal is
`AttemptUnreleased` and the card page keeps its form beside it.

Two off-session rows are held `Processing` without settling, and used to read `Processing` —
"It will show as paid once it clears" to the customer, "Wait for it to settle" to Accounts
canceling the invoice. The guard now reads the note each carries. An off-session **card**
challenge whose cancel could not be proven (`NOTE_CANCEL_UNPROVEN`: nobody can finish it, and
`poll_pending` cancels it) is `Unreleased`, which every read sees — `/pay-card` renders it
("could not be released just now … try again in a few minutes"). An off-session **bank** debit
waiting on microdeposit verification (`NOTE_ACH_VERIFYING`) is `Verifying`: "waiting for your
bank account to be verified — it goes ahead once the account is verified" to the customer, and
to Accounts "waiting on the customer to verify their bank account", never canceled
automatically — cancel it in Stripe if it should not go ahead. When several rows hold one
invoice, the verdict is the one that says most: a payment settling first, then `Verifying`,
then `Unconfirmed`, and "try again shortly" only when nothing else holds it.

**Stripe's 404 is an answer.** A PaymentIntent or Checkout Session Stripe does not have for the
configured key — made in the other mode (Test/Live) or another account, say after WI-039
switches the site to Live — was read as "no answer": the link "could not be closed just now"
and the card attempt was "being processed", on every attempt for good, and `poll_pending` failed
on the row every hour. Now `client.is_missing` marks such a row `Expired` (never `Failed`: it is
not a decline), Accounts are alerted (it may still be payable in another live account), and the
new payment goes on.

**3. Written ahead.** A charge's row is committed `Processing` *before* Stripe is called
(`confirm_card_payment` updates its quote; `charge_saved_method` inserts its row that way).
From then on a timeout, a 5xx, or a worker killed mid-request by a deploy restart leaves a row
every guard reads as in flight. Before, a card row stayed Draft (invisible to every guard) and
an off-session insert was not even committed: the charge could go through with no row at all
for the webhook to find, and the autopay sweep would charge again.

**4. Only a definite answer is a decline.** `client.StripeError` now carries the HTTP
`status_code` (none for a transport error) and Stripe's own `stripe_message`.
`create_intent_settled` treats **400, 401, 402, 403 and 404** as proof nothing was charged:
the row fails, and the payer is told so — in Stripe's words for a card error ("Your card was
declined."), never the raw response. Anything else — no response, a 5xx (Stripe caches it
under the idempotency key, so it replays rather than resolves), a 409 (the same key still
executing), a 429, a 2xx that will not parse — is an **unknown outcome**. It is retried once
with the **same** idempotency key (`ee-element-<row>` / `ee-offsession-<row>`), which Stripe
answers with the first request's stored result if it ran, and runs once if it never arrived.
After an unknown first attempt only a 402 on the retry is trusted: any other refusal could be
about the first attempt having run. Still unknown: the row stays `Processing` with a note, the
customer is sent to "being processed", autopay raises no "declined" alert, dunning waits. This
is what made the previous round's "any failure spends the quote" dangerous: a read timeout
after Stripe had charged left the row `Failed`, invisible to the guard, and Continue charged a
second time under a new key.

**5. `poll_pending` settles what the guard keeps blocking**, so nothing is "being processed"
for good. A row held `Processing` with no PaymentIntent is looked for among the Stripe
Customer's PaymentIntents — the strongly consistent list (`GET /payment_intents?customer=…`),
not search, which lags — by its name in `metadata.stripe_payment`, which both charge paths
set. Found: recorded and settled like any other. Provably absent a quarter of an hour on (the
whole list read, from a day before the row was created, since the row's stamp is site-local
and Stripe's UTC; or a Stripe Customer that does not exist for this key): nothing reached
Stripe, so the row is marked **`Expired`, never `Failed`** — dunning enrols Failed autopay rows
as declined cards and emails the customer so, and this one never reached a card — and the
invoice is payable again (autopay's sweep charges again). Accounts are alerted, and a card-page
payer, who was told "we will email you if it did not go through", is emailed that it did not
go through and nothing was charged. Not provable (more than five pages, or no Stripe Customer
on the row): left blocking. **That email is sent whichever way such a charge turns out not to
have charged**, not only when no PaymentIntent exists: found declined (released at once), found
in 3-D Secure the payer never saw because the answer carrying it was lost (released once its 30
minutes are up — by `poll_pending`, or by the next payment someone starts for the invoice), or
reported declined by the `payment_intent.payment_failed` webhook. A found row carries a marker
(`NOTE_OUTCOME_FOUND`, `card_element.held_unknown`) so whichever path releases it knows; Accounts
are alerted each time, and the payer is emailed unless it is their own new payment that released
it: they are paying again as it lands, and a "your payment did not go through" email for the same
invoice and amount could be taken for the new payment (the owner confirmed this; `/stripe-return`
words its promise for it — see the card page section). When it is canceling the invoice that
releases it, the notice says so — the payer is told the invoice was canceled and no payment is
due, Accounts that it was released as the invoice was canceled — never "you can pay it from your
invoices" or "the invoice can be paid again". The email quotes the total the payer approved, the
invoice amount plus any surcharge.
It also cancels and fails attempts that can no longer charge, under the invoice lock: a card
declined after 3-D Secure, card-page 3-D Secure untouched for 30 minutes, and an off-session
card challenge (nobody is present to finish it). It expires an emailed link whose invoice was
canceled, amended or settled another way — a cheque, a credit note, the QuickBooks sync — so it
no longer matches what is owed (`expire_stale_link`; a completed one is left for the webhook and
Accounts are told the money needs a refund or reallocating). And it settles 404s (above).

**6. Emailed Checkout links are expired first** (the owner's decision). A `Link Sent` Session
stays payable for 24 hours from the customer's inbox, so a card charge, a desk charge, or a
second link started beside it could be followed by the customer completing the link. Before
any of those, `_close_open_checkouts` expires each open Session for the invoice (and for the
invoice it was amended from) (`POST /checkout/sessions/:id/expire`,
`client.expire_checkout_session`) and marks its row `Expired`. Stripe refuses to expire a
completed Session — paid, or an ACH debit submitted, the webhook not yet landed — and then the
new payment is refused, because the invoice is being paid, and the row is marked `Processing`
as the webhook would. Each write is **conditional on the row still being `Link Sent`**, read
under its row lock, so a `checkout.session.completed` webhook that marked it Paid between the
read-back and the write is never overwritten back to Processing. When Stripe answers neither
way, the new payment is refused too (`LinkStillOpen`); nothing was charged. A quote moves no
money, so it leaves the link open. **Autopay and dunning never expire a link**: a dunning retry
re-charges a card that already declined — often the very reason Accounts emailed the link — so
expiring it first left the invoice with no way to be paid when the retry declined again. They
are refused while a link is open (`PaymentBlocked`; dunning reschedules), as
`sweep_missed_autopay` already treated an open link as the invoice being handled. The desk's
link-sending refuses a link whose invoice has since changed (`api.send_payment_link`).

**7. After the charge.** A `succeeded` charge is posted (`reconcile.finalize_payment`) after
the lock is released. If that fails — no deposit account, a closed period, the finalize lock
held by the webhook — the half-made Payment Entry is rolled back to the committed `Processing`
row (which carries its PaymentIntent), the error is logged, and the customer is answered
"Processing" — never "failed", because their card *was* charged. The webhook and
`poll_pending` post it later.

**8. What the invoice looks like now.** `confirm_card_payment` re-reads the invoice where the
money moves, in one read: canceled (ERPNext leaves a canceled invoice's `outstanding_amount`
as it was, so the amount alone would still charge it), a draft, or a return — "This invoice
has changed" (neutral on purpose); a changed balance — "Continue again to see the new total"
(an invoice quote is the whole outstanding, so a cheque posted between Continue and Pay would
otherwise become an overpayment). **"Paid" is said only when ERPNext's status is Paid**: a
zero balance from a credit note ("Credit Note Issued") gets "There is nothing left to pay",
on the page and from the charge. A `Paid` Stripe row on an invoice that still shows a balance
(Accounts canceled its Payment Entry, most often) blocks, but is called "received — please
contact us" (Accounts get their own wording on the desk), never paid.

**9. Page renders never wait long on Stripe.** `/pay` and `/pay-card` read the verdict without
releasing anything, with **4 s** per lookup (`READ_TIMEOUT`) instead of the client's 30. A
lookup that does not answer reads as `Unconfirmed` (blocked — the safe answer) and logs nothing,
and after one fails a list asks Stripe nothing more (each would be another timeout); a slow
Stripe used to hold a web worker 30 s per invoice. The POSTs use 10 s (`CONTROL_TIMEOUT`) for
their lookups, cancels and expiries, so the lock is never held for a full client timeout per
call; the charge itself keeps the full 30.

**`/pay` offers what the endpoints accept.** `www/pay.py` decides which invoices get Card and
Bank from `invoice_payment_blocks` (one ledger query for the list), not the invoice's
`custom_stripe_payment_status` stamp, which hid both buttons for good after an abandoned 3-D
Secure. An invoice with a payment settling shows "Processing…", one with a payment received
on a balance still owing "Payment received — please contact us". Every path that starts a
payment runs the guard exactly once, with release; each render once, without; an ad hoc
payment never (`test_every_path_that_starts_a_payment_runs_the_guard_exactly_once`).

**A pending ACH payment is never canceled.** Only a Payment Element attempt is ever looked up
and released by the guard; `poll_pending` never touches a hosted Checkout row, and never
releases an off-session bank debit in `requires_action`. An ACH debit waiting on microdeposit
verification sits in `requires_action` for days, which for a card attempt would read as
abandoned. `charge_saved_method` used to mark such an off-session debit `Failed` and leave its
PaymentIntent live — invisible to the guard, while dunning charged again, so two debits went
through once the customer verified. Now a bank debit in `requires_action` stays `Processing`
(it blocks, like a hosted ACH debit) and is never canceled, and the guard words it `Verifying`;
an off-session **card** in `requires_action` (a challenge nobody is present to finish) is
canceled at Stripe at once and failed only once that is proven (`cancel_attempt`), else held
`Processing` for `poll_pending`, and the guard words it `Unreleased` (see **2**).

**Dunning and autopay.** Dunning's "a charge is still settling" check used a raw count of
`Processing` rows, which also counted a portal card attempt whose 3-D Secure the customer
abandoned, so the case was pushed back two days at a time; it now reads
`invoice_payment_block`, and `charge_saved_method` releases such an attempt before charging.
Autopay on submit skips an invoice with a `Processing`, `Paid` or `Link Sent` row on it or on
the invoice it was amended from, and `charge_saved_method` runs the full guard either way.

## Canceling an invoice

Frappe refuses a cancel only for **submitted** linked documents, and a `Stripe Payment` is
never submitted. So Accounts could cancel an invoice while an ACH debit for it settled for
days, while a charged card's Payment Entry could not post, or while an emailed link was open —
and the amended copy had a new name that no rule keyed on. Autopay charged the copy on submit;
the original payment then failed to post against a canceled invoice every hour; the customer
had paid twice. `before_invoice_cancel` (Sales Invoice `before_cancel`, in `hooks.py`):

- **refuses** the cancel while a `Processing` Stripe payment for the invoice remains after the
  guard's own release (a dead card attempt is canceled at Stripe; 3-D Secure inside its window
  is not), saying which payment and what to do: wait for it to settle, or refund it — or, for
  a dead card attempt whose cancel Stripe would not confirm just now (`Unreleased`, including an
  off-session card challenge), try again in a few minutes, since that one will never settle; or,
  for a bank debit waiting on the customer's verification (`Verifying`), that it goes ahead once
  they verify, and to cancel it in Stripe if it should not;
- **expires** every `Link Sent` Checkout Session for it first; a completed one refuses the
  cancel, one Stripe cannot be asked about refuses it too, one Stripe does not have is marked
  Expired and Accounts are told;
- leaves a `Paid` row alone: ERPNext's own linked-Payment-Entry check governs that, and the
  amended copy's guard reads the original's rows anyway.

It runs **inside the cancel's transaction and never commits** — ERPNext's own `before_cancel`
has written by then (the Timesheet unlinking), and a commit here would make those writes durable
even if the cancel then failed — and it takes no invoice lock for the same reason. It reads the
ledger with a **locking read** (`frappe.db.get_values(…, for_update=True)`, on the
`sales_invoice` index added for it): the cancel's transaction began long before, and a plain
read would see the ledger as it stood then. Stripe-side actions taken here survive a cancel that
fails later; the rows then read `Processing` / `Link Sent` again until `poll_pending` or the
webhook brings them up to date, which is harmless. A no-op while the integration is off or not
yet installed. The amended-from walk in every guard is the defence in depth for whatever got past
it (a cancel with Stripe off, one with `ignore_validate`).

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
