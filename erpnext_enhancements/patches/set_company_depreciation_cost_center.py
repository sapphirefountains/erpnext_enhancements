"""Give the Company a depreciation cost centre, so an Asset can be saved without one.

The fifth blocker on the ER-2026-420503 form, and the only one of the five that static
analysis could not have found — it surfaced only when ten real rental Assets were
created. ``Asset.validate_cost_center()``::

    else:
        if not frappe.get_cached_value("Company", self.company, "depreciation_cost_center"):
            frappe.throw(_("Please set a Cost Center for the Asset or set an Asset
                            Depreciation Cost Center for the Company {}"))

So an Asset must carry a cost centre **or** the Company must have one, and this site had
neither. It is invisible in ``asset.json`` because ``cost_center`` is not ``reqd``: the
requirement lives in the controller and depends on company configuration.

--------------------------------------------------------------------------------------
Why the Company field and not a `default` on Asset.cost_center
--------------------------------------------------------------------------------------

A Property Setter defaulting ``Asset.cost_center`` was written first and deliberately
replaced. It worked, but it stamped **every** Asset with the rental fleet's cost centre —
right for all ten Assets that exist today and wrong the moment a vehicle or a laptop
becomes an Asset, silently, because a default looks like a considered choice.

The Company field is the fallback ERPNext designed for exactly this, and it layers
correctly: an Asset that knows its own cost centre carries it (the ten rental Assets each
hold ``CL140 - Rentals - SF``), and anything else falls back to the company-wide value
rather than to a guess about what kind of asset it is.

--------------------------------------------------------------------------------------
Why ``Main - SF``, and why this never overwrites
--------------------------------------------------------------------------------------

``Main - SF`` is the Company's existing ``cost_center`` and ``round_off_cost_center``, it
is a leaf (group cost centres are rejected in transactions), and it is **neutral across
asset types** — which is the entire reason for preferring this over the per-asset default.

Where depreciation actually posts is a Finance decision, so this writes **only when the
field is empty**. A value somebody has chosen is never replaced, and a re-run is a no-op.
"""

import frappe

COMPANY_FIELD = "depreciation_cost_center"
#: The Company's existing default cost centre. Leaf, not a group.
PREFERRED_COST_CENTER = "Main - SF"


def execute() -> None:
	for company in frappe.get_all("Company", pluck="name"):
		set_depreciation_cost_center(company)


def set_depreciation_cost_center(company: str) -> bool:
	"""Fill a company's depreciation cost centre if it has none. True if it wrote.

	Skips — loudly in the log, never by raising — when the company already has a value or
	when no usable cost centre exists. A patch that raises aborts ``bench migrate``, which
	on this repo is the deploy.
	"""
	if frappe.db.get_value("Company", company, COMPANY_FIELD):
		return False

	cost_center = _usable_cost_center(company)
	if not cost_center:
		frappe.logger().info(
			f"set_company_depreciation_cost_center: no usable cost centre for {company}, skipping"
		)
		return False

	frappe.db.set_value("Company", company, COMPANY_FIELD, cost_center)
	frappe.logger().info(
		f"set_company_depreciation_cost_center: {company} -> {cost_center}"
	)
	return True


def _usable_cost_center(company: str) -> str | None:
	"""The company's preferred cost centre, else its default, else any leaf it owns.

	Every candidate is checked for ``is_group = 0``: ``validate_cost_center`` rejects a
	group cost centre outright, so seeding one would swap this blocker for a less
	obvious one.
	"""
	candidates = [
		PREFERRED_COST_CENTER,
		frappe.db.get_value("Company", company, "cost_center"),
		frappe.db.get_value("Company", company, "round_off_cost_center"),
	]
	for name in candidates:
		if not name:
			continue
		row = frappe.db.get_value(
			"Cost Center", name, ["company", "is_group"], as_dict=True
		)
		if row and row.company == company and not row.is_group:
			return name

	return frappe.db.get_value(
		"Cost Center", {"company": company, "is_group": 0}, "name"
	)
