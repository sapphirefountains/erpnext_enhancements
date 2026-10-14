# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Orphaned addresses (v1.639.0): the rule-based import-address cleanup.

RUNS ``patches.cleanup_orphan_import_addresses`` against an in-memory frappe stub: rule D
(delete the CRM import's copies of a Customer-linked address) and rule L (link the supplier
import's "<X>-Billing" rows to their Supplier), each with its positive and negative cases -- the
wrong day, the wrong owner, a row from the supplier import's day but outside its one-minute
batch, an already-linked row, no match, the punctuation/case/trailing-space
normalization, a Customer sharing the Supplier's name, a delete refused by the link check (rolled
back, counted, listed), a failed insert, a second run, a missing table -- and static guards: no
``force`` on ``delete_doc``, no ``save()``, keyword ``log_error``, and no hard-coded record names
or email addresses in the patch (the repository is public; targets are found by rule).

Every fixture name is invented.

Bench-free, unittest-style:
``python -m unittest erpnext_enhancements.tests.test_address_orphan_cleanup``.
It installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.
"""

import ast
import copy
import datetime
import io
import re
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH_FILE = APP / "patches" / "cleanup_orphan_import_addresses.py"

CRM_DAY = "2025-07-10 11:30:00.000000"
SUP_DAY = "2025-10-03 09:39:15.000000"
IMPORTER = "importer@example.com"

STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None


class LinkExistsError(Exception):
	pass


def _reset(columns=None):
	STATE.clear()
	STATE.update(
		tables={"Address": [], "Dynamic Link": [], "Supplier": [], "Customer": []},
		columns=columns,
		deletes=[],
		inserts=[],
		saves=[],
		errors=[],
		savepoints=[],
		rollbacks=[],
		refuse_delete=set(),
		fail_insert=set(),
		snapshot=None,
		hashes=0,
	)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.LinkExistsError = LinkExistsError
	frappe.get_traceback = lambda *a, **k: "traceback"

	def generate_hash(*a, **k):
		STATE["hashes"] += 1
		return f"hash{STATE['hashes']:06d}"

	def log_error(title=None, message=None, reference_doctype=None, reference_name=None, **kwargs):
		STATE["errors"].append({"title": title, "message": message})

	def get_all(doctype, filters=None, fields=None, **kwargs):
		out = []
		for row in STATE["tables"][doctype]:
			if all(row.get(k) == v for k, v in (filters or {}).items()):
				out.append(_Dict({f: row.get(f) for f in (fields or ["name"])}))
		return out

	def has_column(doctype, column):
		if doctype not in STATE["tables"]:
			raise Exception(f"Table tab{doctype} does not exist")
		return STATE["columns"] is None or (doctype, column) in STATE["columns"]

	def savepoint(name):
		STATE["savepoints"].append(name)
		STATE["snapshot"] = copy.deepcopy(STATE["tables"])

	def rollback(save_point=None):
		STATE["rollbacks"].append(save_point)
		if save_point and STATE["snapshot"] is not None:
			STATE["tables"] = STATE["snapshot"]

	def delete_doc(doctype, name, *args, **kwargs):
		STATE["deletes"].append((doctype, name, args, kwargs))
		rows = STATE["tables"][doctype]
		if not any(r["name"] == name for r in rows):
			return False
		# v16 runs on_trash before the link check: a refusal happens mid-delete.
		STATE["tables"]["Dynamic Link"].append({"parent": "on_trash side effect", "parenttype": "X"})
		if name in STATE["refuse_delete"]:
			raise LinkExistsError(f"{doctype} {name} is linked")
		STATE["tables"][doctype] = [r for r in rows if r["name"] != name]

	class _Doc(_Dict):
		def db_insert(self):
			STATE["inserts"].append(dict(self))
			if self.get("parent") in STATE["fail_insert"]:
				raise RuntimeError("insert refused")
			STATE["tables"][self["doctype"]].append(dict(self))

		def save(self, *a, **k):
			STATE["saves"].append(dict(self))

		insert = save

	frappe.generate_hash = generate_hash
	frappe.log_error = log_error
	frappe.get_all = get_all
	frappe.get_doc = lambda d: _Doc(d)
	frappe.delete_doc = delete_doc
	frappe.clear_document_cache = lambda *a, **k: None
	frappe.db = types.SimpleNamespace(has_column=has_column, savepoint=savepoint, rollback=rollback)
	sys.modules["frappe"] = frappe


def setUpModule():
	global patch
	_install_frappe_stub()
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements"):
			sys.modules.pop(module, None)
	from erpnext_enhancements.patches import cleanup_orphan_import_addresses as p

	patch = p


def _addr(name, line1="1 Test St", city="Testville", owner="Administrator", creation=CRM_DAY, modified="m0"):
	STATE["tables"]["Address"].append(
		{
			"name": name,
			"address_line1": line1,
			"city": city,
			"owner": owner,
			"creation": creation,
			"modified": modified,
		}
	)


def _link(parent, doctype="Customer", link_name="Acme A"):
	STATE["tables"]["Dynamic Link"].append(
		{
			"parent": parent,
			"parenttype": "Address",
			"parentfield": "links",
			"link_doctype": doctype,
			"link_name": link_name,
		}
	)


def _supplier(name, supplier_name=None):
	STATE["tables"]["Supplier"].append({"name": name, "supplier_name": supplier_name or name})


def _customer(name, customer_name=None):
	STATE["tables"]["Customer"].append({"name": name, "customer_name": customer_name or name})


def _names():
	return {r["name"] for r in STATE["tables"]["Address"]}


def _links_of(parent):
	return [r for r in STATE["tables"]["Dynamic Link"] if r.get("parent") == parent]


def _run():
	out = io.StringIO()
	with redirect_stdout(out):
		tally = patch.execute()
	return tally, out.getvalue()


class TestNormalize(unittest.TestCase):
	def test_punctuation_case_and_spaces_do_not_matter(self):
		self.assertEqual(patch.normalize("1 Test St., Suite 5, "), patch.normalize("1 test st suite 5"))
		self.assertEqual(patch.normalize(None), "")
		self.assertNotEqual(patch.normalize("1 Test St"), patch.normalize("2 Test St"))


class TestRuleDelete(unittest.TestCase):
	def setUp(self):
		_reset()
		_addr("Acme A-Billing", creation="2024-01-01 00:00:00")
		_link("Acme A-Billing")

	def test_a_copy_of_a_customer_address_is_deleted(self):
		_addr("Acme B (Shipping)-Shipping")
		tally, out = _run()
		self.assertNotIn("Acme B (Shipping)-Shipping", _names())
		self.assertIn("Acme A-Billing", _names())
		self.assertEqual(tally["deleted"], 1)
		self.assertIn("consistent (1 delete candidates", out)

	def test_the_match_is_punctuation_case_and_trailing_space_blind(self):
		_addr("Acme B (Shipping)-Shipping", line1="1 TEST ST.", city="testville ")
		tally, _ = _run()
		self.assertEqual(tally["deleted"], 1)

	def test_a_datetime_creation_counts_by_its_date(self):
		_addr("Acme B (Billing)-Billing-1", creation=datetime.datetime(2025, 7, 10, 23, 59, 59))
		tally, _ = _run()
		self.assertEqual(tally["deleted"], 1)

	def test_another_day_is_left_alone_and_not_counted(self):
		_addr("Acme B (Shipping)-Shipping", creation="2025-07-11 00:00:01")
		tally, out = _run()
		self.assertIn("Acme B (Shipping)-Shipping", _names())
		self.assertEqual(STATE["deletes"], [])
		self.assertEqual(sum(tally.values()), 0)

	def test_another_owner_is_left_alone(self):
		_addr("Acme B (Shipping)-Shipping", owner="someone@example.com")
		tally, _ = _run()
		self.assertEqual(STATE["deletes"], [])
		self.assertEqual(tally["delete_skipped_no_match"], 0)

	def test_an_address_with_any_link_is_left_alone(self):
		_addr("Acme B (Shipping)-Shipping")
		_link("Acme B (Shipping)-Shipping", doctype="Contact", link_name="Contact 1")
		_run()
		self.assertEqual(STATE["deletes"], [])

	def test_no_matching_customer_address_is_skipped_and_listed(self):
		_addr("Acme B (Shipping)-Shipping", line1="9 Other Rd")
		tally, out = _run()
		self.assertIn("Acme B (Shipping)-Shipping", _names())
		self.assertEqual(tally["delete_skipped_no_match"], 1)
		self.assertIn("delete_skipped_no_match: Acme B (Shipping)-Shipping", out)
		self.assertTrue(any(e["title"] == "Orphaned addresses left for a person" for e in STATE["errors"]))

	def test_a_supplier_linked_or_unlinked_twin_is_not_a_match(self):
		_reset()
		_addr("Acme S-Billing", creation="2024-01-01 00:00:00")
		_link("Acme S-Billing", doctype="Supplier", link_name="Acme S")
		_addr("Loose-Other", creation="2024-01-01 00:00:00")  # unlinked, same street
		_addr("Acme B (Shipping)-Shipping")
		tally, _ = _run()
		self.assertEqual(tally["deleted"], 0)
		self.assertEqual(tally["delete_skipped_no_match"], 1)

	def test_a_blank_street_never_matches(self):
		_reset()
		_addr("Acme A-Billing", line1="", creation="2024-01-01 00:00:00")
		_link("Acme A-Billing")
		_addr("Acme B (Shipping)-Shipping", line1=" , ")
		tally, _ = _run()
		self.assertEqual(tally["delete_skipped_no_match"], 1)

	def test_a_refused_delete_is_rolled_back_counted_and_listed(self):
		_addr("Acme B (Shipping)-Shipping")
		_addr("Acme C (Shipping)-Shipping")
		STATE["refuse_delete"].add("Acme B (Shipping)-Shipping")
		tally, out = _run()
		self.assertIn("Acme B (Shipping)-Shipping", _names())
		self.assertNotIn("Acme C (Shipping)-Shipping", _names())
		self.assertEqual(tally["delete_refused"], 1)
		self.assertEqual(tally["deleted"], 1)
		self.assertEqual(STATE["rollbacks"], [patch.SAVEPOINT])
		# The refused delete's on_trash write was rolled back with it.
		self.assertEqual(len([r for r in STATE["tables"]["Dynamic Link"] if r["parenttype"] == "X"]), 1)
		self.assertIn("delete_refused: Acme B (Shipping)-Shipping", out)
		self.assertIn("consistent", out)

	def test_delete_doc_is_never_forced(self):
		_addr("Acme B (Shipping)-Shipping")
		_run()
		self.assertTrue(STATE["deletes"])
		for doctype, _name, args, kwargs in STATE["deletes"]:
			self.assertEqual(doctype, "Address")
			self.assertEqual(args, ())
			self.assertNotIn("force", kwargs)
			self.assertIs(kwargs.get("ignore_permissions"), True)


class TestRuleLink(unittest.TestCase):
	def setUp(self):
		_reset()

	def _orphan(self, name="Acme Supply-Billing", owner=IMPORTER, creation=SUP_DAY):
		_addr(name, owner=owner, creation=creation, line1="5 Fake Ave")

	def test_a_billing_address_is_linked_to_its_supplier(self):
		_supplier("Acme Supply")
		self._orphan()
		tally, out = _run()
		self.assertEqual(tally["linked"], 1)
		[row] = _links_of("Acme Supply-Billing")
		self.assertEqual(
			{k: row[k] for k in ("doctype", "parenttype", "parentfield", "idx", "link_doctype", "link_name")},
			{
				"doctype": "Dynamic Link",
				"parenttype": "Address",
				"parentfield": "links",
				"idx": 1,
				"link_doctype": "Supplier",
				"link_name": "Acme Supply",
			},
		)
		self.assertTrue(row["name"])  # named up front: db_insert skips set_new_name
		self.assertEqual(row["link_title"], "Acme Supply")
		self.assertEqual(STATE["saves"], [])
		self.assertEqual(STATE["deletes"], [])
		# The parent Address row is untouched: modified stays.
		self.assertEqual(STATE["tables"]["Address"][0]["modified"], "m0")
		self.assertIn("consistent (0 delete candidates, 1 link candidates)", out)

	def test_the_supplier_is_found_by_supplier_name_too(self):
		_supplier("SUP-0001", supplier_name="Acme Supply")
		self._orphan()
		_run()
		[row] = _links_of("Acme Supply-Billing")
		self.assertEqual(row["link_name"], "SUP-0001")
		self.assertEqual(row["link_title"], "Acme Supply")

	def test_a_customer_with_the_same_name_makes_it_ambiguous(self):
		_supplier("Acme Supply")
		_customer("CUST-1", customer_name="acme supply ")
		self._orphan()
		tally, out = _run()
		self.assertEqual(tally["link_skipped_ambiguous"], 1)
		self.assertEqual(STATE["inserts"], [])
		self.assertIn("link_skipped_ambiguous: Acme Supply-Billing", out)

	def test_two_suppliers_answering_to_the_name_is_ambiguous(self):
		_supplier("Acme Supply")
		_supplier("SUP-0002", supplier_name="Acme Supply")
		self._orphan()
		tally, _ = _run()
		self.assertEqual(tally["link_skipped_ambiguous"], 1)
		self.assertEqual(STATE["inserts"], [])

	def test_no_such_supplier_is_skipped_and_listed(self):
		_supplier("Other Supply")
		self._orphan()
		tally, out = _run()
		self.assertEqual(tally["link_skipped_no_supplier"], 1)
		self.assertIn("link_skipped_no_supplier: Acme Supply-Billing", out)

	def test_outside_the_batch_minute_or_a_numbered_name_is_not_a_candidate(self):
		"""Hand-entered "-Billing" rows from the same day sit outside the minute: left alone."""
		for supplier in ("Acme Supply", "Beta Supply", "Gamma Supply", "Delta Supply", "Echo Supply"):
			_supplier(supplier)
		self._orphan(creation="2025-10-03 09:15:00")  # same day, earlier
		self._orphan(name="Beta Supply-Billing", creation="2025-10-03 14:02:00.000000")  # same day, later
		self._orphan(name="Gamma Supply-Billing", creation="2025-10-03 09:38:59.999999")  # a moment before
		self._orphan(name="Delta Supply-Billing", creation="2025-10-03 09:40:00")  # the end is exclusive
		self._orphan(name="Echo Supply-Billing-1")  # in the minute, but numbered
		tally, out = _run()
		self.assertEqual(STATE["inserts"], [])
		self.assertEqual(sum(tally.values()), 0)
		self.assertIn("0 link candidates", out)

	def test_the_window_start_is_inclusive_and_a_datetime_creation_works(self):
		_supplier("Acme Supply")
		_supplier("Beta Supply")
		self._orphan(creation="2025-10-03 09:39:00")
		self._orphan(name="Beta Supply-Billing", creation=datetime.datetime(2025, 10, 3, 9, 39, 24, 512000))
		tally, _ = _run()
		self.assertEqual(tally["linked"], 2)

	def test_the_owner_does_not_decide_it(self):
		"""The batch is the minute, not the account that ran it."""
		_supplier("Acme Supply")
		self._orphan(owner="Administrator")
		tally, _ = _run()
		self.assertEqual(tally["linked"], 1)

	def test_an_already_linked_address_is_left_alone(self):
		_supplier("Acme Supply")
		self._orphan()
		_link("Acme Supply-Billing", doctype="Supplier", link_name="Acme Supply")
		_run()
		self.assertEqual(STATE["inserts"], [])

	def test_a_failed_insert_is_counted_and_the_rest_continue(self):
		_supplier("Acme Supply")
		_supplier("Beta Supply")
		self._orphan()
		self._orphan(name="Beta Supply-Billing")
		STATE["fail_insert"].add("Acme Supply-Billing")
		tally, out = _run()
		self.assertEqual(tally["failed"], 1)
		self.assertEqual(tally["linked"], 1)
		self.assertIn("link_failed: Acme Supply-Billing", out)
		self.assertIn("consistent", out)
		self.assertTrue(any(e["title"] == "Orphaned address cleanup: a row failed" for e in STATE["errors"]))


class TestSafety(unittest.TestCase):
	def setUp(self):
		_reset()

	def _mixed(self):
		_addr("Acme A-Billing", creation="2024-01-01 00:00:00")
		_link("Acme A-Billing")
		_addr("Acme B (Shipping)-Shipping")  # deleted
		_addr("Acme C (Shipping)-Shipping", line1="9 Other Rd")  # no match
		_addr("Acme D (Shipping)-Shipping")  # refused
		STATE["refuse_delete"].add("Acme D (Shipping)-Shipping")
		_supplier("Acme Supply")
		_supplier("Shared Name")
		_customer("Shared Name")
		_addr("Acme Supply-Billing", owner=IMPORTER, creation=SUP_DAY)  # linked
		_addr("Shared Name-Billing", owner=IMPORTER, creation=SUP_DAY)  # ambiguous
		_addr("Nobody-Billing", owner=IMPORTER, creation=SUP_DAY)  # no supplier
		_addr("Typed By Hand-Billing", owner=IMPORTER, creation="2026-01-05 10:00:00")  # untouched
		_addr("Same Day By Hand-Billing", owner=IMPORTER, creation="2025-10-03 14:02:00")  # untouched
		_supplier("Same Day By Hand")

	def test_the_tally_adds_up(self):
		self._mixed()
		tally, out = _run()
		self.assertEqual(
			tally,
			{
				"deleted": 1,
				"delete_skipped_no_match": 1,
				"delete_refused": 1,
				"linked": 1,
				"link_skipped_ambiguous": 1,
				"link_skipped_no_supplier": 1,
				"failed": 0,
			},
		)
		self.assertIn("consistent (3 delete candidates, 3 link candidates)", out)
		self.assertNotIn("MISMATCH", out)
		self.assertIn("Typed By Hand-Billing", _names())
		self.assertEqual(_links_of("Same Day By Hand-Billing"), [])

	def test_a_second_run_does_nothing(self):
		self._mixed()
		_run()
		STATE["deletes"].clear()
		STATE["inserts"].clear()
		second, _ = _run()
		self.assertEqual(STATE["inserts"], [])
		self.assertEqual(second["deleted"] + second["linked"], 0)
		self.assertEqual(second["delete_refused"], 1)  # still blocked, still listed, still not forced
		self.assertEqual(second["link_skipped_ambiguous"], 1)
		self.assertEqual(second["delete_skipped_no_match"], 1)

	def test_a_missing_table_is_a_quiet_no_op(self):
		_addr("Acme B (Shipping)-Shipping")
		del STATE["tables"]["Dynamic Link"]
		tally, out = _run()
		self.assertIn("nothing to do", out)
		self.assertEqual(STATE["deletes"], [])
		self.assertEqual(tally["failed"], 0)

	def test_a_missing_column_is_a_quiet_no_op(self):
		_reset(columns={("Address", "address_line1"), ("Dynamic Link", "link_doctype")})
		_addr("Acme B (Shipping)-Shipping")
		_, out = _run()
		self.assertIn("nothing to do", out)
		self.assertEqual(STATE["deletes"], [])

	def test_no_supplier_table_skips_the_link_rule_without_raising(self):
		del STATE["tables"]["Supplier"]
		_addr("Acme Supply-Billing", owner=IMPORTER, creation=SUP_DAY)
		tally, out = _run()
		self.assertEqual(tally["link_skipped_no_supplier"], 1)
		self.assertIn("consistent", out)

	def test_the_error_log_is_written_even_when_the_console_cannot_print(self):
		class _Unprintable(io.StringIO):
			def write(self, text):
				raise UnicodeEncodeError("ascii", text, 0, 1, "cannot encode")

		_supplier("Shared Name")
		_customer("Shared Name")
		_addr("Shared Name-Billing", owner=IMPORTER, creation=SUP_DAY)
		with redirect_stdout(_Unprintable()):
			tally = patch.execute()
		self.assertEqual(tally["link_skipped_ambiguous"], 1)
		[entry] = [e for e in STATE["errors"] if e["title"] == "Orphaned addresses left for a person"]
		self.assertIn("link_skipped_ambiguous: Shared Name-Billing", entry["message"])
		# Nor does the no-op path's message raise.
		del STATE["tables"]["Dynamic Link"]
		with redirect_stdout(_Unprintable()):
			patch.execute()

	def test_an_unexpected_error_is_logged_not_raised(self):
		_addr("Acme B (Shipping)-Shipping")
		original = sys.modules["frappe"].get_all
		sys.modules["frappe"].get_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
		try:
			tally, out = _run()
		finally:
			sys.modules["frappe"].get_all = original
		self.assertIn("aborted early", out)
		self.assertEqual(STATE["errors"][0]["title"], "Orphaned address cleanup failed")
		self.assertEqual(tally["failed"], 0)


def _code_strings(tree):
	"""Every str constant in the patch except docstrings."""
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
			body = node.body
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				docstrings.add(id(body[0].value))
	return [
		node
		for node in ast.walk(tree)
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
	]


class TestStaticGuards(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.source = PATCH_FILE.read_text(encoding="utf-8")
		cls.tree = ast.parse(cls.source)

	def test_delete_doc_has_no_force_and_nothing_is_saved(self):
		calls = [
			n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
		]
		deletes = [c for c in calls if c.func.attr == "delete_doc"]
		self.assertTrue(deletes)
		for call in deletes:
			self.assertNotIn("force", {k.arg for k in call.keywords})
		for call in calls:
			self.assertNotIn(call.func.attr, {"save", "insert", "submit", "set_value", "db_update"})

	def test_log_error_is_always_called_with_keywords(self):
		for n in ast.walk(self.tree):
			if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "log_error":
				self.assertEqual(n.args, [])
				self.assertTrue({"title", "message"} <= {k.arg for k in n.keywords})

	def test_no_record_name_or_email_is_hard_coded(self):
		"""The repository is public and the patch finds its targets by rule.

		So no code string may look like an Address name ("<X>-Billing", "(Shipping)"), carry an
		email address, or sit in a collection literal as a proper noun. A collection of strings
		may hold only snake_case identifiers and the doctypes the patch works on.
		"""
		allowed_words = {"Address", "Dynamic Link", "Supplier", "Customer"}
		for node in _code_strings(self.tree):
			text = node.value
			self.assertNotIn("@", text, text)
			self.assertNotIn("(Shipping)", text)
			self.assertNotIn("(Billing)", text)
			if text.endswith(patch.BILLING_SUFFIX):
				self.assertEqual(text, patch.BILLING_SUFFIX)
		for node in ast.walk(self.tree):
			if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
				elements = node.elts
			elif isinstance(node, ast.Dict):
				elements = [*node.keys, *node.values]
			else:
				continue
			for el in elements:
				if isinstance(el, ast.Constant) and isinstance(el.value, str):
					self.assertTrue(
						re.fullmatch(r"[a-z_0-9]+", el.value) or el.value in allowed_words,
						f"string {el.value!r} in a collection literal looks like a hard-coded name",
					)

	def test_the_import_rules_are_the_approved_ones(self):
		self.assertEqual(patch.CRM_IMPORT_DATE, "2025-07-10")
		self.assertEqual(patch.CRM_IMPORT_OWNER, "Administrator")
		self.assertEqual(
			(patch.SUPPLIER_BATCH_START, patch.SUPPLIER_BATCH_END),
			("2025-10-03 09:39:00", "2025-10-03 09:40:00"),
		)

	def test_the_patch_is_registered_post_model_sync(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		entry = "erpnext_enhancements.patches.cleanup_orphan_import_addresses"
		self.assertIn(entry, lines)
		self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))
		self.assertIn("cleanup_orphan_import_addresses", (APP / "patches" / "README.md").read_text("utf-8"))


if __name__ == "__main__":
	unittest.main()
