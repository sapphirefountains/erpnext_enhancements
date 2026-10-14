# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""WI-024 step 1 (v1.636.0): the Opportunity <-> Project link backfill.

The won-without-project warnings read only ``Opportunity.custom_created_project``, so a deal
linked only by ``Project.custom_opportunity`` or the hidden legacy ``custom_project`` kept
nagging. This suite RUNS ``patches.backfill_opportunity_project_links`` against an in-memory
frappe stub: each rule, the Canceled tie-break, the skips and conflicts it must leave alone,
deals that are not Closed Won, a second run, missing columns, and that every write is
``set_value(update_modified=False)`` and never a ``save()`` (WI-024 hazards H1/H3).

Bench-free, unittest-style:
``python -m unittest erpnext_enhancements.tests.test_opportunity_project_link_backfill``.
It installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.
"""

import ast
import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCH_FILE = APP / "patches" / "backfill_opportunity_project_links.py"

ALL_COLUMNS = {
	("Opportunity", "custom_created_project"),
	("Opportunity", "custom_project"),
	("Project", "custom_opportunity"),
}

STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None

	def __setattr__(self, key, value):
		self[key] = value


def _reset(columns=None):
	STATE.clear()
	STATE.update(
		columns=set(ALL_COLUMNS if columns is None else columns),
		tables={"Opportunity": [], "Project": []},
		writes=[],
		errors=[],
		refuse_write=set(),
	)


def _matches(row, filters):
	for field, cond in (filters or {}).items():
		op, value = cond if isinstance(cond, (list, tuple)) else ("=", cond)
		have = row.get(field)
		if op == "=" and have != value:
			return False
		if op == "in" and have not in value:
			return False
		if op == "is" and value == "set" and not have:
			return False
		if op not in ("=", "in", "is"):
			raise AssertionError(f"stub does not know operator {op!r}")
	return True


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_traceback = lambda *a, **k: "traceback"

	def log_error(title=None, message=None, reference_doctype=None, reference_name=None, **kwargs):
		STATE["errors"].append({"title": title, "message": message})

	def get_all(doctype, filters=None, fields=None, **kwargs):
		out = []
		for row in STATE["tables"].get(doctype, []):
			if _matches(row, filters):
				# get_all returns only the requested fields; a column absent from the
				# schema must not be readable just because the test row carries it.
				out.append(_Dict({f: row.get(f) for f in (fields or ["name"])}))
		return out

	def has_column(doctype, column):
		if doctype not in STATE["tables"]:
			raise Exception(f"Table tab{doctype} does not exist")
		return (doctype, column) in STATE["columns"]

	def set_value(doctype, name, field, value=None, update_modified=True, **kwargs):
		STATE["writes"].append((doctype, name, field, value, update_modified))
		if (doctype, name) in STATE["refuse_write"]:
			raise RuntimeError("write refused")
		for row in STATE["tables"][doctype]:
			if row["name"] == name:
				row[field] = value

	frappe.log_error = log_error
	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(has_column=has_column, set_value=set_value)
	sys.modules["frappe"] = frappe


def setUpModule():
	global patch
	_install_frappe_stub()
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements"):
			sys.modules.pop(module, None)
	from erpnext_enhancements.patches import backfill_opportunity_project_links as p

	patch = p


def _opp(name, status="Closed Won", created=None, legacy=None):
	STATE["tables"]["Opportunity"].append(
		{"name": name, "status": status, "custom_created_project": created, "custom_project": legacy}
	)


def _prj(name, opportunity=None, status="Active"):
	STATE["tables"]["Project"].append({"name": name, "status": status, "custom_opportunity": opportunity})


def _get(doctype, name, field):
	return next(r for r in STATE["tables"][doctype] if r["name"] == name)[field]


def _run():
	out = io.StringIO()
	with redirect_stdout(out):
		tally = patch.execute()
	return tally, out.getvalue()


class TestRuleOneLegacyField(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_custom_project_is_copied_and_the_reverse_filled(self):
		_opp("OPP-1", legacy="PRJ-1")
		_prj("PRJ-1")
		tally, _ = _run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-1")
		self.assertEqual(_get("Project", "PRJ-1", "custom_opportunity"), "OPP-1")
		self.assertEqual(tally["forward_from_custom_project"], 1)
		self.assertEqual(tally["reverse_backfilled"], 1)

	def test_custom_project_is_never_written(self):
		_opp("OPP-1", legacy="PRJ-1")
		_prj("PRJ-1")
		_run()
		self.assertFalse([w for w in STATE["writes"] if w[2] == "custom_project"])
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_project"), "PRJ-1")

	def test_a_missing_legacy_project_is_not_copied(self):
		_opp("OPP-1", legacy="PRJ-GONE")
		tally, out = _run()
		self.assertIsNone(_get("Opportunity", "OPP-1", "custom_created_project"))
		self.assertEqual(tally["detail"]["no_project"], 1)
		self.assertIn("PRJ-GONE does not exist", out)

	def test_a_legacy_project_belonging_to_another_deal_is_a_conflict(self):
		_opp("OPP-1", legacy="PRJ-1")
		_opp("OPP-2", status="Open")
		_prj("PRJ-1", opportunity="OPP-2")
		tally, out = _run()
		self.assertIsNone(_get("Opportunity", "OPP-1", "custom_created_project"))
		self.assertEqual(tally["conflicts"], 1)
		self.assertIn("belongs to OPP-2", out)
		self.assertEqual(STATE["writes"], [])

	def test_rule_one_runs_before_rule_two(self):
		_opp("OPP-1", legacy="PRJ-1")
		_prj("PRJ-1")
		_prj("PRJ-2", opportunity="OPP-1")
		tally, _ = _run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-1")
		self.assertEqual(tally["forward_from_custom_project"], 1)
		# PRJ-1 cannot also take the reverse while PRJ-2 already claims the deal.
		self.assertIsNone(_get("Project", "PRJ-1", "custom_opportunity"))
		self.assertEqual(tally["detail"]["reverse_conflicts"], 1)

	def test_without_the_legacy_column_rule_two_still_runs(self):
		_reset(columns=ALL_COLUMNS - {("Opportunity", "custom_project")})
		_opp("OPP-1", legacy="PRJ-1")
		_prj("PRJ-1")
		_prj("PRJ-2", opportunity="OPP-1")
		_run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-2")


class TestRuleTwoReverseLink(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_one_project_pointing_back_is_used(self):
		_opp("OPP-1")
		_prj("PRJ-1", opportunity="OPP-1")
		tally, _ = _run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-1")
		self.assertEqual(tally["forward_from_reverse"], 1)
		self.assertEqual(tally["reverse_backfilled"], 0)
		self.assertEqual(tally["detail"]["reverse_already_set"], 1)

	def test_the_only_live_project_wins_over_a_canceled_one(self):
		"""CRM-OPP-2025-00653: PRJ-00597 Active and PRJ-00752 Canceled -> PRJ-00597."""
		_opp("CRM-OPP-2025-00653")
		_prj("PRJ-00597", opportunity="CRM-OPP-2025-00653", status="Active")
		_prj("PRJ-00752", opportunity="CRM-OPP-2025-00653", status="Canceled")
		tally, _ = _run()
		self.assertEqual(_get("Opportunity", "CRM-OPP-2025-00653", "custom_created_project"), "PRJ-00597")
		self.assertEqual(tally["forward_from_reverse"], 1)
		self.assertEqual(tally["conflicts"], 0)
		self.assertEqual(STATE["errors"], [])

	def test_stock_erpnext_spelling_counts_as_canceled_too(self):
		_opp("OPP-1")
		_prj("PRJ-1", opportunity="OPP-1", status="Cancelled")
		_prj("PRJ-2", opportunity="OPP-1", status="Completed")
		_run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-2")

	def test_the_site_spelling_is_one_of_the_canceled_statuses(self):
		self.assertIn("Canceled", patch.CANCELED_STATUSES)

	def test_several_live_projects_are_skipped_for_review(self):
		_opp("OPP-1")
		_prj("PRJ-1", opportunity="OPP-1", status="Active")
		_prj("PRJ-2", opportunity="OPP-1", status="Completed")
		tally, out = _run()
		self.assertIsNone(_get("Opportunity", "OPP-1", "custom_created_project"))
		self.assertEqual(tally["skipped_multiple"], 1)
		self.assertEqual(STATE["writes"], [])
		self.assertIn("PRJ-1, PRJ-2", out)
		self.assertTrue(any("need review" in e["title"] for e in STATE["errors"]))

	def test_several_canceled_projects_are_skipped_too(self):
		_opp("OPP-1")
		_prj("PRJ-1", opportunity="OPP-1", status="Canceled")
		_prj("PRJ-2", opportunity="OPP-1", status="Canceled")
		tally, _ = _run()
		self.assertEqual(tally["skipped_multiple"], 1)
		self.assertEqual(STATE["writes"], [])

	def test_a_project_already_another_deals_forward_link_is_a_conflict(self):
		"""Two deals must never both name one Project as theirs."""
		_opp("OPP-A", created="PRJ-1")
		_opp("OPP-B")
		_prj("PRJ-1", opportunity="OPP-B")
		tally, out = _run()
		self.assertIsNone(_get("Opportunity", "OPP-B", "custom_created_project"))
		self.assertEqual(tally["detail"]["forward_conflicts"], 1)
		self.assertIn("belongs to OPP-A", out)

	def test_historical_deals_are_included(self):
		"""The canonical link is not a warning rule: historical or not, it is filled."""
		_opp("OPP-1")
		STATE["tables"]["Opportunity"][0]["custom_is_historical"] = 1
		_prj("PRJ-1", opportunity="OPP-1")
		_run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-1")


class TestRuleThreeReverseBackfill(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_blank_reverse_is_filled(self):
		_opp("OPP-1", created="PRJ-1")
		_prj("PRJ-1")
		tally, _ = _run()
		self.assertEqual(_get("Project", "PRJ-1", "custom_opportunity"), "OPP-1")
		self.assertEqual(tally["reverse_backfilled"], 1)

	def test_a_reverse_naming_another_deal_is_left_and_reported(self):
		"""CRM-OPP-2025-00665 -> PRJ-00581, whose reverse names CRM-OPP-2025-00636."""
		_opp("CRM-OPP-2025-00665", created="PRJ-00581")
		_opp("CRM-OPP-2025-00636", status="Open")
		_prj("PRJ-00581", opportunity="CRM-OPP-2025-00636")
		tally, out = _run()
		self.assertEqual(_get("Project", "PRJ-00581", "custom_opportunity"), "CRM-OPP-2025-00636")
		self.assertEqual(_get("Opportunity", "CRM-OPP-2025-00665", "custom_created_project"), "PRJ-00581")
		self.assertEqual(tally["conflicts"], 1)
		self.assertEqual(STATE["writes"], [])
		self.assertIn("PRJ-00581 points at CRM-OPP-2025-00636", out)

	def test_a_forward_link_to_a_missing_project_is_reported_not_written(self):
		_opp("OPP-1", created="PRJ-GONE")
		tally, out = _run()
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(tally["detail"]["dangling"], 1)
		self.assertIn("PRJ-GONE does not exist", out)

	def test_a_non_blank_forward_link_is_never_overwritten(self):
		_opp("OPP-1", created="PRJ-1", legacy="PRJ-2")
		_prj("PRJ-1", opportunity="OPP-1")
		_prj("PRJ-2", opportunity="OPP-1")
		_run()
		self.assertEqual(_get("Opportunity", "OPP-1", "custom_created_project"), "PRJ-1")
		self.assertEqual(STATE["writes"], [])


class TestNotClosedWon(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_lost_and_on_hold_deals_are_reported_only(self):
		_opp("CRM-OPP-2025-00600", status="Lost")
		_opp("CRM-OPP-2025-00668", status="On Hold", legacy="PRJ-00773")
		_prj("PRJ-00551", opportunity="CRM-OPP-2025-00600")
		_prj("PRJ-00773", opportunity="CRM-OPP-2025-00668")
		tally, out = _run()
		self.assertEqual(STATE["writes"], [])
		self.assertIn("CRM-OPP-2025-00600 (Lost) <- PRJ-00551", out)
		self.assertIn("CRM-OPP-2025-00668 (On Hold) <- PRJ-00773", out)
		self.assertEqual(tally["forward_from_reverse"] + tally["forward_from_custom_project"], 0)

	def test_a_not_won_deals_project_is_not_back_filled(self):
		_opp("OPP-1", status="Open", created="PRJ-1")
		_prj("PRJ-1")
		_run()
		self.assertIsNone(_get("Project", "PRJ-1", "custom_opportunity"))


class TestSafety(unittest.TestCase):
	def setUp(self):
		_reset()

	def _mixed(self):
		_opp("OPP-LEGACY", legacy="PRJ-1")
		_opp("OPP-REVERSE")
		_opp("OPP-MULTI")
		_opp("OPP-CONFLICT", created="PRJ-9")
		_opp("OPP-NONE")
		_opp("OPP-OTHER", status="Open")
		_prj("PRJ-1")
		_prj("PRJ-2", opportunity="OPP-REVERSE")
		_prj("PRJ-3", opportunity="OPP-MULTI")
		_prj("PRJ-4", opportunity="OPP-MULTI")
		_prj("PRJ-9", opportunity="OPP-OTHER")

	def test_every_write_is_set_value_without_touching_modified(self):
		self._mixed()
		_run()
		self.assertTrue(STATE["writes"])
		self.assertTrue(all(w[4] is False for w in STATE["writes"]), STATE["writes"])

	def test_a_second_run_writes_nothing(self):
		self._mixed()
		first, _ = _run()
		STATE["writes"].clear()
		second, _ = _run()
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(first["forward_from_custom_project"], 1)
		self.assertEqual(first["forward_from_reverse"], 1)
		self.assertEqual(first["reverse_backfilled"], 1)
		self.assertEqual(second["forward_from_custom_project"] + second["forward_from_reverse"], 0)
		self.assertEqual(second["reverse_backfilled"], 0)
		self.assertEqual(second["skipped_multiple"], first["skipped_multiple"])
		self.assertEqual(second["conflicts"], first["conflicts"])

	def test_the_tally_adds_up(self):
		self._mixed()
		tally, out = _run()
		self.assertIn("consistent (4 blank forward links", out)
		self.assertNotIn("MISMATCH", out)
		detail = tally["detail"]
		self.assertEqual(sum(detail[k] for k in patch.FORWARD_OUTCOMES), 4)
		self.assertEqual(sum(detail[k] for k in patch.REVERSE_OUTCOMES), 3)

	def test_a_refused_write_is_counted_and_does_not_raise(self):
		_opp("OPP-1")
		_opp("OPP-2")
		_prj("PRJ-1", opportunity="OPP-1")
		_prj("PRJ-2", opportunity="OPP-2")
		STATE["refuse_write"].add(("Opportunity", "OPP-1"))
		tally, out = _run()
		self.assertEqual(tally["failed"], 1)
		self.assertEqual(_get("Opportunity", "OPP-2", "custom_created_project"), "PRJ-2")
		self.assertIn("consistent", out)
		self.assertTrue(any("write failed" in e["title"] for e in STATE["errors"]))

	def test_missing_columns_are_a_quiet_no_op(self):
		for missing in (("Opportunity", "custom_created_project"), ("Project", "custom_opportunity")):
			_reset(columns=ALL_COLUMNS - {missing})
			_opp("OPP-1", legacy="PRJ-1")
			_prj("PRJ-1", opportunity="OPP-1")
			tally, out = _run()
			self.assertEqual(STATE["writes"], [])
			self.assertIn("nothing to do", out)
			self.assertEqual(tally["failed"], 0)

	def test_a_missing_table_does_not_raise(self):
		_reset()
		del STATE["tables"]["Project"]
		tally, out = _run()
		self.assertIn("nothing to do", out)
		self.assertEqual(STATE["writes"], [])

	def test_an_unexpected_error_is_logged_not_raised(self):
		_opp("OPP-1")
		original = sys.modules["frappe"].get_all
		sys.modules["frappe"].get_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
		try:
			tally, out = _run()
		finally:
			sys.modules["frappe"].get_all = original
		self.assertIn("aborted early", out)
		self.assertEqual(STATE["errors"][0]["title"], "Opportunity-Project link backfill failed")
		self.assertEqual(tally["failed"], 0)


class TestStaticGuards(unittest.TestCase):
	def test_the_patch_never_saves_a_document(self):
		"""Hazard H3/H1: an Opportunity save fires the closed-won prompt and after_save hooks."""
		source = PATCH_FILE.read_text(encoding="utf-8")
		tree = ast.parse(source)
		for node in ast.walk(tree):
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
				self.assertNotIn(node.func.attr, {"save", "insert", "submit", "db_update"}, node.func.attr)
		code = "\n".join(line.split("#")[0] for line in source.splitlines())
		code = code.replace(ast.get_docstring(tree), "")
		self.assertNotIn(".save(", code)

	def test_every_set_value_passes_update_modified_false(self):
		tree = ast.parse(PATCH_FILE.read_text(encoding="utf-8"))
		calls = [
			n
			for n in ast.walk(tree)
			if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "set_value"
		]
		self.assertTrue(calls)
		for call in calls:
			kw = {k.arg: k.value for k in call.keywords}
			self.assertIn("update_modified", kw)
			self.assertIs(kw["update_modified"].value, False)

	def test_log_error_is_always_called_with_keywords(self):
		tree = ast.parse(PATCH_FILE.read_text(encoding="utf-8"))
		for n in ast.walk(tree):
			if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "log_error":
				self.assertEqual(n.args, [])
				self.assertTrue({"title", "message"} <= {k.arg for k in n.keywords})

	def test_the_patch_is_registered_post_model_sync(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		entry = "erpnext_enhancements.patches.backfill_opportunity_project_links"
		self.assertIn(entry, lines)
		self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))
		self.assertIn(
			"backfill_opportunity_project_links", (APP / "patches" / "README.md").read_text("utf-8")
		)


if __name__ == "__main__":
	unittest.main()
