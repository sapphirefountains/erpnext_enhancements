# Card Surcharging & Fee Pass-Through — Compliance Reference

**Status:** reference for the Stripe Payments surcharge feature (configurable, default **OFF**).
**Last researched:** 2026-06-18.
**This is not legal advice.** Surcharging rules change and vary by state and card network.
Before enabling card surcharging in production, confirm with legal counsel **and** notify
Stripe (your acquirer) and the card networks. Sapphire is in **Bountiful, Utah** — Utah
currently permits surcharging, but you sell/operate beyond Utah, so the multi-state rules
below matter.

---

## TL;DR decision for our build

- We surcharge **card and ACH**, but the feature ships **OFF** until the steps in
  [Go-live checklist](#go-live-checklist) are done.
- **Cards:** surcharge **credit only**, **≤ 3%**, never on **debit/prepaid**, never in a
  **banned state**, always **disclosed before payment** and **itemized on the receipt**.
- **ACH:** a bank-debit "convenience/processing fee" is **not** governed by card-network
  rules — simpler, but still disclose it and keep it reasonable.
- **Hosted-Checkout limitation (important):** on Stripe's hosted Checkout we **cannot detect
  debit vs credit funding** before the customer pays, so we can't auto-exempt debit cards.
  Our integration mitigates this by being **method-first** (the payer explicitly chooses
  "card" vs "bank" and sees the fee before paying) and by keeping the surcharge a clearly
  labelled, separately-itemized line. To *automatically* exempt debit you would need Stripe's
  **Payment Element + a surcharge-provider app** (Yeeld or InterPayments) on a preview API —
  see [Stripe options](#stripe-native-surcharging-options).

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
apply. A flat or small-percent **bank processing/convenience fee** is generally permissible
**with disclosure**. Keep it reasonable (Stripe's ACH cost is 0.8% capped at $5.00), and in
cost-cap states keep any fee at/under your cost. Disclose it the same way.

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
Stripe does **not** auto-surcharge in standard hosted Checkout. Two native routes exist but
both are heavier than our line-item approach:
- **Automatic surcharge** (`automatic_surcharge` on Checkout/Payment Links): **preview API**,
  **US only**, **cards/Apple Pay only**, requires installing a **surcharge-provider app**
  (Yeeld or InterPayments) that computes a compliant, funding-type-aware amount. This is the
  way to **auto-exempt debit**.
- **PaymentIntents `surcharge` API** (public preview, `2026-03-25.preview`): pass the total
  `amount` inclusive of surcharge plus `amount_details[surcharge][amount]` (do **not** add a
  separate line item in that mode); returns `surcharge.maximum_amount` to validate against.

If/when compliant debit exemption becomes a hard requirement, migrate the card path to the
Payment Element + a provider app. Until then we use the method-first, line-item approach.

---

## How our integration implements this
- **Settings (Stripe Payments Settings → Surcharge section):** `surcharge_enabled` (default
  off), `card_surcharge_percent` (cap ≤ 3, validated), `card_surcharge_flat`,
  `ach_fee_percent`, `ach_fee_flat`, `surcharge_income_account`, `surcharge_label`,
  `surcharge_disclosure`.
- **Method-first:** when surcharge is on, the payer picks **card** or **bank** first; we
  create a Checkout Session **locked to that method** with the correct fee as a separate line
  item, and show the disclosure before payment.
- **Accounting:** the Payment Entry allocates the **invoice** amount to the invoice and books
  the **surcharge to `surcharge_income_account`** (via a Payment Entry deduction row), so the
  invoice is never over-allocated.

---

## Go-live checklist (before turning surcharge ON)
1. ☐ Confirm with counsel that surcharging is permitted for your customer base/states.
2. ☐ Send **30-day advance notice** to Visa, Mastercard, Discover, and **Stripe**.
3. ☐ Set `card_surcharge_percent` ≤ **3%** (and ≤ the lowest applicable state cap, and ≤ your
   cost of acceptance).
4. ☐ Decide handling for **banned states (CT, MA, ME, PR) and California** — suppress card
   surcharge for those customers.
5. ☐ Accept or mitigate the **debit-exemption limitation** (hosted Checkout can't detect
   debit; migrate to Payment Element + provider app if you must auto-exempt).
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
