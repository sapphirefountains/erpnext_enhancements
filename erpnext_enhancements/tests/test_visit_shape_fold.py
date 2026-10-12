# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: switching a contract to one visit for the site folds its drafts (v1.599.0).

D4 of Phase D (TASK-2026-02489). Hardware Apartments' contract (MNT-CON-2026-00008) was Per
Feature, with four bare drafts for 19 October, one per fountain. Switched to Per Site Visit
alone, the scheduler would not draft the site visit until all four were finished (it drafts
one only while the contract has no open regular draft). ``visit_shape`` folds them, and
deleting a draft cannot be undone, so it is careful:

* only the next trip's bare drafts fold: dated today or later, never opened, nothing on them,
  planned the same way (day, technician, crew); the earliest is kept, converted in place;
* overdue, opened or started, differently planned and linked drafts are left and named;
* a contract that already has a site visit folds nothing;
* a deleted draft's open ToDos are canceled in the same transaction;
* only this save's switch to Per Site Visit does it.

Stubs a minimal ``frappe`` in ``setUpModule``; runs the real ``visit_shape``.

Run: python -m unittest erpnext_enhancements.tests.test_visit_shape_fold
"""

import datetime
import inspect
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
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.delete_doc",
	"frappe.core",
	"frappe.core.doctype",
	"frappe.core.doctype.comment",
	"frappe.core.doctype.comment.comment",
	MODULE,
)
CONTRACT = "MNT-CON-2026-00008"
AUSTIN = "austin.healey@sapphirefountains.com"

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


class _Contract(_Dict):
	def get_doc_before_save(self):
		return self["_before"]


def _is_set(value):
	return value not in (None, "")


def _get_all(doctype, filters=None, pluck=None, order_by=None, limit=None, fields=None, **kwargs):
	if doctype == "Comment":
		return _comments(filters, pluck, fields, order_by)
	if doctype == "ToDo":
		assert filters["status"] == "Open"
		return [
			t.name
			for t in STATE["todos"]
			if t.reference_type == filters["reference_type"]
			and t.reference_name == filters["reference_name"]
			and t.status == "Open"
		]
	assert doctype == RECORD
	assert filters["docstatus"] == 0 and filters["visit_label"] == ["is", "not set"], filters
	want_serial = filters["serial_no"] == ["is", "set"]
	rows = [
		r
		for r in STATE["records"].values()
		if r.maintenance_contract == filters["maintenance_contract"]
		and r.docstatus == 0
		and not r.visit_label
		and _is_set(r.serial_no) == want_serial
	]
	if order_by:
		assert order_by == "scheduled_visit_date asc, creation asc"
		rows.sort(key=lambda r: (r.scheduled_visit_date or "", r.creation))
	names = [r.name for r in rows]
	return names[:limit] if limit else names


def _exists(doctype, filters):
	if doctype == "File":
		return filters["attached_to_name"] in STATE["files"]
	raise AssertionError(doctype)


def _comments(filters=None, pluck=None, fields=None, order_by=None):
	assert filters["reference_doctype"] == RECORD and filters["comment_type"] == "Comment"
	rows = [c for c in STATE["comments"] if c.reference_name == filters["reference_name"]]
	if pluck:
		return [c[pluck] for c in rows]
	if order_by:
		assert order_by == "creation asc"
		rows = sorted(rows, key=lambda c: c.creation)
	return [_Dict({f: c.get(f) for f in fields}) for c in rows]


def _get_doc(arg, name=None):
	if isinstance(arg, dict):
		assert arg["doctype"] == "Comment"
		comment = _Dict(arg)

		def _db_insert():
			comment.name = f"CMT-NEW-{len(STATE['comments'])}"
			STATE["comments"].append(comment)

		comment.db_insert = _db_insert  # never insert(): it re-authors and re-notifies
		return comment
	return STATE["records"][name]


def _set_value(doctype, name, field, value):
	STATE["set_values"].append((doctype, name, field, value))
	if doctype == RECORD:
		STATE["records"][name][field] = value
	elif doctype == "ToDo":
		next(t for t in STATE["todos"] if t.name == name)[field] = value


def _delete_doc(doctype, name, ignore_permissions=False):
	assert doctype == RECORD and ignore_permissions
	assert not STATE["flags"].mute_messages, "the delete itself is not muted: its real errors show"
	del STATE["records"][name]
	STATE["deleted"].append(name)


def _check_linked(doc, method="Delete"):
	assert STATE["flags"].mute_messages, "a refusal is named in the summary, not shown as a dialog"
	STATE["checked"].append(doc.name)
	if doc.name in STATE["linked"]:
		raise _LinkExistsError(doc.name)


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
	frappe.get_doc = _get_doc
	frappe.delete_doc = _delete_doc
	frappe.msgprint = lambda message, **kwargs: STATE["messages"].append(message)
	frappe.flags = types.SimpleNamespace(mute_messages=False)
	frappe.db = types.SimpleNamespace(
		exists=_exists, set_value=_set_value, has_column=lambda doctype, column: doctype == "Comment"
	)
	core = types.ModuleType("frappe.core")
	core_doctype = types.ModuleType("frappe.core.doctype")
	comment_package = types.ModuleType("frappe.core.doctype.comment")
	comment_module = types.ModuleType("frappe.core.doctype.comment.comment")
	comment_module.update_comment_in_doc = lambda doc: STATE["cache_updates"].append(doc.name)
	sys.modules.update(
		{
			"frappe.core": core,
			"frappe.core.doctype": core_doctype,
			"frappe.core.doctype.comment": comment_package,
			"frappe.core.doctype.comment.comment": comment_module,
		}
	)
	utils = types.ModuleType("frappe.utils")
	utils.formatdate = lambda value: f"[{value}]"
	utils.getdate = lambda value: datetime.date.fromisoformat(str(value)[:10])
	utils.nowdate = lambda: "2026-10-12"
	utils.escape_html = lambda text: text.replace("<", "&lt;")
	frappe.utils = utils
	model = types.ModuleType("frappe.model")
	delete_doc = types.ModuleType("frappe.model.delete_doc")
	delete_doc.check_if_doc_is_linked = _check_linked
	# As on prod: every draft under the maintenance workflow has an open Workflow Action, which
	# delete_doc clears in on_trash before its own link check.
	delete_doc.get_dynamic_linked_docs = lambda doc, method="Delete": [
		{"reference_doctype": "Workflow Action", "reference_docname": f"WA-{doc.name}"}
	] + [
		{"reference_doctype": "Stock Scan Log", "reference_docname": "SSL-1"}
		for _ in [1]
		if doc.name in STATE["dyn_linked"]
	]
	model.delete_doc = delete_doc
	frappe.model = model
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.delete_doc": delete_doc,
		}
	)
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
			"todos": [],
			"files": set(),
			"comments": [],
			"cache_updates": [],
			"linked": set(),
			"dyn_linked": set(),
			"set_values": [],
			"deleted": [],
			"checked": [],
			"messages": [],
			"flags": sys.modules["frappe"].flags,
		}
	)
	STATE["flags"].mute_messages = False


def _draft(name, serial, day="2026-10-19", contract=CONTRACT, **extra):
	record = _Dict(
		name=name,
		maintenance_contract=contract,
		serial_no=serial,
		scheduled_visit_date=day,
		creation=f"2026-10-12 06:00:{len(STATE['records']):02d}",
		docstatus=0,
		workflow_state="Draft",
		visit_label=None,
		technician=AUSTIN,
		crew=[],
		maintenance_results=[],
		chemistry_readings=[],
		cleaning_tasks=[],
		consumables=[],
	)
	record.update(extra)
	STATE["records"][name] = record
	STATE["todos"].append(
		_Dict(name=f"TODO-{name}", reference_type=RECORD, reference_name=name, status="Open")
	)
	return record


def _hardware():
	"""Prod on 2026-10-12: four bare drafts for 19 October, one ToDo each, plus the Winterization."""
	_draft("4lpc1ncnah", "MAINT-HARDWARE-East-Courtyard")
	_draft("4lq1bpi8uk", "MAINT-HARDWARE-East-Lobby")
	_draft("4lr8msbd4j", "MAINT-HARDWARE-West-Courtyard")
	_draft("4lsbmepacj", "MAINT-HARDWARE-West-Lobby")
	_draft("lld24i3de0", None, day="2026-10-24", visit_label="Winterization")


def _fold():
	return shape.fold_feature_drafts(CONTRACT)


class TestHardware(unittest.TestCase):
	def setUp(self):
		_reset()
		_hardware()

	def test_hardwares_half_done_fold_finishes(self):
		"""Prod after the v1.599.1 run: 4lpc1ncnah converted, the other three left as linked by
		their own Workflow Actions. Switched again, they fold into it."""
		STATE["records"]["4lpc1ncnah"].serial_no = None
		result = _fold()
		self.assertEqual(result["kept"], "4lpc1ncnah")
		self.assertEqual(result["removed"], ["4lq1bpi8uk", "4lr8msbd4j", "4lsbmepacj"])
		self.assertEqual(result["linked"], [])

	def test_four_bare_drafts_become_one_site_visit(self):
		result = _fold()
		self.assertEqual(result["kept"], "4lpc1ncnah")
		self.assertEqual(result["removed"], ["4lq1bpi8uk", "4lr8msbd4j", "4lsbmepacj"])
		for key in ("started", "different", "linked", "overdue", "beside"):
			self.assertEqual(result[key], [], key)
		self.assertIsNone(
			STATE["records"]["4lpc1ncnah"].serial_no, "a site visit has no water feature of its own"
		)
		self.assertEqual(
			sorted(STATE["records"]), ["4lpc1ncnah", "lld24i3de0"], "the Winterization is separate"
		)

	def test_the_kept_draft_is_converted_in_place_with_no_save_hooks(self):
		"""A full save would run _autofill_clock_in and could stamp another day's kiosk time."""
		_fold()
		self.assertEqual(STATE["set_values"][0], (RECORD, "4lpc1ncnah", "serial_no", None))
		self.assertNotIn(".save(", inspect.getsource(shape.fold_feature_drafts))

	def test_the_deleted_drafts_todos_are_canceled_in_the_same_transaction(self):
		"""delete_doc removes them only in a job queued after commit, which a deploy flushes."""
		_fold()
		status = {t.reference_name: t.status for t in STATE["todos"]}
		self.assertEqual(
			status,
			{
				"4lpc1ncnah": "Open",
				"4lq1bpi8uk": "Cancelled",
				"4lr8msbd4j": "Cancelled",
				"4lsbmepacj": "Cancelled",
				"lld24i3de0": "Open",
			},
		)

	def test_a_linked_draft_is_left_and_the_delete_itself_is_not_muted(self):
		STATE["linked"].add("4lr8msbd4j")
		result = _fold()
		self.assertEqual(result["linked"], ["4lr8msbd4j"])
		self.assertEqual(result["removed"], ["4lq1bpi8uk", "4lsbmepacj"])
		self.assertIn("4lr8msbd4j", STATE["records"])
		self.assertFalse(STATE["flags"].mute_messages)


def _comment(reference, content, by="nikolas@example.com", parent=None):
	name = f"CMT-{len(STATE['comments'])}"
	STATE["comments"].append(
		_Dict(
			name=name,
			reference_doctype=RECORD,
			reference_name=reference,
			comment_type="Comment",
			content=content,
			comment_email=by,
			comment_by=by,
			owner=by,
			creation=f"2026-10-12 07:{len(STATE['comments']):02d}:00",
			custom_parent_comment=parent,
		)
	)
	return name


class TestComments(unittest.TestCase):
	"""v1.599.1: on 2026-10-12 every Hardware draft carried the scheduler's training warning and
	the Planner's conflict note, so counting a comment as work left all four unfolded."""

	def setUp(self):
		_reset()
		_hardware()
		training = "<!-- training-compliance:636275e86857 --><p><b>Required training is not current</b>"
		conflict = (
			"Kept a conflict on Mon Oct 12: Austin Healey: Booked on a day off. Reason: Working holiday"
		)
		for name in ("4lpc1ncnah", "4lq1bpi8uk", "4lr8msbd4j", "4lsbmepacj"):
			_comment(name, training, by="Administrator")
			_comment(name, conflict)
		_comment("4lpc1ncnah", "Scheduled over a conflict on the Maintenance Planner: Over by 2h")
		self.gate = _comment("4lr8msbd4j", "Gate code is 4471 now")
		_comment("4lr8msbd4j", "@Austin bring the fitting", parent=self.gate)
		# The same warning on another day: a different reason, the same people and courses.
		_comment(
			"4lsbmepacj",
			"<!-- training-compliance:636275e86857 --><p><b>Required training is overdue</b>",
			by="Administrator",
		)

	def test_drafts_with_comments_still_fold(self):
		result = _fold()
		self.assertEqual(result["kept"], "4lpc1ncnah")
		self.assertEqual(result["removed"], ["4lq1bpi8uk", "4lr8msbd4j", "4lsbmepacj"])
		self.assertEqual(result["started"], [])

	def test_their_comments_move_onto_the_site_visit_once_each(self):
		result = _fold()
		on_site_visit = [c for c in STATE["comments"] if c.reference_name == "4lpc1ncnah"]
		self.assertEqual(
			len(on_site_visit), 5, "training, conflict, Planner note, the gate code and its reply"
		)
		self.assertEqual(result["comments"], 2)
		self.assertIn("Their comments are on 4lpc1ncnah now.", shape.describe(result))

	def test_a_copy_keeps_its_author_and_time_and_notifies_nobody(self):
		"""insert() would make whoever saved the contract the author (the timeline shows owner;
		only the owner may edit) and send the @mention again in their name."""
		_fold()
		gate = next(c for c in STATE["comments"] if c.reference_name == "4lpc1ncnah" and "4471" in c.content)
		self.assertEqual(
			(gate.owner, gate.modified_by, gate.comment_email, gate.creation, gate.modified),
			(
				"nikolas@example.com",
				"nikolas@example.com",
				"nikolas@example.com",
				"2026-10-12 07:09:00",
				"2026-10-12 07:09:00",
			),
		)
		self.assertNotIn("insert", gate, "db_insert only: no after_insert, so no mention emails")
		self.assertEqual(len(STATE["cache_updates"]), 2, "the visit's comment cache, as insert would")

	def test_a_reply_points_at_its_parents_copy(self):
		_fold()
		copies = {c.content: c for c in STATE["comments"] if c.reference_name == "4lpc1ncnah"}
		self.assertEqual(
			copies["@Austin bring the fitting"].custom_parent_comment, copies["Gate code is 4471 now"].name
		)

	def test_a_training_warning_is_the_same_by_its_marker_not_its_text(self):
		_fold()
		training = [
			c
			for c in STATE["comments"]
			if c.reference_name == "4lpc1ncnah" and "training-compliance" in c.content
		]
		self.assertEqual(len(training), 1)


class TestWhatIsLeft(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_an_overdue_draft_stays_a_missed_visit_and_is_never_the_site_visit(self):
		"""Fountain A's 28 September form was never filled in, so the scheduler made no new one."""
		_draft("missed", "SN-A", day="2026-09-28")
		_draft("undated", "SN-D", day=None)
		_draft("b", "SN-B")
		_draft("c", "SN-C")
		result = _fold()
		self.assertEqual(result["overdue"], ["undated", "missed"])
		self.assertEqual((result["kept"], result["removed"]), ("b", ["c"]))
		self.assertEqual(STATE["records"]["missed"].serial_no, "SN-A")

	def test_a_draft_opened_or_started_is_left_exactly_as_it_was(self):
		cases = {
			"opened (rows built, answers maybe on a phone)": {"cleaning_tasks": [_Dict(task="Skim")]},
			"notes": {"visit_notes": "Gate code changed"},
			"safety": {"safety_acknowledged": 1},
			"clock": {"clock_in_time": "2026-10-19 08:00:00"},
			"date": {"visit_date": "2026-10-18"},
			"review": {"workflow_state": "Pending Review"},
			"photo": {"fountain_photo": "/private/files/f.jpg"},
			"attachment": {"_file": True},
			"invoice": {"sales_invoice": "ACC-SINV-2026-00001"},
			"digest texted": {"dispatch_digest_sent_on": "2026-10-19"},
			"crew digest texted": {"crew": [_Dict(user="korben@example.com", digest_sent_on="2026-10-19")]},
		}
		for label, extra in cases.items():
			with self.subTest(label=label):
				_reset()
				if extra.pop("_file", None):
					STATE["files"].add("started")
				_draft("started", "SN-A", **extra)
				_draft("bare", "SN-B")
				result = _fold()
				self.assertEqual((result["kept"], result["started"]), ("bare", ["started"]))
				self.assertEqual(STATE["records"]["started"].serial_no, "SN-A")
				self.assertNotIn("started", STATE["deleted"])

	def test_a_draft_planned_differently_is_left(self):
		"""A date, technician or crew somebody set in the Maintenance Planner is not thrown away."""
		_draft("kept", "SN-A", crew=[_Dict(user="korben@example.com")])
		_draft("same", "SN-B", crew=[_Dict(user="korben@example.com")])
		_draft("other_day", "SN-C", day="2026-10-21", crew=[_Dict(user="korben@example.com")])
		_draft("other_tech", "SN-D", technician="jesse@example.com", crew=[_Dict(user="korben@example.com")])
		_draft("other_crew", "SN-E")
		result = _fold()
		self.assertEqual((result["kept"], result["removed"]), ("kept", ["same"]))
		self.assertEqual(
			result["different"], ["other_tech", "other_crew", "other_day"], "by date, as they were drafted"
		)

	def test_a_site_visit_somebody_started_takes_nothing(self):
		"""Never two site visits for one trip, and never folded into work in progress."""
		_draft("site", None, visit_notes="Pump noisy")
		_draft("a", "SN-A")
		_draft("b", "SN-B")
		result = _fold()
		self.assertEqual((result["site_visit"], result["beside"], result["kept"]), ("site", ["a", "b"], None))
		self.assertEqual((STATE["deleted"], STATE["set_values"]), ([], []))

	def test_a_bare_site_visit_takes_the_drafts_planned_like_it(self):
		"""v1.599.2: switched back and forth, or a fold left half done, as Hardware's was on
		2026-10-12 (the site visit made, three drafts left): the drafts fold into it."""
		_draft("site", None)
		_draft("a", "SN-A")
		_draft("b", "SN-B", day="2026-10-26")
		result = _fold()
		self.assertEqual((result["kept"], result["removed"], result["different"]), ("site", ["a"], ["b"]))
		self.assertEqual(
			STATE["set_values"][0][1:], ("TODO-a", "status", "Cancelled"), "no conversion: it is one"
		)

	def test_a_draft_linked_by_more_than_its_workflow_action_is_left(self):
		STATE["dyn_linked"].add("b")
		_draft("a", "SN-A")
		_draft("b", "SN-B")
		result = _fold()
		self.assertEqual((result["kept"], result["linked"]), ("a", ["b"]))

	def test_nothing_bare_folds_nothing(self):
		_draft("a", "SN-A", visit_notes="x")
		result = _fold()
		self.assertEqual((result["kept"], result["started"]), (None, ["a"]))
		self.assertEqual(STATE["set_values"], [])

	def test_another_contracts_drafts_are_not_looked_at(self):
		_draft("ours", "SN-A")
		_draft("theirs", "SN-Z", contract="MNT-CON-2026-00001")
		_fold()
		self.assertEqual(STATE["records"]["theirs"].serial_no, "SN-Z")


class TestOnlyTheSwitch(unittest.TestCase):
	def setUp(self):
		_reset()
		_hardware()

	def _save(self, shape_now, shape_before):
		before = _Dict(visit_shape=shape_before) if shape_before else None
		shape.on_contract_update(_Contract(name=CONTRACT, visit_shape=shape_now, _before=before))

	def test_switching_to_one_visit_folds_and_says_so(self):
		self._save("Per Site Visit", "Per Feature")
		self.assertEqual(len(STATE["records"]), 2)
		(message,) = STATE["messages"]
		self.assertIn("Visit 4lpc1ncnah on [2026-10-19] now covers every water feature", message)
		self.assertIn("Removed 3 per-feature drafts for the same trip that nobody had opened", message)

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

	def test_every_reason_a_draft_was_left_is_said(self):
		self.assertEqual(
			shape.describe(
				{
					"started": ["s"],
					"different": ["d"],
					"linked": ["l"],
					"overdue": ["o"],
				}
			).split("<br>"),
			[
				"Left, because someone has opened or started them: s. Finish or delete them; until then they "
				"stand beside the site visit.",
				"Left, because they are planned for another day, technician or crew: d.",
				"Left, because other records link to them: l.",
				"Left as missed visits, because their date has passed: o.",
			],
		)


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
