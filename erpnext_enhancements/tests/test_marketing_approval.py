"""Bench-free test: draft -> approve -> publish, and the marketing roles (TASK-2026-01486).

What these pin:

1. **A second person approves, from a browser.** A Marketing Manager, never the post's author or
   its last editor, and never through an API key, a token or a background job. Triton's service
   account holds Marketing Manager; the browser rule is what keeps it from approving.
2. **What is approved is what was reviewed.** A post edited after the approver opened it is
   refused, and approval writes the outbox rows after the status, in the same request.
3. **Status, approver and approval time change only through the actions.** A save that types
   "Approved" is refused, and a new post starts as a Draft.
4. **Stopping is always possible; deleting only before anything was queued.**
5. **The roles reach the publishing surface and nothing else.** Marketing Team is granted on
   five Marketing doctypes and nowhere else, never on credentials or switches; the ``Marketing``
   role profile carries it alone.
6. **Every action is POST-only, never guest, and checks edit permission first.**

Filesystem, ``json`` and ``ast`` only, plus two modules that import no frappe (``workflow`` and
``outbox``). No stub, so it shares a CI step with the other marketing suites.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_approval
"""

import ast
import datetime
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.publish import outbox as O
from erpnext_enhancements.marketing.publish import workflow as W

APP = REPO_ROOT / "erpnext_enhancements"
MARKETING = APP / "marketing"
DOCTYPES = MARKETING / "doctype"
PUBLISH = MARKETING / "publish"
T0 = datetime.datetime(2026, 9, 22, 9, 0)
PENDING = {
	"status": O.POST_PENDING_APPROVAL,
	"owner": "writer@example.com",
	"modified_by": "writer@example.com",
}
MANAGER = {W.APPROVER_ROLE}
TRITON = "triton@sapphirefountains.com"


def schema(name):
	return json.loads((DOCTYPES / name / f"{name}.json").read_text(encoding="utf-8"))


def fields(name):
	return {f["fieldname"]: f for f in schema(name)["fields"]}


def statuses():
	return fields("social_post")["status"]["options"].split("\n")


def all_doctypes():
	"""Every DocType JSON in the app: ``{name: schema}``."""
	found = {}
	for path in APP.rglob("doctype/*/*.json"):
		if path.stem != path.parent.name:
			continue
		data = json.loads(path.read_text(encoding="utf-8"))
		if data.get("doctype") == "DocType":
			found[data["name"]] = data
	return found


def granted(role):
	return {
		name: [p for p in data.get("permissions") or [] if p.get("role") == role]
		for name, data in all_doctypes().items()
		if any(p.get("role") == role for p in data.get("permissions") or [])
	}


def tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def function(module, name):
	return next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == name)


# ---------------------------------------------------------------- who is asking


class BrowserTests(unittest.TestCase):
	def test_a_login_is_a_browser(self):
		self.assertTrue(W.signed_in_browser({"user": "boss@example.com", "sid": "a1b2c3d4e5f6"}))

	def test_tokens_jobs_the_console_and_guests_are_not(self):
		# frappe.set_user() -- API keys, OAuth bearer, background jobs, the console -- sets sid = user.
		cases = [
			({"user": TRITON, "sid": TRITON}, None),
			({"user": "boss@example.com", "sid": "a1b2c3d4e5f6"}, "token abc:def"),
			({"user": "boss@example.com", "sid": "a1b2c3d4e5f6"}, "Bearer xyz"),
			({"user": "Guest", "sid": "Guest"}, None),
			({"user": "boss@example.com", "sid": "Guest"}, None),
			({"user": "boss@example.com", "sid": None}, None),
			({"user": "", "sid": "a1b2c3d4e5f6"}, None),
			(None, None),
		]
		for session, header in cases:
			self.assertFalse(W.signed_in_browser(session, header), (session, header))


# ---------------------------------------------------------------- the rules


class StatusEditTests(unittest.TestCase):
	def test_a_new_post_starts_as_a_draft_with_no_approver(self):
		self.assertIsNone(W.status_edit_problem(None, {"status": O.POST_DRAFT}))
		self.assertIsNone(W.status_edit_problem(None, {"status": None}))
		for forged in (
			{"status": O.POST_APPROVED},
			{"status": O.POST_DRAFT, "approver": "boss@example.com"},
			{"status": O.POST_DRAFT, "approved_at": T0},
		):
			self.assertIsNotNone(W.status_edit_problem(None, forged), forged)

	def test_an_ordinary_save_cannot_move_the_workflow_fields(self):
		before = {"status": O.POST_SCHEDULED, "approver": "boss@example.com", "approved_at": T0, "title": "a"}
		self.assertIsNone(W.status_edit_problem(before, {**before, "title": "b"}))
		self.assertIsNone(
			W.status_edit_problem(before, {**before, "approved_at": "2026-09-22 09:00:00"}),
			"the form sends the time back as text; the same moment is no change",
		)
		for change in (
			{"status": O.POST_APPROVED},
			{"approver": "someone@example.com"},
			{"approved_at": T0 + datetime.timedelta(seconds=1)},
			{"approved_at": None},
		):
			self.assertIsNotNone(W.status_edit_problem(before, {**before, **change}), change)
		self.assertIsNone(W.status_edit_problem({"approver": None}, {"approver": ""}), "blank is blank")


class SubmitTests(unittest.TestCase):
	def test_only_a_draft_without_problems_goes_for_approval(self):
		self.assertEqual(W.submit_problems({"status": O.POST_DRAFT}, []), [])
		self.assertEqual(W.submit_problems({}, []), [], "an unset status is a draft")
		self.assertTrue(W.submit_problems(PENDING, []))
		self.assertEqual(W.submit_problems({"status": O.POST_DRAFT}, ["x is wrong"]), ["x is wrong"])

	def test_the_outboxs_own_content_checks_decide(self):
		# An approver is never asked to approve what the outbox would refuse to send.
		problems = O.content_problems({"body": ""}, [], [], {})
		self.assertTrue(any("no accounts" in p for p in problems), problems)
		self.assertTrue(W.submit_problems({"status": O.POST_DRAFT}, problems))
		approved = {"status": O.POST_APPROVED, "approver": "b", "approved_at": T0, "owner": "w", "body": "x"}
		targets, accounts = [{"social_account": "A"}], {"A": {"enabled": 1}}
		self.assertEqual(O.content_problems(approved, targets, [], accounts), [])
		self.assertEqual(
			O.enqueue_problems(approved, targets, [], accounts), [], "enqueue = approval + content"
		)


class ApproveTests(unittest.TestCase):
	def problems(self, post=None, user="boss@example.com", roles=MANAGER, browser=True, unchanged=True):
		return W.approval_problems(post or PENDING, user, roles, browser, unchanged)

	def assertRefused(self, needle, **kw):
		problems = self.problems(**kw)
		self.assertTrue(any(needle in p for p in problems), (needle, problems))

	def test_a_manager_who_did_not_write_it_approves_it_from_a_browser(self):
		self.assertEqual(self.problems(), [])

	def test_each_rule(self):
		self.assertRefused("only a Marketing Manager", roles={"System Manager", W.TEAM_ROLE})
		self.assertRefused("signed-in browser", browser=False)
		self.assertRefused("not Pending Approval", post={**PENDING, "status": O.POST_DRAFT})
		self.assertRefused("you wrote it", user="writer@example.com")
		self.assertRefused(
			"latest change", post={**PENDING, "modified_by": "boss@example.com"}, user="boss@example.com"
		)
		self.assertRefused("changed after you opened it", unchanged=False)

	def test_the_service_account_cannot_approve_whatever_it_holds(self):
		self.assertRefused(
			"signed-in browser",
			user=TRITON,
			roles={W.APPROVER_ROLE, W.TEAM_ROLE, "System Manager"},
			browser=False,
		)


class ResolveRuleTests(unittest.TestCase):
	def test_a_manager_or_system_manager_from_a_browser(self):
		self.assertEqual(W.resolve_problems({"System Manager"}, True), [])
		self.assertEqual(W.resolve_problems({W.APPROVER_ROLE}, True), [])
		self.assertTrue(W.resolve_problems({W.TEAM_ROLE}, True), "drafting is not answering for the network")
		self.assertTrue(W.resolve_problems({"System Manager"}, False), "a token cannot say it was published")


class LifecycleTests(unittest.TestCase):
	def test_every_status_is_classified(self):
		options = set(statuses())
		self.assertLessEqual(W.CANCELABLE | W.DELETABLE, options)
		for status in options:
			post = {"status": status}
			self.assertEqual(W.cancel_problems(post) == [], status in W.CANCELABLE, status)
			self.assertEqual(W.delete_problems(post) == [], status in W.DELETABLE, status)
			self.assertEqual(W.send_back_problems(post) == [], status == O.POST_PENDING_APPROVAL, status)

	def test_what_is_editable_can_always_be_stopped_or_deleted(self):
		self.assertLessEqual(O.EDITABLE_POST_STATUSES, W.CANCELABLE)
		self.assertLessEqual(O.EDITABLE_POST_STATUSES, W.DELETABLE)
		for finished in (O.POST_PUBLISHED, O.POST_PARTIAL, O.POST_FAILED, O.POST_CANCELED):
			self.assertNotIn(finished, W.CANCELABLE)
		for queued in (O.POST_APPROVED, O.POST_SCHEDULED, O.POST_PUBLISHING, O.POST_PUBLISHED):
			self.assertNotIn(queued, W.DELETABLE, "the record of what went out stays")

	def test_a_refusal_reads_as_one_sentence(self):
		self.assertEqual(W.refusal("SPOST-1", "approved", ["a", "b"]), "SPOST-1 cannot be approved: a; b.")


# ---------------------------------------------------------------- the roles


class RoleTests(unittest.TestCase):
	PUBLISHING = {
		"Social Post",
		"Marketing Media Asset",
		"Social Account",
		"Social Post Metric",
		"Social Publish Job",
	}
	EDITABLE = {"Social Post", "Marketing Media Asset"}
	ALLOWED_KEYS = {"role", "read", "write", "create", "delete", "report"}

	def test_marketing_team_reaches_the_publishing_surface_and_nothing_else(self):
		grants = granted(W.TEAM_ROLE)
		self.assertEqual(set(grants), self.PUBLISHING)
		doctypes = all_doctypes()
		for name, perms in grants.items():
			self.assertEqual(doctypes[name]["module"], "Marketing", name)
			self.assertEqual(len(perms), 1, name)
			self.assertLessEqual(
				set(perms[0]), self.ALLOWED_KEYS, (name, "no submit, export, share or permlevel")
			)
			self.assertEqual(bool(perms[0].get("write")), name in self.EDITABLE, name)

	def test_marketing_manager_adds_only_the_same_surface(self):
		grants = granted(W.APPROVER_ROLE)
		# KPI Snapshot (read, report) predates publishing: the Marketing dashboard's department gate.
		self.assertEqual(set(grants), self.PUBLISHING | {"KPI Snapshot"})
		self.assertEqual(
			{k for k, v in grants["KPI Snapshot"][0].items() if v and k != "role"}, {"read", "report"}
		)
		for name in self.PUBLISHING:
			self.assertEqual(
				{k for k, v in grants[name][0].items() if k != "role"},
				{k for k, v in granted(W.TEAM_ROLE)[name][0].items() if k != "role"},
				f"{name}: approving is an action, not a wider grant",
			)

	def test_credentials_and_switches_stay_with_system_manager(self):
		for name in ("Marketing Connections", "Marketing Settings", "Marketing Raw Payload"):
			roles = {p["role"] for p in all_doctypes()[name]["permissions"]}
			self.assertFalse(roles & {W.TEAM_ROLE, W.APPROVER_ROLE}, name)

	def test_no_custom_docperm_widens_them(self):
		rows = json.loads((APP / "fixtures" / "custom_docperm.json").read_text(encoding="utf-8"))
		self.assertFalse([r for r in rows if r.get("role") in (W.TEAM_ROLE, W.APPROVER_ROLE)])

	def test_the_role_profiles(self):
		profiles = {
			p["name"]: [r["role"] for r in p["roles"]]
			for p in json.loads((APP / "fixtures" / "role_profile.json").read_text(encoding="utf-8"))
		}
		self.assertEqual(
			profiles["Marketing"], [W.TEAM_ROLE], "a marketing hire gets drafting and nothing else"
		)
		self.assertEqual(profiles["Marketing Approvers"], [W.APPROVER_ROLE], "a single-role add-on")
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		for name in ("Marketing", "Marketing Approvers"):
			self.assertIn(f'\t\t\t\t\t"{name}",\n', hooks, f"{name} is exported with the other profiles")

	def test_the_approver_is_the_marketing_departments_role(self):
		# The task: follow the department gating api/dashboard_widgets.py uses (api/kpi.py).
		kpi = tree(APP / "api" / "kpi.py")
		roles = next(
			node.value
			for node in kpi.body
			if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "DEPARTMENT_ROLES"
		)
		marketing = next(
			v for k, v in zip(roles.keys, roles.values, strict=True) if ast.literal_eval(k) == "Marketing"
		)
		self.assertIn(W.APPROVER_ROLE, ast.literal_eval(marketing))


class SchemaTests(unittest.TestCase):
	def test_a_duplicate_starts_clean(self):
		post, target = fields("social_post"), fields("social_post_target")
		for name in (*W.WORKFLOW_FIELDS, "network_check"):
			self.assertEqual(post[name].get("no_copy"), 1, name)
		for name in ("status", "external_post_id", "permalink", "published_at"):
			self.assertEqual(target[name].get("no_copy"), 1, name)

	def test_the_workflow_fields_are_read_only_on_the_form(self):
		post = fields("social_post")
		for name in W.WORKFLOW_FIELDS:
			self.assertEqual(post[name].get("read_only"), 1, name)
		self.assertEqual(post["status"].get("default"), O.POST_DRAFT)

	def test_the_attempt_log(self):
		log = fields("social_publish_job")["attempt_log"]
		self.assertEqual(
			(log["fieldtype"], log["options"], log.get("read_only")), ("Table", "Social Publish Attempt", 1)
		)
		attempt = schema("social_publish_attempt")
		self.assertEqual((attempt.get("istable"), attempt["module"]), (1, "Marketing"))


# ---------------------------------------------------------------- wiring


class WiringTests(unittest.TestCase):
	ACTIONS = ["submit_for_approval", "approve", "send_back", "cancel"]

	def setUp(self):
		self.approval = tree(PUBLISH / "approval.py")

	def test_every_action_is_post_only_and_never_guest(self):
		whitelisted = [
			n
			for n in self.approval.body
			if isinstance(n, ast.FunctionDef) and any("whitelist" in ast.unparse(d) for d in n.decorator_list)
		]
		self.assertEqual([n.name for n in whitelisted], self.ACTIONS)
		for fn in whitelisted:
			self.assertEqual(
				[ast.unparse(d) for d in fn.decorator_list], ["frappe.whitelist(methods=['POST'])"]
			)

	def test_every_action_checks_edit_permission_first(self):
		load = ast.unparse(function(self.approval, "_load"))
		self.assertIn("doc.check_permission('write')", load)
		for name in self.ACTIONS:
			first = function(self.approval, name).body[1]  # after the docstring
			self.assertEqual(ast.unparse(first), "doc = _load(post)", name)

	def test_approve_checks_then_approves_then_queues(self):
		body = ast.unparse(function(self.approval, "approve"))
		self.assertIn("browser_request()", body)
		self.assertIn("frappe.get_roles(user)", body)
		checked = body.index("workflow.approval_problems(")
		approved = body.index("_set_status(doc, outbox.POST_APPROVED, approver=user")
		queued = body.index("outbox.enqueue(FrappeStore(), doc.name")
		self.assertLess(checked, approved)
		self.assertLess(approved, queued, "enqueue refuses anything not Approved, so the status comes first")
		self.assertIn("except ValueError", body[queued:])
		self.assertIn("frappe.throw(", body[queued:], "a refusal rolls the approval back")

	def test_the_browser_test_reads_the_session_and_the_header(self):
		body = ast.unparse(function(self.approval, "browser_request"))
		self.assertIn(
			"workflow.signed_in_browser(frappe.session, request.headers.get('Authorization'))", body
		)

	def test_the_rules_stay_bench_free(self):
		for node in ast.walk(tree(PUBLISH / "workflow.py")):
			if isinstance(node, ast.Import | ast.ImportFrom):
				names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
				self.assertFalse(any(n.split(".")[0] == "frappe" for n in names), ast.unparse(node))

	def test_the_controller_and_the_actions_agree_on_the_flag(self):
		controller = tree(DOCTYPES / "social_post" / "social_post.py")
		cls = next(n for n in controller.body if isinstance(n, ast.ClassDef) and n.name == "SocialPost")
		methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
		self.assertEqual(ast.unparse(methods["validate"].body[0]), "self._refuse_status_edits()", "first")
		self.assertIn("self.flags.get('status_change')", ast.unparse(methods["_refuse_status_edits"]))
		self.assertIn("workflow.delete_problems(", ast.unparse(methods["on_trash"]))
		self.assertIn("doc.flags.status_change = True", ast.unparse(function(self.approval, "_set_status")))

	def test_the_form_calls_the_actions_by_post(self):
		js = (DOCTYPES / "social_post" / "social_post.js").read_text(encoding="utf-8")
		self.assertIn('const SOCIAL_ACTIONS = "erpnext_enhancements.marketing.publish.approval.";', js)
		self.assertIn('type: "POST"', js)
		for name in self.ACTIONS:
			self.assertIn(f'"{name}"', js, name)
		self.assertIn('social_action(frm, "approve", { modified: frm.doc.modified })', js)

	def test_the_store_writes_the_log_with_the_state(self):
		sweeper = tree(PUBLISH / "sweeper.py")
		store = next(n for n in sweeper.body if isinstance(n, ast.ClassDef) and n.name == "FrappeStore")
		update = ast.unparse(
			next(n for n in store.body if isinstance(n, ast.FunctionDef) and n.name == "update_job")
		)
		self.assertLess(update.index("doc.append('attempt_log', log)"), update.index("doc.save("), "one save")


if __name__ == "__main__":
	unittest.main()
