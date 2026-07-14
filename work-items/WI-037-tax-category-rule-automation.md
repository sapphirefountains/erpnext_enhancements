# WI-037: Tax Category + Tax Rule automation (OD-2 direction set: Utah-law, stream-differentiated)
**Phase:** 1   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-036; CPA written taxability matrix = go-live sign-off gate   **Blocks:** WI-038

## Why
Tax Category and Tax Rule are both present and EMPTY (0 rows — prod_finance_native), so today rate selection is manual per document — an error factory with 3+ rates and exempt government customers (454 in the 'Government' group).

## Native-first check
Native **Tax Category** + **Tax Rule** (rule engine selects the Sales Taxes and Charges Template by customer tax category / shipping state / priority). Verdict: exactly what the native feature is for; zero custom logic.

## Preconditions
- WI-036 templates exist. **OD-2 resolved in direction (2026-07-14): follow Utah law → stream-differentiated (Build non-taxable to customer expected)**; category names finalized against the CPA's written matrix before go-live.

## Scope
- Create Tax Categories (proposed: 'Taxable', 'Exempt - Government', 'Out of State', plus a Build-stream category for the real-property-improvement exemption per the Utah-law direction — final names per the CPA's written matrix).
- Create Tax Rules mapping each category (+ billing/shipping state where the ruling needs it) → template, with priorities and validity dates.
- One-time backfill (kept inside this item so the tax rollout lands atomically; executed with DATA discipline): `frappe.db.set_value` of `Customer.tax_category='Exempt - Government'` for the 454 customers where `customer_group='Government'` (hazard H1 — db.set_value bypasses the wildcard after_save Triton hook), and default 'Taxable' for the rest per ruling.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabTax Rule\`` equals the designed rule matrix count; `SELECT COUNT(*) FROM \`tabTax Category\`` equals the ratified category count.
- `SELECT COUNT(*) FROM tabCustomer WHERE customer_group='Government' AND (tax_category IS NULL OR tax_category='')` = 0.
- Scenario test documented: a new draft Sales Invoice for a Government customer auto-selects the exempt template; a Taxable in-state customer auto-selects the default template (verified in TEST first).

## Rollback
Delete Tax Rules (documents fall back to manual template selection); keyed restore of tax_category from pre-run export.

## Explicitly NOT in this work item
Address-data cleanup to make state-based rules reliable (addresses were never imported by the sync — repo_qbo_sync; if state-based rules are ruled in, an address-backfill DATA item must be raised with the CRM workstream).
