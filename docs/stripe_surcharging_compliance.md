# Card Surcharging & Fee Pass-Through — Compliance Reference

**Status:** reference for the Stripe Payments surcharge feature (configurable, default **OFF**).
**Last researched:** 2026-07-27 (Stripe API surface re-checked; debit/prepaid/ACH exemption
now enforced in code — see [How our integration implements this](#how-our-integration-implements-this)).
**This is not legal advice.** Surcharging rules change and vary by state and card network.
Before enabling card surcharging in production, confirm with legal counsel **and** notify
Stripe (your acquirer) and the card networks. Sapphire is in **Bountiful, Utah** — Utah
currently permits surcharging, but you sell/operate beyond Utah, so the multi-state rules
below matter.

---

## TL;DR decision for our build

- We surcharge **credit cards only**. The feature ships **OFF** until the steps in
  [Go-live checklist](#go-live-checklist) are done.
- **Cards:** surcharge **credit only**, **≤ 3% and ≤ cost of acceptance**, never on
  **debit/prepaid**, never in a **banned state**, always **disclosed before payment**
  and **itemized on the receipt**.
- **ACH: no fee, ever.** There is no ACH fee setting. A bank-debit convenience fee
  would be lawful (ACH is outside card-network rules), but we decided not to charge
  one, and the capability was removed rather than left switched off — a dormant
  setting is a compliance accident waiting to happen.
- **Debit/prepaid exemption is structural, not configuration.** `_compute_surcharge`
  returns a fee for exactly one input combination: `pm_type == "card"` **and**
  `funding == "credit"`. `debit`, `prepaid`, `unknown` and "not yet known" all return
  zero. No settings value can override this.

### Where a surcharge can and cannot be priced

The funding type only exists once the payer's card does, which decides what each
channel can do:

| Path | Funding known before pricing? | Surcharge |
| --- | --- | --- |
| Hosted Checkout (`checkout.create_payment`) — bank/ACH, emailed links | **No** — line items are fixed when the Session is created | **Always zero** |
| Off-session / autopay / dunning (`saved_methods.charge_saved_method`) | **Yes** — the saved PaymentMethod is read first | Credit only |
| Portal card page (`card_element`, `/pay-card`) | **Yes** — `payment_method_preview.card.funding`, server-side, before the amount is fixed | Credit only |

Hosted Checkout previously priced a fee from the *chosen method* ("card"), which
covers credit, debit and prepaid alike — so a debit customer was shown, and charged,
a fee they may never be charged. That is now impossible: the hosted path passes
`funding=None` through the same gate and always gets zero.

### The portal card page (`/pay-card`)

One-off card payments are collected on our own page so the funding type is knowable
before pricing. The flow, in `stripe_payments/core/card_element.py`:

1. A Payment Element mounts in **deferred intent** mode — no PaymentIntent, so no
   amount is committed.
2. The payer submits; Stripe.js returns a **ConfirmationToken**.
3. `price_card_payment` retrieves it server-side, reads
   `payment_method_preview.card.funding`, prices through the shared gate, and records
   the quote **bound to that token**.
4. The page shows the true total — the fee line appears only if a fee genuinely
   applies, and a debit payer is told plainly that none does — with a **Back** button
   to use a different method. That is the network-required disclosure and opt-out.
5. `confirm_card_payment` charges the quoted total, accepting **only** the token the
   quote was priced against.

The binding in step 5 is the security crux: without it a client could take a quote on
one card and pay with another, which is precisely the debit-surcharge case we are
preventing. One token, one price, one charge.

**Bank/ACH stays on hosted Checkout** — see [ACH fees](#ach-bank-debit-fees).
**Emailed payment links also stay on hosted Checkout**, so they remain fee-free: the
Element page requires a logged-in portal session, and a tokenized guest route is not
built yet.

---

## Hard rules (US, 2026)

| Rule | Detail |
| --- | --- |
| **Max cap** | **3%** of the transaction (Visa/Mastercard lowered 4%→3% on **2023-04-15**), **or your actual cost of acceptance, whichever is lower**. |
| **Debit & prepaid** | **Never surcharge** — prohibited nationwide (Durbin Amendment + network rules), even if a debit card is run as "credit". |
| **Credit only** | Card surcharges apply to **credit cards only**. |
| **Advance notice / registration** | Give **written notice ≥ 30 days in advance** to **Visa**, **Mastercard**, **Discover**, and **your acquirer (Stripe)** before you start surcharging. |
| **Consistency** | Surcharge **consistently** across networks/products — you can't surcharge Visa but not Mastercard. |
| **Amex "equal treatment"** | You must treat Amex the same as the other brands (can't single Amex out). Amex sets no firm % cap but expects it to be reasonable and equal to other brands; Amex also historically **restricts which merchant categories may surcharge** — confirm against your Amex agreement. |
| **Disclosure (the "disclaimer before")** | **Yes, required.** Conspicuously disclose the surcharge **before** the customer commits, and let them **cancel or pick another method**; then itemize the surcharge **separately on the receipt**. |
| **Refunds** | You **must return the surcharge** — full on a full refund, **prorated** on a partial refund. |

### State map (verify before enabling)
- **Outright bans:** **Connecticut, Massachusetts, Maine, Puerto Rico.**
- **California (SB 478):** consumer "drip" surcharges effectively prohibited — the price shown
  must already include mandatory fees; AG-enforced. Treat CA as **do-not-surcharge** for
  consumer sales unless counsel says otherwise.
- **Lower caps than 3%:** **Colorado 2%**, **Illinois 1%**. **New York, New Jersey, Nevada,
  South Dakota:** cannot exceed your **cost of acceptance**.
- Surcharging is otherwise permitted in the large majority of states (≈48) as of Jan 2026,
  following *Expressions Hair Design v. Schneiderman* (2017).

> Because you operate in multiple states, the safe configuration is: **suppress card
> surcharging for customers in banned/CA jurisdictions**, and cap at the **lowest applicable
> limit** for the customer's state. Our settings let you cap the percent; per-state
> suppression is a manual/operational control for now (see checklist).

---

## ACH (bank debit) fees
ACH is **not** a card-network transaction, so the card-brand surcharge rules above don't
apply, and a flat or small-percent bank convenience fee **would** be permissible with
disclosure. **We do not charge one.**

Because ACH never carries a fee, it has nothing for the Payment Element to detect, so
bank payments deliberately stay on Stripe's **hosted Checkout**. Moving them onto the
Element would buy nothing and cost Financial Connections (with its own per-account
pricing), the microdeposit fallback and its 10-day verification window, and Nacha
mandate collection at confirmation — all of which hosted Checkout already handles.

The `ach_fee_percent` / `ach_fee_flat` settings
were removed in v1.185.0 rather than set to zero: keeping fields that the code refuses
to honour invites someone to set one, see no fee, and file a bug — and invites the
opposite failure if the gate is ever loosened. If the business later wants an ACH
convenience fee, re-adding the fields is a deliberate change with its own review.

---

## Disclosure requirements (what the customer must see)
1. **Before payment:** a clear statement that a surcharge/fee applies, the **amount or rate**,
   and that they may **choose another payment method** to avoid it. Our flow shows this on the
   method-choice step, and we pass disclosure text into the Stripe Checkout page
   (`custom_text`) so it appears again before they submit.
2. **The fee is a separate, labelled line item** on the Stripe-hosted page and on Stripe's
   receipt (e.g. "Card processing fee").
3. **Receipts** must show the surcharge separately (Stripe's receipts do this automatically
   for line items).

---

## Refunds
On refund, the surcharge must be returned. Our refund flow (Phase 2) refunds the **full
PaymentIntent** by default, which includes the surcharge line — so a full refund returns it
automatically. For **partial** refunds, prorate the surcharge into the refund amount.

---

## Stripe-native surcharging options
*(Re-researched 2026-07-27.)* Stripe does **not** auto-surcharge in standard hosted
Checkout. Three routes exist:

- **Automatic surcharge** (`automatic_surcharge` on Checkout Sessions / Payment Links):
  the only thing that fixes hosted Checkout's timing problem, because Stripe computes a
  funding-, brand- and country-aware amount and renders it on the hosted page. But it is
  **private preview**, needs a preview `Stripe-Version` header, and requires installing a
  **paid third-party provider app** (Yeeld or InterPayments). US only, cards + Apple Pay,
  `payment` mode only, incompatible with Adaptive Pricing.
- **PaymentIntents `amount_details[surcharge]`** (**public** preview,
  `2026-03-25.preview`): pass the total `amount` inclusive of surcharge plus
  `amount_details[surcharge][amount]`; with `enforce_validation=enabled` Stripe returns
  `maximum_amount` and a `status` of `available`/`unavailable` derived from the real card
  (US/Canada availability is credit-only, so a debit card returns `unavailable`).
  PaymentIntents only — **does not apply to hosted Checkout Sessions**.
- **Payment Element + ConfirmationToken** — **no preview API, no provider app, all GA.**
  `stripe.createConfirmationToken()` on the client returns a token whose
  [`payment_method_preview`](https://docs.stripe.com/api/confirmation_tokens/object) the
  server can retrieve **before any amount is fixed**; it carries `type` (`card` vs
  `us_bank_account`) and `card.funding` (`credit`/`debit`/`prepaid`/`unknown`). Read
  funding → price the surcharge → show the real total → confirm. This also satisfies the
  network requirement to disclose before commitment *and* let the payer back out.

**This is what we built** (v1.186.0) — see
[The portal card page](#the-portal-card-page-pay-card).

Note that "embedded Checkout" (`ui_mode: embedded`) does **not** help: it keeps the
customer on our domain but still fixes line items at session creation, so its surcharge
behaviour is identical to the hosted page. Only collecting the card ourselves — via the
Payment Element — makes the funding type visible before pricing.

---

## How our integration implements this
- **Settings (Stripe Payments Settings → Surcharge section):** `surcharge_enabled` (default
  off), `card_surcharge_percent`, `card_surcharge_flat`, `cost_of_acceptance_percent`
  (2.9), `cost_of_acceptance_flat` (0.30), `surcharge_income_account`, `surcharge_label`,
  `surcharge_disclosure`. There are no ACH fee fields.
- **The gate:** every path prices through `checkout._compute_surcharge`, which returns a
  fee only for `pm_type == "card"` **and** `funding == "credit"`.
- **Cap validation:** `checkout.surcharge_cap_error` enforces percent ≤ 3, percent ≤ cost
  percent **and** flat ≤ cost flat. Componentwise comparison is provably within cost at
  every invoice total, since `pct·X + flat ≤ cost_pct·X + cost_flat` for all `X ≥ 0`. The
  old flat-3% ceiling was not enough: 3% against 2.9% + $0.30 exceeds cost above ~$300.
- **Audit:** `Stripe Payment.card_funding` records what the card actually turned out to
  be, so the decision is reviewable after the fact. `surcharge_voided` /
  `surcharge_refund_id` record a fee that was refunded because the card wasn't credit.
- **Backstop:** if a booked surcharge meets positively-known non-credit funding at
  reconcile time, `reconcile._void_surcharge_if_not_credit` flags the row and enqueues a
  refund, and the income JE is skipped. Idempotent on the persisted refund id — Stripe's
  own idempotency keys expire after 24h, so a late webhook redelivery would slip past them.
- **Disclosure:** shown only when a fee actually applies. `saved_methods.autopay_consent_text`
  composes the enrolment authorization with a surcharge sentence **only while surcharging
  is on**, and the same composed text is what gets stored as proof of authorization.
- **Accounting:** the Payment Entry allocates the **invoice** amount to the invoice and the
  **surcharge is booked to `surcharge_income_account` by a companion Journal Entry** (Dr
  deposit/clearing, Cr surcharge income). It cannot ride on the Payment Entry: erpnext's
  `set_amounts` forces `received_amount == paid_amount` on a same-currency Receive.

---

## Go-live checklist (before turning surcharge ON)
1. ☐ Confirm with counsel that surcharging is permitted for your customer base/states.
2. ☐ Send **30-day advance notice** to Visa, Mastercard, Discover, and **Stripe**.
3. ☐ Set `card_surcharge_percent` ≤ **3%** (and ≤ the lowest applicable state cap, and ≤ your
   cost of acceptance).
4. ☐ Decide handling for **banned states (CT, MA, ME, PR) and California** — suppress card
   surcharge for those customers.
5. ☑ **Debit/prepaid exemption** — done in code (v1.185.0), not a limitation to accept any
   more. Only `funding == "credit"` is ever surcharged. Hosted Checkout cannot price a fee
   at all; one-off card surcharging requires the Payment Element work.
6. ☐ Verify the **disclosure** text and that the fee shows as a **separate line item** on the
   Stripe page and receipt.
7. ☐ Confirm **refunds return the surcharge** (full/prorated).
8. ☐ Apply **Amex equal-treatment** (surcharge all brands equally) and confirm Amex category
   eligibility.

---

## Sources
- [Stripe — Collect surcharges (cards)](https://docs.stripe.com/payments/cards/surcharge) — US credit-only, 3% cap, disclosure, refunds, "not legal advice".
- [Stripe — Automatic surcharge (Checkout)](https://docs.stripe.com/payments/checkout/surcharge/automatic-surcharge) — provider app + preview API; debit/funding-type detection.
- [Mastercard — Merchant surcharge rules](https://www.mastercard.com/us/en/business/support/merchant-surcharge-rules.html)
- [Visa — U.S. Merchant Surcharge Q&A (PDF)](https://usa.visa.com/content/dam/VCOM/global/support-legal/documents/merchant-surcharging-qa-for-web.pdf)
- [American Express — Merchant Policies & Procedures](https://www.americanexpress.com/us/merchant/merchant-regulations.html)
- [Credit Card Surcharge Laws by State (2026) — PaymentCloud](https://paymentcloudinc.com/blog/credit-card-surcharge-laws-by-state/)
- [Credit Card Surcharge Laws by State (2026) — AllayPay](https://allaypay.com/blog/processing/credit-card-surcharge-laws-by-state/)
- [Surcharge rules by network — eBizCharge](https://ebizcharge.com/blog/credit-card-surcharge-rules-by-network-visa-mastercard-and-more/)
- [Credit Card Surcharge Guide 2026 — Strictly](https://strictlyzero.com/announcements/payments-announcements/credit-card-surcharge-guide-2026-rules-legality-and-zero-fee-strategy/)
