# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Visit Review page (``api/maintenance_review.py`` and its wiring).

A1 of the 2026-10-12 Maintenance tools plan (TASK-2026-02474). Pinned here:

* **the role gate**, in code as well as on the Page: Maintenance Reviewer, Maintenance
  Supervisor, System Manager, and nobody else, on every endpoint;
* **the queue**: every visit in Pending Review, longest waiting first, waiting counted from
  when it last went for review (the workflow's own timeline comment);
* **Approve & Submit and Send Back** go through ``apply_workflow`` as the reviewer, found by
  where the transition leads; the person who started a visit is told another reviewer
  approves it and is offered no Approve; a supervisor without the reviewer role is offered
  nothing; a visit changed since it was shown, or no longer waiting, is refused;
* **Send Back needs a note**, which goes on the timeline (escaped) and to the technician;
* **the wiring**: the Page and the report carry the same roles as the code, sit in the
  Sapphire Maintenance module and the workspace, and the page calls only endpoints that exist.

The workflow, the digest and the missed-visit rows are stubbed; their own suites
(``test_visit_review_digest``, ``test_visit_compare``, ``test_missed_visits``) cover them.

Run: python -m unittest erpnext_enhancements.tests.test_visit_review
"""

import ast
import datetime
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RECORD = "Sapphire Maintenance Record"
API = "erpnext_enhancements.api.maintenance_review"
PACKAGE = "erpnext_enhancements.sapphire_maintenance"
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.workflow",
	f"{PACKAGE}.review_digest",
	f"{PACKAGE}.missed_visits",
	API,
)

review = None
STATE = {}
_saved = {}
_saved_attrs = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class PermissionError_(Exception):
	pass


class DoesNotExistError_(Exception):
	pass


class Thrown(Exception):
	pass


class _Visit(_Dict):
	def check_permission(self, ptype):
		STATE["checked"].append((self.name, ptype))

	def add_comment(self, kind, text):
		STATE["comments"].append((self.name, kind, text))


WORKFLOW = _Dict(
	states=[
		_Dict(state="Draft", doc_status="0"),
		_Dict(state="Pending Review", doc_status="0"),
		_Dict(state="Final/Submitted", doc_status="1"),
	],
	transitions=[
		_Dict(
			state="Draft",
			action="Request Review",
			next_state="Pending Review",
			allowed="Maintenance User",
			allow_self_approval=1,
		),
		_Dict(
			state="Pending Review",
			action="Approve & Submit",
			next_state="Final/Submitted",
			allowed="Maintenance Reviewer",
			allow_self_approval=0,
		),
		_Dict(
			state="Pending Review",
			action="Send Back",
			next_state="Draft",
			allowed="Maintenance Reviewer",
			allow_self_approval=1,
		),
	],
)


def _reset():
	STATE.clear()
	STATE.update(
		{
			"user": "lisa@example.com",
			"roles": ["Maintenance Reviewer", "Maintenance Supervisor", "Maintenance User"],
			"visits": {},
			"workflow_comments": [],
			"comments": [],
			"applied": [],
			"checked": [],
			"emailed": [],
			"errors": [],
			"missed": [],
			"missed_raises": False,
		}
	)


def _add_visit(name, modified, owner="Administrator", state="Pending Review", docstatus=0, **extra):
	STATE["visits"][name] = _Visit(
		name=name,
		doctype=RECORD,
		owner=owner,
		workflow_state=state,
		docstatus=docstatus,
		modified=modified,
		project="PRJ-1",
		completion_percent=extra.pop("completion", 100),
		technician=extra.pop("technician", "austin@example.com"),
		**extra,
	)


def _went_for_review(name, when):
	STATE["workflow_comments"].append(_Dict(reference_name=name, creation=when, content="Pending Review"))


# ------------------------------------------------------------------ stubs


def _throw(message, exc=None):
	raise (exc or Thrown)(message)


def _get_all(doctype, filters=None, fields=None, order_by=None, limit=None, **kwargs):
	if doctype == RECORD:
		rows = [
			v
			for v in STATE["visits"].values()
			if v.docstatus == filters["docstatus"] and v.workflow_state == filters["workflow_state"]
		]
		return [_Dict(name=v.name, modified=v.modified) for v in rows]
	if doctype == "Comment":
		assert filters["comment_type"] == "Workflow" and filters["content"] == "Pending Review"
		names = filters["reference_name"][1]
		rows = [c for c in STATE["workflow_comments"] if c.reference_name in names]
		return sorted(rows, key=lambda c: c.creation, reverse=True)
	raise AssertionError(doctype)


def _get_doc(doctype, name):
	assert doctype == RECORD
	return STATE["visits"][name]


def _get_value(doctype, name, fields, **kwargs):
	visit = STATE["visits"].get(name)
	return tuple(visit.get(f) for f in fields) if visit else None


def _get_datetime(value):
	if isinstance(value, datetime.datetime):
		return value
	return datetime.datetime.fromisoformat(str(value))


def _get_transitions(doc, workflow=None):
	return [t for t in WORKFLOW.transitions if t.state == doc.workflow_state and t.allowed in STATE["roles"]]


def _has_approval_access(user, doc, transition):
	# frappe.model.workflow.has_approval_access, verbatim (version-16).
	return user == "Administrator" or transition.get("allow_self_approval") or user != doc.get("owner")


def _apply_workflow(doc, action):
	STATE["applied"].append((doc.name, action, STATE["user"]))
	transition = next(t for t in WORKFLOW.transitions if t.action == action and t.state == doc.workflow_state)
	doc.workflow_state = transition.next_state
	if transition.next_state == "Final/Submitted":
		doc.docstatus = 1
	return doc


def setUpModule():
	global review
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError_
	frappe.throw = _throw
	frappe.get_roles = lambda *a: list(STATE["roles"])
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title"))
	frappe.db = types.SimpleNamespace(
		exists=lambda doctype, name: name in STATE["visits"], get_value=_get_value
	)

	class _Session:
		@property
		def user(self):
			return STATE["user"]

	frappe.session = _Session()

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.escape_html = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.get_datetime = _get_datetime
	utils.getdate = lambda v=None: (
		v.date() if isinstance(v, datetime.datetime) else datetime.date(2026, 10, 12)
	)
	utils.nowdate = lambda: "2026-10-12"
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	workflow = types.ModuleType("frappe.model.workflow")
	workflow.get_transitions = _get_transitions
	workflow.get_workflow = lambda doctype: WORKFLOW
	workflow.get_workflow_name = lambda doctype: "Sapphire Maintenance Workflow"
	workflow.has_approval_access = _has_approval_access
	workflow.apply_workflow = _apply_workflow
	model.workflow = workflow
	frappe.model = model

	digest = types.ModuleType(f"{PACKAGE}.review_digest")

	def _build_context(doc):
		return {
			"site": "Highlands Plaza",
			"visit_date": "10-12-2026",
			"technician_name": "Austin Healey",
			"url": f"/desk/sapphire-maintenance-record/{doc.name}",
			"warnings": ["Warning: 1 reading out of range: pH."],
			"summary": [("Visit", doc.name), ("Crew", "Korben Jessop")],
			"blocks": [],
			"notes_heading": "Visit notes",
			"notes": None,
			"first_visit": doc.name == "FIRST",
			"out_of_range": ["pH"],
			"unanswered": [],
			"photo_count": 2,
		}

	def _send_sent_back(doc, note, reviewer=None):
		STATE["emailed"].append((doc.name, note, reviewer))
		return ["austin@example.com"]

	digest.build_context = _build_context
	digest.send_sent_back = _send_sent_back

	missed = types.ModuleType(f"{PACKAGE}.missed_visits")

	def _missed_visits(today=None, **kwargs):
		if STATE["missed_raises"]:
			raise RuntimeError("missed on fire")
		return STATE["missed"]

	missed.missed_visits = _missed_visits

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.workflow": workflow,
			f"{PACKAGE}.review_digest": digest,
			f"{PACKAGE}.missed_visits": missed,
		}
	)
	import erpnext_enhancements.api as api
	import erpnext_enhancements.sapphire_maintenance as package

	for attr, module in (("review_digest", digest), ("missed_visits", missed)):
		_saved_attrs[attr] = vars(package).get(attr)
		setattr(package, attr, module)
	# A stale attribute would be taken over the fresh import below.
	if "maintenance_review" in vars(api):
		delattr(api, "maintenance_review")

	from erpnext_enhancements.api import maintenance_review

	review = maintenance_review


def tearDownModule():
	import erpnext_enhancements.api as api
	import erpnext_enhancements.sapphire_maintenance as package

	if "maintenance_review" in vars(api):
		delattr(api, "maintenance_review")
	for attr, module in _saved_attrs.items():
		if module is None:
			if hasattr(package, attr):
				delattr(package, attr)
		else:
			setattr(package, attr, module)
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()
		_add_visit("OLD", "2026-10-12 09:00:00")
		_add_visit("NEW", "2026-10-12 08:00:00")
		_add_visit("DRAFT", "2026-10-11 08:00:00", state="Draft")
		_went_for_review("OLD", datetime.datetime(2026, 10, 6, 16, 0))
		_went_for_review("NEW", datetime.datetime(2026, 10, 12, 7, 30))


class TestTheRoleGate(_Base):
	def test_everyone_else_is_refused_on_every_endpoint(self):
		STATE["roles"] = ["Maintenance User", "Projects Manager"]
		calls = (
			lambda: review.get_review_queue(),
			lambda: review.get_review_visit("OLD"),
			lambda: review.approve_visit("OLD"),
			lambda: review.send_back_visit("OLD", note="fix it"),
		)
		for call in calls:
			with self.subTest(call=call), self.assertRaises(PermissionError_):
				call()
		self.assertEqual(STATE["applied"], [])

	def test_the_roles_are_the_reviewers_the_supervisors_and_system_managers(self):
		self.assertEqual(
			set(review.REVIEW_ROLES), {"Maintenance Reviewer", "Maintenance Supervisor", "System Manager"}
		)


class TestTheQueue(_Base):
	def test_longest_waiting_first_counted_from_going_for_review(self):
		"""OLD was modified later (a reviewer's edit) but has waited since the 6th."""
		data = review.get_review_queue()
		self.assertEqual([row["name"] for row in data["waiting"]], ["OLD", "NEW"])
		self.assertEqual([row["days_waiting"] for row in data["waiting"]], [6, 0])
		self.assertNotIn("DRAFT", [row["name"] for row in data["waiting"]])

	def test_a_row_carries_what_a_reviewer_looks_for(self):
		row = review.get_review_queue()["waiting"][0]
		self.assertEqual(
			{
				k: row[k]
				for k in ("site", "technician_name", "crew", "out_of_range", "photo_count", "completion")
			},
			{
				"site": "Highlands Plaza",
				"technician_name": "Austin Healey",
				"crew": "Korben Jessop",
				"out_of_range": ["pH"],
				"photo_count": 2,
				"completion": 100,
			},
		)

	def test_a_visit_with_no_workflow_comment_waits_from_its_last_change(self):
		STATE["workflow_comments"] = []
		self.assertEqual([row["name"] for row in review.get_review_queue()["waiting"]], ["NEW", "OLD"])

	def test_the_missed_visits_ride_along_and_a_failure_is_logged(self):
		STATE["missed"] = [
			{
				"kind": "Not started",
				"site": "Highlands Plaza",
				"fountain": None,
				"visit_label": None,
				"due_date": datetime.date(2026, 10, 7),
				"days_late": 5,
				"technician_name": "Austin Healey",
				"completion": 0,
				"record": "V-9",
				"contract": None,
			}
		]
		data = review.get_review_queue()
		self.assertEqual((data["missed"][0]["due_date"], data["missed_failed"]), ("2026-10-07", False))
		STATE["missed_raises"] = True
		data = review.get_review_queue()
		self.assertEqual((data["missed"], data["missed_failed"]), ([], True))
		self.assertEqual(STATE["errors"], ["Visit Review: missed visits not listed"])

	def test_only_reviewers_are_told_they_can_review(self):
		"""The transitions name Maintenance Reviewer, so a System Manager without it decides nothing."""
		self.assertTrue(review.get_review_queue()["can_review"])
		for roles in (["Maintenance Supervisor"], ["System Manager"]):
			STATE["roles"] = roles
			with self.subTest(roles=roles):
				self.assertFalse(review.get_review_queue()["can_review"])


class TestOneVisit(_Base):
	def test_a_reviewer_may_approve_or_send_back(self):
		data = review.get_review_visit("NEW")
		self.assertEqual(
			(data["can_approve"], data["can_send_back"], data["approve_blocked"]), (True, True, None)
		)
		self.assertEqual(
			(data["position"], data["total"], data["previous"], data["next"]), (2, 2, "OLD", "OLD")
		)
		self.assertEqual(data["context"]["site"], "Highlands Plaza")
		self.assertIn(("NEW", "read"), STATE["checked"])

	def test_the_person_who_started_it_cannot_approve_it(self):
		STATE["visits"]["NEW"].owner = "lisa@example.com"
		data = review.get_review_visit("NEW")
		self.assertFalse(data["can_approve"])
		self.assertTrue(data["can_send_back"], "Send Back allows self approval in the fixture")
		self.assertEqual(data["approve_blocked"], "You started this visit, so another reviewer approves it.")

	def test_the_technician_or_crew_of_a_scheduler_drafted_visit_cannot_approve_it(self):
		visit = STATE["visits"]["NEW"]
		self.assertEqual(visit.owner, "Administrator")
		for change in ({"technician": "lisa@example.com"}, {"crew": [_Dict(user="lisa@example.com")]}):
			_reset()
			_add_visit("NEW", "2026-10-12 08:00:00", **change)
			with self.subTest(change=change):
				data = review.get_review_visit("NEW")
				self.assertFalse(data["can_approve"])
				self.assertTrue(data["can_send_back"])
				self.assertEqual(
					data["approve_blocked"], "You did this visit, so another reviewer approves it."
				)

	def test_another_reviewer_may_approve_it(self):
		STATE["visits"]["NEW"].technician = "james@example.com"
		self.assertTrue(review.get_review_visit("NEW")["can_approve"])

	def test_a_supervisor_who_is_not_a_reviewer_is_offered_nothing(self):
		STATE["roles"] = ["Maintenance Supervisor"]
		data = review.get_review_visit("NEW")
		self.assertEqual((data["can_approve"], data["can_send_back"]), (False, False))

	def test_a_visit_no_longer_waiting_offers_nothing(self):
		data = review.get_review_visit("DRAFT")
		self.assertEqual((data["waiting"], data["can_approve"], data["position"]), (False, False, None))
		self.assertEqual(data["next"], "OLD")

	def test_an_unknown_visit_is_not_found(self):
		with self.assertRaises(DoesNotExistError_):
			review.get_review_visit("NOPE")


class TestApprove(_Base):
	def test_it_applies_the_approve_transition_as_the_reviewer(self):
		result = review.approve_visit("OLD", modified="2026-10-12 09:00:00")
		self.assertEqual(STATE["applied"], [("OLD", "Approve & Submit", "lisa@example.com")])
		self.assertEqual(
			(result["state"], result["docstatus"], result["next"]), ("Final/Submitted", 1, "NEW")
		)

	def test_a_visit_changed_since_it_was_shown_is_refused(self):
		with self.assertRaisesRegex(Thrown, "changed since you opened it"):
			review.approve_visit("OLD", modified="2026-10-12 08:59:59")
		self.assertEqual(STATE["applied"], [])

	def test_a_visit_no_longer_waiting_is_refused(self):
		with self.assertRaisesRegex(Thrown, "no longer waiting"):
			review.approve_visit("DRAFT")

	def test_the_person_who_started_it_is_refused(self):
		STATE["visits"]["OLD"].owner = "lisa@example.com"
		with self.assertRaisesRegex(PermissionError_, "You started this visit"):
			review.approve_visit("OLD", modified="2026-10-12 09:00:00")
		self.assertEqual(STATE["applied"], [])

	def test_the_person_who_did_a_scheduler_drafted_visit_is_refused(self):
		"""Owner Administrator (the nightly scheduler), technician the reviewer: the workflow's
		own rule would allow it (review of PR #1204)."""
		STATE["visits"]["OLD"].technician = "lisa@example.com"
		with self.assertRaisesRegex(PermissionError_, "You did this visit"):
			review.approve_visit("OLD", modified="2026-10-12 09:00:00")
		self.assertEqual(STATE["applied"], [])

	def test_a_version_must_be_named(self):
		"""A direct POST without the version shown cannot approve whatever is current."""
		for call in (
			lambda: review.approve_visit("OLD"),
			lambda: review.send_back_visit("OLD", note="fix"),
		):
			with self.subTest(call=call), self.assertRaisesRegex(Thrown, "the version you saw was not sent"):
				call()
		self.assertEqual(STATE["applied"], [])


class TestSendBack(_Base):
	def test_it_sends_back_with_the_note_on_the_timeline_and_to_the_technician(self):
		result = review.send_back_visit("OLD", note="  pH <looks> off  ", modified="2026-10-12 09:00:00")
		self.assertEqual(STATE["applied"], [("OLD", "Send Back", "lisa@example.com")])
		self.assertEqual(
			STATE["comments"], [("OLD", "Comment", "Sent back for changes: pH &lt;looks&gt; off")]
		)
		self.assertEqual(STATE["emailed"], [("OLD", "pH <looks> off", "lisa@example.com")])
		self.assertEqual(
			(result["state"], result["emailed"], result["next"]), ("Draft", ["austin@example.com"], "NEW")
		)

	def test_a_note_is_required(self):
		for note in (None, "", "   "):
			with self.subTest(note=note), self.assertRaisesRegex(Thrown, "Say what needs fixing"):
				review.send_back_visit("OLD", note=note)
		self.assertEqual(STATE["applied"], [])

	def test_a_stale_visit_is_refused(self):
		with self.assertRaisesRegex(Thrown, "changed since you opened it"):
			review.send_back_visit("OLD", note="fix", modified="2026-10-11 09:00:00")


class TestTheWiring(unittest.TestCase):
	def _json(self, rel):
		return json.loads((APP / rel).read_text(encoding="utf-8"))

	def test_the_page_and_report_carry_the_codes_roles_in_the_maintenance_module(self):
		page = self._json("sapphire_maintenance/page/visit_review/visit_review.json")
		report = self._json(
			"sapphire_maintenance/report/missed_maintenance_visits/missed_maintenance_visits.json"
		)
		for doc in (page, report):
			with self.subTest(doc=doc["name"]):
				self.assertEqual({row["role"] for row in doc["roles"]}, set(review.REVIEW_ROLES))
				self.assertEqual(doc["module"], "Sapphire Maintenance")
		self.assertEqual((page["name"], page["page_name"]), ("visit-review", "visit-review"))
		self.assertEqual(report["report_type"], "Script Report")

	def test_the_workspace_links_both(self):
		workspace = self._json(
			"sapphire_maintenance/workspace/sapphire_maintenance/sapphire_maintenance.json"
		)
		links = {(link.get("link_type"), link.get("link_to")) for link in workspace["links"]}
		self.assertIn(("Page", "visit-review"), links)
		self.assertIn(("Report", "Missed Maintenance Visits"), links)
		self.assertIn("visit-review", {s.get("link_to") for s in workspace["shortcuts"]})
		tools = next(l for l in workspace["links"] if l.get("type") == "Card Break" and l["label"] == "Tools")
		index = workspace["links"].index(tools)
		card = workspace["links"][index + 1 : index + 1 + tools["link_count"]]
		self.assertTrue(all(link.get("type") == "Link" for link in card), "link_count must cover the card")

	def test_the_page_calls_only_whitelisted_endpoints_that_exist(self):
		js = (APP / "sapphire_maintenance/page/visit_review/visit_review.js").read_text(encoding="utf-8")
		called = set(re.findall(r'VR_API \+ "(\w+)"', js))
		self.assertEqual(called, {"get_review_queue", "get_review_visit", "approve_visit", "send_back_visit"})
		tree = ast.parse((APP / "api/maintenance_review.py").read_text(encoding="utf-8"))
		whitelisted = {
			node.name
			for node in tree.body
			if isinstance(node, ast.FunctionDef)
			and any("whitelist" in ast.unparse(d) for d in node.decorator_list)
		}
		self.assertEqual(called, whitelisted)

	def test_the_decisions_are_post_only(self):
		src = (APP / "api/maintenance_review.py").read_text(encoding="utf-8")
		for name in ("approve_visit", "send_back_visit"):
			with self.subTest(name=name):
				self.assertIn(f'@frappe.whitelist(methods=["POST"])\ndef {name}(', src)

	def test_the_page_shows_only_context_the_digest_builds(self):
		src = (APP / "api/maintenance_review.py").read_text(encoding="utf-8")
		digest = (APP / "sapphire_maintenance/review_digest.py").read_text(encoding="utf-8")
		block = src[src.index('"context": {') : src.index('"position"')]
		for key in re.findall(r'"(\w+)",', block):
			with self.subTest(key=key):
				self.assertIn(f'"{key}":', digest)


if __name__ == "__main__":
	unittest.main()
