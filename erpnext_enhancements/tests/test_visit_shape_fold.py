# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: switching a contract to one visit for the site folds its drafts (v1.599.0).

D4 of Phase D (TASK-2026-02489). Hardware Apartments' contract (MNT-CON-2026-00008) was Per
Feature, with four untouched drafts for 19 October, one per fountain. Switched to Per Site
Visit alone, the scheduler would not draft the site visit until all four were finished (it
drafts one only while the contract has no open regular draft). ``visit_shape`` folds them:

* the earliest untouched per-feature draft becomes the site visit, saved before anything is
  deleted, and the rest are deleted;
* a draft somebody has started, or one something links to, is left and named;
* only this save's switch to Per Site Visit does it, and only unlabelled, open, per-feature
  drafts of this contract are looked at.

Stubs a minimal ``frappe`` in ``setUpModule``; runs the real ``visit_shape`` and ``task_rules``.

Run: python -m unittest erpnext_enhancements.tests.test_visit_shape_fold
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RECORD = "Sapphire Maintenance Record"
MODULE = "erpnext_enhancements.sapphire_maintenance.visit_shape"
STUBBED = ("frappe", "frappe.utils", MODULE)

shape = None
STATE = {}
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _LinkExistsError(Exception):
	pass


class _Record(_Dict):
	"""Just enough of a Document: get/set, and save into the store."""

	def set(self, key, value):
		self[key] = value

	def save(self, ignore_permissions=False):
		assert ignore_permissions, "the fold saves as the system"
		if STATE["fail_save"]:
			raise RuntimeError("validation failed")
		STATE["saved"].append((self.name, dict(self)))


class _Contract(_Dict):
	def get_doc_before_save(self):
		return self["_before"]


def _get_all(doctype, filters=None, pluck=None, order_by=None, **kwargs):
	assert doctype == RECORD
	assert filters == {
		"maintenance_contract": filters["maintenance_contract"],
		"docstatus": 0,
		"visit_label": ["is", "not set"],
		"serial_no": ["is", "set"],
	}, filters
	assert order_by == "scheduled_visit_date asc, creation asc"
	rows = [
		r
		for r in STATE["records"].values()
		if r.maintenance_contract == filters["maintenance_contract"]
		and r.docstatus == 0
		and not r.visit_label
		and r.serial_no
	]
	rows.sort(key=lambda r: (r.scheduled_visit_date, r.creation))
	return [r.name for r in rows]


def _delete_doc(doctype, name, ignore_permissions=False):
	assert doctype == RECORD and ignore_permissions
	assert STATE["flags"].mute_messages, "a refusal is named in the summary, not shown as a dialog"
	if name in STATE["linked"]:
		raise _LinkExistsError(name)
	del STATE["records"][name]
	STATE["deleted"].append(name)


def setUpModule():
	global shape
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	import erpnext_enhancements.sapphire_maintenance as package

	if "visit_shape" in vars(package):
		delattr(package, "visit_shape")

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe.LinkExistsError = _LinkExistsError
	frappe.get_all = _get_all
	frappe.get_doc = lambda doctype, name: STATE["records"][name]
	frappe.delete_doc = _delete_doc
	frappe.msgprint = lambda message, **kwargs: STATE["messages"].append(message)
	frappe.flags = types.SimpleNamespace(mute_messages=False)
	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, *a: float(v or 0)
	utils.formatdate = lambda value: f"[{value}]"
	utils.escape_html = lambda text: text.replace("<", "&lt;")
	frappe.utils = utils
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	from erpnext_enhancements.sapphire_maintenance import visit_shape

	shape = visit_shape


def tearDownModule():
	import erpnext_enhancements.sapphire_maintenance as package

	if "visit_shape" in vars(package):
		delattr(package, "visit_shape")
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset():
	STATE.clear()
	STATE.update(
		{
			"records": {},
			"saved": [],
			"deleted": [],
			"messages": [],
			"linked": set(),
			"fail_save": False,
			"flags": sys.modules["frappe"].flags,
		}
	)
	STATE["flags"].mute_messages = False


def _draft(name, serial, day="2026-10-19", contract="MNT-CON-2026-00008", **extra):
	record = _Record(
		name=name,
		maintenance_contract=contract,
		serial_no=serial,
		scheduled_visit_date=day,
		creation=f"{day} 06:00:{len(STATE['records']):02d}",
		docstatus=0,
		workflow_state="Draft",
		visit_label=None,
		maintenance_results=[],
		chemistry_readings=[],
		cleaning_tasks=[],
		consumables=[],
	)
	record.update(extra)
	STATE["records"][name] = record
	return record


def _hardware():
	"""Prod on 2026-10-12: four bare drafts for 19 October, plus the Winterization."""
	_draft("4lpc1ncnah", "MAINT-HARDWARE-East-Courtyard")
	_draft("4lq1bpi8uk", "MAINT-HARDWARE-East-Lobby")
	_draft("4lr8msbd4j", "MAINT-HARDWARE-West-Courtyard")
	_draft("4lsbmepacj", "MAINT-HARDWARE-West-Lobby")
	_draft("lld24i3de0", None, day="2026-10-24", visit_label="Winterization")


class TestTheFold(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_hardwares_four_drafts_become_one_site_visit(self):
		_hardware()
		result = shape.fold_feature_drafts("MNT-CON-2026-00008")
		self.assertEqual(result["kept"], "4lpc1ncnah")
		self.assertEqual(result["removed"], ["4lq1bpi8uk", "4lr8msbd4j", "4lsbmepacj"])
		self.assertEqual(result["left"], [])
		kept = STATE["records"]["4lpc1ncnah"]
		self.assertIsNone(kept.serial_no, "a site visit has no water feature of its own")
		self.assertEqual(kept.scheduled_visit_date, "2026-10-19")
		self.assertIn("lld24i3de0", STATE["records"], "the Winterization is a separate visit")
		self.assertEqual(sorted(STATE["records"]), ["4lpc1ncnah", "lld24i3de0"])

	def test_the_kept_draft_is_saved_before_anything_is_deleted(self):
		_hardware()
		STATE["fail_save"] = True
		with self.assertRaises(RuntimeError):
			shape.fold_feature_drafts("MNT-CON-2026-00008")
		self.assertEqual(STATE["deleted"], [])

	def test_the_earliest_is_kept_and_its_rows_are_emptied_for_the_wizard_to_rebuild(self):
		_draft("late", "SN-B", day="2026-10-26")
		_draft(
			"early",
			"SN-A",
			day="2026-10-19",
			cleaning_tasks=[_Dict(task="Skim")],
			chemistry_readings=[_Dict(reading="pH")],
		)
		result = shape.fold_feature_drafts("MNT-CON-2026-00008")
		self.assertEqual((result["kept"], result["removed"]), ("early", ["late"]))
		self.assertEqual(STATE["records"]["early"].cleaning_tasks, [])
		self.assertEqual(STATE["records"]["early"].chemistry_readings, [])

	def test_a_started_draft_is_left_and_named(self):
		cases = {
			"notes": {"visit_notes": "Gate code changed"},
			"safety": {"safety_acknowledged": 1},
			"clock": {"clock_in_time": "2026-10-19 08:00:00"},
			"date": {"visit_date": "2026-10-18"},
			"review": {"workflow_state": "Pending Review"},
			"reading": {"chemistry_readings": [_Dict(reading="pH", reading_value=7.4)]},
			"task_na": {"cleaning_tasks": [_Dict(task="Heater check", not_applicable=1)]},
			"answer": {"maintenance_results": [_Dict(question="Pump", selection="Pass")]},
			"dose": {"consumables": [_Dict(item="Chlorine", qty=2)]},
			"photo": {"fountain_photo": "/private/files/f.jpg"},
		}
		for label, extra in cases.items():
			with self.subTest(label=label):
				_reset()
				_draft("started", "SN-A", **extra)
				_draft("bare", "SN-B")
				result = shape.fold_feature_drafts("MNT-CON-2026-00008")
				self.assertEqual((result["kept"], result["left"]), ("bare", ["started"]))
				self.assertEqual(STATE["records"]["started"].serial_no, "SN-A", "left exactly as it was")

	def test_an_opened_but_untouched_draft_still_folds(self):
		"""The wizard builds a draft's rows on first open; empty rows are not work."""
		_draft(
			"opened",
			"SN-A",
			cleaning_tasks=[_Dict(task="Skim", notes="  ")],
			consumables=[_Dict(item="Cl", qty=0)],
		)
		_draft("bare", "SN-B")
		self.assertEqual(shape.fold_feature_drafts("MNT-CON-2026-00008")["kept"], "opened")

	def test_nothing_untouched_folds_nothing(self):
		_draft("a", "SN-A", visit_notes="x")
		result = shape.fold_feature_drafts("MNT-CON-2026-00008")
		self.assertEqual(result, {"kept": None, "removed": [], "left": ["a"]})
		self.assertEqual(STATE["saved"], [])

	def test_a_draft_something_links_to_is_left_and_the_messages_unmuted_again(self):
		_hardware()
		STATE["linked"].add("4lr8msbd4j")
		result = shape.fold_feature_drafts("MNT-CON-2026-00008")
		self.assertEqual(result["removed"], ["4lq1bpi8uk", "4lsbmepacj"])
		self.assertEqual(result["left"], ["4lr8msbd4j"])
		self.assertFalse(STATE["flags"].mute_messages)

	def test_another_contracts_drafts_are_not_touched(self):
		_draft("ours", "SN-A")
		_draft("theirs", "SN-Z", contract="MNT-CON-2026-00001")
		shape.fold_feature_drafts("MNT-CON-2026-00008")
		self.assertEqual(STATE["records"]["theirs"].serial_no, "SN-Z")


class TestOnlyTheSwitch(unittest.TestCase):
	def setUp(self):
		_reset()
		_hardware()

	def _save(self, shape_now, shape_before):
		before = _Dict(visit_shape=shape_before) if shape_before else None
		contract = _Contract(name="MNT-CON-2026-00008", visit_shape=shape_now, _before=before)
		shape.on_contract_update(contract)

	def test_switching_to_one_visit_folds_and_says_so(self):
		self._save("Per Site Visit", "Per Feature")
		self.assertEqual(len(STATE["records"]), 2)
		(message,) = STATE["messages"]
		self.assertIn("Visit 4lpc1ncnah on [2026-10-19] now covers every water feature", message)
		self.assertIn("Removed 3 per-feature drafts nobody had started", message)

	def test_any_other_save_does_nothing(self):
		for now, before in (
			("Per Site Visit", "Per Site Visit"),
			("Per Feature", "Per Site Visit"),
			("Per Feature", "Per Feature"),
			("Per Site Visit", None),
		):
			with self.subTest(now=now, before=before):
				self._save(now, before)
				self.assertEqual(len(STATE["records"]), 5)
				self.assertEqual(STATE["messages"], [])

	def test_nothing_to_fold_says_nothing(self):
		_reset()
		self._save("Per Site Visit", "Per Feature")
		self.assertEqual(STATE["messages"], [])


class TestTheWiring(unittest.TestCase):
	def test_the_contract_folds_on_update(self):
		source = (
			APP
			/ "sapphire_maintenance/doctype/sapphire_maintenance_contract/sapphire_maintenance_contract.py"
		).read_text(encoding="utf-8")
		on_update = source[source.index("def on_update(self):") :]
		on_update = on_update[: on_update.index("\n\tdef ", 1)]
		self.assertIn("on_contract_update(self)", on_update)


if __name__ == "__main__":
	unittest.main()
