"""Seed the Item "EST-SCOPE" that an Estimate's quotation lines use, and its zero price (v1.836.0).

An Estimate issues a native Quotation with one line per phase (concept flow step 4). A Quotation line
needs an Item, and a phase of a fountain is not a product, so every line uses this one non-stock,
sales-only service Item and carries the phase's name, scope text and price itself
(``api/estimating.issue_quotation``).

Two steps, each through ``estimating.seed.run`` so neither can abort the migrate:

1. **The Item**, named ``SERVICE, ESTIMATE SCOPE`` (the Item Naming Schema's shape; see
   ``estimating.seed.SCOPE_ITEM_NAME`` for the one finding left). Group "Service", unit **"Unit"**
   ("Nos" is disabled on prod). Never a stock item, so it can never reach a warehouse.
2. **A 0 Item Price on the default selling price list.** Prod auto-inserts a missing Item Price
   from the first transaction's rate, which would make the first phase price ever issued the list
   price for EST-SCOPE (``estimating.seed.hold_zero_price`` says why it matters).

Insert-only and idempotent.
"""

from erpnext_enhancements.estimating import seed


def execute():
	seed.run(
		"seed_estimate_scope_item",
		"Item EST-SCOPE",
		seed.seed_scope_item,
		"create Item EST-SCOPE (non-stock, sales only, group Service, unit Unit), named "
		f"{seed.SCOPE_ITEM_NAME!r}.",
	)
	seed.run(
		"seed_estimate_scope_item",
		"EST-SCOPE zero price",
		lambda: seed.hold_zero_price(seed.default_selling_price_list()),
		"add an Item Price of 0 for EST-SCOPE on the default selling price list.",
	)
