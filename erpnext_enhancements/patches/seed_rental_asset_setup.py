"""Create the Asset Category and Location without which **no Asset can ever be saved**.

ER-2026-420503 reported the Asset form as broken. It is, but not in the way a form is
usually broken. Measured on production on 2026-09-14: ``Asset`` 0, ``Asset Category`` 0,
``Location`` 0, and Items with ``is_fixed_asset = 1`` 0.

Those zeros are a closed loop, not four separate gaps:

* ``Asset.location`` is ``reqd: 1`` and links to ``Location``. With no Location record
  there is **nothing selectable in a mandatory field**, so the form cannot be saved by
  anyone, ever, regardless of what else is fixed.
* ``Item.asset_category`` is ``mandatory_depends_on: is_fixed_asset``. With no Asset
  Category, no Item can be made a fixed asset.
* ``Asset.item_code`` is filtered ``{is_fixed_asset: 1, is_stock_item: 0}`` by
  ``asset.js``. With no fixed-asset Item, that picker is permanently empty — and worse,
  an Item created inline from it saves fine and then vanishes from the field, which is
  exactly the "after creating the new item code it doesn't exist" in the bug report.

So the chain is: no Asset Category -> no fixed-asset Item -> no selectable item code ->
no Asset. Relaxing mandatory flags (see the Asset Property Setters in
``fixtures/property_setter.json``) does not touch a single link in it.

--------------------------------------------------------------------------------------
Why a patch and not a fixture
--------------------------------------------------------------------------------------

This is **seed data the business then edits**, and that is precisely what a fixture
cannot be. ``bench migrate`` deletes and re-inserts every fixture document from its JSON
on every fixture-touching deploy, so keys omitted from the JSON are wiped — the
depreciation accounts, finance books and address that Finance fills in later would be
silently reverted to whatever this file said, on a deploy that had nothing to do with
assets. A one-shot patch writes the records once and then leaves them alone.

Both creates are guarded on ``frappe.db.exists``, so a re-run is a no-op and this is
safe to leave in ``patches.txt`` forever.

--------------------------------------------------------------------------------------
Account choices, and the ones deliberately left blank
--------------------------------------------------------------------------------------

``Asset Category.accounts`` is a ``reqd`` child table and each row needs a
``fixed_asset_account`` whose ``account_type`` is exactly ``Fixed Asset``.
**14000 - Rental Fountains - SF already exists** in the chart of accounts, correctly
typed and non-group — the accounting for this was set up long before the operational
side was. Using it is what makes this seeding rather than inventing.

``accumulated_depreciation_account`` is left **blank on purpose**: there are zero
accounts on this site with ``account_type = "Accumulated Depreciation"``.
``15000 - Accumulated Depreciation - SF`` exists but is typed ``Fixed Asset``, and
``AssetCategory.validate_account_types`` would reject it. Wiring a mistyped account to
make a form submit is how a depreciation entry ends up in the wrong place; leaving it
empty means ERPNext throws a clear message naming the field on the day somebody first
turns depreciation on, which is the right person and the right moment to decide it.

``enable_cwip_accounting`` stays **0**, and that one is load-bearing rather than
cosmetic: with CWIP enabled, ``Asset.validate_asset_values()`` demands a Purchase
Receipt or Purchase Invoice on any asset that is not Existing/Composite — re-blocking
the exact save this patch exists to unblock.

``non_depreciable_category`` is left at its default. Whether the rental fleet
depreciates is a Finance policy question, and this patch has no business answering it.
"""

import frappe

ASSET_CATEGORY = "Rental Fountain Fleet"
#: Already present in the chart of accounts, ``account_type = "Fixed Asset"``, non-group.
FIXED_ASSET_ACCOUNT = "14000 - Rental Fountains - SF"
#: Optional, and correctly typed (``account_type = "Depreciation"``). Unused until
#: somebody ticks Calculate Depreciation, but wiring the one account that IS right
#: costs nothing and saves a lookup later.
DEPRECIATION_EXPENSE_ACCOUNT = "5203 - Depreciation - SF"

LOCATION = "Upstairs Rental Warehouse"


def execute() -> None:
	seed_asset_category()
	seed_location()


def seed_asset_category() -> bool:
	"""Create the rental-fleet Asset Category. Returns True if it wrote one.

	No-op when the category already exists, or when the company or its fixed-asset
	account is missing — on a site without either, throwing here would abort
	``bench migrate``, and on this repo ``bench migrate`` is the deploy.
	"""
	if frappe.db.exists("Asset Category", ASSET_CATEGORY):
		return False

	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		frappe.logger().info("seed_rental_asset_setup: no Company, skipping Asset Category")
		return False

	if not frappe.db.exists("Account", FIXED_ASSET_ACCOUNT):
		# The account name embeds the company abbreviation, so this is the one thing
		# here that cannot be assumed on a differently-named site.
		frappe.logger().info(
			f"seed_rental_asset_setup: {FIXED_ASSET_ACCOUNT} not found, skipping Asset Category"
		)
		return False

	row = {
		"company_name": company,
		"fixed_asset_account": FIXED_ASSET_ACCOUNT,
	}
	if frappe.db.exists("Account", DEPRECIATION_EXPENSE_ACCOUNT):
		row["depreciation_expense_account"] = DEPRECIATION_EXPENSE_ACCOUNT

	category = frappe.new_doc("Asset Category")
	category.asset_category_name = ASSET_CATEGORY
	category.enable_cwip_accounting = 0
	category.append("accounts", row)
	category.insert(ignore_permissions=True)
	frappe.logger().info(f"seed_rental_asset_setup: created Asset Category {ASSET_CATEGORY}")
	return True


def seed_location() -> bool:
	"""Create the rental store Location. Returns True if it wrote one.

	``Location`` needs only ``location_name``; the address and the map polygon are for
	whoever knows the site, not for this file.
	"""
	if frappe.db.exists("Location", LOCATION):
		return False

	location = frappe.new_doc("Location")
	location.location_name = LOCATION
	location.insert(ignore_permissions=True)
	frappe.logger().info(f"seed_rental_asset_setup: created Location {LOCATION}")
	return True
