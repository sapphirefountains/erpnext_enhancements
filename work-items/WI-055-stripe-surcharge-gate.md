# WI-055: Surcharge go-live compliance gate (stays OFF until the 8-item checklist passes)
**Phase:** 2   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-039; WI-041 (checklist item 7); completion of the 8-item go-live checklist in docs/stripe_surcharging_compliance.md (OD-7 RESOLVED 2026-07-14: launch WITHOUT surcharge — this item stays Phase 2 and only executes if/when the business later wants it, checklist-complete first)   **Blocks:** nothing

## Why
Surcharging code is built and even enabled on TEST (`surcharge_enabled`=1 — test_vs_prod), but card surcharging is legally gated: credit-only, ≤3% and ≤ cost of acceptance, never debit/prepaid, banned states, 30-day network notice, disclosure requirements (repo_payments compliance summary). Flipping it on prod without the checklist is a compliance incident, not a feature launch.

## Native-first check
No native surcharge feature exists; the custom module implements it (method-first hosted-Checkout design — repo_payments). Verdict: pure configuration of existing custom code, gated on compliance.

## Preconditions — the 8 checklist items, each independently verifiable (from docs/stripe_surcharging_compliance.md via repo_payments)
1. counsel sign-off; 2. 30-day advance notice to Visa/Mastercard/Discover/Stripe; 3. cap configuration (≤3% and ≤ cost of acceptance / lowest applicable state cap); 4. banned-state suppression procedure (manual/operational — CT, MA, ME, PR); 5. debit-limitation acceptance (hosted Checkout cannot detect debit — method-first design); 6. disclosure verification (pre-payment + receipt line item); 7. refund-returns-surcharge verification (full auto, partial prorated — depends on WI-041 landing); 8. Amex equal-treatment.

## Scope
On prod Single `Stripe Payments Settings` (fieldnames verified — repo_payments): `surcharge_enabled`=1, `card_surcharge_percent`/`card_surcharge_flat`, `ach_fee_percent`/`ach_fee_flat` (ACH convenience fee: disclosed + reasonable; Stripe ACH cost is 0.8% cap $5), `surcharge_income_account` (create/pick an Income account, e.g. a 'Card Surcharge Income' leaf — new account under Income tree), `surcharge_label`, `surcharge_disclosure`.

## Acceptance criteria
- tabSingles `surcharge_enabled`=1 with all four rate fields and `surcharge_income_account` set; checklist evidence filed (8 artifacts).
- A live card payment shows the surcharge as a separate labelled line; the Payment Entry settles the invoice at face value and a **companion Journal Entry** credits `surcharge_income_account` (`Dr Deposit/Clearing / Cr Surcharge Income`), so the deposit account ends at charge + surcharge (mechanism: `reconcile._book_surcharge`, fixed in v1.158.3 — erpnext forbids received > paid on a Receive PE, so the earlier `_apply_surcharge` deduction approach could never post).
- A full refund returns the surcharge (test transaction).

## Rollback
`surcharge_enabled`=0 — instant, no data cleanup needed (surcharge only applies at checkout creation time).

## Explicitly NOT in this work item
Migrating to Stripe Payment Element for true debit exemption (explicitly deferred in the compliance doc); ACH-only fee launch as a separate earlier step (allowed branch — ACH convenience fees are outside card-network rules; may be flipped independently if counsel approves).
