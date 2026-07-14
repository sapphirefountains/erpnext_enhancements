# WI-013: CEO dollar-threshold PO escalation via native Authorization Rule
**Phase:** 0   **Type:** FIXTURE   **Size:** S
**Blocked by:** WI-010, WI-012, threshold value decision (precondition)   **Blocks:** WI-022

## Why
Large purchases need CEO sign-off, but the desired percentage-of-budget rule is NOT buildable now — all 625 projects have `estimated_costing` 0/NULL (prod_projects_opps), so a percentage has no denominator. Day one = fixed dollar threshold; the % rule is Phase 2, blocked on budget discipline (hard rule 5).

## Native-first check
Native **Authorization Rule** — doctype verified present and EMPTY (0 rows) in this v16 build (prod_finance_native). SUFFICIENT: `transaction='Purchase Order'`, `based_on='Grand Total'`, `value=<threshold>` blocks submission above the threshold for users lacking `approving_role`; the CEO (holding the approving role) submits normally. Approving-role semantics: the rule exempts holders of `approving_role`, so the escalation path is "PM saves PO, CEO submits" — no workflow needed. A custom approval doctype would be a defect.

## Preconditions
- Business decides the threshold (proposal to present: $5,000; NOT decided here).
- An approving role exists and is held ONLY by the CEO (seeded 'PO Approver' via WI-010, or native 'Purchase Master Manager' if role hygiene confirms only the CEO holds it).
- OD-1 branch noted: Authorization Rule is per-company; if JDH becomes a second company, clone the rule with `company='JDH...'`.

## Scope
- One Authorization Rule row: `transaction='Purchase Order'`, `based_on='Grand Total'`, `company='Sapphire Fountains'`, `value=<threshold>`, `approving_role='PO Approver'`.
- The rule ships VERSION-CONTROLLED (review correction C1 — this item is retyped FIXTURE): either add an `{"dt": "Authorization Rule", "filters": [["name","in",[<the rule name>]]]}`-style allowlist entry to the hooks.py fixtures list, or ship a `seed_po_authorization_rule` patch following the app's `seed_*` precedent (idempotent exists-check, appended to `patches.txt` [post_model_sync]). The threshold value lives in the patch/fixture; it deploys to both sites via the normal main-branch deploy — no hand replay on prod.

## Acceptance criteria
- `SELECT COUNT(*) FROM `tabAuthorization Rule`` = 1 on test (then prod) with the exact field values above.
- The rule's definition is visible in the repo (fixture JSON entry or seed patch) and applies idempotently on migrate — review correction C1.
- TEST: PM user submitting a PO with grand_total > threshold receives the authorization error; CEO user submits the same PO successfully; PO ≤ threshold submits for PM.

## Rollback
Remove the fixture/patch entry and redeploy; delete the Authorization Rule row (returns to unrestricted submit).

## Explicitly NOT in this work item
Percentage-of-budget escalation (Phase 2; blocked on budgets per hard rule 5); Purchase Invoice/Payment Entry approval (WI-044); multi-level approval chains.
