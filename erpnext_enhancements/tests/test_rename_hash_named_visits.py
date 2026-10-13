# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: the visits named before v1.597.0 get PRJ-#####-YYYY-V### names (v1.610.0).

Nik, 2026-10-13: "I'm still seeing the old record numbers (4lvpg04h6h) instead of PRJ-#####-V###".
``patches/rename_hash_named_visits.py`` renames them. Pinned here, through the real patch
against a fake database:

* each visit goes into the series its own autoname would have used (project, the year of its
  day), numbered in the order the visits were done, after whatever the series has used: its
  tabSeries counter or a name already in the scheme, whichever is higher;
* visits already in the scheme, with no project, or with a project name that cannot lead a
  series are left alone, so a second run does nothing;
* the old name is kept in ``legacy_name``; the visit's Calendar Sync Link rows follow it, with
  the same Google event; each series' counter ends at the highest number given out;
* ``frappe.rename_doc`` is called with the keywords Frappe 16.30 accepts (no
  ``ignore_permissions``: that wedged every migrate on 2026-08-07);
* one visit that fails is rolled back whole and logged, the rest are renamed, and the patch
  never raises (a raising patch aborts ``bench migrate``, which is the deploy).

Stubs a minimal ``frappe`` in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_rename_hash_named_visits
"""

import copy
import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
PATCH_MODULE = "erpnext_enhancements.patches.rename_hash_named_visits"
RECORD_MODULE = "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record"
COMPARE_MODULE = "erpnext_enhancements.sapphire_maintenance.visit_compare"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	PATCH_MODULE,
	RECORD_MODULE,
	COMPARE_MODULE,
)
DOCTYPE = "Sapphire Maintenance Record"
LINK = "Calendar Sync Link"

patch = None
STATE = {}
_saved = {}

D = datetime.date
DT = datetime.datetime


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Boom(Exception):
	pass


def _reset():
	STATE.clear()
	STATE.update(
		{
			"records": {},
			"links": {},
			"series": {},
			"doctypes": {DOCTYPE, LINK},
			"renames": [],
			"set_values": [],
			"errors": [],
			"savepoints": [],
			"rollbacks": [],
			"commits": 0,
			"fail": set(),
			"fail_get_all": False,
		}
	)


def _visit(name, project, creation, visit_date=None, scheduled_visit_date=None):
	STATE["records"][name] = _Dict(
		name=name,
		project=project,
		visit_date=visit_date,
		scheduled_visit_date=scheduled_visit_date,
		creation=creation,
		legacy_name=None,
	)


def _link(name, source_name, key="", event_id="erp123"):
	STATE["links"][name] = _Dict(
		name=name,
		source_doctype=DOCTYPE,
		source_name=source_name,
		source_ref=f"{DOCTYPE}|{source_name}|{key}",
		event_id=event_id,
		content_hash="abc",
	)


def _getdate(value=None):
	if value is None:
		return D(2026, 10, 13)
	if isinstance(value, DT):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **kwargs):
	if STATE["fail_get_all"]:
		raise _Boom("database gone")
	if doctype == DOCTYPE:
		assert limit_page_length == 0, "every visit, not the first page"
		rows = sorted(STATE["records"].values(), key=lambda r: str(r.creation))
		return [_Dict({f: row.get(f) for f in fields}) for row in rows]
	assert doctype == LINK
	return [
		_Dict({f: row.get(f) for f in fields})
		for row in STATE["links"].values()
		if all(row.get(k) == v for k, v in filters.items())
	]


def _exists(doctype, name=None):
	if doctype == "DocType":
		return name in STATE["doctypes"]
	assert doctype == DOCTYPE
	return name in STATE["records"]


def _sql(query, values=None, **kwargs):
	if query.strip().upper().startswith("SELECT"):
		assert "`tabSeries`" in query
		(key,) = values
		return [(STATE["series"][key],)] if key in STATE["series"] else []
	assert "INSERT INTO `tabSeries`" in query and "ON DUPLICATE KEY UPDATE" in query, query
	assert "GREATEST(COALESCE(`current`, 0), %s)" in query, "a NULL counter must not stay NULL"
	key, number, again = values
	assert number == again
	STATE["series"][key] = max(STATE["series"].get(key) or 0, number)
	return []


def _set_value(doctype, name, field, value=None, update_modified=True):
	assert update_modified is False, "a rename is not an edit"
	STATE["set_values"].append((doctype, name, field, value))
	changes = field if isinstance(field, dict) else {field: value}
	table = STATE["records"] if doctype == DOCTYPE else STATE["links"]
	table[name].update(changes)


def _rename_doc(
	doctype,
	old,
	new,
	force=False,
	merge=False,
	*,
	ignore_if_exists=False,
	show_alert=True,
	rebuild_search=True,
):
	"""Frappe 16.30's ``frappe.rename_doc``: these keywords and no others (no ignore_permissions)."""
	STATE["renames"].append(
		{
			"doctype": doctype,
			"old": old,
			"new": new,
			"force": force,
			"show_alert": show_alert,
			"rebuild_search": rebuild_search,
		}
	)
	if old in STATE["fail"]:
		raise _Boom(f"cannot rename {old}")
	row = STATE["records"].pop(old)
	row.name = new
	STATE["records"][new] = row
	return new


def _savepoint(name):
	STATE["savepoints"].append(name)
	STATE["snapshot"] = copy.deepcopy((STATE["records"], STATE["links"]))


def _rollback(save_point=None):
	assert save_point, "only ever back to the savepoint, never the whole transaction"
	STATE["rollbacks"].append(save_point)
	STATE["records"], STATE["links"] = STATE["snapshot"]


def _commit():
	STATE["commits"] += 1


def setUpModule():
	global patch
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda title=None, message=None: STATE["errors"].append((title, message))
	frappe.get_all = _get_all
	frappe.rename_doc = _rename_doc
	frappe.db = types.SimpleNamespace(
		exists=_exists,
		sql=_sql,
		set_value=_set_value,
		savepoint=_savepoint,
		rollback=_rollback,
		commit=_commit,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: "2026-10-13"
	utils.now_datetime = lambda: DT(2026, 10, 13, 9, 0)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)
	from erpnext_enhancements.patches import rename_hash_named_visits

	patch = rename_hash_named_visits


def tearDownModule():
	import erpnext_enhancements.patches as patches
	import erpnext_enhancements.sapphire_maintenance as package
	import erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record as record_package

	for owner, attr in (
		(patches, "rename_hash_named_visits"),
		(package, "visit_compare"),
		(record_package, "sapphire_maintenance_record"),
	):
		if attr in vars(owner):
			delattr(owner, attr)
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _run():
	patch.execute()
	return {row.legacy_name: name for name, row in STATE["records"].items() if row.legacy_name}


class TestTheNumbers(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_numbered_in_the_order_the_visits_were_done(self):
		"""Created out of order (the scheduler drafts ahead): the day decides, then creation."""
		_visit("4lvpg04h6h", "PRJ-00179", DT(2026, 9, 1, 8), visit_date="2026-10-06")
		_visit("2hkckpg374", "PRJ-00179", DT(2026, 9, 2, 8), scheduled_visit_date="2026-09-22")
		_visit("4ltqt3v803", "PRJ-00179", DT(2026, 9, 3, 8), visit_date="2026-09-22")
		_visit("aaaaaaaaaa", "PRJ-00228", DT(2026, 9, 4, 8), visit_date="2026-10-01")
		self.assertEqual(
			_run(),
			{
				"2hkckpg374": "PRJ-00179-2026-V001",
				"4ltqt3v803": "PRJ-00179-2026-V002",
				"4lvpg04h6h": "PRJ-00179-2026-V003",
				"aaaaaaaaaa": "PRJ-00228-2026-V001",
			},
		)

	def test_the_year_is_the_visits_own_day(self):
		"""Its date, else its scheduled date, else the day it was created: as autoname does."""
		_visit("dated", "PRJ-1", DT(2026, 1, 3), visit_date="2025-12-30", scheduled_visit_date="2026-01-05")
		_visit("planned", "PRJ-1", DT(2025, 12, 20), scheduled_visit_date="2026-01-05")
		_visit("created", "PRJ-1", DT(2025, 11, 2))
		self.assertEqual(
			_run(),
			{"dated": "PRJ-1-2025-V002", "created": "PRJ-1-2025-V001", "planned": "PRJ-1-2026-V001"},
		)

	def test_numbering_starts_after_the_counter_and_any_name_already_in_the_scheme(self):
		STATE["series"]["PRJ-00179-2026-V"] = 2
		_visit("PRJ-00179-2026-V005", "PRJ-00179", DT(2026, 10, 12), visit_date="2026-10-12")
		_visit("old1", "PRJ-00179", DT(2026, 9, 1), visit_date="2026-09-01")
		STATE["series"]["PRJ-00228-2026-V"] = 7
		_visit("old2", "PRJ-00228", DT(2026, 9, 1), visit_date="2026-09-01")
		self.assertEqual(_run(), {"old1": "PRJ-00179-2026-V006", "old2": "PRJ-00228-2026-V008"})

	def test_the_series_ends_at_the_highest_number_given_out(self):
		"""So the next visit's make_autoname("PRJ-00179-2026-V.###") takes the number after it."""
		STATE["series"]["PRJ-00179-2026-V"] = 1
		for index in range(3):
			_visit(f"old{index}", "PRJ-00179", DT(2026, 9, 1 + index), visit_date=f"2026-09-0{1 + index}")
		_visit("other", "PRJ-00500", DT(2026, 9, 1), visit_date="2027-01-04")
		_run()
		self.assertEqual(STATE["series"], {"PRJ-00179-2026-V": 4, "PRJ-00500-2027-V": 1})
		self.assertEqual(STATE["commits"], 1)

	def test_a_thousandth_visit_is_not_cut_short(self):
		STATE["series"]["PRJ-1-2026-V"] = 999
		_visit("old", "PRJ-1", DT(2026, 9, 1), visit_date="2026-09-01")
		self.assertEqual(_run(), {"old": "PRJ-1-2026-V1000"})
		self.assertEqual(STATE["series"]["PRJ-1-2026-V"], 1000)


class TestWhatIsLeftAlone(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_names_in_the_scheme_no_project_and_unusable_projects(self):
		_visit("PRJ-00179-2026-V001", "PRJ-00179", DT(2026, 10, 12))
		_visit("noproject1", None, DT(2026, 9, 1))
		_visit("parens0001", "Myers (Sanctuary)", DT(2026, 9, 1))
		_visit("dotted0001", "PRJ.1", DT(2026, 9, 1))
		self.assertEqual(_run(), {})
		self.assertEqual(STATE["renames"], [])
		self.assertEqual(STATE["series"], {})
		self.assertEqual(STATE["errors"], [])

	def test_a_second_run_does_nothing(self):
		_visit("4lvpg04h6h", "PRJ-00179", DT(2026, 9, 1), visit_date="2026-09-01")
		first = _run()
		renames = len(STATE["renames"])
		self.assertEqual(_run(), first)
		self.assertEqual(len(STATE["renames"]), renames)

	def test_a_name_already_taken_is_not_overwritten(self):
		"""The counter is raised past every name in the scheme, so this needs a counter behind a
		visit the scheme cannot see; it is logged and left, not merged."""
		_visit("old", "PRJ-1", DT(2026, 9, 1), visit_date="2026-09-01")
		STATE["records"]["PRJ-1-2026-V001"] = _Dict(
			name="PRJ-1-2026-V001", project="OTHER", creation=DT(2026, 1, 1)
		)
		original = patch.used_numbers
		patch.used_numbers = lambda names, keys: {}
		try:
			self.assertEqual(_run(), {})
		finally:
			patch.used_numbers = original
		self.assertIn("old", STATE["records"])
		self.assertEqual([title for title, _m in STATE["errors"]], ["rename_hash_named_visits"])


class TestWhatFollowsTheRename(unittest.TestCase):
	def setUp(self):
		_reset()
		_visit("4lvpg04h6h", "PRJ-00179", DT(2026, 9, 1), visit_date="2026-10-06")

	def test_the_old_name_is_kept(self):
		_run()
		self.assertEqual(STATE["records"]["PRJ-00179-2026-V001"].legacy_name, "4lvpg04h6h")

	def test_rename_doc_cascades_with_the_keywords_frappe_16_30_takes(self):
		_run()
		self.assertEqual(
			STATE["renames"],
			[
				{
					"doctype": DOCTYPE,
					"old": "4lvpg04h6h",
					"new": "PRJ-00179-2026-V001",
					"force": True,
					"show_alert": False,
					"rebuild_search": False,
				}
			],
		)
		source = (APP / "patches" / "rename_hash_named_visits.py").read_text(encoding="utf-8")
		code = source.split('"""', 2)[-1]
		self.assertNotIn("ignore_permissions=", code)
		self.assertNotIn("enqueue(", code, "a deploy flushes the queue right after the migrate")

	def test_its_calendar_rows_follow_with_the_same_google_event(self):
		_link("CSL-1", "4lvpg04h6h")
		_link("CSL-2", "4lvpg04h6h", key="crew|korben@example.com")
		_link("CSL-3", "someoneelse")
		_run()
		links = STATE["links"]
		self.assertEqual(links["CSL-1"].source_name, "PRJ-00179-2026-V001")
		self.assertEqual(links["CSL-1"].source_ref, f"{DOCTYPE}|PRJ-00179-2026-V001|")
		self.assertEqual(links["CSL-2"].source_ref, f"{DOCTYPE}|PRJ-00179-2026-V001|crew|korben@example.com")
		self.assertEqual(links["CSL-1"].event_id, "erp123", "the same Google event, not a new one")
		self.assertEqual(links["CSL-1"].content_hash, "", "the next sync writes the event again")
		self.assertEqual(links["CSL-3"].source_ref, f"{DOCTYPE}|someoneelse|")
		self.assertEqual(links["CSL-3"].content_hash, "abc")

	def test_no_calendar_doctype_no_calendar_work(self):
		STATE["doctypes"].discard(LINK)
		_run()
		self.assertFalse([row for row in STATE["set_values"] if row[0] == LINK])

	def test_the_ref_rewrite_touches_only_this_visits_name_segment(self):
		self.assertEqual(patch.rewrite_ref(f"{DOCTYPE}|old|old", "old", "new"), f"{DOCTYPE}|new|old")
		self.assertEqual(patch.rewrite_ref("Task|old|", "old", "new"), "Task|old|")
		self.assertEqual(patch.rewrite_ref(f"{DOCTYPE}|older|", "old", "new"), f"{DOCTYPE}|older|")
		self.assertEqual(patch.rewrite_ref(None, "old", "new"), "")


class TestItNeverRaises(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_one_failure_is_rolled_back_and_the_rest_go_on(self):
		_visit("first", "PRJ-1", DT(2026, 9, 1), visit_date="2026-09-01")
		_visit("broken", "PRJ-1", DT(2026, 9, 2), visit_date="2026-09-02")
		_visit("third", "PRJ-1", DT(2026, 9, 3), visit_date="2026-09-03")
		_link("CSL-1", "broken")
		STATE["fail"].add("broken")
		self.assertEqual(_run(), {"first": "PRJ-1-2026-V001", "third": "PRJ-1-2026-V003"})
		self.assertIn("broken", STATE["records"], "keeps its old name")
		self.assertEqual(STATE["links"]["CSL-1"].source_name, "broken")
		self.assertEqual(STATE["rollbacks"], ["rename_hash_named_visit"])
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertIn("broken was not renamed to PRJ-1-2026-V002", STATE["errors"][0][1])
		self.assertEqual(STATE["series"]["PRJ-1-2026-V"], 3)

	def test_a_failure_after_the_rename_undoes_the_rename(self):
		"""The calendar move failing must not leave a renamed visit with no legacy name."""
		_visit("old", "PRJ-1", DT(2026, 9, 1), visit_date="2026-09-01")
		_link("CSL-1", "old")
		original = patch.move_calendar_links

		def boom(old, new):
			raise _Boom("calendar")

		patch.move_calendar_links = boom
		try:
			self.assertEqual(_run(), {})
		finally:
			patch.move_calendar_links = original
		self.assertIn("old", STATE["records"])
		self.assertNotIn("PRJ-1-2026-V001", STATE["records"])
		self.assertNotIn("PRJ-1-2026-V", STATE["series"], "nothing given out, nothing counted")

	def test_a_broken_database_is_logged_not_raised(self):
		_visit("old", "PRJ-1", DT(2026, 9, 1))
		STATE["fail_get_all"] = True
		patch.execute()
		self.assertEqual([title for title, _m in STATE["errors"]], ["rename_hash_named_visits"])

	def test_a_site_without_the_doctype_is_left_alone(self):
		STATE["doctypes"].discard(DOCTYPE)
		STATE["fail_get_all"] = True
		patch.execute()
		self.assertEqual(STATE["errors"], [])


class TestTheWiring(unittest.TestCase):
	def test_it_runs_after_model_sync_so_legacy_name_exists(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		entry = "erpnext_enhancements.patches.rename_hash_named_visits"
		self.assertIn(entry, lines)
		self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))

	def test_the_field_and_its_search(self):
		import json

		meta = json.loads(
			(
				APP
				/ "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.json"
			).read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(fields["legacy_name"]["fieldtype"], "Data")
		self.assertEqual(fields["legacy_name"]["read_only"], 1)
		self.assertEqual(fields["legacy_name"]["search_index"], 1)
		self.assertIn("legacy_name", [f.strip() for f in meta["search_fields"].split(",")])
		self.assertIn("legacy_name", meta["field_order"])

	def test_the_scheme_is_the_controllers(self):
		"""The patch's idea of a name in the scheme must match what autoname makes."""
		self.assertTrue(patch.NEW_STYLE.search("PRJ-00179-2026-V001"))
		self.assertTrue(patch.NEW_STYLE.search("Myers Sanctuary-2026-V012"))
		self.assertFalse(patch.NEW_STYLE.search("4lvpg04h6h"))
		self.assertEqual(patch.number_of("PRJ-00179-2026-V1000"), 1000)
		source = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.py"
		).read_text(encoding="utf-8")
		self.assertIn('make_autoname(f"{project}-{year}-V.###", doc=self)', source)


if __name__ == "__main__":
	unittest.main()
