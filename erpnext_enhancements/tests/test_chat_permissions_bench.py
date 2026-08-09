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

Fixtures are three users, two rooms and four messages, all created by the suite and all
rolled back with the enclosing transaction. Nothing here reads production data, and the
message bodies are deliberately dull.
"""

from __future__ import annotations

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

	def test_the_oversight_role_sees_every_room_and_every_message(self) -> None:
		self._set_oversight_role(OVERSIGHT_ROLE)
		_ensure_role(OVERSIGHT_ROLE)
		user = frappe.get_doc("User", AUDITOR)
		user.append("roles", {"role": OVERSIGHT_ROLE})
		user.save(ignore_permissions=True)

		frappe.set_user(AUDITOR)
		self.assertEqual(permissions.chat_room_query(AUDITOR), "")
		self.assertEqual(permissions.chat_message_query(AUDITOR), "")
		self.assertIs(permissions.chat_room_has_permission(self.other_room, "read", AUDITOR), True)

		names = [row.name for row in frappe.get_list("Chat Room", fields=["name"], limit_page_length=0)]
		self.assertIn(self.room, names)
		self.assertIn(self.other_room, names)

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
