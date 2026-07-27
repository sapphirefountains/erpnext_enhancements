# WI-055: Surcharge go-live compliance gate (stays OFF until the 8-item checklist passes)
**Phase:** 2   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-039; WI-041 (checklist item 7); completion of the 8-item go-live checklist in docs/stripe_surcharging_compliance.md (OD-7 RESOLVED 2026-07-14: launch WITHOUT surcharge — this item stays Phase 2 and only executes if/when the business later wants it, checklist-complete first)   **Blocks:** nothing

## Why
Surcharging code is built, but card surcharging is legally gated: credit-only, ≤3% and ≤ cost of acceptance, never debit/prepaid, banned states, 30-day network notice, disclosure requirements (repo_payments compliance summary). Flipping it on prod without the checklist is a compliance incident, not a feature launch.

> **2026-07-27 — this WI's premise was wrong.** It asserted the feature "stays OFF"; prod was in fact **Live with `surcharge_enabled`=1 at `card_surcharge_percent`=2.9**, contrary to OD-7. No payment had been taken through it (0 `Stripe Payment` rows), so nothing was mis-charged, but the next card payment would have been surcharged regardless of funding type. Set back to `surcharge_enabled`=0 the same day. **Checklist items 1 (counsel) and 2 (30-day network notice) are both still outstanding**, so surcharging is unlawful today on that axis alone, independent of debit handling — it must stay off until they complete.

Separately, v1.185.0 moved the debit/prepaid/ACH exemption from "accepted limitation" to enforced code (see below), so item 5 is closed.

## Native-first check
No native surcharge feature exists; the custom module implements it (method-first hosted-Checkout design — repo_payments). Verdict: pure configuration of existing custom code, gated on compliance.

## Preconditions — the 8 checklist items, each independently verifiable (from docs/stripe_surcharging_compliance.md via repo_payments)
1. ☐ counsel sign-off; 2. ☐ 30-day advance notice to Visa/Mastercard/Discover/Stripe; 3. cap configuration (now validated in code: ≤3% **and** ≤ cost of acceptance componentwise); 4. banned-state suppression procedure (manual/operational — CT, MA, ME, PR); 5. ☑ **debit/prepaid exemption — enforced in code as of v1.185.0**, no longer an accepted limitation; 6. disclosure verification (pre-payment + receipt line item); 7. refund-returns-surcharge verification (full auto, partial prorated — depends on WI-041 landing); 8. Amex equal-treatment.

## Scope
On prod Single `Stripe Payments Settings` (fieldnames verified — repo_payments): `surcharge_enabled`=1, `card_surcharge_percent`/`card_surcharge_flat`, `cost_of_acceptance_percent`/`cost_of_acceptance_flat` (2.9 / 0.30 — the surcharge is validated against these), `surcharge_income_account` (create/pick an Income account, e.g. a 'Card Surcharge Income' leaf — new account under Income tree), `surcharge_label`, `surcharge_disclosure`. **No ACH fee fields exist** — `ach_fee_percent`/`ach_fee_flat` were removed in v1.185.0; ACH never carries a fee.

**Turning `surcharge_enabled`=1 surcharges credit cards on two paths:** off-session/autopay (`saved_methods`), and one-off portal card payments via the Payment Element page at `/pay-card` (`card_element`, v1.186.0). **Emailed payment links and the desk "Pay" button stay on hosted Checkout and remain fee-free** — that path cannot know the funding type before pricing, and the Element page currently requires a logged-in portal session. A tokenized guest route is the remaining gap if surcharging emailed links matters.

## Acceptance criteria
- tabSingles `surcharge_enabled`=1 with all four rate fields and `surcharge_income_account` set; checklist evidence filed (8 artifacts).
- A live card payment shows the surcharge as a separate labelled line; the Payment Entry settles the invoice at face value and a **companion Journal Entry** credits `surcharge_income_account` (`Dr Deposit/Clearing / Cr Surcharge Income`), so the deposit account ends at charge + surcharge (mechanism: `reconcile._book_surcharge`, fixed in v1.158.3 — erpnext forbids received > paid on a Receive PE, so the earlier `_apply_surcharge` deduction approach could never post).
- A full refund returns the surcharge (test transaction).

## Rollback
`surcharge_enabled`=0 — instant, no data cleanup needed (surcharge only applies at checkout creation time).

## Explicitly NOT in this work item
A tokenized guest route for the card Element page, which is what emailed payment links would need to carry a surcharge (they stay fee-free on hosted Checkout today). Moving ACH onto the Payment Element — rejected on the merits: ACH never carries a fee, so the Element detects nothing for it while adding Financial Connections, microdeposits and Nacha mandate handling that hosted Checkout already does. ACH convenience fees — decided against and removed from the codebase, not merely disabled.
