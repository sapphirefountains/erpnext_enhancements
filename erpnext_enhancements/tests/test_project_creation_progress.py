"""Bench-free tests for the progress of "Create Project in PM System" (v1.749.0).

Nik, 2026-10-27: "create a progress bar to show it's current progress in creation and auto
refresh the page when it's done so the new fields show up." The bar is drawn from what the
background job reports, so these run the real job
(``crm_enhancements.api.create_project_from_opportunity_background``), the real enqueue and the
real state endpoint (``crm_enhancements.project_creation_progress``) against a stubbed
``frappe``. The client half runs under node: ``scripts/test_project_creation_progress.mjs``.

What these guard, most expensive first:

* **A second Project.** Every deploy FLUSHDBs the job queue, so "start it again" has to be safe.
  The job takes the Opportunity's row lock before anything else, makes nothing for an
  Opportunity that has its Project, links a Project that already points back at it instead of
  making another, and rolls a failure back until the link is committed. Before v1.749.0 a
  failure was caught, the job returned normally, and the worker's closing commit kept whatever
  had been inserted, so a retry made a duplicate.
* **A bar that lies.** The steps the job reports must be the steps the dialog lists, in order,
  each counting one more finished; a bar driven by anything else is a guess.
* **A bar that never ends.** Every outcome — made, already made, blocked, failed — is recorded
  and broadcast, because the dialog waits for exactly that. The early return for an
  already-linked Opportunity used to send nothing at all.
* **Progress that breaks the job.** Progress is advisory: a cache or socket failure must not stop
  a Project being made.

Every name here is invented. Run: python -m unittest erpnext_enhancements.tests.test_project_creation_progress -v
"""

import enum
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

REQUESTER = "pat.example@example.com"
INBOX = "office@example.com"
OPP = "CRM-OPP-2099-00001"

#: Mutable state the frappe stub reads at call time; reset per test.
STATE = {}

api = None
progress = None


class StubThrow(Exception):
	pass


class StubPermissionError(StubThrow):
	pass


class JobStatus(str, enum.Enum):
	QUEUED = "queued"
	STARTED = "started"
	FINISHED = "finished"


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key)

	def __setattr__(self, key, value):
		self[key] = value


#: Every child table the job copies, plus the two it reads; the fake Opportunity has them all, empty.
TABLES = {
	"custom_value_stream",
	"custom_contacts__address_table",
	"custom_scope_contributors",
	"custom_design_customer_requests",
	"custom_design_deliverables",
	"custom_build_customer_requests",
	"custom_build_deliverables",
	"custom_service_customer_requests",
	"custom_service_deliverables",
	"custom_rent_customer_requests",
	"custom_rent_deliverables",
	"notes",
}


#: The Opportunity's one attachment, unless a test says otherwise.
SITE_PLAN = {
	"file_name": "site-plan.pdf",
	"file_url": "/private/files/site-plan.pdf",
	"is_private": 1,
	"folder": "",
}


def _event(*entry):
	STATE["events"].append(entry)


class FakeDoc:
	"""Attribute and ``.get`` access, and the writes the job makes, each recorded in order."""

	def __init__(self, doctype, name=None, **fields):
		self.__dict__.update(doctype=doctype, name=name, _fields=dict(fields), flags=types.SimpleNamespace())
		self.__dict__["meta"] = types.SimpleNamespace(get_field=lambda fieldname: None)

	def __getattr__(self, key):
		return self._fields.get(key)

	def __setattr__(self, key, value):
		if key == "name":
			self.__dict__["name"] = value
		else:
			self._fields[key] = value

	def get(self, key, default=None):
		if key in TABLES:
			return self._fields.get(key) or []
		value = self._fields.get(key)
		return default if value is None else value

	def set(self, key, value):
		self._fields[key] = value

	def append(self, table, row=None):
		row = FakeDoc(table)
		self._fields.setdefault(table, []).append(row)
		return row

	def update(self, values):
		self._fields.update(values)

	def insert(self, ignore_permissions=False):
		if self.doctype == "Project":
			fail = STATE.get("insert_raises")
			if fail:
				raise fail
			self.name = "PRJ-0001"
			STATE["projects"].append(self)
		_event("insert", self.doctype, self.name)
		return self

	def save(self, ignore_permissions=False):
		if self.doctype == "Opportunity" and STATE.get("fail_at") == "link":
			raise RuntimeError("the save fell over")
		_event("save", self.doctype, self._fields.get("custom_created_project"))

	def db_set(self, fieldname, value=None, **kwargs):
		self._fields[fieldname] = value

	def as_dict(self):
		return {"name": self.name, **self._fields}


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def throw(msg, exc=None, title=None):
		if exc is StubPermissionError:
			raise StubPermissionError(msg)
		raise StubThrow(msg)

	def set_user(user):
		frappe.session.user = user

	def get_doc(doctype, name=None):
		if doctype == "Opportunity":
			return STATE["opp"]
		if doctype == "Project Template":
			return FakeDoc("Project Template", name, tasks=[])
		if doctype == "Project" and name in STATE["back_projects"]:
			row = STATE["back_projects"][name]
			return FakeDoc("Project", name, project_name=row.get("title", name), **row)
		raise AssertionError(f"unexpected get_doc({doctype!r})")

	def get_all(doctype, filters=None, fields=None, **kwargs):
		if doctype == "File":
			if STATE.get("fail_at") == "attachments":
				raise RuntimeError("the file table is unhappy")
			# v1.749.1: the job asks what the Project already holds (the on_update hook copies first).
			if (filters or {}).get("attached_to_doctype") == "Project":
				return [_Dict(row) for row in STATE.get("project_files", [])]
			return [_Dict(row) for row in STATE.get("opportunity_files", [SITE_PLAN])]
		return []

	def enqueue(method, **kwargs):
		if method.endswith("deliver_closed_won_alerts") and STATE.get("fail_at") == "alerts":
			raise RuntimeError("redis went away")
		STATE["enqueued"].append((method, kwargs))
		_event("enqueue", method)
		return STATE.get("enqueue_returns", object())

	def publish_realtime(event=None, message=None, user=None, **kwargs):
		if event == "ee_project_creation_progress" and STATE.get("publish_raises"):
			raise RuntimeError("socket down")
		STATE["published"].append((event, message, user))

	def log_error(title=None, message=None, **kwargs):
		STATE["errors"].append((title, kwargs))
		return types.SimpleNamespace(name="ERR-0001")

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		STATE["perm_checks"].append((doctype, ptype, doc))
		permitted = STATE.get("permitted", True)
		if throw and not permitted:
			raise StubPermissionError(f"No permission for {doctype} {doc}")
		return permitted

	db = types.SimpleNamespace()

	def get_value(doctype, filters=None, fieldname=None, for_update=False, order_by=None, **kwargs):
		STATE["db_calls"].append((doctype, filters, fieldname, for_update, order_by))
		if doctype == "Opportunity":
			# The locked read can see a link the document loaded beside it does not (another job
			# committed in between): the job must act on this value.
			if for_update and "locked_value" in STATE:
				return STATE["locked_value"]
			return STATE["opp"].get("custom_created_project")
		if doctype == "Project":
			if isinstance(filters, str):  # one Project by name: the row lock on the one found
				return filters if filters in STATE["back_projects"] else None
			# Back-linked Projects, oldest first, with the job's own status filter applied.
			excluded = (filters or {}).get("status", [None, []])[1]
			for name, row in sorted(STATE["back_projects"].items(), key=lambda item: item[1]["creation"]):
				if row.get("status") not in excluded:
					return name
			return None
		return None

	def has_column(doctype, column):
		# The real one takes a DocType and raises on an unknown table; never a "tab" prefix.
		if doctype.startswith("tab"):
			raise AssertionError(f"has_column takes a DocType, got {doctype!r}")
		return True

	db.get_value = get_value
	db.has_column = has_column
	db.commit = lambda: _event("commit")
	db.rollback = lambda: _event("rollback")

	cache_store = {}

	def cache_set(key, value, expires_in_sec=None):
		if STATE.get("cache_raises"):
			raise RuntimeError("cache down")
		cache_store[key] = value
		_event("cache", (value or {}).get("phase"))

	cache = types.SimpleNamespace(
		get_value=lambda key: cache_store.get(key),
		set_value=cache_set,
		store=cache_store,
	)

	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	frappe.throw = throw
	frappe.ValidationError = StubThrow
	frappe.PermissionError = StubPermissionError
	frappe._ = lambda s: s
	frappe._dict = _Dict
	frappe.session = types.SimpleNamespace(user=REQUESTER)
	frappe.set_user = set_user
	frappe.get_doc = get_doc
	frappe.new_doc = lambda doctype: FakeDoc(doctype)
	frappe.get_all = get_all
	frappe.get_meta = lambda doctype: types.SimpleNamespace()
	frappe.enqueue = enqueue
	frappe.publish_realtime = publish_realtime
	frappe.log_error = log_error
	frappe.get_traceback = lambda *a, **k: "Traceback (most recent call last): ..."
	frappe.sendmail = lambda **kwargs: STATE["sent"].append(kwargs)
	frappe.has_permission = has_permission
	frappe.db = db
	frappe.cache = cache

	utils = types.ModuleType("frappe.utils")
	utils.escape_html = lambda s: str(s)
	utils.strip_html_tags = lambda s: __import__("re").sub(r"<[^>]+>", "", str(s))
	utils.validate_email_address = lambda address, throw=False: address if "@" in str(address) else ""
	utils.get_url_to_form = lambda doctype, name: f"https://erp.example.com/desk/{doctype}/{name}"
	frappe.utils = utils

	background_jobs = types.ModuleType("frappe.utils.background_jobs")

	def get_job_status(job_id):
		STATE["job_ids"].append(job_id)
		status = STATE.get("job_status")
		if isinstance(status, list):  # one answer per ask, in order
			status = status.pop(0) if status else None
		if isinstance(status, Exception):
			raise status
		return status

	background_jobs.get_job_status = get_job_status
	utils.background_jobs = background_jobs

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.utils.background_jobs"] = background_jobs

	# The job's collaborators, each standing in for a module that needs a site.
	def module(name, **attrs):
		mod = types.ModuleType(name)
		for key, value in attrs.items():
			setattr(mod, key, value)
		sys.modules[name] = mod
		return mod

	module(
		"erpnext_enhancements.email_style",
		wrap=lambda body, **kw: body,
		rich=lambda text: text,
		kv=lambda rows: "",
		button=lambda url, label: "",
	)
	module("erpnext_enhancements.sync_contact", apply_primary_contact_details=lambda project: None)
	module(
		"erpnext_enhancements.crm_enhancements.handoff",
		handoff_block_reason=lambda opportunity: STATE.get("blocked"),
	)

	def provision(opp_folder_id, project_folder_name, party_name, project_type=None):
		if STATE.get("fail_at") == "drive":
			raise RuntimeError("Drive said no")
		return "FOLDER-1", "https://drive.example.com/folder/1"

	module(
		"erpnext_enhancements.google_drive.drive_utils", provision_project_folder_for_opportunity=provision
	)
	module("erpnext_enhancements.google_drive.drive_sync", log_sync=lambda *a, **k: None)
	return frappe


def setUpModule():
	global api, progress
	frappe = _install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.crm_enhancements.api") or name.endswith(
			"project_creation_progress"
		):
			sys.modules.pop(name, None)
	from erpnext_enhancements.crm_enhancements import api as api_mod
	from erpnext_enhancements.crm_enhancements import project_creation_progress as progress_mod

	api = api_mod
	progress = progress_mod
	globals()["frappe"] = frappe


def _reset(**overrides):
	STATE.clear()
	STATE.update(
		events=[],
		published=[],
		enqueued=[],
		errors=[],
		sent=[],
		projects=[],
		db_calls=[],
		job_ids=[],
		opp=FakeDoc(
			"Opportunity", OPP, custom_opportunity_name="Lakeside Plaza Fountain", party_name="Acme Water Co"
		),
		job_status=None,
		back_projects={},
		perm_checks=[],
	)
	STATE.update(overrides)
	sys.modules["frappe"].cache.store.clear()
	sys.modules["frappe"].session.user = REQUESTER


def _run_job(users=(INBOX,)):
	api.create_project_from_opportunity_background(OPP, list(users), "Fountain Build")


def _progress_events():
	return [(m["step"], m["done"], user) for event, m, user in STATE["published"] if event == progress.EVENT]


def _statuses():
	return [(m, user) for event, m, user in STATE["published"] if event == "project_creation_status"]


def _final_state():
	return sys.modules["frappe"].cache.store.get(f"ee:project_creation:{OPP}")


class TestTheJob(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_each_step_is_reported_in_order_to_the_requester_only(self):
		_run_job()
		self.assertEqual(
			_progress_events(),
			[
				("check", 0, REQUESTER),
				("project", 1, REQUESTER),
				("attachments", 2, REQUESTER),
				("link", 3, REQUESTER),
				("drive", 4, REQUESTER),
			],
		)
		state = _final_state()
		self.assertEqual(state["phase"], "finished")
		self.assertEqual(state["done"], state["total"])
		self.assertEqual(state["result"]["status"], "success")
		self.assertEqual(state["result"]["project"], "PRJ-0001")

	def test_the_steps_reported_are_the_steps_the_dialog_lists(self):
		# A step the job reports but STEPS lacks would draw a bar the job never fills.
		source = (REPO_ROOT / "erpnext_enhancements" / "crm_enhancements" / "api.py").read_text(
			encoding="utf-8"
		)
		reported = __import__("re").findall(r'^\t+step = "(\w+)"', source, flags=__import__("re").M)
		# A linked Project takes two of the steps again (link, then drive), so the keys are compared
		# as a set; the order a made Project reports them in is test_each_step_is_reported_in_order.
		self.assertEqual(set(reported), set(progress.STEP_KEYS))

	def test_success_is_broadcast_with_the_project_and_emailed(self):
		_run_job()
		(status, _user), *_rest = _statuses()
		self.assertEqual(status["status"], "success")
		self.assertEqual(status["project"], "PRJ-0001")
		self.assertTrue(status["drive_success"])
		self.assertEqual([user for _m, user in _statuses()], [INBOX, REQUESTER])
		self.assertEqual([mail["recipients"] for mail in STATE["sent"]], [[INBOX]])

	def _attachments_copied(self):
		"""File inserts the job made before it committed: the attachments step's, not the Drive link's."""
		events = STATE["events"]
		end = events.index(("commit",))
		return [e for e in events[:end] if e[:2] == ("insert", "File")]

	def test_an_attachment_the_project_lacks_is_copied_once(self):
		_run_job()
		self.assertEqual(len(self._attachments_copied()), 1)

	def test_an_attachment_the_hook_already_copied_is_not_copied_again(self):
		# The Project's on_update hook runs inside project.insert and copies first (v1.749.1).
		STATE["project_files"] = [dict(SITE_PLAN)]
		_run_job()
		self.assertEqual(self._attachments_copied(), [])
		self.assertEqual(_final_state()["result"]["status"], "success")

	def test_a_file_the_opportunity_holds_twice_is_copied_once(self):
		STATE["opportunity_files"] = [dict(SITE_PLAN), dict(SITE_PLAN), {**SITE_PLAN, "file_name": "site-plan (1).pdf"}]
		_run_job()
		self.assertEqual(len(self._attachments_copied()), 1)

	def test_the_outcome_is_recorded_before_it_is_broadcast(self):
		# A client that hears the status and then asks project_creation_state must read the outcome.
		_run_job()
		cached = [i for i, e in enumerate(STATE["events"]) if e == ("cache", "finished")]
		self.assertTrue(cached)
		self.assertEqual(STATE["published"][-1][0], "project_creation_status")

	def test_the_row_lock_comes_before_anything_else(self):
		_run_job()
		first = STATE["db_calls"][0]
		self.assertEqual(first[:3], ("Opportunity", OPP, "custom_created_project"))
		self.assertTrue(first[3], "the first read must be SELECT ... FOR UPDATE")

	def test_an_opportunity_with_its_project_gets_no_second_one(self):
		STATE["opp"].custom_created_project = "PRJ-0007"
		_run_job()
		self.assertEqual(STATE["projects"], [])
		self.assertNotIn(("commit",), STATE["events"])
		self.assertEqual(STATE["sent"], [])
		statuses = _statuses()
		self.assertEqual(len(statuses), 1, "only the requester hears; nothing new happened")
		status, user = statuses[0]
		self.assertEqual((status["status"], status["project"], user), ("exists", "PRJ-0007", REQUESTER))
		self.assertFalse(status["adopted"])
		self.assertEqual(_final_state()["result"]["status"], "exists")

	def test_the_locked_reads_value_is_the_one_acted_on(self):
		# The document loaded beside it says nothing is linked; the locked read says another job did.
		STATE["locked_value"] = "PRJ-0009"
		_run_job()
		self.assertEqual(STATE["projects"], [])
		status, _user = _statuses()[0]
		self.assertEqual((status["status"], status["project"]), ("exists", "PRJ-0009"))

	def test_a_project_pointing_back_is_linked_not_duplicated(self):
		STATE["back_projects"] = {
			"PRJ-0003": {
				"status": "Active",
				"creation": "2099-01-02",
				"custom_drive_folder_id": "FOLDER-OLD",
			},
			"PRJ-0004": {"status": "Active", "creation": "2099-03-01"},
		}
		_run_job()
		self.assertEqual(STATE["projects"], [])
		self.assertIn(("save", "Opportunity", "PRJ-0003"), STATE["events"], "the oldest claim is the Project")
		self.assertLess(
			STATE["events"].index(("save", "Opportunity", "PRJ-0003")), STATE["events"].index(("commit",))
		)
		project_lookup = next(c for c in STATE["db_calls"] if c[0] == "Project")
		self.assertEqual(
			project_lookup[1], {"custom_opportunity": OPP, "status": ["not in", ["Cancelled", "Canceled"]]}
		)
		# Not a locking read: custom_opportunity has no index, so FOR UPDATE would lock every row of
		# tabProject it scans, and the gaps, until the link commits (the v1.749.0 review).
		self.assertFalse(project_lookup[3], "the back-link query must not lock")
		self.assertEqual(project_lookup[4], "creation asc")
		locks = [c for c in STATE["db_calls"] if c[0] == "Project" and c[3]]
		self.assertEqual(locks, [("Project", "PRJ-0003", "name", True, None)], "only the found row, by name")
		status, user = _statuses()[0]
		self.assertEqual(
			(status["status"], status["project"], status["adopted"]), ("exists", "PRJ-0003", True)
		)
		self.assertEqual(STATE["sent"], [], "no New Project Created email: nothing is new")
		self.assertTrue(status["drive_success"], "it already has its folder")

	def test_a_linked_project_gets_the_team_sms_and_its_drive_folder(self):
		STATE["back_projects"] = {
			"PRJ-0003": {"status": "Open", "creation": "2099-01-02", "title": "Lakeside"}
		}
		_run_job()
		methods = [method for method, _kw in STATE["enqueued"]]
		self.assertEqual(methods, ["erpnext_enhancements.status_alerts.deliver_closed_won_alerts"])
		self.assertIn(("insert", "File", None), STATE["events"], "the Drive link is attached")
		self.assertLess(STATE["events"].index(("commit",)), STATE["events"].index(("insert", "File", None)))
		status, _user = _statuses()[0]
		self.assertEqual(
			(status["status"], status["adopted"], status["drive_success"]), ("exists", True, True)
		)
		self.assertEqual(
			[(step, done) for step, done, _u in _progress_events()], [("check", 0), ("link", 3), ("drive", 4)]
		)
		self.assertEqual(_final_state()["done"], len(progress.STEPS))

	def test_a_linked_projects_drive_failure_is_reported_not_fatal(self):
		STATE["back_projects"] = {"PRJ-0003": {"status": "Open", "creation": "2099-01-02"}}
		STATE["fail_at"] = "drive"
		_run_job()
		self.assertNotIn(("rollback",), STATE["events"])
		status, _user = _statuses()[0]
		self.assertEqual((status["status"], status["project"]), ("exists", "PRJ-0003"))
		self.assertIn("drive_error", status)
		self.assertTrue(_final_state()["result"]["drive_error"])

	def test_a_cancelled_project_is_passed_over_and_a_new_one_made(self):
		# A PM retired it; relinking it would bring back a dead job, without a Drive folder or the SMS.
		STATE["back_projects"] = {
			"PRJ-0002": {"status": "Cancelled", "creation": "2099-01-01"},
			"PRJ-0005": {"status": "Canceled", "creation": "2099-01-02"},
		}
		_run_job()
		self.assertEqual(len(STATE["projects"]), 1)
		status, _user = _statuses()[0]
		self.assertEqual((status["status"], status["project"]), ("success", "PRJ-0001"))

	def test_a_blocked_hand_off_stops_at_the_check(self):
		STATE["blocked"] = "Schedule the Hand-Off Meeting on the Opportunity first."
		_run_job()
		self.assertEqual(STATE["projects"], [])
		self.assertEqual(_progress_events(), [("check", 0, REQUESTER)])
		status, _user = _statuses()[0]
		self.assertEqual(status["status"], "blocked")
		self.assertEqual(status["blocked_reason"], STATE["blocked"])
		state = _final_state()
		self.assertEqual((state["step"], state["done"]), ("check", 0))

	def test_a_failure_before_the_link_rolls_back_and_says_where(self):
		STATE["fail_at"] = "attachments"
		_run_job()
		self.assertIn(("rollback",), STATE["events"])
		self.assertNotIn(("commit",), STATE["events"])
		status, _user = _statuses()[0]
		self.assertEqual(status["status"], "failed")
		self.assertIsNone(status["project_doc"], "a rolled-back project must not be reported as made")
		self.assertEqual(status["failed_step"], "attachments")
		self.assertEqual(status["reason"], "It stopped at: Copying the opportunity's attachments.")
		self.assertEqual(status["error_log"], "ERR-0001")
		self.assertEqual(STATE["errors"][0][1].get("reference_name"), OPP)
		self.assertEqual(STATE["sent"], [])
		self.assertEqual(_final_state()["done"], 2)

	def test_a_failed_link_save_rolls_the_project_back(self):
		# The project was inserted and project_doc set, but the link never committed.
		STATE["fail_at"] = "link"
		_run_job()
		self.assertIn(("rollback",), STATE["events"])
		status, _user = _statuses()[0]
		self.assertEqual(status["status"], "failed")
		self.assertIsNone(status["project_doc"])
		self.assertEqual(status["failed_step"], "link")

	def test_a_message_written_for_people_is_passed_on(self):
		STATE["insert_raises"] = StubThrow("<b>Project Name</b> is required")
		_run_job()
		status, _user = _statuses()[0]
		self.assertEqual(status["reason"], "Project Name is required")
		self.assertEqual(status["failed_step"], "project")

	def test_a_failure_after_the_link_keeps_the_project(self):
		STATE["fail_at"] = "alerts"
		_run_job()
		self.assertNotIn(("rollback",), STATE["events"])
		status, _user = _statuses()[0]
		self.assertEqual(status["status"], "success")
		self.assertEqual(status["project"], "PRJ-0001")

	def test_a_drive_failure_is_a_made_project_with_a_warning(self):
		STATE["fail_at"] = "drive"
		_run_job()
		status, _user = _statuses()[0]
		self.assertEqual(status["status"], "success")
		self.assertIn("drive_error", status)
		self.assertTrue(_final_state()["result"]["drive_error"])

	def test_progress_that_fails_never_stops_the_job(self):
		STATE["publish_raises"] = True
		STATE["cache_raises"] = True
		_run_job()
		self.assertEqual(len(STATE["projects"]), 1)
		self.assertEqual(_statuses()[0][0]["status"], "success")

	def test_the_requester_hears_even_when_only_inboxes_are_listed(self):
		STATE["fail_at"] = "attachments"
		_run_job(users=(INBOX,))
		self.assertEqual([user for _m, user in _statuses()], [INBOX, REQUESTER])


class TestTheEnqueue(unittest.TestCase):
	def setUp(self):
		_reset()

	def _enqueue(self):
		return api.enqueue_project_creation(OPP, users=INBOX, project_template="Fountain Build")

	def test_queues_one_deduplicated_job_per_opportunity(self):
		result = self._enqueue()
		((method, kwargs),) = STATE["enqueued"]
		self.assertTrue(method.endswith("create_project_from_opportunity_background"))
		self.assertEqual(kwargs["job_id"], f"ee-project-from-opportunity-{OPP}")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["queue"], "long")
		self.assertEqual(result["status"], "queued")
		self.assertEqual([s["key"] for s in result["steps"]], list(progress.STEP_KEYS))
		self.assertEqual(result["state"]["phase"], "queued")

	def test_the_queued_state_is_written_before_the_job_can_start(self):
		self._enqueue()
		events = STATE["events"]
		self.assertLess(events.index(("cache", "queued")), [e[0] for e in events].index("enqueue"))
		self.assertEqual(
			[(m["phase"], u) for e, m, u in STATE["published"] if e == progress.EVENT],
			[("queued", REQUESTER)],
		)

	def test_a_creation_under_way_is_shown_not_started_again(self):
		STATE["job_status"] = JobStatus.STARTED
		sys.modules["frappe"].cache.store[f"ee:project_creation:{OPP}"] = {
			"phase": "running",
			"step": "drive",
			"done": 4,
		}
		result = self._enqueue()
		self.assertEqual(STATE["enqueued"], [])
		self.assertEqual(result["status"], "running")
		self.assertEqual(result["phase"], "running")
		self.assertEqual(
			_final_state()["step"], "drive", "the running job's progress must not be overwritten"
		)

	def test_an_opportunity_with_its_project_queues_nothing(self):
		STATE["opp"].custom_created_project = "PRJ-0007"
		self.assertEqual(self._enqueue(), {"status": "exists", "project": "PRJ-0007"})
		self.assertEqual(STATE["enqueued"], [])

	def test_starting_it_needs_write_access_to_the_opportunity(self):
		# The job runs as Administrator and can link a Project; nobody without write may start it.
		STATE["permitted"] = False
		with self.assertRaises(StubPermissionError):
			self._enqueue()
		self.assertEqual(STATE["perm_checks"], [("Opportunity", "write", OPP)])
		self.assertEqual(STATE["enqueued"], [])
		self.assertEqual(STATE["events"], [], "nothing recorded, not even the queued state")

	def test_a_deduplicated_no_op_reports_the_job_that_is_there(self):
		# Another request queued this Opportunity's job between the check and the enqueue: the
		# first ask finds no job, frappe.enqueue then deduplicates (returns None), and the second
		# ask finds the other request's job.
		STATE["enqueue_returns"] = None
		STATE["job_status"] = [None, JobStatus.QUEUED]
		result = self._enqueue()
		self.assertEqual(len(STATE["enqueued"]), 1, "the enqueue was reached, and deduplicated")
		self.assertEqual(result["status"], "running", "not the 'queued' this call never made")
		self.assertEqual((result["phase"], result["job"]), ("queued", "queued"))
		self.assertEqual([s["key"] for s in result["steps"]], list(progress.STEP_KEYS))
		self.assertEqual(STATE["job_ids"], [f"ee-project-from-opportunity-{OPP}"] * 2)

	def test_a_bad_address_is_refused_before_anything_is_recorded(self):
		with self.assertRaises(StubThrow):
			api.enqueue_project_creation(OPP, users="Pat Example", project_template="Fountain Build")
		self.assertEqual(STATE["events"], [])


class TestWhereItStands(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_phase_of(self):
		cases = [
			# (recorded phase, RQ status, linked project) -> phase
			((None, None, None), "idle"),
			(("queued", "queued", None), "queued"),
			(("running", "started", None), "running"),
			(("queued", "started", None), "queued"),
			(("finished", None, "PRJ-0001"), "finished"),
			(("finished", "finished", None), "finished"),
			# The job has said how it ended while RQ still says started: its own word wins.
			(("finished", "started", None), "finished"),
			((None, None, "PRJ-0001"), "done"),
			(("running", None, None), "lost"),
			(("queued", None, None), "lost"),
			(("running", "finished", None), "lost"),
			(("running", "unknown", None), "running"),
			(("queued", "unknown", None), "queued"),
		]
		for (recorded, job, project), expected in cases:
			with self.subTest(recorded=recorded, job=job, project=project):
				state = {"phase": recorded} if recorded else None
				self.assertEqual(progress.phase_of(state, job, project), expected)

	def test_job_status(self):
		STATE["job_status"] = JobStatus.STARTED
		self.assertEqual(progress.job_status(OPP), "started")
		self.assertEqual(STATE["job_ids"], [f"ee-project-from-opportunity-{OPP}"])
		STATE["job_status"] = None
		self.assertIsNone(progress.job_status(OPP))
		STATE["job_status"] = ConnectionError("redis down")
		self.assertEqual(progress.job_status(OPP), "unknown")

	def test_a_job_a_deploy_flushed_reads_as_lost(self):
		sys.modules["frappe"].cache.store[f"ee:project_creation:{OPP}"] = {
			"phase": "running",
			"step": "project",
			"done": 1,
		}
		state = progress.project_creation_state(OPP)
		self.assertEqual(state["phase"], "lost")
		self.assertIsNone(state["project"])
		self.assertEqual(len(state["steps"]), len(progress.STEPS))

	def test_reading_needs_read_access(self):
		STATE["permitted"] = False
		with self.assertRaises(StubPermissionError):
			progress.project_creation_state(OPP)

	def test_mark_finished_counts_only_the_steps_done(self):
		self.assertEqual(
			progress.mark_finished(OPP, REQUESTER, {"status": "success"})["done"], len(progress.STEPS)
		)
		self.assertEqual(
			progress.mark_finished(OPP, REQUESTER, {"status": "exists"})["done"], len(progress.STEPS)
		)
		self.assertEqual(progress.mark_finished(OPP, REQUESTER, {"status": "failed"}, step="link")["done"], 3)
		self.assertEqual(
			progress.mark_finished(OPP, REQUESTER, {"status": "failed"}, step="nonsense")["done"], 0
		)

	def test_an_unknown_step_never_raises(self):
		self.assertEqual(progress.mark_step(OPP, REQUESTER, "nonsense")["done"], 0)

	def test_a_stored_value_that_is_not_a_state_is_ignored(self):
		sys.modules["frappe"].cache.store[f"ee:project_creation:{OPP}"] = "garbage"
		self.assertIsNone(progress.read_state(OPP))


def _js_without_comments(source):
	"""``source`` with its ``//`` and ``/* */`` comments gone, so a check that something is absent
	cannot be satisfied (or defeated) by a comment that mentions it."""
	import re

	source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
	return re.sub(r"(^|[^:])//[^\n]*", r"\1", source)


def _method_body(source, signature):
	start = source.index(signature)
	end = source.index("\n\t}\n", start)
	return source[start:end]


class TestTheSurfaces(unittest.TestCase):
	"""The two surfaces scripts/test_project_creation_progress.mjs does not execute."""

	JS = REPO_ROOT / "erpnext_enhancements"

	def test_the_guided_page_stands_aside_while_the_dialog_watches(self):
		source = (self.JS / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js").read_text(
			encoding="utf-8"
		)
		body = _js_without_comments(_method_body(source, "\ton_project_status(data) {"))
		guard = body.index("crm.is_tracking_project_creation(data.opportunity_name)) return;")
		self.assertLess(guard, body.index("this.reload_record()"), "a watched creation must not reload twice")
		self.assertLess(guard, body.index("frappe.msgprint("))
		self.assertIn('data.status === "exists"', body, "an already-linked deal is not a failure")
		# TASK-2026-02984: its own success toast offers the next step the dialog offers, on the same gate.
		self.assertIn("crm.build_out_offer()", body)
		self.assertIn('action_label: __("Build out from a planner template")', body)
		self.assertIn("on_action: () => crm.open_planner_build_out(project)", body)

	def test_the_hand_off_tab_saves_first_and_leaves_the_reload_to_the_dialog(self):
		source = (self.JS / "public" / "js" / "crm_enhancements" / "opportunity_handoff.js").read_text(
			encoding="utf-8"
		)
		body = _js_without_comments(_method_body(source, "\tfunction create_project(frm) {"))
		self.assertIn("if (frm.is_dirty()) {", body)
		self.assertLess(body.index("frm.is_dirty()"), body.index("open_create_project_dialog"))
		self.assertNotIn(
			"on_success", body, "the dialog reloads when the project exists; a second reload is noise"
		)
		self.assertNotIn("reload_doc", body)

	def test_only_someone_who_may_change_the_deal_is_offered_the_creation(self):
		# v1.749.0 review: enqueue_project_creation needs write on the Opportunity, so a reader who
		# was asked "Create project now?" filled in the dialog only to be refused.
		guided = (self.JS / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js").read_text(
			encoding="utf-8"
		)
		nudge = _js_without_comments(_method_body(guided, "\tnudge_won_without_project() {"))
		self.assertLess(
			nudge.index("if (!(this.flow && this.flow.can_write)) return;"),
			nudge.index("crm.confirm_won_next_step("),
			"the guided page's own edit check, before it asks",
		)
		tab = _js_without_comments(
			(self.JS / "public" / "js" / "crm_enhancements" / "opportunity_handoff.js").read_text(
				encoding="utf-8"
			)
		)
		self.assertIn('can_write: !!frappe.perm.has_perm(frm.doctype, 0, "write", frm.doc),', tab)
		self.assertIn("if (!f.project && f.can_write && (f.gate_open || !f.gate_applies)) {", tab)


if __name__ == "__main__":
	unittest.main()
