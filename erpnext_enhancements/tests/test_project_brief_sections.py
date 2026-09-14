# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The type-specific half of the Project Brief, and the two ways it goes quiet.

``project_enhancements/project_brief.py`` decides which of Sapphire's five lines of
work a Project involves and what each needs on the printed sheet. Both halves of
that fail *silently* -- a brief with a section missing still prints, and a brief
with a blank line still prints -- so neither has a symptom anybody would report.
This suite runs the module rather than reading it.

**What is actually at risk.**

*The section that cannot exist.* ``Products`` is a Value Stream and has never been
a Project Type: the ``seed_delivery_and_products_categories`` patch created the
Project Type ``Delivery`` but only the value streams ``Delivery`` and ``Products``.
Every Products job on prod is ``project_type = "Design"``. So the obvious
simplification -- key the brief on ``project_type``, it is one field instead of a
child table -- deletes the Products brief entirely and leaves every other brief
looking right. It is also lossy for the 21 Design-stage jobs carrying a Build
stream and the 6 the other way round. :meth:`ProjectBriefTypes` pins both.

*The field that quietly stops existing.* Every value on a type section comes from a
Custom Field or a linked doctype's field by name. Rename one in the fixtures and
the brief prints the label with an empty slot next to it, forever, looking exactly
like a project nobody filled in. :class:`ProjectBriefFieldNames` checks every name
the module reads against the JSON that defines it.

**The stub models the framework, not intuition.** ``frappe.get_all`` ignores
permissions; ``frappe.get_list`` applies them. The module therefore gates each
cross-doctype read on an explicit ``frappe.has_permission`` and the stub's
``get_all`` asserts nothing else -- :class:`ProjectBriefPermissions` turns every
gate off and requires the reads not to happen, which is the only way to tell a
permission check from a comment claiming there is one.

Bench-free: its own ``frappe`` stub, its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_project_brief_sections
"""

import ast
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
BRIEF_PY = APP / "project_enhancements/project_brief.py"
CUSTOM_FIELDS_JSON = APP / "fixtures/custom_field.json"
PROJECT_CONTRACT_JSON = APP / "project_enhancements/doctype/project_contract/project_contract.json"
MAINTENANCE_CONTRACT_JSON = (
	APP / "sapphire_maintenance/doctype/sapphire_maintenance_contract" "/sapphire_maintenance_contract.json"
)

#: Rows the stub serves, keyed by doctype. Reset per test.
STATE = {"rows": {}, "docs": {}, "permissions": True, "reads": []}

project_brief = None


# ------------------------------------------------------------------- the stub


class FakeDoc(dict):
	"""A Document as this module uses one: ``.name``, ``.get``, ``.as_dict``."""

	@property
	def name(self):
		return self.get("name")

	def as_dict(self):
		return dict(self)


def _matches(row, filters):
	"""The subset of frappe's filter grammar this module actually uses."""
	for field, condition in (filters or {}).items():
		value = row.get(field)
		if isinstance(condition, (list, tuple)):
			operator, target = condition
			if operator == "<":
				if not (value or 0) < target:
					return False
			elif operator == "!=":
				if value == target:
					return False
			elif operator == "in":
				if value not in target:
					return False
			else:
				raise AssertionError(f"stub does not implement operator {operator!r}")
		elif value != condition:
			return False
	return True


def _install_stub():
	fake = types.ModuleType("frappe")

	def has_permission(doctype, ptype="read", **kwargs):
		return bool(STATE["permissions"])

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		STATE["reads"].append(doctype)
		rows = [r for r in STATE["rows"].get(doctype, []) if _matches(r, filters)]
		if pluck:
			return [r.get(pluck) for r in rows]
		if fields:
			return [{f: r.get(f) for f in fields} for r in rows]
		return [dict(r) for r in rows]

	def get_doc(doctype, name):
		STATE["reads"].append(doctype)
		return FakeDoc(STATE["docs"][(doctype, name)])

	fake.has_permission = has_permission
	fake.get_all = get_all
	fake.get_doc = get_doc
	fake.db = types.SimpleNamespace(
		get_value=lambda doctype, name, field: STATE["docs"].get((doctype, name), {}).get(field)
	)
	sys.modules["frappe"] = fake

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda value, *a: float(value or 0)
	utils.strip_html = lambda text: str(text or "")
	fake.utils = utils
	sys.modules["frappe.utils"] = utils


def setUpModule():
	"""Load the module from its file, not through its package.

	``project_enhancements/__init__.py`` pulls in the rest of the module's server
	code, which needs a far larger slice of frappe than the brief does. Importing
	by path keeps the stub honest: it only has to model what *this* file calls.
	"""
	global project_brief

	_install_stub()
	spec = importlib.util.spec_from_file_location("ee_project_brief_under_test", BRIEF_PY)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	project_brief = module


def _project(**fields):
	"""A Project carrying only what a test sets, like a sparsely-filled real one."""
	doc = FakeDoc({"name": "PRJ-00001", "project_type": ""})
	doc.update(fields)
	return doc


def _streams(*names):
	"""``custom_value_stream`` rows, as the Table MultiSelect stores them."""
	return [FakeDoc({"value_stream": name}) for name in names]


def _blocks(sections, section_type):
	for section in sections:
		if section["type"] == section_type:
			return {block["title"]: block for block in section["blocks"]}
	return {}


class ProjectBriefTestCase(unittest.TestCase):
	def setUp(self):
		STATE["rows"] = {}
		STATE["docs"] = {}
		STATE["permissions"] = True
		STATE["reads"] = []


# ------------------------------------------------------------------- which sections


class ProjectBriefTypes(ProjectBriefTestCase):
	"""Which lines of work a Project involves, and why one field cannot say."""

	def test_products_comes_only_from_the_value_stream(self):
		"""The case that a ``project_type``-only brief would delete outright.

		Every Products job on prod is stage Design. Reading ``project_type`` alone
		is not a partial answer here, it is no answer.
		"""
		doc = _project(project_type="Design", custom_value_stream=_streams("Products"))
		self.assertEqual(project_brief.applicable_types(doc), ["Design", "Products"])

	def test_a_job_spanning_two_streams_gets_both_sections(self):
		doc = _project(project_type="Design", custom_value_stream=_streams("Build"))
		self.assertEqual(project_brief.applicable_types(doc), ["Design", "Build"])

		sections = project_brief.brief_sections(doc)
		self.assertEqual([s["type"] for s in sections], ["Design", "Build"])

	def test_the_declared_stage_leads(self):
		"""``project_type`` first, then the rest in canonical order."""
		doc = _project(project_type="Events", custom_value_stream=_streams("Design", "Events"))
		self.assertEqual(project_brief.applicable_types(doc), ["Events", "Design"])

	def test_the_second_link_field_on_the_child_row_is_read_too(self):
		"""The child doctype carries `value_stream` AND `value_streams`.

		Live rows use the first; a row written through the other must not vanish
		from the brief.
		"""
		doc = _project(custom_value_stream=[FakeDoc({"value_streams": "Service"})])
		self.assertEqual(project_brief.applicable_types(doc), ["Service"])

	def test_an_internal_job_gets_no_sections_at_all(self):
		"""Internal, Overhead, Delivery and untyped jobs leave the brief as it was.

		73 prod Projects have no ``project_type``; the brief must still print for
		them, unchanged, rather than growing five empty headings.
		"""
		for stage in ("", "Internal", "Overhead", "Other", "Delivery"):
			with self.subTest(stage=stage):
				doc = _project(project_type=stage)
				self.assertEqual(project_brief.applicable_types(doc), [])
				self.assertEqual(project_brief.brief_sections(doc), [])

	#: The block each stream's section is anchored on, given the data that stream
	#: is about. Build, Events, Products and Design anchor on a block that renders
	#: blank, so every such job reads as one; Service has no Service-specific
	#: fields on Project at all, so its section appears only once somebody has
	#: written a scope row or raised an agreement. That asymmetry is a judgement
	#: call, not an oversight -- 338 of the 354 Service jobs on prod have neither.
	ANCHORS = {
		"Design": ({}, "Design Phases & Fees"),
		"Build": ({}, "Production & Budget"),
		"Products": ({}, "Supply & Billing"),
		"Events": ({}, "Event Schedule"),
		"Service": (
			{
				"custom_service_customer_requests": [
					FakeDoc({"service_customer_requests": "Weekly chemistry check"})
				]
			},
			"Customer Requests",
		),
	}

	def test_a_service_job_with_nothing_written_on_it_gets_no_section(self):
		"""The asymmetry above, stated as a fact so it survives a tidy-up.

		Service is the one stream with no fields of its own on Project. Making its
		section unconditional would mean printing twelve blank lines of somebody
		else's maintenance agreement on 338 of the 354 Service jobs on prod.
		"""
		doc = _project(project_type="Service")
		self.assertEqual(project_brief.brief_sections(doc), [])

	def test_every_declared_type_builds_its_anchor_block(self):
		"""No entry in BRIEF_TYPES without a builder, and none without content."""
		for stream in project_brief.BRIEF_TYPES:
			fields, anchor = self.ANCHORS[stream]
			with self.subTest(stream=stream):
				doc = _project(project_type="", custom_value_stream=_streams(stream), **fields)
				sections = project_brief.brief_sections(doc)
				self.assertEqual([s["type"] for s in sections], [stream])
				self.assertIn(anchor, _blocks(sections, stream))


# ------------------------------------------------------------------- what a section holds


class ProjectBriefBlocks(ProjectBriefTestCase):
	"""The fillable-form contract: which blocks survive an empty project."""

	def test_the_projects_own_fields_print_blank(self):
		"""An Events sheet with four blank slots is the point of the sheet.

		The printed original left a slot for everything ERPNext had no source for,
		and somebody wrote the setup time onto it.
		"""
		doc = _project(project_type="Events")
		blocks = _blocks(project_brief.brief_sections(doc), "Events")

		self.assertIn("Event Schedule", blocks)
		self.assertEqual([row["value"] for row in blocks["Event Schedule"]["rows"]], [None, None, None, None])

	def test_lists_tables_and_text_disappear_when_empty(self):
		"""A table of equipment nobody listed is not a form, it is an empty grid."""
		doc = _project(project_type="Events")
		blocks = _blocks(project_brief.brief_sections(doc), "Events")

		self.assertNotIn("Equipment", blocks)
		self.assertNotIn("Customer Requests", blocks)
		self.assertNotIn("Notes for Scheduling", blocks)

	def test_a_linked_documents_fields_disappear_with_the_document(self):
		"""No rental contract means no rental agreement, not an unfilled one.

		Every one of the 16 Project Contracts on prod is ``maintenance``, so
		without this distinction every Events brief in the building would carry
		nine blank currency lines under a "Rental Terms" heading for an agreement
		that does not exist. The Events dates above it still print.
		"""
		doc = _project(project_type="Events")
		blocks = _blocks(project_brief.brief_sections(doc), "Events")

		self.assertNotIn("Rental Terms", blocks)
		self.assertIn("Event Schedule", blocks)

	def test_a_fully_received_order_is_not_an_open_one(self):
		"""Status alone says it is: a delivered order sits at ``To Bill``.

		PRJ-00759 carries three of them. "Open Purchase Orders" has to mean what
		it says on a sheet somebody takes to a site.
		"""
		STATE["rows"] = {
			"Purchase Order": [
				{
					"name": "PO-2026-00289",
					"project": "PRJ-00001",
					"docstatus": 1,
					"status": "To Bill",
					"per_received": 100.0,
					"grand_total": 162.12,
				},
				{
					"name": "PO-2026-00290",
					"project": "PRJ-00001",
					"docstatus": 1,
					"status": "To Receive and Bill",
					"per_received": 0.0,
					"grand_total": 284.61,
				},
			],
			"Purchase Order Item": [],
		}
		doc = _project(project_type="Build")
		block = _blocks(project_brief.brief_sections(doc), "Build")["Open Purchase Orders"]
		self.assertEqual([row[0] for row in block["rows"]], ["PO-2026-00290"])

	def test_the_events_dates_and_their_notes_ride_on_one_line(self):
		"""Reading a time without its note is how a truck reaches a locked gate."""
		doc = _project(
			project_type="Events",
			custom_setup_date_time="2026-07-04 08:00:00",
			custom_setup_date_time_notes="Gate code 4412, service lift only",
		)
		schedule = _blocks(project_brief.brief_sections(doc), "Events")["Event Schedule"]
		setup = next(row for row in schedule["rows"] if row["label"] == "Setup")

		self.assertEqual(setup["value"], "2026-07-04 08:00:00")
		self.assertEqual(setup["note"], "Gate code 4412, service lift only")
		self.assertEqual(setup["format"], "datetime")

	def test_scope_rows_become_bullets(self):
		doc = _project(
			project_type="Design",
			custom_design_customer_requests=[
				FakeDoc({"design_customer_requests": "Three-tier basin"}),
				FakeDoc({"design_customer_requests": "   "}),
			],
		)
		blocks = _blocks(project_brief.brief_sections(doc), "Design")
		self.assertEqual(blocks["Customer Requests"]["items"], ["Three-tier basin"])

	def test_a_long_table_is_truncated_and_says_so(self):
		"""A partial sheet that reads as the whole list is the failure to avoid."""
		orders = [
			{
				"name": f"PUR-ORD-{index:04d}",
				"project": "PRJ-00001",
				"docstatus": 1,
				"status": "To Receive and Bill",
				"grand_total": 100,
			}
			for index in range(project_brief.MAX_TABLE_ROWS + 5)
		]
		STATE["rows"] = {"Purchase Order": orders, "Purchase Order Item": []}

		doc = _project(project_type="Build")
		block = _blocks(project_brief.brief_sections(doc), "Build")["Open Purchase Orders"]

		self.assertEqual(len(block["rows"]), project_brief.MAX_TABLE_ROWS)
		self.assertEqual(block["truncated"], 5)

	def test_a_job_with_no_invoices_shows_blanks_not_zero(self):
		"""``$0.00`` claims "billed nothing"; a blank says "not billed yet"."""
		doc = _project(project_type="", custom_value_stream=_streams("Products"))
		billing = _blocks(project_brief.brief_sections(doc), "Products")["Supply & Billing"]
		invoiced = next(row for row in billing["rows"] if row["label"] == "Invoiced to Date")
		self.assertIsNone(invoiced["value"])


class ProjectBriefContractMatching(ProjectBriefTestCase):
	"""One doctype, every template's fields, only one template's values filled."""

	def _contract(self, template, **fields):
		row = {"name": "SF-X-0001", "project": "PRJ-00001", "contract_template": template}
		row.update({"docstatus": 0, "status": "Draft"})
		STATE["rows"] = {"Project Contract": [row]}
		STATE["docs"] = {("Project Contract", "SF-X-0001"): dict(row, **fields)}

	def test_a_section_reads_only_the_templates_it_names(self):
		"""A maintenance contract must not fill in "Rental Terms".

		Project Contract carries the rental fields whatever template it was raised
		from, so a fallback to "the most recent contract" would print a rental fee
		schedule made entirely of another agreement's zeros -- under a heading that
		asserts they are this job's rental terms.
		"""
		self._contract("maintenance", base_rental_fee=4200, annual_maintenance_fee=9000)

		doc = _project(project_type="Events")
		self.assertNotIn("Rental Terms", _blocks(project_brief.brief_sections(doc), "Events"))

	def test_the_matching_template_does_fill_it_in(self):
		self._contract("rental", base_rental_fee=4200, total_rental_amount=5100)

		doc = _project(project_type="Events")
		rental = _blocks(project_brief.brief_sections(doc), "Events")["Rental Terms"]
		base = next(row for row in rental["rows"] if row["label"] == "Base Rental Fee")
		self.assertEqual(base["value"], 4200)

	def test_template_key_is_matched_as_well_as_the_link(self):
		"""Both columns name the template; a contract setting either must match."""
		row = {
			"name": "SF-X-0002",
			"project": "PRJ-00001",
			"contract_template": None,
			"template_key": "owner",
			"docstatus": 0,
			"status": "Draft",
		}
		STATE["rows"] = {"Project Contract": [row]}
		STATE["docs"] = {("Project Contract", "SF-X-0002"): dict(row, design_retainer=2500)}

		doc = _project(project_type="Design")
		fees = _blocks(project_brief.brief_sections(doc), "Design")["Design Phases & Fees"]
		retainer = next(row for row in fees["rows"] if row["label"].startswith("Design Retainer"))
		self.assertEqual(retainer["value"], 2500)


class ProjectBriefPermissions(ProjectBriefTestCase):
	"""``get_all`` ignores permissions, so the gate has to be written by hand."""

	def test_no_cross_doctype_read_happens_without_read_permission(self):
		"""The brief is whitelisted and login-only.

		It carries contract fees, committed purchase-order value and maintenance
		terms, so a reader who cannot open those doctypes gets a section short --
		not a section full of somebody else's numbers.
		"""
		STATE["permissions"] = False
		doc = _project(
			project_type="Events",
			custom_value_stream=_streams("Build", "Service", "Products", "Design"),
		)

		sections = project_brief.brief_sections(doc)

		self.assertEqual(STATE["reads"], [])
		self.assertTrue(sections, "the Project's own fields still render")

	def test_a_doctype_this_site_has_not_got_costs_a_section_not_a_traceback(self):
		"""``has_permission`` raises for an unknown doctype, it does not say False.

		It loads the doctype's meta to answer, and this module reaches into five
		doctypes across four modules of this app. The gate therefore sits inside
		its own try -- without which a site missing one of them would get an
		exception out of the brief button rather than a shorter brief.
		"""

		def explode(doctype, ptype="read", **kwargs):
			raise RuntimeError(f"DocType {doctype} not found")

		frappe = sys.modules["frappe"]
		original = frappe.has_permission
		frappe.has_permission = explode
		try:
			doc = _project(
				project_type="Events",
				custom_value_stream=_streams("Build", "Service", "Products", "Design"),
			)
			sections = project_brief.brief_sections(doc)
		finally:
			frappe.has_permission = original

		self.assertEqual(STATE["reads"], [])
		self.assertTrue(sections, "the Project's own fields still render")


# ------------------------------------------------------------------- field names


def _fieldnames(doctype_json):
	data = json.loads(doctype_json.read_text(encoding="utf-8"))
	return {field["fieldname"] for field in data.get("fields", [])}


def _project_custom_fieldnames():
	rows = json.loads(CUSTOM_FIELDS_JSON.read_text(encoding="utf-8"))
	return {row["fieldname"] for row in rows if row.get("dt") == "Project"}


def _gets_on(tree, variable):
	"""Every literal key passed to ``<variable>.get(...)`` in ``tree``.

	AST, so a fieldname appearing in a docstring or a comment is not collected --
	which matters because the module's prose names several of the fields it reads
	and one it deliberately does not.
	"""
	keys = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
			continue
		if node.func.attr != "get" or not node.args:
			continue
		target = node.func.value
		if not isinstance(target, ast.Name) or target.id != variable:
			continue
		if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
			keys.add(node.args[0].value)
	return keys


class ProjectBriefFieldNames(ProjectBriefTestCase):
	"""A renamed field blanks a brief line and prints the label anyway."""

	@classmethod
	def setUpClass(cls):
		cls.tree = ast.parse(BRIEF_PY.read_text(encoding="utf-8"))

	def test_every_project_field_read_exists_on_project(self):
		known = _project_custom_fieldnames() | {
			# The standard Project fields the module reads.
			"project_type",
			"custom_value_stream",
		}
		for fieldname in _gets_on(self.tree, "doc"):
			with self.subTest(fieldname=fieldname):
				self.assertIn(fieldname, known)

	def test_every_scope_child_table_and_column_exists(self):
		project_fields = _project_custom_fieldnames()
		for stream, mapping in project_brief.SCOPE_TABLES.items():
			table_field, column, deliverables_field, deliverables_column = mapping
			with self.subTest(stream=stream):
				self.assertIn(table_field, project_fields)
				self.assertIn(deliverables_field, project_fields)
				# The child doctypes are single-column tables whose one column is
				# named after the table -- assert it, because the brief reads that
				# column by name and would silently bullet nothing if it moved.
				self.assertEqual(column, table_field[len("custom_") :])
				self.assertEqual(deliverables_column, deliverables_field[len("custom_") :])

	def test_every_project_contract_field_read_exists(self):
		known = _fieldnames(PROJECT_CONTRACT_JSON)
		for fieldname in _gets_on(self.tree, "contract"):
			with self.subTest(fieldname=fieldname):
				self.assertIn(fieldname, known)

	def test_every_maintenance_agreement_field_read_exists(self):
		known = _fieldnames(MAINTENANCE_CONTRACT_JSON) | {"name"}
		for fieldname in _gets_on(self.tree, "agreement"):
			with self.subTest(fieldname=fieldname):
				self.assertIn(fieldname, known)

	def test_products_does_not_read_the_events_gated_delivery_date(self):
		"""``custom_delivery_date_time`` is inside a section gated to Events.

		Its section break's ``depends_on`` is ``project_type == 'Events'`` and every
		Products job on prod is stage Design, so the field is invisible on the form
		it would come from. Printing a date nobody can correct is worse than a gap.

		Collected by AST, not by grepping: the function's own docstring names the
		field in the course of explaining why it is absent, and a text search would
		match that and pass while the code read it.
		"""
		products = next(
			node
			for node in ast.walk(self.tree)
			if isinstance(node, ast.FunctionDef) and node.name == "_products_blocks"
		)
		self.assertNotIn("custom_delivery_date_time", _gets_on(products, "doc"))


if __name__ == "__main__":
	unittest.main()
