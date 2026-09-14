"""Invariants for the four feedback requests shipped together in v1.455.0.

ER-2026-312391 (per-item PO status), ER-2026-362239 (expected delivery dates),
ER-2026-420503 (the Asset form), ER-2026-312370 (rental inspections).

Bench-free by construction: reads the fixture JSON, `patches.txt` and the hooks/controller
source with AST and regex, and imports none of it.

**Why these particular assertions.** Every one of them pins a decision where the obvious
edit is the wrong one, and where getting it wrong produces no error — the shape this
repo keeps being bitten by. In each case the code still runs; it just quietly stops doing
the thing it exists for.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_er_batch
"""

import ast
import json
import pathlib
import re
import unittest

APP = pathlib.Path(__file__).resolve().parents[1]
CUSTOM_FIELDS = APP / "fixtures" / "custom_field.json"
PROPERTY_SETTERS = APP / "fixtures" / "property_setter.json"
PATCHES_TXT = APP / "patches.txt"
HOOKS = APP / "hooks.py"
BOOKING_API = APP / "api" / "booking.py"
PROCUREMENT_API = APP / "api" / "procurement.py"
BOOKING_JS = APP / "asset_management" / "doctype" / "asset_booking" / "asset_booking.js"
BOOKING_JSON = APP / "asset_management" / "doctype" / "asset_booking" / "asset_booking.json"
INSPECTION_PY = APP / "asset_management" / "doctype" / "rental_inspection" / "rental_inspection.py"
ITEM_JSON = (
	APP / "asset_management" / "doctype" / "rental_inspection_item" / "rental_inspection_item.json"
)
ASSET_JS = APP / "public" / "js" / "asset_management" / "asset_form.js"


def _by_name(path):
	return {row["name"]: row for row in json.loads(path.read_text(encoding="utf-8"))}


def _methods(path, classname):
	"""{method name: ast.FunctionDef} for one class, read without importing."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	for node in tree.body:
		if isinstance(node, ast.ClassDef) and node.name == classname:
			return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
	raise AssertionError(f"class {classname} not found in {path.name}")


def _executable_source(func):
	"""Source of a function with its docstring removed.

	An absence assertion has to strip prose first. The docstring explaining why a handler
	must never touch `schedule_date` necessarily contains the words "schedule_date", so a
	naive `assertNotIn` against the whole function fails on its own documentation — which
	is how this suite failed the first time it ran.
	"""
	body = func.body
	if (
		body
		and isinstance(body[0], ast.Expr)
		and isinstance(body[0].value, ast.Constant)
		and isinstance(body[0].value.value, str)
	):
		body = body[1:]
	return "\n".join(ast.unparse(node) for node in body)


def _module_function(path, name):
	"""A module-level function by name, read without importing."""
	for node in ast.parse(path.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name} not found in {path.name}")


def _calls_in(func):
	"""Bare method names called as self.<name>() inside a function body."""
	found = set()
	for node in ast.walk(func):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
			if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
				found.add(node.func.attr)
	return found


class PurchaseOrderFieldsTest(unittest.TestCase):
	"""ER-2026-312391 and ER-2026-362239."""

	def setUp(self):
		self.fields = _by_name(CUSTOM_FIELDS)
		self.props = _by_name(PROPERTY_SETTERS)

	def test_item_status_has_no_default(self):
		"""A `default` here would stamp a fulfillment claim onto every historical row.

		On a NORMAL doctype (unlike a Single) `ADD COLUMN ... DEFAULT x` makes MariaDB
		write x into every existing row as part of the one ALTER — the v1.280.3 trap. The
		blank option *is* "no status", so the correct value for ~700 untouched Purchase
		Order Item rows is empty, which a defaultless column already gives for free.

		The original AI work breakdown asked for a backfill patch here. There is nothing
		to backfill, and a backfill keyed on emptiness would have matched zero rows,
		committed, and logged itself as successful.
		"""
		field = self.fields["Purchase Order Item-custom_item_status"]
		self.assertIn(field.get("default"), (None, ""), "custom_item_status must ship with no default")
		self.assertTrue(
			field["options"].startswith("\n"),
			"the blank first option is what 'no status' means — do not drop it",
		)

	def test_status_and_delivery_date_survive_submit(self):
		"""Both are useless without allow_on_submit, and it is a one-character regression.

		143 of the 197 Purchase Orders on production are Closed and 22 more are awaiting
		receipt or billing, so every order anyone would track is already submitted. A
		field that locks on submit answers the question for nobody.
		"""
		for name in (
			"Purchase Order Item-custom_item_status",
			"Purchase Order-custom_expected_delivery_date",
		):
			self.assertEqual(self.fields[name].get("allow_on_submit"), 1, f"{name} must be editable after submit")

	def test_the_item_delivery_date_is_the_native_field(self):
		"""`Purchase Order Item.expected_delivery_date` already exists in stock ERPNext v16.

		Date, allow_on_submit, just collapsed out of sight. Creating a Custom Field with
		that fieldname is rejected as a duplicate, so the item half of this feature is a
		Property Setter that surfaces the native field in the grid. The original work
		breakdown asked for the Custom Field and would have failed outright.
		"""
		self.assertNotIn(
			"Purchase Order Item-expected_delivery_date",
			self.fields,
			"do not add a Custom Field shadowing the native expected_delivery_date — "
			"surface it with a Property Setter instead",
		)
		surfaced = self.props.get("Purchase Order Item-expected_delivery_date-in_list_view")
		self.assertIsNotNone(surfaced, "the native expected_delivery_date must be in the item grid")
		self.assertEqual(surfaced["value"], "1")

	def test_the_cascade_is_registered_and_only_fills_blanks(self):
		"""One direction, blanks only — and it must not become the Required By cascade.

		ERPNext already cascades `schedule_date` in
		`buying_controller.validate_schedule_date()`, and does more than cascade it (the
		header is pulled up to the earliest row). A second implementation would fight it.
		"""
		hooks = HOOKS.read_text(encoding="utf-8")
		self.assertIn(
			"erpnext_enhancements.api.procurement.cascade_expected_delivery_date",
			hooks,
			"the cascade must be wired as a Purchase Order before_validate hook",
		)

		func = None
		tree = ast.parse(PROCUREMENT_API.read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name == "cascade_expected_delivery_date":
				func = node
		self.assertIsNotNone(func, "cascade_expected_delivery_date not found in api/procurement.py")

		body = _executable_source(func)
		self.assertNotIn(
			"schedule_date",
			body,
			"this handler must never touch Required By — ERPNext already cascades that one",
		)
		self.assertIn(
			"if not row.get('expected_delivery_date')",
			body.replace('"', "'"),
			"the cascade must fill blank rows only; a per-item override is the point of the request",
		)


class AssetFormTest(unittest.TestCase):
	"""ER-2026-420503."""

	def setUp(self):
		self.props = _by_name(PROPERTY_SETTERS)

	def test_asset_type_defaults_to_existing_asset(self):
		"""This one default is what makes a hand-staged Asset saveable at all.

		`Asset.validate()` calls `validate_gross_and_purchase_amount()`, which returns
		early ONLY for asset_type "Existing Asset" and otherwise throws whenever
		net_purchase_amount != purchase_amount — and purchase_amount is hidden, read-only
		and first written in on_submit. So with a blank asset_type there is no value of
		net_purchase_amount that saves: empty throws "mandatory", anything else throws
		"should be equal to purchase amount".

		It reads like a cosmetic default. It is the fix.
		"""
		prop = self.props.get("Asset-asset_type-default")
		self.assertIsNotNone(prop, "Asset.asset_type must default to Existing Asset")
		self.assertEqual(prop["value"], "Existing Asset")

	def test_draft_dates_are_mandatory_only_at_submit(self):
		"""purchase_date and available_for_use_date must not block a draft.

		ERPNext's own controller agrees about the second one: `validate_in_use_date()` is
		called from `on_submit`, not `validate`, so the doctype's draft-time
		mandatory_depends_on is stricter than the code that enforces it.
		"""
		for field in ("purchase_date", "available_for_use_date"):
			prop = self.props.get(f"Asset-{field}-mandatory_depends_on")
			self.assertIsNotNone(prop, f"Asset.{field} must be mandatory only at submit")
			self.assertEqual(prop["value"], "eval:doc.docstatus==1")

		self.assertEqual(
			self.props["Asset-purchase_date-reqd"]["value"],
			"0",
			"purchase_date's static reqd must be cleared, or mandatory_depends_on never gets a say",
		)

	def test_the_cost_center_fallback_is_on_the_company_not_the_asset(self):
		"""The fifth blocker, and the only one static analysis could not have found.

		`Asset.validate_cost_center()` throws unless the Asset carries a cost centre or
		the Company has a depreciation cost centre — and this site had neither. It is
		invisible in `asset.json` because `cost_center` is not `reqd`; the rule lives in
		the controller and depends on company configuration, so it appears only when you
		actually insert an Asset.

		**The fix must not be a `default` on `Asset.cost_center`.** That was written
		first and deliberately replaced: it worked, but it stamped EVERY Asset with the
		rental fleet's cost centre — right for all ten Assets today and wrong the moment
		a vehicle becomes an Asset, silently, because a default reads as a considered
		choice. The Company field is the fallback ERPNext designed for this, and it
		layers correctly: an Asset that knows its own cost centre keeps it, anything
		else falls back company-wide rather than to a guess about what kind of asset it is.
		"""
		self.assertNotIn(
			"Asset-cost_center-default",
			self.props,
			"do not default Asset.cost_center — it would stamp non-rental assets with "
			"the rental fleet's cost centre. Set Company.depreciation_cost_center instead.",
		)
		self.assertIn(
			"erpnext_enhancements.patches.set_company_depreciation_cost_center",
			PATCHES_TXT.read_text(encoding="utf-8"),
		)

	def test_the_cost_center_patch_never_overwrites_a_finance_decision(self):
		"""Where depreciation posts is Finance's call; this may only fill an empty field.

		And it must reject group cost centres: `validate_cost_center` refuses one
		outright, so seeding a group would swap this blocker for a less obvious one.
		"""
		patch = APP / "patches" / "set_company_depreciation_cost_center.py"
		src = patch.read_text(encoding="utf-8")

		guard = _module_function(patch, "set_depreciation_cost_center")
		body = _executable_source(guard)
		self.assertIn(
			"if frappe.db.get_value",
			body,
			"the patch must read the current value and bail out when it is already set",
		)
		self.assertIn("is_group", src, "a group cost centre must be rejected as a candidate")

	def test_seed_patch_is_registered(self):
		"""Location had zero rows, and Asset.location is reqd — nothing else matters until this runs."""
		self.assertIn(
			"erpnext_enhancements.patches.seed_rental_asset_setup",
			PATCHES_TXT.read_text(encoding="utf-8"),
		)

	def test_item_quick_entry_override_is_scoped_to_the_asset_form(self):
		"""The override is global once loaded; without the guard it leaks across the session.

		`frappe.ui.form.ItemQuickEntryForm` is looked up by name by
		`frappe.ui.form.make_quick_entry`, so an unguarded subclass would make EVERY Item
		created anywhere in the session a non-stock fixed asset. `ControlLink.new_doc()`
		sets `frappe._from_link` immediately before the call, which is what makes the
		guard possible.
		"""
		js = ASSET_JS.read_text(encoding="utf-8")
		self.assertIn("frappe.ui.form.ItemQuickEntryForm", js)
		self.assertIn("frappe._from_link", js, "the quick-entry override must be guarded by _from_link")
		self.assertRegex(
			js,
			r'df\.parent === "Asset"',
			"the guard must require that the dialog was opened from the Asset form",
		)
		self.assertIn("is_fixed_asset: 1", js)
		self.assertIn(
			"is_stock_item: 0",
			js,
			"Item.validate_fixed_asset throws 'must be a non-stock item', and the dialog defaults it to 1",
		)


class RentalInspectionTest(unittest.TestCase):
	"""ER-2026-312370."""

	def test_completeness_gates_are_on_submit_not_validate(self):
		"""Draft must stay cheap; only the sign-off is strict.

		A crew member fills a checklist over several minutes and saves as they go.
		Refusing to save a half-filled draft would repeat the exact mistake that produced
		ER-2026-420503. What must not happen is a *submitted* sheet that is silently
		incomplete, because that reads as "everything came back fine".
		"""
		methods = _methods(INSPECTION_PY, "RentalInspection")
		self.assertIn("before_submit", methods)

		submit_gates = _calls_in(methods["before_submit"])
		self.assertIn("validate_every_row_answered", submit_gates)
		self.assertIn("validate_adverse_rows_explained", submit_gates)

		draft_calls = _calls_in(methods["validate"])
		self.assertNotIn(
			"validate_every_row_answered",
			draft_calls,
			"completeness must not be enforced at draft — it makes the sheet unsaveable mid-walk",
		)
		self.assertIn("set_findings", draft_calls, "the roll-ups must be true on every save")

	def test_generation_refuses_to_produce_an_empty_checklist(self):
		"""An empty inspection submits clean and reads as 'everything accounted for'.

		That is a signed record asserting a complete return on no evidence — the worst
		output this feature could produce, and the same failure direction as the
		trailing-space check in CLAUDE.md: it passes.
		"""
		tree = ast.parse(BOOKING_API.read_text(encoding="utf-8"))
		resolver = None
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name == "_resolve_rows":
				resolver = node
		self.assertIsNotNone(resolver, "_resolve_rows not found in api/booking.py")
		self.assertTrue(
			any(
				isinstance(n, ast.Call)
				and isinstance(n.func, ast.Attribute)
				and n.func.attr == "throw"
				for n in ast.walk(resolver)
			),
			"_resolve_rows must throw when it has no template and no pre-shipping sheet",
		)

	def test_buttons_are_gated_to_rental_bookings(self):
		"""create_composite_booking wraps every rental in a Travel and a Maintenance leg.

		So an ungated button offers a packing checklist three times per rental, on two
		bookings that ship nothing.
		"""
		js = BOOKING_JS.read_text(encoding="utf-8")
		self.assertRegex(
			js,
			r"booking_type\s*!==\s*'Rental'",
			"the inspection buttons must only appear on Rental bookings",
		)
		called = set(re.findall(r"erpnext_enhancements\.api\.booking\.(\w+)", js))
		whitelisted = set()
		for node in ast.parse(BOOKING_API.read_text(encoding="utf-8")).body:
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					call = dec.func if isinstance(dec, ast.Call) else dec
					if isinstance(call, ast.Attribute) and call.attr == "whitelist":
						whitelisted.add(node.name)
		self.assertTrue(called, "no api.booking xcalls found in the JS — did the path change?")
		self.assertFalse(
			sorted(called - whitelisted),
			f"the JS calls methods that are not whitelisted: {sorted(called - whitelisted)}",
		)

	def test_na_is_an_option_and_is_not_a_silent_escape_hatch(self):
		"""N/A must exist, must require a note, and must not count as a shortfall.

		A category-level template covers every fountain of a type, so it necessarily
		lists parts a given unit does not carry. Without N/A the crew's only options were
		to delete the row or mark a real component Missing — filing a false shortfall.

		But N/A is also the ONLY value that removes a row from the findings, which makes
		it exactly what somebody would reach for to make a genuinely missing part stop
		being a problem. So it is in CONDITIONS_NEEDING_A_NOTE: the escape hatch costs a
		written sentence. That pairing is the whole point and is what this pins.
		"""
		options = json.loads(ITEM_JSON.read_text(encoding="utf-8"))
		condition = next(f for f in options["fields"] if f["fieldname"] == "condition")
		self.assertIn("N/A", condition["options"].split("\n"))

		src = INSPECTION_PY.read_text(encoding="utf-8")
		self.assertRegex(
			src,
			r"CONDITIONS_NEEDING_A_NOTE\s*=\s*\(\*ADVERSE_CONDITIONS,\s*NOT_APPLICABLE\)",
			"N/A must require a note, or it becomes a one-click way to erase a shortfall",
		)

		# The note gate must read the wider tuple; the damage roll-up must not.
		methods = _methods(INSPECTION_PY, "RentalInspection")
		note_gate = _executable_source(methods["validate_adverse_rows_explained"])
		self.assertIn("CONDITIONS_NEEDING_A_NOTE", note_gate)

		findings = _executable_source(methods["set_findings"])
		self.assertIn(
			"NOT_APPLICABLE",
			findings,
			"an N/A row must be skipped by the shortfall calculation, or a template row "
			"for a part this fountain never had manufactures a shortfall",
		)

	def test_the_na_constant_agrees_across_both_modules(self):
		"""`api/booking.py` duplicates NOT_APPLICABLE rather than importing the controller.

		Two spellings of the same string would mean the return sheet silently stopped
		dropping N/A rows, with nothing raising anywhere.
		"""
		def literal(path, name):
			for node in ast.parse(path.read_text(encoding="utf-8")).body:
				if isinstance(node, ast.Assign):
					for t in node.targets:
						if isinstance(t, ast.Name) and t.id == name:
							return ast.literal_eval(node.value)
			raise AssertionError(f"{name} not found in {path.name}")

		self.assertEqual(
			literal(INSPECTION_PY, "NOT_APPLICABLE"),
			literal(BOOKING_API, "NOT_APPLICABLE"),
		)

	def test_the_booking_reaches_its_inspections_by_connection(self):
		"""Connections, not two allow_on_submit Link fields the API would have to write back."""
		links = json.loads(BOOKING_JSON.read_text(encoding="utf-8"))["links"]
		self.assertTrue(
			any(
				link.get("link_doctype") == "Rental Inspection"
				and link.get("link_fieldname") == "asset_booking"
				for link in links
			),
			"Asset Booking must list Rental Inspection in its Connections",
		)


if __name__ == "__main__":
	unittest.main()
