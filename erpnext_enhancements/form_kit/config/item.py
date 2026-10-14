"""The guided flow for an Item (form kit P8.1, ERPNext TASK-2026-02790): a new part named the
POL-0602 way, bought from its supplier at its price, kept in its bin and labeled, without opening
the Item form.

It walks the real Item form, like every flow: nothing the form shows is hidden (owner,
2026-10-13: "keep showing everything"). The first five steps are the core of the flow, the ones a
buyer adding a part needs, each with a block the Item extension draws above the form's own fields
(``public/js/guided_item/``, picked by DocType through ``public/js/form_kit/extensions.js``):

* ``item_naming``: the name builder (the seven segments of POL-0602 from pick lists drawn from
  the catalog) and the code helper (vendor part number, ``CON-``, ``PDT-####``, ``SRV-###``),
  with the naming guard's own preview (``api.item_flow.preview_item``).
* ``item_supplier``: the default supplier, the vendor's part number and purchase link (an Item
  Supplier row) and the last buying price (an Item Price on the buying list).
* ``item_stock``: the default bin, its reorder level and quantity, and the bin's QR label.
* ``item_asset``: for a fixed-asset item, the Asset itself, so the two never come apart.

Everything after them is a ``more`` step: the rest of the Item form, a tap away in the rail and
behind *More details*, while Next on the last core step offers Done.

**Not the default yet.** Parker and Clegg use the Item form every day and the flow has not been
reviewed, so ``open_by_default`` is False: an Item opens in the form as before, and the guided
page is reached on purpose (the form's *Guided* button, the guided page's start screen,
``/desk/guided-entry/Item/new``).

**Sections move; fields are named.** The Item ``field_order`` Property Setter
(``scripts/layout/specs/item.json``) predates the Safety Data and Maintenance Billing sections, and
v16 places a custom Section Break whose ``insert_after`` field is followed by a field of the same
type straight after it (``Meta.sort_fields``), so on production the Maintenance Billing section
holds Default Unit of Measure, Maintain Stock and the fixed-asset ticks. The day the generator is
run again they move back to the top. So the steps move those fields by name (``fields``), which
holds either way, and never rely on which section holds them.
"""

_SERIES = (
	"item.js shows it only when Stock Settings names items by series; Sapphire names every Item by "
	"its Item Code (POL-0602), so the form hides it."
)

#: What every Item should have, stock or not (the start screen's progress).
BASE = [
	{"field": "item_code", "label": "Item Code"},
	{"field": "item_name", "label": "Item Name"},
	{"field": "item_group", "label": "Item Group"},
	{"field": "stock_uom", "label": "Default Unit of Measure"},
	{"field": "supplier_items", "row_field": "supplier", "label": "A supplier"},
]
#: A stock item also wants a reorder rule for the bin it is kept in.
STOCKED = [
	*BASE,
	{"field": "reorder_levels", "row_field": "warehouse_reorder_level", "label": "A reorder level"},
]

CONFIG = {
	"doctype": "Item",
	# Not where an Item opens yet: see the module docstring.
	"open_by_default": False,
	"skip_fields": {
		"naming_series": _SERIES,
		"stock_levels_html": "The stock levels widget ERPNext's item.js draws for a saved stock item.",
		"prices_html": "The Item Price list ERPNext's item.js draws on the Pricing tab.",
		"custom_pump_curve_chart": "The pump curve chart water_engineering/pump_curve_chart.js draws from the curve points.",
		"custom_comments_field": "The Comments app (comments_auto.js) mounts here; the flow has none.",
	},
	"create": {
		# Step 1: what an Item needs to exist. Maintain Stock and Is Fixed Asset are asked here
		# because they decide which steps follow, and a fixed asset cannot be saved without its
		# category (Item.validate_fixed_asset).
		"fields": [
			"item_code",
			"custom_sku",
			"item_name",
			"item_group",
			"stock_uom",
			"is_stock_item",
			"is_fixed_asset",
			"asset_category",
		],
		# The guided page's id for the create (form kit P4.7): a create resent after a lost answer
		# returns the Item the first attempt made instead of being refused as a duplicate code.
		"client_id_field": "custom_guided_client_id",
	},
	# item.js: a fixed asset is never a stock item (Item.validate_fixed_asset refuses one), and
	# ticking it makes Maintain Stock read-only and unticked. The other way round, a stock item is
	# no longer an asset.
	"clears": {"is_fixed_asset": ["is_stock_item"], "is_stock_item": ["is_fixed_asset"]},
	# The guided page's start screen: the Items this person added, newest change first.
	"mine": {
		"label": "Items you added",
		"owner_field": "owner",
		"title_field": "item_name",
		"detail_fields": ["item_group"],
		"open": {"field": "disabled", "not_in": [1]},
	},
	"steps": [
		{
			"key": "basics",
			"title": "Name & code",
			"intro": (
				"Build the name in POL-0602 order, choose the code, and say whether it is kept in "
				"stock; saving this step creates the Item."
			),
			# By name, in the order a buyer thinks of them (see the module docstring): the Details
			# tab's top section is left with only the series, which the form hides too.
			"fields": [
				"item_code",
				"custom_sku",
				"custom_item_identifier",
				"item_name",
				"item_group",
				"stock_uom",
				"is_stock_item",
				"is_fixed_asset",
				"asset_category",
			],
			"extension": "item_naming",
		},
		{
			"key": "description",
			"title": "Description",
			"intro": "The brand and the longer description buyers and technicians read on documents.",
			"sections": ["section_break_11"],
		},
		{
			"key": "supplier",
			"title": "Supplier & price",
			"intro": (
				"Who we buy it from, their part number and link, and what we last paid, so a "
				"purchase order can offer them."
			),
			"sections": ["purchasing_tab", "section_break_ylma"],
			"extension": "item_supplier",
		},
		{
			"key": "stock",
			"title": "Stock & bin",
			"intro": "Where it is kept, when to buy more and how many, and the label for its bin.",
			"fields": ["safety_stock"],
			"sections": ["reorder_section"],
			"applies": {"field": "is_stock_item", "in": [1]},
			"extension": "item_stock",
		},
		{
			"key": "asset",
			"title": "Fixed asset",
			"intro": "The Asset record for this item, made here so the Item and the Asset never come apart.",
			"fields": ["auto_create_assets", "is_grouped_asset", "asset_naming_series"],
			"applies": {"field": "is_fixed_asset", "in": [1]},
			"extension": "item_asset",
		},
		{
			"key": "settings",
			"title": "Buying & selling settings",
			"intro": "Whether it can be bought, sold or substituted, its selling rate and the receipt tolerances.",
			"fields": ["disabled"],
			"sections": ["section_break_zlmj", "section_break_gjns", "section_break_znra"],
			"more": True,
		},
		{
			"key": "chemical",
			"title": "Safety data & visit billing",
			"intro": "For a chemical: its safety data sheet, and how a maintenance visit bills it.",
			"sections": ["custom_sds_section", "custom_maintenance_billing_section"],
			"more": True,
		},
		{
			"key": "pump",
			"title": "Pump specifications",
			"intro": "The rated flow, head and electrical details the fountain designer selects pumps by.",
			"sections": ["custom_pump_section"],
			"applies": {"field": "item_group", "in": ["Pumps"]},
			"more": True,
		},
		{
			"key": "stock_more",
			"title": "More stock settings",
			"intro": "How it is valued, barcodes, and serial or batch tracking.",
			"sections": [
				"inventory_valuation_section",
				"inventory_settings_section",
				"sb_barcodes",
				"serial_nos_and_batches",
			],
			"applies": {"field": "is_stock_item", "in": [1]},
			"more": True,
		},
		{
			"key": "purchasing_more",
			"title": "More purchasing",
			"intro": "Drop shipping and where it is made, for customs.",
			"sections": ["supplier_details", "foreign_trade_details"],
			"more": True,
		},
		{
			"key": "sales",
			"title": "Sales",
			"intro": "The unit it is sold in, commission and discount limits, and customers' own codes.",
			"sections": ["sales_details", "customer_details"],
			"applies": {"field": "is_sales_item", "in": [1]},
			"more": True,
		},
		{
			"key": "units",
			"title": "Units & tax",
			"intro": "Other units it comes in, with their conversion, and its tax templates.",
			"sections": ["unit_of_measure_conversion", "section_break_oilf", "section_break_fxqz"],
			"more": True,
		},
		{
			"key": "accounting",
			"title": "Accounting defaults",
			"intro": "Per-company defaults such as the warehouse and accounts, and deferred revenue or expense.",
			"sections": ["section_break_wfkx", "deferred_accounting_section"],
			"applies": {"field": "is_fixed_asset", "in": [0, None]},
			"more": True,
		},
		{
			"key": "manufacturing",
			"title": "Manufacturing & quality",
			"intro": "Bills of materials, subcontracting, the manufacturer's part number and inspections.",
			"sections": ["manufacturing", "section_break_xili", "quality_tab"],
			"more": True,
		},
		{
			"key": "variants",
			"title": "Variants",
			"intro": "Whether this item is a template with variants, or a variant of another.",
			"sections": ["variants_section"],
			"applies": {"field": "is_fixed_asset", "in": [0, None]},
			"more": True,
		},
	],
	"stages": {
		"field": "is_stock_item",
		"default": 0,
		"labels": {0: "Non-stock item", 1: "Stock item"},
		"lists": {0: BASE, 1: STOCKED},
	},
}
