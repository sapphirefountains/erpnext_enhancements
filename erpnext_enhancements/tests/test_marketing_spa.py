"""Bench-free test: the /marketing app's server side (TASK-2026-01487).

What these pin:

1. **Who gets in.** Marketing Team, Marketing Manager or System Manager, at the page and at every
   endpoint; a signed-out visitor is sent to log in and back.
2. **The composer writes only what it may.** ``clean_post_input`` takes an allowlist: a payload
   carrying ``status``, ``approver`` or ``owner`` changes nothing.
3. **Times are the site's.** A time with a zone offset is refused rather than converted, and a
   new time in the past is refused (empty means "as soon as it is approved").
4. **The endpoint surface.** Every endpoint is POST-only and never guest, checks access first,
   and the client's ``M`` map and the whitelisted functions are the same set, so a rename with
   no matching edit fails here instead of 404ing in front of somebody.
5. **The shell and the route.** One ``website_route_rules`` entry serves the whole subtree, the
   controller's filename has no hyphen, and the shell loads bundles and bakes no post data in.

Filesystem, ``json``, ``ast`` and ``re`` only, plus modules that import no frappe
(``spa_rules``, ``workflow``, ``outbox``, ``constants``). No stub, so it shares a CI step with
the other marketing suites.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_spa
"""

import ast
import datetime
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import outbox as O
from erpnext_enhancements.marketing.publish import spa_rules as S
from erpnext_enhancements.marketing.publish import workflow as W

APP = REPO_ROOT / "erpnext_enhancements"
PUBLISH = APP / "marketing" / "publish"
SPA = PUBLISH / "spa.py"
ACTIONS = PUBLISH / "approval.py"
TRANSPORT = APP / "public" / "js" / "marketing" / "transport.js"
WWW = APP / "www"
NOW = datetime.datetime(2026, 9, 22, 10, 0)

#: Whitelisted endpoints the app deliberately does not dial, each with a reason.
NOT_DIALLED_BY_THE_APP: dict[str, str] = {}


def tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def whitelisted(path):
	return {
		node.name: node
		for node in tree(path).body
		if isinstance(node, ast.FunctionDef)
		and any("whitelist" in ast.unparse(d) for d in node.decorator_list)
	}


# ---------------------------------------------------------------- the rules


class AccessTests(unittest.TestCase):
	def test_the_three_roles_and_nobody_else(self):
		for role in ("Marketing Team", "Marketing Manager", "System Manager"):
			self.assertTrue(S.can_use({role, "All"}), role)
		for roles in (set(), {"All", "Guest"}, {"Sales Manager"}, {"Accounts Manager", "Employee"}):
			self.assertFalse(S.can_use(roles), roles)
		self.assertEqual(S.ACCESS_ROLES, {W.TEAM_ROLE, W.APPROVER_ROLE, "System Manager"})


class TimeTests(unittest.TestCase):
	def test_site_local_times(self):
		self.assertEqual(S.parse_local_datetime("2026-09-23 09:05"), datetime.datetime(2026, 9, 23, 9, 5))
		self.assertEqual(
			S.parse_local_datetime("2026-09-23T09:05:30"), datetime.datetime(2026, 9, 23, 9, 5, 30)
		)
		self.assertEqual(
			S.parse_local_datetime("2026-09-23 09:05:30.123456"), datetime.datetime(2026, 9, 23, 9, 5, 30)
		)
		self.assertIsNone(S.parse_local_datetime(""))
		self.assertIsNone(S.parse_local_datetime(None))
		for bad in (
			"2026-09-23T09:05:00Z",
			"2026-09-23 09:05+02:00",
			"tomorrow",
			"2026-02-30 09:00",
			"23/09/2026 9:00",
		):
			with self.assertRaises(ValueError, msg=bad):
				S.parse_local_datetime(bad)

	def test_calendar_window(self):
		first, last = S.calendar_window("2026-08-30", "2026-10-03")
		self.assertEqual(
			(first, last), (datetime.datetime(2026, 8, 30), datetime.datetime(2026, 10, 3, 23, 59, 59))
		)
		S.calendar_window("2026-07-26", "2026-09-05")  # the widest month grid there is: six weeks
		for start, end in (("2026-10-03", "2026-08-30"), ("2026-01-01", "2026-03-01"), ("x", "2026-01-01")):
			with self.assertRaises(ValueError, msg=(start, end)):
				S.calendar_window(start, end)

	def test_a_new_time_must_be_in_the_future(self):
		self.assertIsNone(S.schedule_problem(None, NOW), "empty means as soon as it is approved")
		self.assertIsNone(S.schedule_problem(NOW + datetime.timedelta(hours=1), NOW))
		self.assertIsNone(
			S.schedule_problem(NOW - datetime.timedelta(minutes=2), NOW), "the form was open a moment"
		)
		self.assertIn("already passed", S.schedule_problem(NOW - datetime.timedelta(days=1), NOW))


class ComposerInputTests(unittest.TestCase):
	def payload(self, **kw):
		base = {
			"title": "",
			"body": "Hello\nsecond line",
			"link": " https://sapphirefountains.com ",
			"scheduled_at": "2026-09-30 10:00:00",
			"targets": [{"social_account": "SACC-Facebook-1", "variant_text": " hi ", "first_comment": ""}],
			"media": ["MMA-1", {"asset": "MMA-2"}],
		}
		base.update(kw)
		return base

	def test_the_allowlist(self):
		values, targets, media = S.clean_post_input(
			self.payload(
				status="Approved",
				approver="boss@example.com",
				approved_at="2026-09-22",
				owner="x",
				name="SPOST-9",
			)
		)
		self.assertEqual(set(values), set(S.POST_FIELDS))
		for field in ("status", "approver", "approved_at", "owner", "name"):
			self.assertNotIn(field, values)
		self.assertEqual(values["link"], "https://sapphirefountains.com")
		self.assertEqual(values["scheduled_at"], datetime.datetime(2026, 9, 30, 10, 0))
		self.assertEqual(values["title"], "Hello", "an empty title takes the first line")
		self.assertIsNone(values["video_thumbnail"])
		self.assertEqual(
			targets, [{"social_account": "SACC-Facebook-1", "variant_text": "hi", "first_comment": ""}]
		)
		self.assertEqual(media, ["MMA-1", "MMA-2"])
		self.assertEqual(set(S.POST_FIELDS) & set(W.WORKFLOW_FIELDS), set(), "no workflow field is writable")

	def test_refusals(self):
		cases = [
			"not a dict",
			self.payload(targets="SACC-1"),
			self.payload(targets=[{"variant_text": "x"}]),
			self.payload(targets=[{"social_account": "A"}, {"social_account": "A"}]),
			self.payload(targets=[{"social_account": f"A{i}"} for i in range(S.MAX_TARGETS + 1)]),
			self.payload(media=[""]),
			self.payload(media=[f"MMA-{i}" for i in range(S.MAX_MEDIA + 1)]),
			self.payload(scheduled_at="2026-09-30T10:00:00Z"),
		]
		for payload in cases:
			with self.assertRaises(ValueError, msg=str(payload)[:80]):
				S.clean_post_input(payload)

	def test_default_title(self):
		self.assertEqual(S.default_title({"title": " Launch "}), "Launch")
		self.assertEqual(S.default_title({"video_title": "Plaza build"}), "Plaza build")
		self.assertEqual(S.default_title({}), "Untitled post")
		self.assertEqual(len(S.default_title({"body": "x" * 500})), S.TITLE_MAX)

	def test_targets_keep_their_rows(self):
		existing = [{"name": "row-fb", "social_account": "FB"}, {"name": "row-li", "social_account": "LI"}]
		wanted = [
			{"social_account": "LI", "variant_text": "new"},
			{"social_account": "YT", "variant_text": ""},
		]
		update, add, remove = S.plan_targets(existing, wanted)
		self.assertEqual(update, [("row-li", {"social_account": "LI", "variant_text": "new"})])
		self.assertEqual(add, [{"social_account": "YT", "variant_text": ""}])
		self.assertEqual(remove, ["row-fb"])


class ActionTests(unittest.TestCase):
	def test_what_each_person_may_do(self):
		pending = {"status": O.POST_PENDING_APPROVAL, "owner": "writer@x", "modified_by": "writer@x"}
		manager = S.post_actions(pending, "boss@x", {W.APPROVER_ROLE}, True, True)
		self.assertTrue(manager["can_approve"] and manager["can_edit"] and manager["can_send_back"])
		self.assertEqual(manager["approve_problems"], [])
		author = S.post_actions(pending, "writer@x", {W.APPROVER_ROLE}, True, True)
		self.assertFalse(author["can_approve"])
		self.assertTrue(any("wrote it" in p for p in author["approve_problems"]), "the reason is shown")
		token = S.post_actions(pending, "boss@x", {W.APPROVER_ROLE}, True, False)
		self.assertFalse(token["can_approve"], "never from a token")
		reader = S.post_actions(pending, "boss@x", {W.APPROVER_ROLE}, False, True)
		self.assertFalse(any(reader[k] for k in ("can_edit", "can_approve", "can_cancel", "can_delete")))

		scheduled = S.post_actions({"status": O.POST_SCHEDULED}, "boss@x", {W.APPROVER_ROLE}, True, True)
		self.assertEqual(
			{
				k: scheduled[k]
				for k in ("can_edit", "can_submit", "can_approve", "can_cancel", "can_delete", "locked")
			},
			{
				"can_edit": False,
				"can_submit": False,
				"can_approve": False,
				"can_cancel": True,
				"can_delete": False,
				"locked": True,
			},
		)
		self.assertEqual(scheduled["approve_problems"], [], "reasons only matter while pending")
		draft = S.post_actions({"status": O.POST_DRAFT}, "w@x", {W.TEAM_ROLE}, True, True)
		self.assertTrue(draft["can_submit"] and draft["can_delete"] and not draft["can_approve"])


class QuotaAndLimitsTests(unittest.TestCase):
	def test_quota_view(self):
		rows = S.quota_view(
			[
				{
					"name": "IG",
					"network": "Instagram",
					"account_name": "@s",
					"quota_remaining": 18,
					"quota_checked_at": NOW - datetime.timedelta(hours=1),
				},
				{
					"name": "YT",
					"network": "YouTube",
					"account_name": "Channel",
					"quota_remaining": 0,
					"quota_checked_at": None,
				},
				{
					"name": "OLD",
					"network": "Instagram",
					"handle": "old",
					"quota_remaining": 5,
					"quota_checked_at": NOW - datetime.timedelta(days=3),
				},
				{"name": "FB", "network": "Facebook", "quota_remaining": None, "quota_checked_at": None},
			],
			NOW,
		)
		self.assertEqual(
			[r["account"] for r in rows], ["IG", "YT", "OLD"], "only the two networks with a counted quota"
		)
		self.assertEqual((rows[0]["remaining"], rows[0]["stale"]), (18, False))
		self.assertEqual(
			(rows[1]["remaining"], rows[1]["stale"]), (None, True), "never checked is unknown, not zero"
		)
		self.assertEqual((rows[2]["remaining"], rows[2]["stale"], rows[2]["label"]), (5, True, "old"))

	def test_limits_come_from_the_constants_the_server_checks_with(self):
		limits = S.limits()
		self.assertEqual(set(limits), set(P.PUBLISH_NETWORKS))
		self.assertEqual(limits["Instagram"]["text"], P.INSTAGRAM_CAPTION_MAX)
		self.assertEqual(limits["LinkedIn"]["text"], P.LINKEDIN_COMMENTARY_MAX)
		self.assertEqual(limits["YouTube"]["text_bytes"], P.YOUTUBE_DESCRIPTION_MAX_BYTES)


class AssetTests(unittest.TestCase):
	def test_asset_input(self):
		values = S.asset_input(
			{
				"title": " Plaza ",
				"asset_type": "Image",
				"mime_type": "IMAGE/JPEG",
				"width": "1080",
				"height": 1350,
				"duration_seconds": "",
			}
		)
		self.assertEqual(
			values,
			{
				"title": "Plaza",
				"asset_type": "Image",
				"usage_rights": "Needs client approval",
				"alt_text": None,
				"mime_type": "image/jpeg",
				"width": 1080,
				"height": 1350,
				"duration_seconds": None,
			},
		)
		self.assertEqual(
			S.asset_input(
				{"title": "V", "asset_type": "Video", "mime_type": "video/mp4", "duration_seconds": 12.5}
			)["duration_seconds"],
			12.5,
		)
		for bad in (
			{"asset_type": "Image"},
			{"title": "x", "asset_type": "Audio"},
			{"title": "x", "asset_type": "Image", "usage_rights": "Anything goes"},
			{"title": "x", "asset_type": "Image", "mime_type": "video/mp4"},
			{"title": "x", "asset_type": "Document", "mime_type": "text/html"},
			{"title": "x", "asset_type": "Image", "width": -1},
			{"title": "x", "asset_type": "Image", "width": "wide"},
		):
			with self.assertRaises(ValueError, msg=bad):
				S.asset_input(bad)

	def test_the_rights_match_the_doctype(self):
		import json

		fields = {
			f["fieldname"]: f
			for f in json.loads(
				(
					APP / "marketing" / "doctype" / "marketing_media_asset" / "marketing_media_asset.json"
				).read_text(encoding="utf-8")
			)["fields"]
		}
		self.assertEqual(tuple(fields["usage_rights"]["options"].split("\n")), S.USAGE_RIGHTS)
		self.assertEqual(tuple(fields["asset_type"]["options"].split("\n")), S.ASSET_TYPES)
		self.assertEqual(
			S.USAGE_RIGHTS[0], fields["usage_rights"]["default"], "an upload starts as the doctype's default"
		)

	def test_metric_totals(self):
		totals = S.metric_totals([{"impressions": 10, "reach": None}, {"impressions": 5, "clicks": 2}])
		self.assertEqual(
			totals, {"impressions": 15, "reach": None, "engagements": None, "clicks": 2, "video_views": None}
		)


# ---------------------------------------------------------------- the surface


class SurfaceTests(unittest.TestCase):
	def test_every_endpoint_is_post_only_never_guest_and_checks_access_first(self):
		endpoints = whitelisted(SPA)
		self.assertGreaterEqual(len(endpoints), 10)
		for name, fn in endpoints.items():
			self.assertEqual(
				[ast.unparse(d) for d in fn.decorator_list], ["frappe.whitelist(methods=['POST'])"], name
			)
			first = next(
				s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
			)
			self.assertEqual(ast.unparse(first), "_require()", f"{name} checks access before anything else")

	def test_the_client_dials_exactly_the_whitelisted_functions(self):
		js = TRANSPORT.read_text(encoding="utf-8")
		spa = re.search(r'const SPA = "([^"]+)"', js).group(1)
		actions = re.search(r'const ACTIONS = "([^"]+)"', js).group(1)
		self.assertEqual(spa, "erpnext_enhancements.marketing.publish.spa")
		self.assertEqual(actions, "erpnext_enhancements.marketing.publish.approval")
		dialled = set()
		for prefix, name in re.findall(r":\s*`\$\{(SPA|ACTIONS)\}\.(\w+)`", js):
			dialled.add(f"{spa if prefix == 'SPA' else actions}.{name}")
		exposed = {f"{spa}.{n}" for n in whitelisted(SPA)} | {f"{actions}.{n}" for n in whitelisted(ACTIONS)}
		self.assertEqual(dialled - exposed, set(), "the app dials something that is not there")
		self.assertEqual(exposed - dialled - set(NOT_DIALLED_BY_THE_APP), set(), "an endpoint nobody calls")

	def test_save_post_writes_only_through_the_allowlist(self):
		node = whitelisted(SPA)["save_post"]
		fn = ast.unparse(node)
		self.assertIn("spa_rules.clean_post_input(", fn)
		self.assertIn("doc.update(fields)", fn)
		# It reads doc.status (is the post still editable?); it never assigns a workflow field.
		assigned = {
			target.attr
			for stmt in ast.walk(node)
			if isinstance(stmt, ast.Assign | ast.AugAssign)
			for target in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target])
			if isinstance(target, ast.Attribute)
		}
		self.assertEqual(assigned & {"status", "approver", "approved_at", "status_change"}, set())
		for forbidden in ("flags.status_change", "ignore_permissions", "'approver'", "'approved_at'"):
			self.assertNotIn(forbidden, fn)
		self.assertIn(
			"get_datetime(modified) != get_datetime(doc.modified)", fn, "a stale composer is refused"
		)
		self.assertIn("EDITABLE_POST_STATUSES", fn)

	def test_reschedule_refuses_approved_posts_and_stale_calendars(self):
		fn = ast.unparse(whitelisted(SPA)["reschedule"])
		self.assertIn("EDITABLE_POST_STATUSES", fn)
		self.assertIn("get_datetime(modified) != get_datetime(doc.modified)", fn)
		self.assertIn("spa_rules.schedule_problem(", fn)

	def test_create_asset_takes_only_the_callers_unattached_upload(self):
		fn = ast.unparse(whitelisted(SPA)["create_asset"])
		self.assertIn("row.owner != frappe.session.user", fn)
		self.assertIn("row.attached_to_doctype", fn)
		self.assertIn("frappe.has_permission(ASSET, 'create')", fn)

	def test_nothing_here_bypasses_permissions(self):
		source = SPA.read_text(encoding="utf-8")
		self.assertNotIn("ignore_permissions", source)
		self.assertNotIn("frappe.db.sql", source)
		self.assertNotIn("set_user", source)

	def test_the_rules_stay_bench_free(self):
		for node in ast.walk(tree(PUBLISH / "spa_rules.py")):
			if isinstance(node, ast.Import | ast.ImportFrom):
				names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
				self.assertFalse(any(n.split(".")[0] == "frappe" for n in names), ast.unparse(node))


class ShellTests(unittest.TestCase):
	def test_one_route_rule_serves_the_subtree(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('{"from_route": "/marketing/<path:marketing_path>", "to_route": "marketing"},', hooks)
		self.assertIn(
			'{"from_route": "/feedback/<path:feedback_path>", "to_route": "feedback"},',
			hooks,
			"its twin survives",
		)

	def test_the_controller(self):
		self.assertTrue((WWW / "marketing.py").exists())
		self.assertFalse(list(WWW.glob("marketing-*.py")), "a hyphenated controller is never imported")
		source = (WWW / "marketing.py").read_text(encoding="utf-8")
		fn = ast.unparse(
			next(
				n
				for n in tree(WWW / "marketing.py").body
				if isinstance(n, ast.FunctionDef) and n.name == "get_context"
			)
		)
		self.assertIn("raise frappe.Redirect", fn)
		self.assertIn("redirect-to=", fn, "the deep link survives the login")
		self.assertIn("spa_rules.can_use(frappe.get_roles())", fn)
		self.assertIn("frappe.PermissionError", fn)
		self.assertLess(fn.index("frappe.Redirect"), fn.index("can_use"), "sign in first, then the role")
		self.assertRegex(source, r"(?m)^no_cache = 1$")

	def test_the_shell_loads_bundles_and_no_data(self):
		shell = (WWW / "marketing.html").read_text(encoding="utf-8")
		self.assertIn("bundled_asset('marketing.bundle.js')", shell)
		self.assertIn("bundled_asset('marketing.bundle.css')", shell)
		self.assertNotIn("/assets/erpnext_enhancements/", shell)
		boot = re.search(r"window\.EE_MARKETING_BOOT = \{([\s\S]*?)\};", shell).group(1)
		self.assertEqual(re.findall(r"(\w+):", boot), ["user", "full_name", "csrf_token", "build"])
		self.assertTrue((APP / "public" / "js" / "marketing.bundle.js").exists())
		self.assertTrue((APP / "public" / "css" / "marketing.bundle.css").exists())


if __name__ == "__main__":
	unittest.main()
