# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The two v1.532.0 seed patches: what each writes, and what each must never overwrite. Bench-free.

* ``seed_naming_digest_recipient`` puts the Purchasing Agent on the Monday naming digest. Its
  whole value is the two branches it does NOT write: a Single that has never been saved (one
  written row would stop it loading its declared defaults), and a field that already has a
  row, even an empty one (somebody emptied the list to stop the email; that is a decision).
* ``seed_inventory_kpi_targets`` gives Nik's approved targets to the inventory and new-item
  naming KPIs. A row that exists wins, and every key must be one its department's snapshot
  actually publishes, graded in the direction the snapshot grades it -- a target on a
  misspelt key sits in the table grading nothing, and nobody sees it.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_inventory_seed_patches
"""

import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SNAPSHOTS = APP / "kpi_dashboards" / "snapshots.py"
PATCHES_TXT = APP / "patches.txt"

STATE = {}
recipient = None
targets = None


def _reset():
	STATE.clear()
	STATE.update(
		{
			"doctypes": {"Inventory Scanner Settings", "KPI Target"},
			"has_field": True,
			"singles_rows": [],
			"written": [],
			"targets": set(),
			"inserted": [],
			"fail_insert": set(),
			"errors": [],
		}
	)


class _Meta:
	def get_field(self, fieldname):
		return object() if STATE["has_field"] else None


class _Doc:
	def __init__(self, data):
		self.data = data

	def insert(self, ignore_permissions=False):
		name = f"TGT-{self.data['department']}-{self.data['kpi_key']}-{self.data['period']}"
		if name in STATE["fail_insert"]:
			raise RuntimeError("insert failed")
		STATE["inserted"].append(self.data)
		STATE["targets"].add(name)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def exists(doctype, name=None):
		if doctype == "DocType":
			return name in STATE["doctypes"]
		if doctype == "KPI Target":
			return name in STATE["targets"]
		raise AssertionError(doctype)

	def sql(query, params=None):
		assert "tabSingles" in query and "field" in query, query
		return [(f,) for f in STATE["singles_rows"]]

	def set_single_value(doctype, field, value):
		STATE["written"].append((doctype, field, value))

	frappe.db = types.SimpleNamespace(
		exists=exists,
		sql=sql,
		set_single_value=set_single_value,
		commit=lambda: None,
		rollback=lambda: None,
	)
	frappe.get_meta = lambda doctype: _Meta()
	frappe.get_doc = lambda data: _Doc(data)
	frappe.clear_document_cache = lambda *a, **k: None
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	sys.modules["frappe"] = frappe


def setUpModule():
	global recipient, targets
	_install_frappe_stub()
	for name in (
		"erpnext_enhancements.patches.seed_naming_digest_recipient",
		"erpnext_enhancements.patches.seed_inventory_kpi_targets",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.patches import seed_inventory_kpi_targets as t
	from erpnext_enhancements.patches import seed_naming_digest_recipient as r

	recipient = r
	targets = t


class DigestRecipientSeedTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_saved_single_without_the_field_gets_the_address_once(self):
		STATE["singles_rows"] = ["camera_enabled", "default_warehouse"]
		self.assertTrue(recipient.seed())
		self.assertEqual(
			STATE["written"],
			[
				(
					"Inventory Scanner Settings",
					"naming_digest_recipients",
					"parker.bailey@sapphirefountains.com",
				)
			],
		)

	def test_a_never_saved_single_is_left_alone(self):
		STATE["singles_rows"] = []
		self.assertFalse(recipient.seed())
		self.assertEqual(STATE["written"], [])

	def test_an_existing_row_wins_even_an_empty_one(self):
		"""The patch sees field names only, so an emptied list is a row like any other."""
		STATE["singles_rows"] = ["camera_enabled", "naming_digest_recipients"]
		self.assertFalse(recipient.seed())
		self.assertEqual(STATE["written"], [])

	def test_no_field_or_no_doctype_writes_nothing(self):
		STATE["singles_rows"] = ["camera_enabled"]
		STATE["has_field"] = False
		self.assertFalse(recipient.seed())
		STATE["has_field"] = True
		STATE["doctypes"] = set()
		self.assertFalse(recipient.seed())
		self.assertEqual(STATE["written"], [])

	def test_execute_cannot_raise(self):
		"""A patch that raises aborts `bench migrate`, which on this repo is the deploy."""
		db = sys.modules["frappe"].db
		original = db.sql

		def broken(*args, **kwargs):
			raise RuntimeError("db gone")

		db.sql = broken
		try:
			recipient.execute()
		finally:
			db.sql = original
		self.assertEqual(len(STATE["errors"]), 1)


class KpiTargetSeedTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_every_target_is_seeded_on_a_fresh_site(self):
		targets.execute()
		self.assertEqual(len(STATE["inserted"]), len(targets.TARGETS))
		by_key = {d["kpi_key"]: d for d in STATE["inserted"]}
		self.assertEqual(by_key["store_runs_30"]["target_value"], 4)
		self.assertEqual(by_key["items_below_reorder"]["target_value"], 5)
		self.assertEqual(by_key["stocked_items_counted_90"]["target_value"], 100)
		self.assertEqual(by_key["item_naming_new_compliance_pct"]["target_value"], 100)
		for key in ("stocked_items_out", "placeholder_cost_stock_lines", "unpriced_po_lines_90"):
			self.assertEqual(by_key[key]["target_value"], 0, key)
			self.assertEqual(by_key[key]["direction"], "Lower is better", key)
		for d in STATE["inserted"]:
			self.assertEqual(d["period"], "Daily")

	def test_an_existing_row_is_never_touched(self):
		STATE["targets"] = {"TGT-Operations-store_runs_30-Daily"}
		targets.execute()
		keys = {d["kpi_key"] for d in STATE["inserted"]}
		self.assertNotIn("store_runs_30", keys)
		self.assertEqual(len(keys), len(targets.TARGETS) - 1)

	def test_one_failure_costs_one_target(self):
		STATE["fail_insert"] = {"TGT-Operations-items_below_reorder-Daily"}
		targets.execute()
		self.assertEqual(len(STATE["inserted"]), len(targets.TARGETS) - 1)
		self.assertEqual(len(STATE["errors"]), 1)

	def test_no_kpi_package_no_writes(self):
		STATE["doctypes"] = set()
		targets.execute()
		self.assertEqual(STATE["inserted"], [])

	def test_each_key_is_published_by_its_department_in_the_same_direction(self):
		source = SNAPSHOTS.read_text(encoding="utf-8")
		functions = {"Operations": "_operations_metrics", "Product": "_product_metrics"}
		for department, key, _label, _target, _unit, direction, _why in targets.TARGETS:
			with self.subTest(key=key):
				start = source.index(f"def {functions[department]}(")
				end = source.index("\ndef ", start + 1)
				body = source[start:end]
				at = body.find(f'"{key}"')
				self.assertNotEqual(at, -1, f"{functions[department]} does not publish {key}")
				graded = re.search(r"metrics\.(LOWER|HIGHER)", body[at:])
				self.assertIsNotNone(graded)
				expected = "LOWER" if direction == "Lower is better" else "HIGHER"
				self.assertEqual(graded.group(1), expected)


class RegistrationTest(unittest.TestCase):
	def test_both_patches_are_registered_post_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text[text.index("[post_model_sync]") :]
		for name in ("seed_naming_digest_recipient", "seed_inventory_kpi_targets"):
			self.assertIn(f"erpnext_enhancements.patches.{name}\n", post)


if __name__ == "__main__":
	unittest.main()
