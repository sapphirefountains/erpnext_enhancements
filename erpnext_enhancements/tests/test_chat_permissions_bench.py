"""Row-level chat permissions, against a real database. **THIS SUITE DOES NOT RUN IN CI.**

======================================================================================
NOT RUN IN CI. A HUMAN MUST RUN IT AND RECORD THE RESULT AT THE PHASE 1 CHECKPOINT.
======================================================================================

    bench --site <site> run-tests --app erpnext_enhancements \\
        --module erpnext_enhancements.tests.test_chat_permissions_bench

    # or, narrowed to one case while iterating:
    bench --site <site> run-tests --app erpnext_enhancements \\
        --module erpnext_enhancements.tests.test_chat_permissions_bench \\
        --test test_a_non_member_sees_no_rooms_on_the_list_path

There is no Frappe integration-test job in this repo. It was removed because v16's
test-record auto-generation walks the whole ERPNext doctype dependency graph and kept
aborting on environment gaps, so it gated PRs on churn unrelated to this app (`ci.yml`,
closing note). Everything here needs a real `DatabaseQuery`, a real DocPerm stack and a
real `frappe.set_user`, none of which can be faked usefully — a stubbed permission test
asserts that the stub works.

That makes this suite's rating **High and its value conditional**: it is the security test
CI does not run, so it is worth exactly as much as the discipline of running it. Appendix B
§3.6 requires its output at the checkpoint. If you are reading this because a chat
permission changed, run it before you merge.

What it covers, and why each one is here
----------------------------------------

* **A non-member is denied on the list path and on the single-document path.** Two
  different mechanisms — `permission_query_conditions` filters `get_list`;
  `has_permission` refuses a read by name — and passing one proves nothing about the other.
  The desk's report view is exercised too, because ADR §9-F's standing acceptance bar is
  literally *"even a raw report view cannot leak another user's rooms."*
* **The v16 explicit-`True` rule.** A `has_permission` hook that returns `None` — or falls
  off the end of a branch — now **denies**. Every hook is asserted to return a real `bool`
  on every path, exception paths included. The failure mode is a silent lockout rather than
  a leak, which is the safe direction, but it is a production outage nobody can debug from
  the symptom, and it is invisible to every bench-free test.
* **The admin oversight role sees everything** — and only because it holds the configured
  role. Decision #12's escape hatch is the one thing that returns an unrestricted `""`, so
  the test that matters is the *negative* one: the same user without the role is scoped
  like anybody else, and the hatch stays shut while `admin_oversight_role` is blank (which
  is how it ships).
* **Raw SQL without the membership filter would leak.** Documented as a negative example
  and *never executed as one* — see :class:`TestRawSqlIsNotProtectedByTheseHooks`.

Phase 2 additions (§4.K) — rows now arrive from a second origin
---------------------------------------------------------------

Phase 1 wrote these hooks and **never ran them**. Phase 2 gives them a second writer
(Google), a dozen background jobs with no session user, an audit table holding deleted
bodies, and senders who have no ERPNext `User` at all. Each of the following is one test,
and each defends something the Phase 1 shape did not have to survive:

* **Every read entry point, not just `get_list`.** `frappe.get_list`, the desk **report
  view**, `frappe.client.get_list`, the `/api/resource` handlers, and `frappe.get_doc`. The
  report view is called out separately because it is the path people forget, and ADR §9-F's
  standing acceptance bar is worded about it.
* **A `DocShare` row must not widen a chat room read.** Read from the v16 source this
  session: `frappe/database/query.py` ORs `table.name.isin(shared_docs)` into the WHERE
  after every condition, commented *"shared docs trump all other restrictions"*. `Chat Room`
  carries a `read` DocPerm, so this is **reachable** rather than theoretical. See
  :class:`TestADocShareMustNotWidenAChatRoomRead` — a failure there is a finding about the
  platform, not a flaky test, and the test names say so.
* **Tombstones.** Per the contract the deleted body deliberately stays on the live row
  (Google's tombstone is empty of content, so ERPNext is the only copy). That makes
  `is_deleted = 0` an obligation of every read path, and this suite pins where that
  obligation lives.
* **`Chat Message Revision` is tighter than `Chat Message`, not equal.** It is where
  superseded and deleted content lives.
* **A message with `sender_email` and a null `sender`** — a Chat member with no ERPNext
  account — is still scoped by room membership. The membership rule keys on the room, and
  this proves an unattributable sender did not accidentally become an unscoped one.
* **The private-file 403**, authenticated and unauthenticated. Duplicated on purpose:
  `tests/test_chat_attachments_bench.py` covers it from the attachment side, and this suite
  is the one a human is told to run when a chat *permission* changes.

Fixtures are three users, two rooms and four messages, all created by the suite and all
rolled back with the enclosing transaction. Nothing here reads production data, and the
message bodies are deliberately dull.

Two honesty notes for whoever runs this
---------------------------------------

1. **Nothing here has ever been executed.** Not by CI, which has no bench, and not by the
   session that wrote the Phase 2 additions, which had no Frappe installed. Treat the first
   run as a debugging session: an entry point whose signature moved will surface as a
   `TypeError` or an `ImportError`, which fails the test rather than skipping it. That is
   deliberate — a security test that skips is worse than one that breaks.
2. **The REST cases call the whitelisted handler, not an HTTP socket.** `/api/resource/<DT>`
   dispatches to `frappe.client.get_list` and `/api/resource/<DT>/<name>` to
   `frappe.client.get`; those functions are what is exercised. What that does *not* prove is
   the routing layer above them — a future route that reaches `DatabaseQuery` by another
   path is out of this suite's reach.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

# v16 renamed the base class; `frappe.tests.utils.FrappeTestCase` is the compatibility
# shim and every other bench suite in this repo still imports it. Prefer the new name so
# this file does not need editing when the shim goes, and fall back so it runs on a bench
# that still predates it. Both give the per-test transaction rollback this suite relies on.
try:  # pragma: no cover - which branch runs is a property of the bench, not the code
	from frappe.tests import IntegrationTestCase as _ChatTestCase
except ImportError:  # pragma: no cover
	from frappe.tests.utils import FrappeTestCase as _ChatTestCase

from erpnext_enhancements.chat import permissions

#: Three identities. `OUTSIDER` is the whole point: a real, enabled, logged-in employee who
#: simply is not in the room. Not a guest, not a disabled account — the realistic attacker
#: here is a colleague, and every "denied" assertion below is about them.
MEMBER = "chat_perm_member@example.com"
OUTSIDER = "chat_perm_outsider@example.com"
AUDITOR = "chat_perm_auditor@example.com"

OVERSIGHT_ROLE = "Chat Auditor"
MEMBER_ROLE = "Chat User"


def _ensure_role(role_name: str) -> None:
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def _ensure_user(email: str, roles: list[str]) -> str:
	"""Create the user if absent and give it `roles` **directly**.

	Direct role grants are correct *here* and wrong in production: a profiled user's roles
	are rebuilt from their Role Profiles on every save, so a direct grant is wiped. These
	test users hold no profile, so nothing rebuilds them. Real grants go through a Role
	Profile (`patches/seed_chat_roles.py`, ADR §A.8).
	"""
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	existing = {row.role for row in user.get("roles") or []}
	for role in roles:
		_ensure_role(role)
		if role not in existing:
			user.append("roles", {"role": role})
	user.save(ignore_permissions=True)
	return email


class ChatPermissionFixture(_ChatTestCase):
	"""Two rooms, three users, four messages. Shared by every case below."""

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")

		_ensure_user(MEMBER, [MEMBER_ROLE])
		_ensure_user(OUTSIDER, [MEMBER_ROLE])
		_ensure_user(AUDITOR, [MEMBER_ROLE])

		# The hatch ships blank and several tests depend on it being blank. Any test that
		# wants it open opens it explicitly and says so.
		self._set_oversight_role("")

		self.room = self._make_room("Chat permissions - member's room")
		self.other_room = self._make_room("Chat permissions - a room nobody here is in")

		self._add_member(self.room, MEMBER, is_active=1)
		# A departed member with a stamped boundary: CQ-10 says history stays visible up to
		# the moment they left, and nothing after.
		self._add_member(self.room, AUDITOR, is_active=0, left_seq=2)

		self.msg_1 = self._make_message(self.room, seq=1, text="one")
		self.msg_2 = self._make_message(self.room, seq=2, text="two")
		self.msg_3 = self._make_message(self.room, seq=3, text="three")
		self.foreign_message = self._make_message(self.other_room, seq=1, text="not yours")

	def tearDown(self) -> None:
		frappe.set_user("Administrator")
		super().tearDown()

	# ------------------------------------------------------------------ fixture helpers

	def _set_oversight_role(self, role: str) -> None:
		"""Set the hatch, then make sure the *next read* actually sees it.

		`_oversight_role()` reads through `frappe.db.get_single_value`, which is cached in
		two places on v16 — the document cache and `frappe.db.value_cache`. A test that sets
		the field and does not clear both reads the previous value and passes for the wrong
		reason, which on a permission test is the worst kind of green.
		"""
		settings = frappe.get_single("Chat Settings")
		settings.admin_oversight_role = role
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Chat Settings", "Chat Settings")
		if getattr(frappe.db, "value_cache", None):
			frappe.db.value_cache = {}
		self.assertEqual(
			(frappe.db.get_single_value("Chat Settings", "admin_oversight_role") or ""),
			role,
			"the oversight role did not take effect; a cache is still serving the old value",
		)

	def _make_room(self, title: str) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Chat Room",
				"room_type": "Group",
				"title": title,
				"provisioning_mode": "Not Mirrored",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _add_member(self, room: str, user: str, is_active: int = 1, left_seq: int = 0) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Chat Room Member",
				"room": room,
				"user": user,
				"role": "Member",
				"is_active": is_active,
				"left_seq": left_seq,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_message(self, room: str, seq: int, text: str) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Chat Message",
				"room": room,
				"seq": seq,
				"sender": MEMBER,
				"sender_kind": "Human",
				"message_type": "Text",
				"text": text,
				"text_plain": text,
				"client_message_id": f"client-bench-{room[:8]}-{seq}",
				"sync_origin": "ERPNext",
				"sync_state": "Not Mirrored",
			}
		).insert(ignore_permissions=True)
		return doc.name


# ---------------------------------------------------------------------------
# The list path
# ---------------------------------------------------------------------------


class TestTheListPath(ChatPermissionFixture):
	"""`permission_query_conditions`, exercised through a real `DatabaseQuery`.

	`frappe.get_list` — not `frappe.get_all`, which is `get_list(ignore_permissions=True)`
	wearing a friendlier name and would pass this suite while proving nothing.
	"""

	def test_a_non_member_sees_no_rooms_on_the_list_path(self) -> None:
		frappe.set_user(OUTSIDER)
		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertNotIn(self.room, names)
		self.assertNotIn(self.other_room, names)

	def test_a_member_sees_their_own_room_and_only_that(self) -> None:
		frappe.set_user(MEMBER)
		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertIn(self.room, names)
		self.assertNotIn(self.other_room, names)

	def test_a_non_member_sees_no_messages_on_the_list_path(self) -> None:
		"""`Chat Message` carries zero DocPerm, so this should be refused twice over.

		Asserted anyway: the query condition is the gate that has to be standing on the day
		somebody adds a `System Manager` DocPerm row so they can look at a message in the
		desk — the most likely regression in the whole feature (ADR §F.18.1).
		"""
		frappe.set_user(OUTSIDER)
		names = [
			row.name for row in frappe.get_list("Chat Message", fields=["name"], limit_page_length=0)
		]
		self.assertEqual(names, [])

	def test_the_report_view_cannot_leak_another_users_rooms(self) -> None:
		"""ADR §9-F's standing acceptance bar, as an executable assertion.

		The desk report view goes through the same `DatabaseQuery` but asks for arbitrary
		field lists, a group-by and a different ordering — the shapes most likely to have
		the condition dropped. `title` is requested explicitly because the leak that would
		matter is the room's *name*, not its id.
		"""
		frappe.set_user(OUTSIDER)
		rows = frappe.get_list(
			"Chat Room",
			fields=["name", "title", "room_type"],
			order_by="modified desc",
			limit_page_length=0,
		)
		self.assertEqual(rows, [])

		grouped = frappe.get_list(
			"Chat Room",
			fields=["room_type", "count(name) as total"],
			group_by="room_type",
			limit_page_length=0,
		)
		self.assertEqual(
			[row for row in grouped if (row.get("total") or 0) > 0],
			[],
			"a group-by must not disclose that rooms exist",
		)

	def test_a_departed_member_reads_nothing_pending_cq_10(self) -> None:
		"""CQ-10 is unanswered, so the message rule fails closed. `AUDITOR` left at seq 2.

		This test asserts the *absence* of a rule on purpose. `left_seq` exists as a column
		and grants nothing: what someone may still read after leaving a room is a governance
		question the business has not answered, and the first implementation of this phase
		answered it in code, in the access-widening direction. Until a human decides, a
		departed member is simply a non-member.

		**When CQ-10 is answered "keep history up to the leave point", this test inverts** —
		`msg_1` and `msg_2` become `assertIn` and only `msg_3` stays out. Do not soften it
		before then: a green test asserting the wider rule is how the widening gets read as
		approved.
		"""
		frappe.set_user(AUDITOR)
		names = [
			row.name for row in frappe.get_list("Chat Message", fields=["name"], limit_page_length=0)
		]
		self.assertNotIn(self.msg_1, names, "left_seq must not grant history while CQ-10 is open")
		self.assertNotIn(self.msg_2, names, "left_seq must not grant history while CQ-10 is open")
		self.assertNotIn(self.msg_3, names, "a departed member must not receive a live feed")

	def test_a_departed_member_loses_the_room_document_immediately(self) -> None:
		"""Deliberately stricter than the message rule, and the asymmetry is the design.

		`Chat Room` read is the doc-room join, and a socket join is evaluated once and never
		re-checked — so a departed member holding room read would get a live feed of
		everything said after they left. Removing somebody from a room has to remain a
		control (ADR §H.4.3).
		"""
		frappe.set_user(AUDITOR)
		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertNotIn(self.room, names)


# ---------------------------------------------------------------------------
# The single-document path
# ---------------------------------------------------------------------------


class TestTheSingleDocumentPath(ChatPermissionFixture):
	"""`has_permission`, through `frappe.has_permission(doctype, doc=<name>)`.

	That is the entry point the socket's `doc_subscribe` reaches (ADR §H.4.1), so these
	assertions are the realtime boundary and not merely the REST one.
	"""

	def test_a_non_member_is_denied_a_room_by_name(self) -> None:
		frappe.set_user(OUTSIDER)
		self.assertFalse(frappe.has_permission("Chat Room", doc=self.room, ptype="read"))

	def test_a_member_is_allowed_their_own_room_by_name(self) -> None:
		frappe.set_user(MEMBER)
		self.assertTrue(frappe.has_permission("Chat Room", doc=self.room, ptype="read"))

	def test_a_member_is_denied_a_room_they_are_not_in(self) -> None:
		frappe.set_user(MEMBER)
		self.assertFalse(frappe.has_permission("Chat Room", doc=self.other_room, ptype="read"))

	def test_a_non_member_is_denied_a_message_by_name(self) -> None:
		frappe.set_user(OUTSIDER)
		self.assertFalse(frappe.has_permission("Chat Message", doc=self.msg_1, ptype="read"))

	def test_get_doc_by_name_refuses_a_non_member(self) -> None:
		"""The realistic attack: a colleague who guessed or was sent a room id.

		`frappe.get_doc(...).check_permission()` is the path a whitelisted endpoint takes,
		and it must raise rather than return the document.
		"""
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Chat Room", self.room).check_permission("read")

	def test_the_hook_denies_a_room_that_does_not_exist(self) -> None:
		"""A blank or unresolvable docname must be a refusal, not an unscoped allow."""
		for doc in (None, "", "Chat Room That Was Never Created"):
			self.assertIs(
				permissions.chat_room_has_permission(doc, "read", OUTSIDER),
				False,
				f"docname {doc!r}",
			)


# ---------------------------------------------------------------------------
# The v16 explicit-True rule
# ---------------------------------------------------------------------------


class TestEveryHookReturnsARealBoolean(ChatPermissionFixture):
	"""On v16, a `has_permission` hook returning `None` **denies**.

	So a missing `return True` is a silent lockout, and a missing `return` at all is the
	same thing wearing a different disguise. `assertIsInstance(..., bool)` is the assertion
	that catches both, and `assertIs(..., True/False)` catches the near-miss where a hook
	returns a truthy *object* (a Document, a non-empty string) that Frappe will accept today
	and something stricter may not tomorrow.
	"""

	HOOKS = (
		("Chat Room", "chat_room_has_permission"),
		("Chat Room Member", "chat_room_member_has_permission"),
		("Chat Message", "chat_message_has_permission"),
		("Chat Attachment", "chat_attachment_has_permission"),
	)

	def _docs_for(self, doctype: str) -> list[Any]:
		"""Every shape `doc` genuinely arrives as, plus the ones that break a naive hook."""
		shapes: list[Any] = [None, "", {}, "nonexistent-name"]
		if doctype == "Chat Room":
			shapes += [self.room, frappe.get_doc("Chat Room", self.room), {"name": self.room}]
		elif doctype == "Chat Message":
			shapes += [
				frappe.get_doc("Chat Message", self.msg_1),
				{"room": self.room, "seq": 1},
				{"room": "", "seq": None},
			]
		elif doctype == "Chat Room Member":
			shapes += [{"room": self.room, "user": MEMBER}, {"room": None, "user": None}]
		else:
			shapes += [{"message": self.msg_1}, {"message": ""}, {"message": "nonexistent"}]
		return shapes

	def test_every_hook_returns_a_bool_for_every_user_and_document_shape(self) -> None:
		for doctype, funcname in self.HOOKS:
			hook = getattr(permissions, funcname)
			for user in (MEMBER, OUTSIDER, AUDITOR, "Administrator", "Guest", "", None):
				for doc in self._docs_for(doctype):
					result = hook(doc, "read", user)
					self.assertIsInstance(
						result,
						bool,
						f"{funcname}({doc!r}, 'read', {user!r}) returned "
						f"{result!r} ({type(result).__name__}); on v16 a non-True return "
						"DENIES, so a None here is a silent production lockout",
					)

	def test_every_hook_returns_a_bool_when_the_database_raises(self) -> None:
		"""The exception paths, which are the ones a happy-path test never reaches.

		Every membership probe in `permissions.py` wraps its query in `except: return False`
		— fail closed, because an unanswerable membership question is a "no". This forces
		that branch by making the probe itself raise.
		"""

		def _explode(*args: Any, **kwargs: Any) -> Any:
			# A plain exception on purpose: `permissions.py` catches bare `Exception`
			# precisely because it cannot predict what a failing connection raises, and
			# pinning a specific class here would test the test rather than the guard.
			raise RuntimeError("simulated database failure")

		original_exists = frappe.db.exists
		original_get_value = frappe.db.get_value
		try:
			frappe.db.exists = _explode
			frappe.db.get_value = _explode
			for doctype, funcname in self.HOOKS:
				hook = getattr(permissions, funcname)
				for doc in self._docs_for(doctype):
					result = hook(doc, "read", OUTSIDER)
					self.assertIs(
						result,
						False,
						f"{funcname} must fail CLOSED when the database is unavailable, "
						f"got {result!r}",
					)
		finally:
			frappe.db.exists = original_exists
			frappe.db.get_value = original_get_value

	def test_every_query_condition_returns_a_string(self) -> None:
		"""A `permission_query_conditions` hook returning `None` is a TypeError in
		`DatabaseQuery`, i.e. it takes the list view down rather than leaking."""
		for funcname in (
			"chat_room_query",
			"chat_room_member_query",
			"chat_message_query",
			"chat_attachment_query",
		):
			builder = getattr(permissions, funcname)
			for user in (MEMBER, OUTSIDER, "Administrator", "Guest", "", None):
				condition = builder(user)
				self.assertIsInstance(condition, str, f"{funcname}({user!r})")

	def test_guest_is_refused_everywhere_rather_than_defaulted(self) -> None:
		"""`Guest` is a real, addressable session, not an absence of one.

		`user:Guest` is a *shared* realtime room, so treating Guest as "no user, apply the
		default" would put every anonymous visitor in the same bucket (CHAT-RT-3).
		"""
		for funcname in ("chat_room_query", "chat_message_query", "chat_attachment_query"):
			self.assertEqual(getattr(permissions, funcname)("Guest"), "1 = 0", funcname)
		for _doctype, funcname in self.HOOKS:
			self.assertIs(getattr(permissions, funcname)(self.room, "read", "Guest"), False, funcname)


# ---------------------------------------------------------------------------
# The oversight escape hatch
# ---------------------------------------------------------------------------


class TestTheOversightRole(ChatPermissionFixture):
	"""Decision #12: the configured oversight role may read chat it is not a participant in.

	The hatch is the only thing in this module that returns an unrestricted `""`, so it gets
	the most negative tests: shut by default, opened only by the settings field, and gated
	on the role rather than on anything a caller can supply.
	"""

	def test_the_hatch_is_shut_by_default(self) -> None:
		"""`admin_oversight_role` ships blank, and blank must mean nobody.

		Failing closed here is what makes "we have not decided who audits chat yet" a safe
		state rather than an open one.
		"""
		self._set_oversight_role("")
		frappe.set_user(AUDITOR)
		# Asserted by behaviour, not by comparing SQL text: the fragment's exact spelling is
		# an implementation detail that a query-plan tweak is allowed to change, and a test
		# that pins it would fail for a reason that is not a security regression.
		self.assertNotEqual(permissions.chat_room_query(AUDITOR), "")
		self.assertFalse(frappe.has_permission("Chat Room", doc=self.other_room, ptype="read"))

	def test_the_oversight_role_is_scoped_like_everybody_else_in_these_hooks(self) -> None:
		"""**Inverted in v1.301.0.** This used to assert the opposite, and the assertion was
		the specification, so read the reason rather than the diff.

		These hooks used to hand an oversight-role holder an unrestricted scope. Tracing every
		consumer showed the audited oversight path used none of it — `retrieve_for_oversight`
		gates on `_has_oversight` itself and reads through raw SQL — while three unaudited
		things used all of it: `rooms.get_room` (which returned `last_message_preview` and the
		roster to a non-member, from the SPA, with no reason and no row), the socket join, and
		the desk list/report views.

		Decision #12 is not revoked; it moved to `chat/governance/viewer.py`, which demands a
		reason and writes one hash-chained row. What this test now pins is that the *hooks*
		grant nothing — G6-7 satisfied by the absent capability, the same way it was already
		satisfied for `Chat Message`.
		"""
		self._set_oversight_role(OVERSIGHT_ROLE)
		_ensure_role(OVERSIGHT_ROLE)
		user = frappe.get_doc("User", AUDITOR)
		user.append("roles", {"role": OVERSIGHT_ROLE})
		user.save(ignore_permissions=True)

		frappe.set_user(AUDITOR)
		self.assertNotEqual(permissions.chat_room_query(AUDITOR), "")
		self.assertNotEqual(permissions.chat_message_query(AUDITOR), "")
		self.assertIs(permissions.chat_room_has_permission(self.other_room, "read", AUDITOR), False)

		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertIn(self.room, names, "the auditor's OWN room must still be visible")
		self.assertNotIn(self.other_room, names, "a room they are not in must not be")

	def test_the_audited_door_still_opens_for_the_role(self) -> None:
		"""The other half, and the reason the change above is a relocation rather than a
		revocation: the one function permitted to open the hatch still does."""
		self._set_oversight_role(OVERSIGHT_ROLE)
		_ensure_role(OVERSIGHT_ROLE)
		user = frappe.get_doc("User", AUDITOR)
		user.append("roles", {"role": OVERSIGHT_ROLE})
		user.save(ignore_permissions=True)

		self.assertEqual(
			permissions.membership_filter_sql("`r`.`name`", AUDITOR, allow_oversight=True), "1 = 1"
		)

	def test_the_same_user_without_the_role_is_scoped_like_anybody_else(self) -> None:
		"""The load-bearing negative: configuring the role must not widen it to everyone."""
		self._set_oversight_role(OVERSIGHT_ROLE)
		_ensure_role(OVERSIGHT_ROLE)

		frappe.set_user(OUTSIDER)
		self.assertNotEqual(permissions.chat_room_query(OUTSIDER), "")
		self.assertIs(permissions.chat_room_has_permission(self.room, "read", OUTSIDER), False)
		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertEqual(names, [])

	def test_a_role_that_does_not_exist_does_not_open_the_hatch(self) -> None:
		"""A typo in the settings field must fail closed, not fail open."""
		self._set_oversight_role("Chat Auditorr")
		frappe.set_user(AUDITOR)
		self.assertIs(permissions.chat_room_has_permission(self.other_room, "read", AUDITOR), False)


# ---------------------------------------------------------------------------
# Raw SQL — the documented negative example
# ---------------------------------------------------------------------------


class TestRawSqlIsNotProtectedByTheseHooks(ChatPermissionFixture):
	"""The most likely route to a real data leak in this system, written down.

	**Nothing in this class executes an unfiltered query.** Running the leak to prove it
	leaks would put another user's message text into a test process and a CI log for no
	information — everyone already agrees it leaks; that is the premise, not the finding.
	What is worth asserting is that the *remedy* works and cannot silently evaporate.

	The negative example, for the record and for the reviewer who is about to write a
	history-paging query::

        # WRONG. Returns every message in the company, for anybody who can reach the
        # endpoint. No DocPerm, no permission_query_conditions, no has_permission --
        # frappe.db.sql bypasses the entire permission stack, and so does frappe.get_all.
        rows = frappe.db.sql(
            "select name, text from `tabChat Message` where room = %s order by seq desc",
            (room,),
        )

    The fix is never "remember to check membership first"; it is to AND in the shared
    fragment, so the raw path and the hook path cannot drift::

        # RIGHT.
        rows = frappe.db.sql(
            f"select name, text from `tabChat Message` "
            f"where room = %s and {membership_filter_sql('`tabChat Message`.`room`', "
            f"seq_column='`tabChat Message`.`seq`')} order by seq desc",
            (room,),
        )
	"""

	def test_the_shared_fragment_actually_filters_a_raw_query(self) -> None:
		"""The remedy, executed: the same raw SQL, with the filter, returns nothing.

		This is the safe half of the demonstration — a query that *should* return no rows,
		asserted to return no rows.
		"""
		fragment = permissions.membership_filter_sql(
			"`tabChat Message`.`room`",
			user=OUTSIDER,
			seq_column="`tabChat Message`.`seq`",
		)
		rows = frappe.db.sql(
			f"select name from `tabChat Message` where {fragment}",
			as_dict=True,
		)
		self.assertEqual(rows, [], "the membership fragment failed to scope a raw query")

	def test_a_member_still_sees_their_own_rooms_through_the_fragment(self) -> None:
		"""The fragment has to be a filter, not an off switch — a `1 = 0` that always
		returns nothing would pass the test above and break the product."""
		fragment = permissions.membership_filter_sql(
			"`tabChat Message`.`room`",
			user=MEMBER,
			seq_column="`tabChat Message`.`seq`",
		)
		names = {
			row["name"] for row in frappe.db.sql(
				f"select name from `tabChat Message` where {fragment}", as_dict=True
			)
		}
		self.assertIn(self.msg_1, names)
		self.assertNotIn(self.foreign_message, names)

	def test_the_fragment_never_returns_an_empty_string(self) -> None:
		"""An empty string vanishes out of a hand-written `WHERE` and the query then
		returns everything. Unrestricted is spelled `1 = 1`; denied is spelled `1 = 0`.
		Both survive being concatenated by a careless caller; `""` does not."""
		for user in (MEMBER, OUTSIDER, AUDITOR, "Administrator", "Guest", "", None):
			fragment = permissions.membership_filter_sql("`tabChat Message`.`room`", user=user)
			self.assertTrue(fragment.strip(), f"empty fragment for {user!r}")

	def test_the_user_value_is_escaped_into_the_fragment(self) -> None:
		"""These strings are concatenated straight into a `WHERE`, and there is no parameter
		binding available on this seam. Escaping is the whole defence.

		A permission hook is the last place in a codebase you want an injection.
		"""
		hostile = "x' or '1'='1"
		fragment = permissions.membership_filter_sql("`tabChat Message`.`room`", user=hostile)

		# The escaped form is what appears; the raw form never does.
		self.assertIn(frappe.db.escape(hostile), fragment)
		self.assertNotIn(f"'{hostile}'", fragment)

		# And the result is still valid SQL that matches nothing — an injection that merely
		# breaks the query would be a bug too, just a louder one.
		rows = frappe.db.sql(f"select name from `tabChat Message` where {fragment}", as_dict=True)
		self.assertEqual(rows, [])

	def test_visible_room_names_is_the_python_twin_and_agrees_with_the_sql(self) -> None:
		"""Two implementations of one rule is how a leak arrives; assert they agree."""
		self.assertEqual(set(permissions.visible_room_names(MEMBER)), {self.room})
		self.assertEqual(permissions.visible_room_names(OUTSIDER), [])
		self.assertEqual(permissions.visible_room_names("Guest"), [])
		# A departed member is not an active member, even though they can still read history.
		self.assertEqual(permissions.visible_room_names(AUDITOR), [])


# ---------------------------------------------------------------------------
# The zero-DocPerm doctrine, asserted against the live schema
# ---------------------------------------------------------------------------


class TestTheZeroDocPermDoctrine(ChatPermissionFixture):
	"""ADR §F.18.1 Layer 1, checked against the DocTypes as the site actually has them.

	`tests/test_chat_guardrails.py` asserts this against the JSON files, bench-free. This
	asserts it against the *database* — which is a different question, because a DocPerm
	row added through the desk lives only in `tabDocPerm` and no JSON file will show it.
	That is precisely the regression the doctrine is exposed to.
	"""

	def test_only_chat_room_carries_a_docperm(self) -> None:
		zero_docperm = (
			"Chat Message",
			"Chat Room Member",
			"Chat Attachment",
			"Chat Relay Job",
			"Chat Inbound Event",
			"Chat Event Subscription",
		)
		for doctype in zero_docperm:
			perms = frappe.get_all("DocPerm", filters={"parent": doctype}, fields=["role", "read"])
			self.assertEqual(
				perms,
				[],
				f"{doctype} has grown a DocPerm row. The zero-DocPerm doctrine is Layer 1 of "
				"the chat permission model; if this is deliberate, the standing obligation "
				"(ADR §F.18.1) requires a permission_query_conditions + has_permission pair "
				"to land in the SAME commit.",
			)

	def test_chat_room_carries_exactly_the_one_read_docperm(self) -> None:
		perms = frappe.get_all(
			"DocPerm",
			filters={"parent": "Chat Room"},
			fields=["role", "read", "write", "create", "delete"],
		)
		self.assertEqual(len(perms), 1, "Chat Room's single DocPerm row is the deliberate exception")
		row = perms[0]
		self.assertEqual(row["role"], MEMBER_ROLE)
		self.assertTrue(row["read"])
		for operation in ("write", "create", "delete"):
			self.assertFalse(
				row[operation],
				f"Chat Room's DocPerm grants {operation}; the membership hook narrows ROWS, "
				"the DocPerm decides OPERATIONS, and read is all this one is allowed to be",
			)

	def test_both_hooks_are_registered_for_every_scoped_doctype(self) -> None:
		"""Ten-and-ten parity is the house doctrine: every query condition has a twin.

		Read from the live hooks rather than the file, so a hook that fails to load is
		caught here rather than by its absence going unnoticed in production.
		"""
		hooks = frappe.get_hooks()
		queries = hooks.get("permission_query_conditions") or {}
		singles = hooks.get("has_permission") or {}
		for doctype in ("Chat Room", "Chat Room Member", "Chat Message", "Chat Attachment"):
			self.assertIn(doctype, queries, f"{doctype} has no permission_query_conditions")
			self.assertIn(doctype, singles, f"{doctype} has no has_permission twin")


# ===========================================================================
# PHASE 2 (§4.K) — rows now arrive from a second origin
# ===========================================================================


def _rendered(payload: Any) -> str:
	"""Whatever a read path returned, flattened to text so a leak cannot hide in its shape.

	The REST and report-view handlers each return their own container — a list of dicts, a
	`{"keys": [...], "values": [...]}` compression, sometimes a bare list of names — and
	pinning any one of those shapes would make this suite fail on a Frappe upgrade for a
	reason that is not a security regression. The question worth asking is shape-independent:
	*does the room's identifier or title appear anywhere in the response at all.*
	"""
	try:
		return frappe.as_json(payload)
	except Exception:
		return repr(payload)


class TestEveryReadEntryPointDeniesANonMember(ChatPermissionFixture):
	"""One test per entry point. Passing one proves nothing about the others.

	`get_list` and the report view go through `DatabaseQuery` / the v16 query `Engine`, which
	is where `permission_query_conditions` is applied. `frappe.client.get_list` adds its own
	argument handling on top. `frappe.get_doc(...).check_permission()` consults
	`has_permission` and nothing else. Four mechanisms; four tests.
	"""

	def test_the_list_path_returns_nothing_from_a_room_the_user_is_not_in(self) -> None:
		frappe.set_user(OUTSIDER)
		rows = frappe.get_list("Chat Room", fields=["name", "title"], limit_page_length=0)
		self.assertNotIn(self.room, _rendered(rows))

	def test_the_desk_report_view_returns_nothing_from_that_room(self) -> None:
		"""**The path people forget**, exercised through the handler the desk actually calls.

		`frappe.desk.reportview.get` reads its arguments from `frappe.local.form_dict`, which
		is why they are staged there rather than passed — calling it any other way would test
		a call signature instead of the endpoint. ADR §9-F's acceptance bar is literally
		*"even a raw report view cannot leak another user's rooms."*
		"""
		from frappe.desk import reportview

		frappe.set_user(OUTSIDER)
		saved = frappe.local.form_dict
		try:
			frappe.local.form_dict = frappe._dict(
				{
					"doctype": "Chat Room",
					"fields": json.dumps(["`tabChat Room`.`name`", "`tabChat Room`.`title`"]),
					"filters": json.dumps([]),
					"order_by": "`tabChat Room`.`modified` desc",
					"start": 0,
					"page_length": 100,
				}
			)
			rendered = _rendered(reportview.get())
		finally:
			frappe.local.form_dict = saved

		self.assertNotIn(self.room, rendered, "the report view leaked a room id")
		self.assertNotIn(
			"Chat permissions - member's room",
			rendered,
			"the report view leaked a room TITLE, which is the disclosure that matters — a "
			"room called 'Redundancies Q3' is a leak even with no message attached to it",
		)

	def test_frappe_client_get_list_returns_nothing_from_that_room(self) -> None:
		"""`/api/method/frappe.client.get_list`, and the list form of `/api/resource/<DT>`.

		Same handler, two routes. It is a separate test from `frappe.get_list` because it
		normalises its own arguments before delegating, and argument normalisation is where a
		filter gets dropped.
		"""
		from frappe.client import get_list as client_get_list

		frappe.set_user(OUTSIDER)
		rendered = _rendered(client_get_list("Chat Room", fields=["name", "title"], limit_page_length=0))
		self.assertNotIn(self.room, rendered)

	def test_the_api_resource_single_document_handler_refuses(self) -> None:
		"""`/api/resource/Chat Room/<name>` dispatches to `frappe.client.get`.

		It must raise rather than return the document. A handler that returned `{}` would
		also pass a naive "no data leaked" assertion while telling the caller the room
		exists.
		"""
		from frappe.client import get as client_get

		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			client_get("Chat Room", self.room)

	def test_get_doc_raises_for_a_non_member(self) -> None:
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Chat Room", self.room).check_permission("read")

	def test_a_member_still_reaches_their_own_room_through_every_entry_point(self) -> None:
		"""The load-bearing negative: a gate that denies everybody passes every test above.

		Without this, the cheapest way to make this class green is to break chat.
		"""
		from frappe.client import get as client_get
		from frappe.client import get_list as client_get_list

		frappe.set_user(MEMBER)
		self.assertIn(
			self.room,
			[row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)],
		)
		self.assertIn(self.room, _rendered(client_get_list("Chat Room", fields=["name"])))
		self.assertTrue(client_get("Chat Room", self.room))


class TestADocShareMustNotWidenAChatRoomRead(ChatPermissionFixture):
	"""**A finding, if it fails.** Read from the v16 source this session:

	`frappe/database/query.py` builds the WHERE by ANDing every
	`permission_query_conditions` fragment and then does::

	    # shared docs trump all other restrictions
	    where_condition |= table.name.isin(shared_docs)

	An OR after every AND. So a `DocShare` row makes a document visible **regardless of what
	the membership hook returned**. On `Chat Message` that is unreachable — zero DocPerm
	refuses before any query runs — but **`Chat Room` carries a `read` DocPerm**, which is
	exactly what makes the doc-room socket join resolvable, so on `Chat Room` the path is
	open.

	Why it matters here rather than in the abstract: sharing is a one-click action in the
	desk, ERPNext core code shares documents as a side effect of assignment and workflow, and
	a shared `Chat Room` is a live socket subscription to everything said in it from that
	moment on. Nobody sharing a "room" record would expect that.

	If these tests fail, **do not soften them**. The finding is that chat room membership can
	be widened by a mechanism outside the chat module, and the fix is a design decision
	(refuse the share in a `DocShare` validate hook, or drop `Chat Room`'s DocPerm and find
	another way to make the socket join resolvable) — not a test edit.
	"""

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")
		self.share = self._share_room_with_outsider()

	def _share_room_with_outsider(self) -> str:
		"""Insert the `DocShare` row directly, and prove it landed.

		Directly rather than through `frappe.share.add` so this does not depend on that
		helper's keyword signature, which is the sort of thing that moves between versions —
		and a share that silently failed to be created would make every assertion below pass
		while testing nothing, which is the worst possible outcome for this particular class.
		"""
		doc = frappe.get_doc(
			{
				"doctype": "DocShare",
				"share_doctype": "Chat Room",
				"share_name": self.room,
				"user": OUTSIDER,
				"read": 1,
			}
		)
		doc.flags.ignore_share_permission = True
		doc.insert(ignore_permissions=True)
		# Never committed — the enclosing test transaction rolls the share back with
		# everything else, which matters here more than usual: a DocShare row surviving a
		# crashed run would silently widen a real room on whatever site this was executed on.
		self.assertTrue(
			frappe.db.exists(
				"DocShare", {"share_doctype": "Chat Room", "share_name": self.room, "user": OUTSIDER}
			),
			"the DocShare row was not created, so every assertion in this class would pass "
			"while proving nothing. Fix the fixture before reading anything into a green run.",
		)
		return doc.name

	def test_a_docshare_row_does_not_put_the_room_in_a_non_members_list(self) -> None:
		frappe.set_user(OUTSIDER)
		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertNotIn(
			self.room,
			names,
			"FINDING, not a flake: a DocShare row widened a chat room read past the "
			"membership hook. v16's query Engine ORs shared documents in after ANDing every "
			"permission condition ('shared docs trump all other restrictions'), and Chat Room "
			"carries a read DocPerm, so the path is reachable. Room read is the socket "
			"doc-room join and a join is checked ONCE — so this is a live feed of a "
			"conversation, granted by a mechanism the chat module does not control. Escalate; "
			"do not relax this assertion.",
		)

	def test_a_docshare_row_does_not_pass_the_single_document_gate(self) -> None:
		"""The `has_permission` half, which is the one the socket join consults.

		Frappe merges share rights into `get_doc_permissions`, so this can diverge from the
		list result above — and the divergence direction matters: the list path is the desk,
		the single-document path is realtime.
		"""
		frappe.set_user(OUTSIDER)
		self.assertFalse(
			frappe.has_permission("Chat Room", doc=self.room, ptype="read"),
			"FINDING: a DocShare row satisfied the single-document gate on Chat Room. That "
			"gate IS the realtime security boundary (ADR §H.4.1) — doc_subscribe calls it "
			"before joining doc:Chat Room/<room>, and membership is never re-checked after "
			"the join.",
		)

	def test_the_chat_hook_itself_still_refuses_the_shared_room(self) -> None:
		"""Narrow the blame. If the two tests above fail but this one passes, the chat module
		is correct and the platform is widening it — which is a different bug with a different
		owner and a different fix."""
		self.assertIs(permissions.chat_room_has_permission(self.room, "read", OUTSIDER), False)
		self.assertNotEqual(permissions.chat_room_query(OUTSIDER), "")

	def test_a_share_does_not_reach_the_messages_in_the_shared_room(self) -> None:
		"""Even if the room leaks, the bodies must not follow it.

		`Chat Message` has zero DocPerm, so this should hold by a completely different
		mechanism — the permission stack refuses before any hook or share is consulted. Worth
		its own assertion precisely because it is independent: it is the containment that
		survives the room-level finding.
		"""
		frappe.set_user(OUTSIDER)
		names = [row.name for row in frappe.get_list("Chat Message", fields=["name"], limit_page_length=0)]
		self.assertEqual(names, [])
		self.assertFalse(frappe.has_permission("Chat Message", doc=self.msg_1, ptype="read"))


class TestTombstonedRowsDoNotReturnDeletedBodies(ChatPermissionFixture):
	"""I10, and the contract's deliberate divergence from the prompt.

	The prompt says move a deleted body into the audit table and clear the live row. The
	contract (§5, ADR §F.6.5) says **keep it on the row**, because Google's tombstone is rich
	in metadata and empty of content — `showDeleted=true` returns the delete time and never
	the text — so if ERPNext drops the body, nobody has it.

	The consequence is the thing these tests pin: `is_deleted = 0` is an obligation of every
	**read path**, not of the permission layer. `membership_filter_sql` scopes by room and
	says nothing about deletion, by design, because the Phase 6 oversight endpoint has to be
	able to see through it. So a reader that forgets the `is_deleted = 0` predicate returns
	deleted text to an ordinary member — and that is a leak of exactly the content a user
	explicitly asked to remove.
	"""

	def setUp(self) -> None:
		super().setUp()
		self.deleted_message = self._make_message(self.room, seq=20, text="please forget I said this")
		# Set directly rather than through `.save()`: `on_update` would run the outbound
		# propagation path, and what a reader sees given a row state is a different question
		# from how the row reached that state. `update_modified=False` matches the relay's own
		# discipline — the D6 cache watermark reads `max(modified)`.
		frappe.db.set_value(
			"Chat Message",
			self.deleted_message,
			{"is_deleted": 1, "deleted_by": MEMBER, "deletion_source": "ERPNext"},
			update_modified=False,
		)

	def test_a_non_member_gets_nothing_whether_or_not_the_row_is_tombstoned(self) -> None:
		"""Tombstoning must not accidentally widen. A row in an unusual state is exactly the
		row a filter forgets."""
		frappe.set_user(OUTSIDER)
		self.assertEqual(frappe.get_list("Chat Message", fields=["name", "text"], limit_page_length=0), [])
		self.assertFalse(frappe.has_permission("Chat Message", doc=self.deleted_message, ptype="read"))
		self.assertIs(
			permissions.chat_message_has_permission(
				frappe.get_doc("Chat Message", self.deleted_message), "read", OUTSIDER
			),
			False,
		)

	def test_the_body_is_retained_on_the_row_because_google_keeps_no_copy(self) -> None:
		"""The divergence, asserted so nobody "fixes" it back to the prompt's shape.

		Clearing the live row would make the ERPNext delete destructive and irreversible, on
		the strength of a tombstone that contains no text.
		"""
		body = frappe.db.get_value("Chat Message", self.deleted_message, "text")
		self.assertTrue(
			body,
			"the deleted message's body was cleared from the live row. Google's tombstone has "
			"no content (ADR §F.6.5 / §G.7.4), so ERPNext is the only copy and this is data "
			"destruction, not redaction. If the retention policy genuinely changed, the "
			"oversight path in Phase 6 has to change with it.",
		)

	def test_the_membership_fragment_does_not_filter_deleted_rows_so_readers_must(self) -> None:
		"""**Where the obligation lives**, pinned as an executable statement.

		This asserts the *absence* of a filter on purpose. The fragment is membership-only so
		the Phase 6 oversight endpoint can see through it; that means every ordinary read
		path — history paging, search, the Phase 3 endpoint, Phase 5 retrieval — must AND
		`is_deleted = 0` itself.

		**If somebody adds deletion filtering to `membership_filter_sql`, this test goes red,
		and that is the point:** the oversight path breaks silently in the same change, and
		this is the only thing that says so.
		"""
		fragment = permissions.membership_filter_sql(
			"`tabChat Message`.`room`",
			user=MEMBER,
			seq_column="`tabChat Message`.`seq`",
		)
		names = {
			row["name"]
			for row in frappe.db.sql(f"select name from `tabChat Message` where {fragment}", as_dict=True)
		}
		self.assertIn(
			self.deleted_message,
			names,
			"membership_filter_sql now excludes tombstoned rows. That is a behaviour change "
			"with two consequences nobody will notice from the diff: the Phase 6 oversight "
			"read (decision #12 — an admin role can read all history) silently stops seeing "
			"deleted content, and every read path that was correctly ANDing `is_deleted = 0` "
			"now does it twice. Decide deliberately and update this test and the oversight "
			"path together.",
		)
		with_filter = {
			row["name"]
			for row in frappe.db.sql(
				f"select name from `tabChat Message` where {fragment} and ifnull(is_deleted, 0) = 0",
				as_dict=True,
			)
		}
		self.assertNotIn(self.deleted_message, with_filter)
		self.assertIn(self.msg_1, with_filter, "the deletion predicate must not hide live rows")


class TestChatMessageRevisionIsTighterThanTheMessageTable(ChatPermissionFixture):
	"""The audit table is where superseded and deleted bodies live.

	It is the one table where a leak survives the user's decision to delete, so the contract
	gives it **tighter** permissions than `Chat Message`, not equal ones: zero DocPerm and —
	unlike `Chat Message` — no `permission_query_conditions` / `has_permission` pair at all.
	The platform therefore refuses it to everybody except `Administrator`.

	One consequence is worth stating rather than discovering: **the oversight role does not
	open this table either.** A `has_permission` hook can only restrict what a DocPerm
	already grants, and there is no DocPerm, so there is nothing for a hook to narrow. Phase
	6's e-discovery endpoint must therefore read it with `ignore_permissions=True` behind its
	own explicit role gate, and pay for the read with `permissions.note_privileged_read`.
	That is a heavier, more visible motion than a DocPerm row, and it is the right one for
	this table.
	"""

	def setUp(self) -> None:
		super().setUp()
		self.revision = frappe.get_doc(
			{
				"doctype": "Chat Message Revision",
				"message": self.msg_1,
				"room": self.room,
				"revision_no": 1,
				"change_type": "Delete",
				"origin": "ERPNext",
				"actor": MEMBER,
				"text_before": "the superseded body that must not leak",
				"text_after": "",
			}
		).insert(ignore_permissions=True)

	def test_it_carries_no_docperm_at_all(self) -> None:
		perms = frappe.get_all("DocPerm", filters={"parent": "Chat Message Revision"}, fields=["role"])
		self.assertEqual(
			perms,
			[],
			"Chat Message Revision has grown a DocPerm row. This table holds the body of "
			"every superseded edit and every deleted message; a read row on it re-opens the "
			"desk form, /api/resource, report view, export and the generic MCP read tools "
			"over content a user explicitly deleted. If oversight genuinely needs it, the "
			"Phase 6 endpoint reads with ignore_permissions behind its own role gate and "
			"writes an audit row — it does not need a DocPerm.",
		)

	def test_an_ordinary_member_cannot_list_or_open_a_revision(self) -> None:
		"""A member of the room the revision belongs to. The closest thing to a legitimate
		reader there is, and still refused."""
		frappe.set_user(MEMBER)
		self.assertEqual(frappe.get_list("Chat Message Revision", fields=["name"], limit_page_length=0), [])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Chat Message Revision", self.revision.name).check_permission("read")

	def test_a_non_member_cannot_reach_it_either(self) -> None:
		frappe.set_user(OUTSIDER)
		self.assertEqual(frappe.get_list("Chat Message Revision", fields=["name"], limit_page_length=0), [])
		self.assertFalse(frappe.has_permission("Chat Message Revision", doc=self.revision.name, ptype="read"))

	def test_the_oversight_role_does_not_open_it_and_that_is_the_design(self) -> None:
		"""Asserted so the behaviour is a decision on the record rather than a surprise.

		Somebody will one day configure `admin_oversight_role`, try to read a revision, get
		refused and assume the hatch is broken. It is not: there is no DocPerm for a hook to
		narrow, and that is deliberately heavier than the message table's posture.
		"""
		self._set_oversight_role(OVERSIGHT_ROLE)
		_ensure_role(OVERSIGHT_ROLE)
		user = frappe.get_doc("User", AUDITOR)
		user.append("roles", {"role": OVERSIGHT_ROLE})
		user.save(ignore_permissions=True)

		frappe.set_user(AUDITOR)
		self.assertEqual(
			frappe.get_list("Chat Message Revision", fields=["name"], limit_page_length=0),
			[],
			"the oversight role can now list Chat Message Revision. If a DocPerm was added to "
			"make that work, ADR §F.18.4's standing obligation fires and the SAME commit owes "
			"a permission_query_conditions + has_permission pair scoped to the role, plus a "
			"note_privileged_read call on every path.",
		)

	def test_the_superseded_body_is_not_reachable_through_the_message_it_belongs_to(self) -> None:
		"""Belt and braces: reading the parent message must not drag the revision along.

		Frappe does not fetch unrelated child documents, but the revision carries `message`
		and `room` links and a future convenience helper is exactly how it would start being
		joined in.
		"""
		frappe.set_user(MEMBER)
		rendered = _rendered(frappe.get_list("Chat Message", fields=["name"], limit_page_length=0))
		self.assertNotIn("the superseded body that must not leak", rendered)


class TestAnExternalSenderIsStillScopedByRoomMembership(ChatPermissionFixture):
	"""§4.H: a Chat member with no ERPNext `User` — `sender_email` set, `sender` null.

	`sender` was relaxed from `reqd` in Phase 2 precisely so these messages are **stored**
	rather than dropped: ERPNext is the record of what was said, and a mirror that silently
	loses the messages in which the conversation left the company acquires holes in exactly
	the rows somebody will one day need.

	The risk that creates is small and specific. Every membership rule in this module keys on
	the **room**, never on the sender, so an unattributable sender should change nothing.
	"Should" is doing work in that sentence — a null Link is the shape that makes a join
	behave unexpectedly, and this is the one row type where nobody's `User` record can be
	used as a fallback scope.
	"""

	EXTERNAL = "someone@external-partner.example"

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")
		self.external_message = frappe.get_doc(
			{
				"doctype": "Chat Message",
				"room": self.room,
				"seq": 30,
				"sender": None,
				"sender_email": self.EXTERNAL,
				"sender_kind": "Human",
				"message_type": "Text",
				"text": "external partner text that must stay in the room",
				"text_plain": "external partner text that must stay in the room",
				"client_message_id": "client-bench-external-30",
				"sync_origin": "Google Chat",
				"sync_state": "Inbound",
			}
		).insert(ignore_permissions=True)

	def test_the_row_stored_with_no_sender_link(self) -> None:
		"""The fixture is the premise of every assertion below; prove it before using it."""
		row = frappe.db.get_value(
			"Chat Message", self.external_message.name, ["sender", "sender_email"], as_dict=True
		)
		self.assertFalse(row.get("sender"), "sender should be null for an external Chat member")
		self.assertEqual(row.get("sender_email"), self.EXTERNAL)

	def test_a_non_member_cannot_read_it(self) -> None:
		frappe.set_user(OUTSIDER)
		self.assertEqual(frappe.get_list("Chat Message", fields=["name"], limit_page_length=0), [])
		self.assertFalse(frappe.has_permission("Chat Message", doc=self.external_message.name, ptype="read"))
		self.assertIs(
			permissions.chat_message_has_permission(self.external_message, "read", OUTSIDER),
			False,
		)

	def test_the_raw_membership_fragment_scopes_it_to_the_room(self) -> None:
		"""The path that matters, since `Chat Message` has zero DocPerm: history paging and
		search read this table with the fragment, not with `get_list`."""
		outsider_fragment = permissions.membership_filter_sql(
			"`tabChat Message`.`room`", user=OUTSIDER, seq_column="`tabChat Message`.`seq`"
		)
		self.assertEqual(
			frappe.db.sql(f"select name from `tabChat Message` where {outsider_fragment}", as_dict=True),
			[],
		)

		member_fragment = permissions.membership_filter_sql(
			"`tabChat Message`.`room`", user=MEMBER, seq_column="`tabChat Message`.`seq`"
		)
		names = {
			row["name"]
			for row in frappe.db.sql(
				f"select name from `tabChat Message` where {member_fragment}", as_dict=True
			)
		}
		self.assertIn(
			self.external_message.name,
			names,
			"a room member cannot see a message sent by an external Chat user in their own "
			"room. The scope is the ROOM, never the sender — dropping these rows out of a "
			"member's view is the other half of the same bug as leaking them to a stranger.",
		)

	def test_the_hook_admits_a_member_for_the_external_message(self) -> None:
		self.assertIs(permissions.chat_message_has_permission(self.external_message, "read", MEMBER), True)


class TestTheExceptionPathsOfEveryHook(ChatPermissionFixture):
	"""v16 denies on `None`, so the exception branches are the ones that lock production out.

	:class:`TestEveryHookReturnsARealBoolean` already forces the membership probes to raise.
	This class forces the two the oversight hatch depends on — `frappe.db.get_single_value`
	(reading `Chat Settings.admin_oversight_role`) and `frappe.get_roles` — which are the
	first calls in every hook and therefore the ones a happy-path test never reaches.

	A hook that raised here would take down every desk page touching a chat DocType, and it
	would do it during `bench migrate` on a site where `Chat Settings` does not exist yet.
	"""

	HOOKS = (
		"chat_room_has_permission",
		"chat_room_member_has_permission",
		"chat_message_has_permission",
		"chat_attachment_has_permission",
	)
	BUILDERS = (
		"chat_room_query",
		"chat_room_member_query",
		"chat_message_query",
		"chat_attachment_query",
	)

	def _explode(self, *args: Any, **kwargs: Any) -> Any:
		raise RuntimeError("simulated failure reading Chat Settings / roles")

	def test_hooks_fail_closed_when_the_settings_read_raises(self) -> None:
		original = frappe.db.get_single_value
		try:
			frappe.db.get_single_value = self._explode
			for funcname in self.HOOKS:
				hook = getattr(permissions, funcname)
				result = hook({"room": self.room, "message": self.msg_1, "user": MEMBER}, "read", OUTSIDER)
				self.assertIs(
					result,
					False,
					f"{funcname} did not fail closed when Chat Settings was unreadable; got "
					f"{result!r}. An unanswerable question about the oversight role is a 'no'.",
				)
		finally:
			frappe.db.get_single_value = original

	def test_hooks_fail_closed_when_the_role_lookup_raises(self) -> None:
		original = frappe.get_roles
		try:
			frappe.get_roles = self._explode
			for funcname in self.HOOKS:
				hook = getattr(permissions, funcname)
				result = hook({"room": self.other_room}, "read", OUTSIDER)
				self.assertIs(result, False, f"{funcname} returned {result!r} with roles unreadable")
		finally:
			frappe.get_roles = original

	def test_query_builders_still_return_a_string_when_the_settings_read_raises(self) -> None:
		"""A `permission_query_conditions` hook returning `None` is a `TypeError` inside
		`DatabaseQuery` — it takes the list view down rather than leaking, but it takes it
		down for everybody including the people who are allowed to be there."""
		original = frappe.db.get_single_value
		try:
			frappe.db.get_single_value = self._explode
			for funcname in self.BUILDERS:
				condition = getattr(permissions, funcname)(OUTSIDER)
				self.assertIsInstance(condition, str, funcname)
				self.assertNotEqual(
					condition,
					"",
					f"{funcname} returned an UNRESTRICTED condition while Chat Settings was "
					"unreadable. An unreadable oversight setting must never read as 'the "
					"hatch is open' — that is failing open on the one path that grants "
					"everything.",
				)
		finally:
			frappe.db.get_single_value = original

	def test_the_fragment_builder_fails_closed_rather_than_unrestricted(self) -> None:
		"""`membership_filter_sql` spells unrestricted as `1 = 1`. Under a failing settings
		read it must never produce that."""
		original = frappe.db.get_single_value
		try:
			frappe.db.get_single_value = self._explode
			fragment = permissions.membership_filter_sql("`tabChat Message`.`room`", user=OUTSIDER)
			self.assertNotEqual(fragment.strip(), "1 = 1")
			self.assertTrue(fragment.strip())
		finally:
			frappe.db.get_single_value = original


class TestThePrivateFileBoundary(ChatPermissionFixture):
	"""§4.I's live-bench acceptance test, from the permission side.

	Every chat attachment is `is_private = 1` and attached to the `Chat Message` row, which
	makes Frappe's private-file check delegate to `chat_message_has_permission` — so
	attachment security follows room security **by construction** rather than by a second
	rule somebody has to maintain.

	*"By construction"* is exactly the kind of claim that is true right up until it is not:
	there is a long tail of reported issues where Frappe private files are more accessible
	than expected. So it is asserted, twice, against the function the web route actually
	calls.

	Deliberately duplicated with `tests/test_chat_attachments_bench.py`. That suite owns the
	attachment pipeline; this one is what a human is told to run when a chat *permission*
	changes, and the two get run at different times by different people.
	"""

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")
		self.private_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "chat-perm-boundary.txt",
				"attached_to_doctype": "Chat Message",
				"attached_to_name": self.foreign_message,
				"is_private": 1,
				"content": "bytes that belong to a room the outsider is not in",
			}
		).insert(ignore_permissions=True)

	def test_the_attachment_is_private_which_is_what_makes_the_check_run_at_all(self) -> None:
		"""A public file is served by the web server with no auth and no hook of ours is
		consulted. The whole delegation argument rests on this one flag."""
		self.assertTrue(frappe.db.get_value("File", self.private_file.name, "is_private"))
		self.assertTrue(str(self.private_file.file_url or "").startswith("/private/"))

	def test_an_authenticated_non_member_is_refused_the_file_url(self) -> None:
		from frappe.utils.response import download_private_file

		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			download_private_file(self.private_file.file_url)

	def test_an_unauthenticated_request_is_refused_the_file_url(self) -> None:
		"""Guest is refused on a different line of `download_private_file` from the
		authenticated stranger, so it is a genuinely separate case rather than the same
		assertion twice."""
		from frappe.utils.response import download_private_file

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			download_private_file(self.private_file.file_url)

	def test_the_file_row_itself_is_not_listable_by_a_non_member(self) -> None:
		"""The URL is not the only way in — `/api/resource/File` is a list of file rows, and a
		row carries the name and the URL even when the bytes are refused."""
		frappe.set_user(OUTSIDER)
		rendered = _rendered(
			frappe.get_list(
				"File",
				filters={"attached_to_doctype": "Chat Message"},
				fields=["name", "file_url"],
				limit_page_length=0,
			)
		)
		self.assertNotIn(self.private_file.name, rendered)
