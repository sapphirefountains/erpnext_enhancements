"""The generic AI tools must not be able to reach chat data. Bench-free.

This is the enforcement half of ADR 0009 §I.2 (invariant I5), and it exists
because the obvious answer is two-thirds right and the missing third is the one
that matters.

The three generic Frappe Assistant Core tools that could read a coworker's
private message are ``get_document``, ``list_documents`` and
``run_database_query``. Withholding DocPerm closes the first two. It does
**nothing** to the third: ``run_database_query``'s own stated security model is
*"Restricted to SELECT statements only. Requires System Manager role for
security"* — a role check and a read-only-SQL check. Raw SQL sits underneath
DocPerm, ``permission_query_conditions`` and ``has_permission``, so no Frappe
permission mechanism touches it, and a System Manager is one
``select text, sender from `tabChat Message``` away from every private message
on the site, delivered into a model's context window.

Nor is "no DocPerm" the same as "unreachable". ``frappe/permissions.py``
short-circuits ``if user == "Administrator"`` and allows everything, so a
zero-DocPerm DocType is still fully readable by Administrator through the desk,
``/api/resource`` and therefore ``get_document``.

FAC is a third-party app in neither repository, and this app's own tripwire
forbids importing it, so the refusal is imposed at the seam this app already
owns: ``_gate.py``'s wrap of ``BaseTool._safe_execute``, which sees every FAC
tool call including FAC's own built-ins.

--------------------------------------------------------------------------------------
The four assertions, and why each one is here rather than assumed
--------------------------------------------------------------------------------------

1. **The DocType JSONs carry no permissions** — except ``Chat Room``, which
   asserts the inverse (a DocPerm *and* both permission hooks registered for
   it), because the socket join needs it. This is the assertion that catches the
   most likely regression in the whole feature: somebody adding a System Manager
   row so they can look at a message in the desk.
2. **Every chat DocType is in the denylist, by set equality against the
   filesystem.** Not containment — equality. A chat DocType added next year
   fails this build by default instead of silently escaping the denylist, which
   is the same failure mode ``test_every_registered_tool_is_classified`` exists
   to prevent.
3. **The gate refuses with gating OFF**, and with the confirm-flow bypass flag
   ON, and never calls the underlying tool. The precondition is the whole point:
   ``ai_write_gating_enabled`` ships dormant, so a refusal that depended on it
   would be off in production today. A denylist that can be switched off from a
   settings form is not an invariant.
4. **The seam is still attached** — deferred to the bench canary, and the
   residual recorded rather than papered over. See
   :class:`TestTheSeamIsAttached`.

What this file cannot do: it never executes a real FAC tool, a real MariaDB
query or a real MCP session. It proves the refusal is reached and returns FAC's
error envelope. The end-to-end statement — open an MCP session as a real System
Manager, issue ``SELECT COUNT(*) FROM `tabChat Message``` and get the refusal
rather than a number — is a Phase 6 manual acceptance step.

Run: python -m unittest erpnext_enhancements.tests.test_chat_mcp_denylist -v
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP_DIR = Path(__file__).resolve().parents[1]
CHAT_DOCTYPE_DIR = APP_DIR / "chat" / "doctype"

#: The chat DocTypes permitted to carry a DocPerm row, each with the reason.
#:
#: The rule is about **content**, not about the word "Chat": a DocType a role
#: can read must hold no conversation, or must narrow that read with a hook pair
#: in the same commit. Three qualify, and the three reasons are different, which
#: is why this is a dict of prose rather than a list of names.
DOCPERM_EXCEPTIONS = {
	"Chat Room": (
		"The realtime security boundary, and the one exception that carries "
		"conversation-adjacent data (room titles, linked documents, who is talking to "
		"whom). socket.io's `doc_subscribe` calls back into Python and runs the full "
		"document-level permission stack before joining `doc:Chat Room/<room>` — and on "
		"a DocType with no DocPerm row the platform refuses everybody but Administrator, "
		"so zero DocPerm here would not be a tightening, it would be realtime chat "
		"switched off for every user. What makes the doctype-wide `read` safe is the "
		"hook pair, which is asserted separately below: without it, `read` on Chat Room "
		"would mean read on every room in the company, DMs included."
	),
	"Chat Retrieval Audit": (
		"The decision #12 privileged-read log: System Manager, read and report only. It "
		"records THAT a non-participant read happened, by whom and over which rooms — "
		"never what was said. No body, no snippet, and the query text only behind a flag "
		"that ships off. An audit log nobody can look at is not an audit log, which is "
		"why this one inverts the doctrine rather than escaping it."
	),
	"Chat Audit Log": (
		"Phase 6's governance log: System Manager, read and report only, and the same "
		"inversion Chat Retrieval Audit makes for the same reason — an audit log nobody "
		"can look at is not an audit log. It records acts of administration OVER chat "
		"(who was granted oversight, who expanded a tombstone, what a retention run "
		"destroyed as a count and a seq range) and by design holds no message text in any "
		"field, because a log of what was deleted that contains what was deleted has not "
		"deleted anything. Its controller refuses every update and every delete, so read "
		"is the only operation the row supports from any path."
	),
	"Chat Settings": (
		"The Single: identifiers, feature flags, kill switches, quotas and budgets, and "
		"no message content. It also holds no credential and must never hold one — the "
		"keyless domain-wide-delegation design means there is nothing to store, and the "
		"guardrail suite asserts no chat DocType declares a Password field. An operator "
		"who cannot open the settings form cannot turn the feature off in an incident."
	),
}

_MIN_REASON = 80

_gate = None


def setUpModule():
	global _gate
	from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

	install_stubs()

	import frappe

	if not hasattr(frappe, "whitelist"):
		frappe.whitelist = lambda *a, **k: (lambda f: f)

	from erpnext_enhancements.assistant_tools import _gate as gate_module

	_gate = gate_module


# --------------------------------------------------------------------------- helpers


def _doctype_jsons():
	"""``{DocType name: parsed json}`` for every DocType under ``chat/doctype/``.

	Filesystem-derived on purpose. A hand-maintained list of chat DocTypes is a
	list that is wrong within a month, and the month it is wrong is the month
	the new table is readable through ``run_database_query``.
	"""
	out = {}
	for path in sorted(CHAT_DOCTYPE_DIR.glob("*/*.json")):
		if path.stem != path.parent.name:
			continue  # a fixture or a sibling json, not the DocType definition
		data = json.loads(path.read_text(encoding="utf-8"))
		if data.get("doctype") != "DocType":
			continue
		out[data.get("name") or path.stem] = data
	return out


def _hooks_source():
	return (APP_DIR / "hooks.py").read_text(encoding="utf-8")


class TestTheWalkIsNotVacuous(unittest.TestCase):
	"""Every assertion below iterates the filesystem. An empty walk is a suite
	that passes while guarding nothing — the failure this repo has already
	shipped once, when a pytest suite sat on a unittest step and collected zero
	tests."""

	def test_the_chat_doctype_directory_was_found(self):
		self.assertTrue(
			CHAT_DOCTYPE_DIR.is_dir(),
			f"{CHAT_DOCTYPE_DIR} is not a directory. Fix the path; do not delete the test.",
		)

	def test_the_walk_found_the_doctypes_that_are_known_to_exist(self):
		found = _doctype_jsons()
		for expected in ("Chat Message", "Chat Room", "Chat Room Member"):
			self.assertIn(
				expected,
				found,
				f"{expected} was not found by the DocType walk. Either it moved, or the "
				"walk stopped matching and every assertion in this file is now vacuous.",
			)


class TestNoChatDocTypeCarriesPermissions(unittest.TestCase):
	"""Layer 1: the content DocTypes are unreachable by every role, because the
	permission stack refuses before any hook is consulted when there is no
	DocPerm row to consult."""

	def test_every_chat_doctype_ships_zero_docperms(self):
		offenders = []
		for name, data in sorted(_doctype_jsons().items()):
			if name in DOCPERM_EXCEPTIONS:
				continue
			if data.get("permissions"):
				roles = sorted({row.get("role", "?") for row in data["permissions"]})
				offenders.append(f"{name}: {roles}")
		self.assertFalse(
			offenders,
			"these chat DocTypes carry DocPerm rows:\n  "
			+ "\n  ".join(offenders)
			+ "\n\nA DocPerm is doctype-wide. `read` on Chat Message does not mean 'read "
			"the rooms I am in' — it means read every message on the site, through the "
			"desk's report view, /api/resource, global search and FAC's get_document. "
			"Zero DocPerm is what makes that structurally impossible rather than a rule "
			"four read paths have to remember.\n"
			"If a role genuinely needs a desk list view — the likely trigger is a Chat "
			"Auditor — that commit must add BOTH permission_query_conditions AND "
			"has_permission for the DocType in the same change, and change this test on "
			"purpose.",
		)

	def test_every_docperm_exception_states_why_it_is_one(self):
		"""An exemption list nobody prunes is a hole nobody is watching — and
		this one is three names long, so there is no excuse for a bare name."""
		for name, reason in sorted(DOCPERM_EXCEPTIONS.items()):
			with self.subTest(doctype=name):
				self.assertIn(name, _doctype_jsons(), f"{name} no longer exists on disk")
				self.assertGreaterEqual(
					len(reason.strip()),
					_MIN_REASON,
					f"DOCPERM_EXCEPTIONS[{name!r}] has no real justification. Say which "
					"columns the DocType holds, why none of them is conversation, and — if "
					"any of them is — what narrows the doctype-wide read back down.",
				)

	def test_chat_room_is_the_documented_exception_and_pays_for_it(self):
		"""The inverse assertion. ``Chat Room`` must have a DocPerm (the socket
		join needs one) *and* must have both hooks registered, because the
		DocPerm alone would mean read on every room in the company."""
		data = _doctype_jsons().get("Chat Room")
		self.assertIsNotNone(data, "Chat Room is missing from chat/doctype/")
		self.assertTrue(
			data.get("permissions"),
			"Chat Room now ships zero DocPerm. That is not a tightening — "
			"socket.io's doc_subscribe runs the full document-level permission stack "
			"before joining doc:Chat Room/<room>, and on a DocType with no DocPerm row "
			"the platform refuses everyone but Administrator. Realtime chat stops working "
			"for every user. If this is deliberate, the realtime security boundary needs a "
			"different mechanism and this test needs rewriting to describe it.",
		)
		source = _hooks_source()
		for hook in ("permission_query_conditions", "has_permission"):
			self.assertIn(
				hook,
				source,
				f"hooks.py no longer declares {hook}",
			)
		# Both hook registers must name Chat Room. Parsing hooks.py is
		# deliberately avoided (it imports frappe); the pair is asserted by
		# counting occurrences of the handler names, which are unique strings.
		for handler in (
			"chat.permissions.chat_room_query",
			"chat.permissions.chat_room_has_permission",
		):
			self.assertIn(
				handler,
				source,
				f"{handler} is not registered in hooks.py. Chat Room carries a "
				"doctype-wide read DocPerm; without both hooks that read means every "
				"room in the company, including the DMs.",
			)


class TestTheDenylistCoversEveryChatDocType(unittest.TestCase):
	def test_set_equality_against_the_filesystem(self):
		on_disk = set(_doctype_jsons())
		declared = set(_gate.CHAT_DENYLIST_DOCTYPES)
		missing = sorted(on_disk - declared)
		stale = sorted(declared - on_disk)
		self.assertFalse(
			missing,
			f"these chat DocTypes exist on disk and are NOT in "
			f"_gate.CHAT_DENYLIST_DOCTYPES: {missing}.\n\n"
			"Until they are, a System Manager can read them through "
			"run_database_query, which no Frappe permission mechanism touches. This is "
			"set equality rather than containment precisely so a newly added chat table "
			"fails the build by default instead of escaping the denylist silently.",
		)
		self.assertFalse(
			stale,
			f"_gate.CHAT_DENYLIST_DOCTYPES names {stale}, which no longer exist under "
			"chat/doctype/. Delete them in the same commit as the removal: a stale entry "
			"is noise in the one list that has to be read carefully, and noise in a "
			"security list is how the real gap hides.",
		)


class _Recorder:
	"""A stand-in for FAC's real ``_safe_execute``. Records rather than runs, so
	"the tool never executed" is an assertion rather than an inference."""

	def __init__(self):
		self.calls = []

	def __call__(self, tool, arguments):
		self.calls.append((getattr(tool, "name", ""), arguments))
		return {"success": True, "result": "LEAKED", "execution_time": 0.0}


def _tool(name):
	return type("FakeTool", (), {"name": name})()


class TestTheGateRefusesWithGatingOff(unittest.TestCase):
	"""The precondition is the assertion. ``ai_write_gating_enabled`` ships
	dormant, so a refusal that ran only while it was on would be off in
	production today."""

	def setUp(self):
		self.recorder = _Recorder()
		self._real_gating_enabled = _gate._gating_enabled
		_gate._gating_enabled = lambda: False
		self.addCleanup(setattr, _gate, "_gating_enabled", self._real_gating_enabled)

		# The shared stub frappe has no `flags`, which the pass-through branch
		# below the denylist reads. Installed here rather than in install_stubs
		# so the stub set stays as small as the modules under test need it.
		import frappe

		if not hasattr(frappe, "flags"):
			frappe.flags = type("Flags", (), {})()
			self.addCleanup(delattr, frappe, "flags")

	def _refuse(self, tool_name, arguments):
		response = _gate._gated_execute(_tool(tool_name), self.recorder, arguments)
		self.assertFalse(
			response.get("success"),
			f"{tool_name} with {arguments!r} was not refused: {response!r}",
		)
		self.assertEqual(
			self.recorder.calls,
			[],
			f"{tool_name} was refused in its response but the underlying tool RAN "
			"anyway. A refusal envelope around a completed read is not a refusal — the "
			"rows have already been fetched, and on the run_database_query path they "
			"have already been read out of the message table.",
		)
		return response

	def test_get_document_on_a_chat_doctype_is_refused(self):
		response = self._refuse("get_document", {"doctype": "Chat Message", "name": "x"})
		self.assertIn(
			"chat.retrieval.gate.retrieve",
			response.get("error", ""),
			"the refusal must name the one supported door. A bare 'denied' teaches the "
			"model to try a different spelling of the same query; naming the endpoint "
			"redirects it.",
		)

	def test_list_documents_on_a_chat_doctype_is_refused(self):
		self._refuse("list_documents", {"doctype": "Chat Room Member", "limit": 500})

	def test_a_child_table_is_refused_too(self):
		"""``Chat Mention`` and ``Chat Retrieval Audit Room`` are child tables.
		A child row reaches message content transitively, and ``/api/resource``
		will happily list one."""
		self._refuse("list_documents", {"doctype": "Chat Mention"})

	def test_the_plain_sql_read_is_refused(self):
		self._refuse("run_database_query", {"query": "select text from `tabChat Message`"})

	def test_every_evasion_of_the_sql_match_is_refused(self):
		"""String-matching SQL loses to backticks, case, comments, whitespace,
		subqueries and ``information_schema`` — if it tries to parse. The rule
		is coarse and absolute instead: if the normalised text contains a chat
		table name, the whole call is refused."""
		evasions = {
			"no backticks": "select text from tabChat Message",
			"mixed case": "SeLeCt TeXt FrOm `TABCHAT MESSAGE`",
			"leading block comment": "/* harmless reporting query */ select 1 from `tabChat Message`",
			"trailing line comment": "select 1 from `tabChat Message` -- for the dashboard",
			"extra whitespace": "select 1 from `tab  Chat    Message`",
			"join in a subquery": (
				"select r.name from `tabChat Room` r where r.name in "
				"(select m.room from `tabChat Message` m)"
			),
			"information_schema": (
				"select column_name from information_schema.columns " "where table_name = 'tabChat Message'"
			),
			"doctype name without the tab prefix": (
				"select * from `tabDocField` where parent = 'Chat Message'"
			),
		}
		for label, query in sorted(evasions.items()):
			with self.subTest(evasion=label):
				self.recorder.calls.clear()
				self._refuse("run_database_query", {"query": query})

	def test_run_python_code_is_refused_because_it_preloads_frappe(self):
		"""The sandbox pre-loads ``frappe``, so ``frappe.db.sql`` is one line
		away from being the same leak with a different argument name."""
		self._refuse(
			"run_python_code",
			{"code": "print(frappe.db.sql('select text from `tabChat Message` limit 5'))"},
		)

	def test_the_refusal_survives_the_confirm_flow_bypass_flag(self):
		"""``ai_gate_bypass`` is how the desk confirmation re-executes an
		approved action for real. It must not be a way past this branch, which
		is why the denylist is checked above it rather than below."""
		import frappe

		had_flags = hasattr(frappe, "flags")
		original = getattr(frappe, "flags", None)
		frappe.flags = type("F", (), {"ai_gate_bypass": True})()
		try:
			self._refuse("get_document", {"doctype": "Chat Message", "name": "x"})
		finally:
			if had_flags:
				frappe.flags = original
			else:
				del frappe.flags

	def test_a_non_chat_call_still_passes_through_untouched(self):
		"""The negative control. A denylist that refuses everything is a denylist
		nobody keeps — and an over-broad normaliser would do exactly that."""
		response = _gate._gated_execute(
			_tool("run_database_query"),
			self.recorder,
			{"query": "select name from `tabTask` where status = 'Open'"},
		)
		self.assertTrue(response.get("success"), response)
		self.assertEqual(len(self.recorder.calls), 1)

	def test_a_read_of_an_unrelated_doctype_passes_through(self):
		response = _gate._gated_execute(
			_tool("get_document"), self.recorder, {"doctype": "Task", "name": "TASK-0001"}
		)
		self.assertTrue(response.get("success"), response)


class TestTheNormaliserItself(unittest.TestCase):
	"""Unit-level, because the normaliser is the whole defence on the SQL path
	and its failure mode is silent under-refusal."""

	def test_the_needle_is_contiguous_after_normalisation(self):
		self.assertIn("tabchatmessage", _gate._normalise_for_denylist("`tabChat Message`"))

	def test_comments_do_not_hide_a_table_name(self):
		self.assertIn(
			"tabchatmessage",
			_gate._normalise_for_denylist("/* x */ select 1 from `tabChat/*y*/ Message`"),
		)

	def test_a_non_string_is_not_a_match(self):
		self.assertEqual(_gate._normalise_for_denylist(None), "")
		self.assertEqual(_gate._normalise_for_denylist({"query": "x"}), "")

	def test_the_most_specific_table_is_named_in_the_refusal(self):
		"""``Chat Room`` is a prefix of ``Chat Room Member`` once whitespace is
		gone. Reporting the prefix would send an operator reading the log to the
		wrong table."""
		self.assertEqual(
			_gate.chat_denylist_hit(
				"run_database_query", {"query": "select user from `tabChat Room Member`"}
			),
			"Chat Room Member",
		)

	def test_a_tool_with_no_text_argument_declared_is_not_text_matched(self):
		"""Only ``run_database_query`` and ``run_python_code`` take free text.
		Searching every argument of every tool for a table name would refuse a
		coworker asking Triton about the word 'chatroom'."""
		self.assertIsNone(
			_gate.chat_denylist_hit("some_reporting_tool", {"query": "select 1 from `tabChat Message`"})
		)

	def test_arguments_that_are_not_a_dict_do_not_crash_the_branch(self):
		self.assertIsNone(_gate.chat_denylist_hit("get_document", None))
		self.assertIsNone(_gate.chat_denylist_hit("get_document", ["Chat Message"]))


class TestTheSeamIsAttached(unittest.TestCase):
	"""Assertion 4, and the residual stated rather than implied.

	Whether ``_gate.apply_gate()`` actually wrapped ``BaseTool._safe_execute``
	can only be observed against a real FAC install, and the canary for it
	already exists: ``apply_gate()`` writes an Error Log when the seam is
	missing, and ``test_ai_gating_integration.test_gate_marker_present`` fails
	on bench CI. **Bench CI is not run for this app** — there is no Frappe
	integration-test job — so the denylist's *attachment* is covered by a test
	that does not execute anywhere automatically. That is the residual. What is
	asserted here is the half that can be: the seam's name, and that
	``apply_gate`` is still the thing that installs it.
	"""

	def test_apply_gate_still_targets_safe_execute(self):
		source = (APP_DIR / "assistant_tools" / "_gate.py").read_text(encoding="utf-8")
		self.assertIn(
			"_safe_execute",
			source,
			"the gate no longer names _safe_execute. If FAC renamed the seam, every "
			"refusal in this file is unreachable in production while this suite stays "
			"green.",
		)
		self.assertIn("BaseTool._safe_execute = gated_safe_execute", source)

	def test_the_denylist_branch_is_above_the_settings_check(self):
		"""Order is the invariant, and it is asserted on the source because
		there is no way to observe it from a passing call: both orders refuse
		when gating is on, and only one refuses when it is off."""
		source = (APP_DIR / "assistant_tools" / "_gate.py").read_text(encoding="utf-8")
		body = source.split("def _gated_execute(", 1)[1]
		denylist_at = body.find("chat_denylist_hit")
		bypass_at = body.find("frappe.flags")
		# The call, not the settings-field name: `ai_write_gating_enabled`
		# contains `_gating_enabled` as a substring, so searching for the bare
		# name matches the comment that explains this ordering and the test
		# fails on its own documentation.
		gating_at = body.find("_gating_enabled()")
		self.assertNotEqual(denylist_at, -1, "the denylist branch is gone from _gated_execute")
		self.assertLess(
			denylist_at,
			bypass_at,
			"the chat denylist is checked AFTER the confirm-flow bypass. An approved "
			"pending action would then be able to re-execute a chat read.",
		)
		self.assertLess(
			denylist_at,
			gating_at,
			"the chat denylist is checked AFTER the ai_write_gating_enabled check. That "
			"flag ships dormant, so the refusal would be off in production today.",
		)


if __name__ == "__main__":  # pragma: no cover
	unittest.main()
