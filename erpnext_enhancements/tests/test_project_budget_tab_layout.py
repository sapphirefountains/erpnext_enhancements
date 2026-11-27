"""The Project form's Budget tab and the three tabs ERPNext 16.60 added (v1.845.2, 2026-11-25).

ERPNext 16.60.0 gave Project nine Tab Breaks where 16.55 had four: Overview (frappe/erpnext#60646),
Tasks (#59586) and Budgets (#63053), plus Details, More Info and a few fields inside them. Our
``Project-main-field_order`` Property Setter did not list any of them, so ``Meta.sort_fields`` placed
each new field after the standard field that precedes it in ERPNext's own order. That put the Tasks
tab after ``cost_center`` and the Budgets tab after ``per_gross_margin``: in the middle of our Budget
tab. The procurement tracker, the procurement buttons, Material Pickup and Budget Notes all ended up
on ERPNext's Budgets tab, and its "Accounting" section took the Total Time Elapsed field. Overview,
prepended as the first tab, became the tab every Project opened on.

Nikolas, 2026-11-25: procurement tracker at the top of the Budget tab, the rest by use (he approved
the order below, which was ranked from six months of prod data). Hide Overview, hide Tasks, and put
ERPNext's Budgets list at the bottom of the Budget tab.

Frappe v16 lays a form out from the ``field_order`` array (``frappe/model/meta.py``,
``Meta.sort_fields``). So this suite pins that array, and checks that every ``insert_after`` on the
tab agrees with it, so the next edit cannot be misled by a stale value.

Bench-free: reads the fixture files, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_project_budget_tab_layout
"""

import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
FIXTURES = APP / "fixtures"

#: Fields ERPNext 16.60.0 added to Project. A field missing from ``field_order`` is placed by
#: ``sort_fields`` after its ERPNext neighbour, which is how two of these tabs ended up splitting
#: the Budget tab.
UPSTREAM_16_60_FIELDS = (
	"overview_tab",
	"overview_dashboard",
	"details_tab",
	"accounting_section",
	"column_break_accounting",
	"tasks_tab",
	"tasks_html",
	"costing_dashboard",
	"budgets_tab",
	"budgets_html",
	"section_break_sjmx",
	"users_tab",
)

#: The Budget tab's visible sections, top to bottom, with the field that matters in each.
BUDGET_SECTIONS = (
	("custom_section_break_xbww4", "custom_material_request_feed"),  # procurement tracker
	("custom_procurement_section", "custom_btn_material_request"),
	("custom_pickup_routing_section", "custom_btn_pick_routing_map"),
	("custom_project_estimated_amount", "custom_project_dollar_amount"),
	("custom_budget_notes", "custom_notes_relating_to_budget"),
	("custom_project_estimated_cost", "custom_materials_budget"),
	("custom_customer_payment_section", "custom_payment_received"),
	("custom_initial_invoice_section", "custom_first_invoice_percentage"),
	("custom_budget_lines_section", "custom_budget_lines"),
	("custom_account_budgets_section", "budgets_html"),
)

#: Sections that sit after the visible ones on the Budget tab. Each is hidden or holds nothing.
HIDDEN_TAIL = ("custom_section_break_kyvkd", "accounting_section", "project_details", "margin")

#: Nik's answers: hidden, never deleted (Custom Fields and standard fields alike).
HIDDEN_TABS = ("overview_tab", "tasks_tab", "budgets_tab")

#: Every field in ERPNext v16.60.0's ``projects/doctype/project/project.json``, in its own order
#: (identical at v16.61.0). CI has no ERPNext checkout, so the list is copied here; refresh it from the
#: tag prod runs after an upgrade, and extend ``UPSTREAM_16_60_FIELDS`` with what is new.
STANDARD_16_60 = (
	"overview_tab", "overview_dashboard", "details_tab", "naming_series", "project_name", "status",
	"project_type", "percent_complete_method", "column_break_5", "project_template", "priority",
	"department", "is_active", "percent_complete", "section_break_18", "expected_start_date",
	"expected_end_date", "column_break_20", "actual_start_date", "actual_end_date", "actual_time",
	"accounting_section", "company", "estimated_costing", "column_break_accounting", "cost_center",
	"tasks_tab", "tasks_html", "more_info_tab", "customer_details", "customer", "column_break_14",
	"sales_order", "section_break0", "notes", "costing_tab", "costing_dashboard", "project_details",
	"total_costing_amount", "total_purchase_cost", "column_break_28", "total_sales_amount",
	"total_billable_amount", "total_billed_amount", "total_consumed_material_cost", "margin",
	"gross_margin", "column_break_37", "per_gross_margin", "budgets_tab", "budgets_html",
	"monitor_progress_tab", "section_break_sjmx", "collect_progress", "holiday_list", "frequency",
	"from_time", "to_time", "first_email", "second_email", "daily_time_to_send", "day_to_send",
	"weekly_time_to_send", "column_break_45", "subject", "message", "users_tab", "users_section",
	"users", "copied_from", "connections_tab",
)  # fmt: skip

#: The 26 Project Custom Fields prod holds outside the fixtures (``is_system_generated = 1``; checked
#: 2026-11-25), each with the file that creates it, or None for the six the 2026-04 contact work left
#: on the site and no current code creates (``migrate_contact_data`` only copies values into them).
CODE_OWNED = {
	"custom_procurement_section": "patches/reorder_procurement_buttons.py",
	"custom_btn_material_request": "patches/reorder_procurement_buttons.py",
	"custom_btn_purchase_order": "patches/reorder_procurement_buttons.py",
	"custom_col_break_1": "patches/reorder_procurement_buttons.py",
	"custom_btn_request_quote": "patches/reorder_procurement_buttons.py",
	"custom_btn_purchase_receipt": "patches/reorder_procurement_buttons.py",
	"custom_col_break_2": "patches/reorder_procurement_buttons.py",
	"custom_btn_supplier_quotation": "patches/reorder_procurement_buttons.py",
	"custom_btn_purchase_invoice": "patches/reorder_procurement_buttons.py",
	"custom_pickup_routing_section": "patches/add_project_pick_routing_button.py",
	"custom_btn_pick_routing_map": "patches/add_project_pick_routing_button.py",
	"primary_contact": "setup/custom_fields.py",
	"section_break_contacts": "setup/custom_fields.py",
	"contact_list_html": "setup/custom_fields.py",
	"section_break_map": "setup/custom_fields.py",
	"primary_address": "setup/custom_fields.py",
	"location_map_col_break": "setup/custom_fields.py",
	"location_map_html": "setup/custom_fields.py",
	"section_break_address_list": "setup/custom_fields.py",
	"address_list_html": "setup/custom_fields.py",
	"primary_contact_job_title": None,
	"primary_contact_phone": None,
	"primary_contact_section": None,
	"primary_contact_col_break": None,
	"primary_contact_email": None,
	"address_and_contact": None,
}

#: Breaks and fields whose type the tests need; the patch-created ones are not in the fixtures.
SECTION_BREAKS_NOT_IN_FIXTURES = {
	"custom_procurement_section",
	"custom_pickup_routing_section",
	"accounting_section",
	"project_details",
	"margin",
}


def _property_setters():
	return {r["name"]: r for r in json.loads((FIXTURES / "property_setter.json").read_text(encoding="utf-8"))}


def _custom_fields():
	rows = json.loads((FIXTURES / "custom_field.json").read_text(encoding="utf-8"))
	return {r["fieldname"]: r for r in rows if r["dt"] == "Project"}


def _field_order():
	return json.loads(_property_setters()["Project-main-field_order"]["value"])


def _budget_tab(order):
	return order[order.index("custom_budget") : order.index("costing_tab")]


def _is_section_break(name, fields):
	if name in fields:
		return fields[name]["fieldtype"] == "Section Break"
	return name in SECTION_BREAKS_NOT_IN_FIXTURES


class TestTheUpstreamFieldsArePlaced(unittest.TestCase):
	def test_every_16_60_field_is_in_the_array(self):
		order = _field_order()
		for name in UPSTREAM_16_60_FIELDS:
			self.assertIn(name, order, name)
		self.assertEqual(len(order), len(set(order)), "field_order has a duplicate")

	def test_every_entry_in_the_array_is_a_real_field(self):
		"""sort_fields drops a name it does not know, so a stale entry is silent; this makes it loud."""
		known = set(STANDARD_16_60) | set(_custom_fields()) | set(CODE_OWNED)
		unknown = [name for name in _field_order() if name not in known]
		self.assertEqual(unknown, [])

	def test_the_code_owned_fields_are_where_the_list_says(self):
		for fieldname, source in CODE_OWNED.items():
			self.assertNotIn(fieldname, _custom_fields(), f"{fieldname} is in the fixtures now")
			if source:
				text = (APP / source).read_text(encoding="utf-8")
				self.assertIn(f'"{fieldname}"', text, f"{fieldname} not created in {source}")

	def test_the_standard_list_holds_every_16_60_field(self):
		for name in UPSTREAM_16_60_FIELDS:
			self.assertIn(name, STANDARD_16_60, name)
		self.assertEqual(len(STANDARD_16_60), len(set(STANDARD_16_60)))

	def test_details_is_the_first_tab(self):
		"""With Details first, sort_fields prepends nothing and Projects open on Details."""
		self.assertEqual(_field_order()[0], "details_tab")

	def test_the_hidden_tabs_hold_only_their_own_field_and_sit_at_the_end(self):
		order = _field_order()
		at = order.index("overview_tab")
		self.assertEqual(
			order[at:],
			["overview_tab", "overview_dashboard", "tasks_tab", "tasks_html", "budgets_tab", "connections_tab"],
		)

	def test_overview_tasks_and_budgets_tabs_are_hidden(self):
		setters = _property_setters()
		for field in HIDDEN_TABS:
			row = setters[f"Project-{field}-hidden"]
			self.assertEqual(
				(row["doc_type"], row["field_name"], row["property"], row["value"]),
				("Project", field, "hidden", "1"),
				field,
			)

	def test_the_other_new_fields_land_where_they_are_empty_or_hidden(self):
		order = _field_order()
		self.assertEqual(order[order.index("costing_tab") + 1], "costing_dashboard")
		self.assertEqual(order[order.index("monitor_progress_tab") + 1], "section_break_sjmx")
		# users_tab ("More Info") is followed straight by another tab, so it is empty and Frappe hides it.
		self.assertEqual(order[order.index("users_tab") + 1], "more_info_tab")


class TestTheBudgetTab(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.order = _field_order()
		cls.tab = _budget_tab(cls.order)
		cls.fields = _custom_fields()

	def test_the_procurement_tracker_comes_first(self):
		self.assertEqual(self.tab[:3], ["custom_budget", "custom_section_break_xbww4", "custom_material_request_feed"])

	def test_the_sections_run_in_the_approved_order(self):
		sections = [name for name in self.tab if _is_section_break(name, self.fields)]
		self.assertEqual(tuple(sections), tuple(s for s, _ in BUDGET_SECTIONS) + HIDDEN_TAIL)
		for section, field in BUDGET_SECTIONS:
			at = self.tab.index(section)
			following = self.tab[at + 1 :]
			nxt = next((i for i, n in enumerate(following) if _is_section_break(n, self.fields)), len(following))
			self.assertIn(field, following[:nxt], f"{field} not under {section}")

	def test_no_tab_break_inside_the_budget_tab(self):
		for name in self.tab[1:]:
			self.assertNotIn(name, ("tasks_tab", "budgets_tab", "overview_tab", "details_tab"), name)
			if name in self.fields:
				self.assertNotEqual(self.fields[name]["fieldtype"], "Tab Break", name)

	def test_the_visible_sections_are_not_hidden(self):
		for section, _ in BUDGET_SECTIONS:
			if section in self.fields:
				self.assertEqual(self.fields[section]["hidden"], 0, section)

	def test_the_tail_stays_hidden_or_empty(self):
		setters = _property_setters()
		self.assertEqual(self.fields["custom_section_break_kyvkd"]["hidden"], 1)
		self.assertEqual(setters["Project-project_details-hidden"]["value"], "1")
		self.assertEqual(setters["Project-margin-hidden"]["value"], "1")
		# ERPNext's Accounting section is left with no fields at all: a column break, then the next section.
		at = self.tab.index("accounting_section")
		self.assertEqual(self.tab[at + 1 : at + 3], ["column_break_accounting", "project_details"])

	def test_total_time_elapsed_is_back_in_project_estimated_amount(self):
		at = self.tab.index("custom_project_estimated_amount")
		self.assertEqual(
			self.tab[at : at + 6],
			[
				"custom_project_estimated_amount",
				"custom_project_dollar_amount",
				"actual_time",
				"custom_column_break_dgj8s",
				"custom_project_spend",
				"custom_total_time_elapsed",
			],
		)

	def test_the_account_budgets_section(self):
		section = self.fields["custom_account_budgets_section"]
		self.assertEqual((section["fieldtype"], section["label"]), ("Section Break", "Account Budgets"))
		self.assertEqual(section["hidden"], 0)
		self.assertEqual(self.tab[self.tab.index("custom_account_budgets_section") + 1], "budgets_html")

	def test_nothing_outside_the_array_is_placed_inside_the_tab(self):
		"""A Custom Field left out of the array is placed by its insert_after. Pointing into this tab, a
		Section Break would take every field after it, up to the next break, under its heading."""
		tab = set(self.tab)
		strays = sorted(
			name
			for name, row in self.fields.items()
			if name not in self.order and row.get("insert_after") in tab
		)
		self.assertEqual(strays, [], "add these to Project-main-field_order explicitly")

	def test_insert_after_agrees_with_the_array(self):
		"""v16 places by the array; an insert_after that disagrees is a trap for the next edit."""
		for name in self.tab[1:]:
			if name not in self.fields:
				continue
			with self.subTest(field=name):
				after = self.fields[name]["insert_after"]
				self.assertEqual(self.tab.index(after), self.tab.index(name) - 1)


if __name__ == "__main__":
	unittest.main()
