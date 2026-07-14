# WI-026: Customer Group taxonomy backfill (1,146 ungrouped customers)
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** OD-4   **Blocks:** WI-054

## Why
1,146 of 1,602 customers have empty `customer_group` (1 true NULL + 1,145 empty-string — the two states must be handled separately), 454 are 'Government', 1 'Individual', 1 sits on the root 'All Customer Groups' (prod_customers_items). Group-based reporting, pricing, and the Products-stream segment view are all dead until this is filled. Meanwhile `customer_type` ALREADY carries segment truth: Commercial=1040, Company=365, Residential=172, Individual=13, Partnership=12.

## Native-first check
Native Customer Group tree + native `Customer.customer_group` field; backfill via Data Import/db update. Verdict: native master data; no code.

## Preconditions
- **OD-4 resolved** — branches this item must be ready to execute either way:
  - (a) Segment is a project attribute AND Products needs customer-level segment (the brief's stated direction): build Customer Group children {Commercial, Residential, Government} (+ mapping rule for customer_type values 'Company'→Commercial and 'Partnership'→Commercial, ratified by sales) and backfill from `customer_type`; the WI-054 report unions project-segment with this customer-level segment.
  - (b) Segment lives ONLY at project level: customer_group is repurposed for a channel/type taxonomy or every ungrouped customer moves to a neutral leaf group (e.g. a 'Commercial' catch-all) purely to un-break grouping.

## Scope
- Create the ratified Customer Group tree (CONFIG-scale side effect of a DATA item; groups are plain master records).
- Backfill: `UPDATE tabCustomer SET customer_group=<target> WHERE customer_group IS NULL OR customer_group=''` segmented by the customer_type mapping (hazard H1: direct SQL/`frappe.db.set_value`, never doc.save() — Customer has validate/on_update contact-sync hooks and the wildcard Triton hook; H2 irrelevant, no inserts).
- Fix the 2 stragglers: the 1 'All Customer Groups' row and the 1 'Individual' row re-homed per taxonomy.
- The 454 'Government' rows keep their group (it doubles as the tax-exempt population for WI-037).

## Acceptance criteria
- `SELECT COUNT(*) FROM tabCustomer WHERE customer_group IS NULL OR customer_group=''` = 0.
- `SELECT COUNT(*) FROM tabCustomer WHERE customer_group='All Customer Groups'` = 0.
- Group distribution counts match the mapping worksheet totals (e.g. branch (a): Commercial >= 1040+365+12 minus overrides).

## Rollback
The pre-run values are captured in a one-off export; restore by keyed UPDATE from that export.

## Explicitly NOT in this work item
Customer `tax_category` assignment (WI-037); customer_type edits; deduplication/merge of customers.
