# WI-025: Item Group rollout — classify 583 items into the existing 16-group taxonomy
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** nothing   **Blocks:** WI-034, WI-036 (item-tax assignment by group)

## Why
The SKU taxonomy (CON/SPP/RAW/SUB/FTN/PKG/SVC) exists as the Item Group tree, but 6 of 7 container groups hold ZERO items and 257 of 583 items sit on the root 'All Item Groups' (prod_customers_items: populated groups today — All Item Groups 257, Products 195, Electrical 37, Office 36, Service 34, E-Stop Components 18, Pumps 5, Service Fountains 1). Purchasing, sales analytics, and item-tax assignment are group-blind until items are homed.

## Native-first check
Native Item Group tree + `Item.item_group`; native Sales Analytics groups by Item Group once populated. Verdict: native; DATA only.

## Preconditions
- Since items cannot sit on `is_group=1` nodes, each empty container (Raw Materials, Spare Parts, Sub Assemblies, Finished Fountains, Kits and Bundles, Services) either receives leaf children (design them with ops) or is flipped to `is_group=0` where no sub-structure is needed — decide per container in a 1-hour design pass.
- Classification workbook covering all 583 items (item_code, current group, target leaf). Seed signals: 91 item_codes carry CON-* (→ Consumables children), the 12 `custom_item_identifier` CON-* rows, and the existing Electrical/Service/Office placements. `custom_sku` is NOT authoritative (14 ad-hoc values — prod_customers_items).

## Scope
- Apply the workbook via `frappe.db.set_value('Item', code, 'item_group', target)` in batches (hazard H1: the wildcard `'*'` after_save Triton sync hook fires on every doc save — db.set_value bypasses ORM hooks; batch with periodic commits and confirm the Triton sync target is paused).
- Reclassify the 257 root-parked items to leaves; re-home the 195 'Products' items into the taxonomy where the workbook says so (or ratify 'Products' as a legitimate leaf for the Products revenue stream — note OD-3/OD-4 adjacency, but this is an ops call, not an OD).

## Acceptance criteria
- `SELECT COUNT(*) FROM tabItem WHERE item_group='All Item Groups'` = 0.
- `SELECT COUNT(*) FROM tabItem i JOIN \`tabItem Group\` g ON g.name=i.item_group WHERE g.is_group=1` = 0.
- Every container group either has >=1 leaf child or is itself a leaf.

## Rollback
Keyed restore from pre-run (item_code, item_group) export.

## Explicitly NOT in this work item
Renaming item_codes to the SKU scheme (renames ripple through every linked doc — separate decision for the inventory workstream); populating custom_sku; stock opening (no stock value: Stock Entry table empty).
