# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The oversight read path, asserted at the seams a database cannot reach. Bench-free, AST.

`retrieve_for_oversight()` had zero callers for its whole life, and two defects that only a
caller would have surfaced:

1. **The verbatim tier never ran.** T1 was gated on a single ``room`` being supplied, and the
   oversight path supplies a *set* and no thread. So an oversight read returned chunks and
   digests and **no actual message** — while looking complete, which is the part that matters.
   A read that answers with summaries when it was asked for a transcript does not get
   reported as broken; it gets believed.

2. **The authored tier returned the auditor's own messages.** It ran as ``acting``, which is
   right for an ordinary retrieve and exactly inverted for oversight: the auditor got content
   they could already read, in place of the content they had just stated a reason to see.

3. **Then the tier that fixed defect 1 lost whole rooms.** ``seq`` is a *per-room* counter, so
   the single ``order by seq desc limit 200`` serving the whole named set ranked counters that
   were never comparable. A busy room's tail took every slot and a quiet room named in the
   same read came back empty — while chunks and digests still answered. Defect 1 again, one
   room at a time, arriving through the ``ORDER BY`` instead of through the call site.

Both of the first two were invisible to every existing test because nothing called the
function, and they are asserted **by AST**, on the shape of the call.

**The third could not be, and that is why this suite is no longer AST-only.** It lives in the
interaction between an ``ORDER BY`` and a room set; every assertion about the source text that
would have caught it — ``order by `seq` desc`` is present, ``%(limit)s`` is bound — was already
here and passed on the broken code, and passes on the fixed code too. So the module now also
installs a ``frappe`` stub whose ``db.sql`` answers from **what the statement bound** rather
than from how it was written. That is what lets one fake both reproduce the old defect and
verify the new behaviour; :class:`TheFakeItself` is the control that keeps it honest, because a
fake that cannot fail proves nothing.
"""

import ast
import pathlib
import re
import sys
import types
import unittest

GATE = (
	pathlib.Path(__file__).resolve().parents[1] / "chat" / "retrieval" / "gate.py"
)
VIEWER = (
	pathlib.Path(__file__).resolve().parents[1] / "chat" / "governance" / "viewer.py"
)

_STUBBED: list[str] = []

#: The corpus, and the seq ranges are the whole point. `room-busy` alone can fill a 200-row
#: global limit twenty-five times over, so any read that ranks these three against each other
#: by `seq` comes back containing nothing but `room-busy`.
_CORPUS: dict[str, int] = {"room-busy": 5000, "room-quiet": 40, "room-tiny": 3}


def _rooms_in_clause(query: str) -> list[str]:
	"""The room names inlined in a ``room in ('a', 'b')`` clause.

	The one place the fake reads the statement instead of the bound values, and unavoidable:
	the room set is variable-length, so `_room_list_sql` escapes the names into the SQL rather
	than binding them. It exists for the control test, which hands the fake the *old* flat
	statement — the one with no `room` parameter to answer from.
	"""
	found = re.search(r"`room`\s+in\s+\(([^)]*)\)", query)
	if not found:
		return []
	return [part.strip().strip("'") for part in found.group(1).split(",") if part.strip()]


class _FakeDB:
	"""Enough MariaDB to answer the question this tier asks, and deliberately no more.

	It reproduces three semantics and passes over everything else in the statement: which rooms
	were asked for, ``order by seq desc`` as a **global** sort across whatever it selected, and
	``LIMIT``. The global sort is not an approximation — it is exactly what MariaDB does with a
	per-room counter over a multi-room set, so the defect is reproduced by construction rather
	than by parsing for it.

	**Keyed on the bound parameters, not on the statement text.** A fake that recognises the
	implementation is a mock: it goes green the day the implementation is rewritten, whatever
	the rewrite does. A fake that answers what was *asked for* is a database. It is not, and
	must never become, a SQL engine — `test_chat_sql_columns` is right that a checker which
	tries becomes fragile and is then ignored.
	"""

	def __init__(self) -> None:
		self.statements: list[tuple[str, dict]] = []

	def escape(self, value: object) -> str:
		return "'" + str(value).replace("'", "''") + "'"

	def get_single_value(self, _doctype: str, _field: str) -> None:
		return None

	def sql(self, query: str, values: dict | None = None, as_dict: bool = False, **_kw):
		bound = dict(values or {})
		self.statements.append((query, bound))
		if "tabChat Message" not in query:
			return []
		rooms = [bound["room"]] if bound.get("room") else _rooms_in_clause(query)
		rows = [
			{
				"name": f"{room}-{seq}",
				"room": room,
				"seq": seq,
				"sender": "someone",
				"sender_email": "someone@example.com",
				"text": f"{room} message {seq}",
				"thread_root": None,
				# Every seventh message is a tombstone. The retrieval tiers exclude these and
				# the transcript keeps them, which is the difference the tests below turn on.
				"is_deleted": 1 if seq % 7 == 0 else 0,
				"is_edited": 1 if seq % 5 == 0 else 0,
				"edited_at": "2026-08-14 09:00:00" if seq % 5 == 0 else None,
				"creation": None,
				"gchat_create_time": None,
			}
			for room in rooms
			for seq in range(1, _CORPUS.get(room, 0) + 1)
		]
		# The gate binds a positive `before_seq` only when it also emits the clause, so keying
		# on the bound value is faithful to the question rather than to the statement's text.
		before = bound.get("before_seq")
		if before:
			rows = [row for row in rows if row["seq"] < int(before)]
		rows.sort(key=lambda row: row["seq"], reverse=True)
		limit = bound.get("limit")
		return rows if limit is None else rows[: int(limit)]


def setUpModule() -> None:
	for name in ("frappe", "frappe.utils", "frappe.model", "frappe.model.document"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
			_STUBBED.append(name)

	frappe = sys.modules["frappe"]
	utils = sys.modules["frappe.utils"]

	def _cint(value: object) -> int:
		try:
			return int(float(value))  # type: ignore[arg-type]
		except (TypeError, ValueError):
			return 0

	utils.cint = _cint
	utils.get_datetime = lambda value=None: value
	utils.now_datetime = lambda: None
	utils.now = lambda: "2026-08-15 12:00:00.000000"
	frappe.utils = utils
	frappe.cint = _cint
	frappe.session = types.SimpleNamespace(user="auditor@example.com")

	def _set_user(user):
		frappe.session.user = user

	# `_acting_as` sets the session for the duration of a read, because the shared membership
	# fragment resolves its own default from the session and a helper that forgot the argument
	# would silently widen to every room.
	frappe.set_user = _set_user
	frappe.log_error = lambda **_kw: None
	frappe._ = lambda text: text
	frappe.db = _FakeDB()
	frappe.get_cached_doc = lambda *_a, **_k: None

	document = sys.modules["frappe.model.document"]
	if not hasattr(document, "Document"):
		document.Document = object


def tearDownModule() -> None:
	for name in _STUBBED:
		sys.modules.pop(name, None)


def _tree():
	return ast.parse(GATE.read_text(encoding="utf-8"))


def _func(name):
	for node in ast.walk(_tree()):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in gate.py")


def _run_call_in(func_name):
	"""The `_run(...)` call inside a named function, as an AST node."""
	for node in ast.walk(_func(func_name)):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_run":
			return node
	raise AssertionError(f"{func_name}() does not call _run()")


def _kwarg(call, name):
	for kw in call.keywords:
		if kw.arg == name:
			return kw.value
	return None


class RunSignatureTest(unittest.TestCase):
	"""`_run` must make every caller answer the question that was previously implicit."""

	def test_authored_user_has_no_default(self):
		"""The regression this whole module exists for.

		While the tier defaulted to `acting`, adding a caller meant inheriting a decision
		nobody had made for it. A required keyword-only argument turns that into a compile-time
		question: whose messages is this tier about?
		"""
		run = _func("_run")
		names = [a.arg for a in run.args.kwonlyargs]
		self.assertIn("authored_user", names)
		idx = names.index("authored_user")
		self.assertIsNone(
			run.args.kw_defaults[idx],
			"authored_user must have NO default — a default is how the oversight path "
			"silently inherited `acting` and fed the auditor their own messages",
		)

	def test_verbatim_across_rooms_exists_and_defaults_off(self):
		"""Off by default: the mention path is the common one and is thread-shaped."""
		run = _func("_run")
		names = [a.arg for a in run.args.kwonlyargs]
		self.assertIn("verbatim_across_rooms", names)
		default = run.args.kw_defaults[names.index("verbatim_across_rooms")]
		self.assertIsInstance(default, ast.Constant)
		self.assertIs(default.value, False)


class CallSiteTest(unittest.TestCase):
	"""Each caller states its own intent, and the two intents are different."""

	def test_the_ordinary_retrieve_authors_as_the_asker(self):
		call = _run_call_in("retrieve")
		value = _kwarg(call, "authored_user")
		self.assertIsInstance(value, ast.Name)
		self.assertEqual(value.id, "acting")

	def test_the_oversight_read_never_authors_as_the_auditor(self):
		"""The defect, pinned by name.

		`authored_user=acting` here is not a style preference — it returns the auditor their
		own messages while an audit is in progress, which is both useless and misleading.
		"""
		call = _run_call_in("retrieve_for_oversight")
		value = _kwarg(call, "authored_user")
		self.assertIsInstance(value, ast.Name, "oversight must pass a name, not a literal")
		self.assertEqual(value.id, "subject")
		self.assertNotEqual(value.id, "acting")

	def test_the_oversight_read_asks_for_the_cross_room_verbatim_tier(self):
		"""Without this the tier is dead: `room` is None on this path and always will be."""
		call = _run_call_in("retrieve_for_oversight")
		value = _kwarg(call, "verbatim_across_rooms")
		self.assertIsInstance(value, ast.Constant)
		self.assertIs(value.value, True)

	def test_the_oversight_read_still_passes_no_room(self):
		"""Which is why the thread-shaped tier could never have fired here."""
		call = _run_call_in("retrieve_for_oversight")
		self.assertIsInstance(_kwarg(call, "room"), ast.Constant)
		self.assertIsNone(_kwarg(call, "room").value)

	def test_subject_is_a_parameter_of_the_oversight_entry_point(self):
		"""Optional — not every oversight read is about a person — but it must exist."""
		fn = _func("retrieve_for_oversight")
		self.assertIn("subject", [a.arg for a in fn.args.kwonlyargs])


class VerbatimTierTest(unittest.TestCase):
	"""The new SQL, asserted on the properties that are not negotiable."""

	def setUp(self):
		self.src = ast.get_source_segment(
			GATE.read_text(encoding="utf-8"), _func("_oversight_room_messages")
		)

	def test_it_applies_the_membership_filter(self):
		"""Every content read in this package goes through the shared fragment.

		`allow_oversight=True` is correct *here* and only here — this is the gate, which
		`chat/permissions.py` names as the one module allowed to open that hatch.
		"""
		self.assertIn("membership_filter_sql", self.src)
		self.assertIn("allow_oversight=True", self.src)

	def test_it_excludes_deleted_rows(self):
		"""Seeing through a tombstone is a different act with its own audit event (§4.E).

		Folding it into the ordinary oversight read would make every such read an expansion
		and erase the distinction the export's `include_deleted_content` flag depends on.
		"""
		self.assertIn("`is_deleted` = 0", self.src)

	def test_it_orders_by_seq_and_not_by_a_timestamp(self):
		"""`seq` is immutable and never renumbered.

		`creation` is site-local while an origin timestamp is UTC, so sorting a mixed-origin
		transcript by either is wrong by the site offset — in the direction that always
		favours one origin.
		"""
		self.assertIn("order by `seq` desc", self.src)
		self.assertNotIn("order by `creation`", self.src)

	def test_it_binds_its_parameters(self):
		"""The room list is built by `_room_list_sql`; everything else is bound."""
		self.assertIn("%(expression)s", self.src)
		self.assertIn("%(limit)s", self.src)

	def test_an_empty_room_set_returns_nothing_rather_than_everything(self):
		"""The failure that turns a bug into an incident.

		`_room_list_sql` on an empty set would produce a clause matching no room or,
		depending how it degrades, none at all — so the guard is explicit and first.
		"""
		fn = _func("_oversight_room_messages")
		first = fn.body[1] if isinstance(fn.body[0], ast.Expr) else fn.body[0]
		self.assertIsInstance(first, ast.If, "the empty-room guard must be the first statement")
		self.assertIn("allowed_rooms", ast.dump(first.test))


def _module_constant(path: pathlib.Path, name: str) -> object:
	"""A module-level constant read by AST, without importing the module.

	`chat/governance/viewer.py` pulls its whole whitelisted surface in at import — rate
	limiter, permissions, the audit writer — and this suite wants exactly one integer off it.
	"""
	for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {path.name}")


class VerbatimTierBehaviourTest(unittest.TestCase):
	"""The rows themselves, against a fake that answers what the statement bound."""

	def setUp(self) -> None:
		from erpnext_enhancements.chat import permissions
		from erpnext_enhancements.chat.retrieval import gate

		self.gate = gate
		self.db = _FakeDB()
		sys.modules["frappe"].db = self.db
		# The fragment is `test_chat_rawsql_guard`'s business and the bench suite's; pinning it
		# to a constant here keeps this suite about ordering, which is the thing that broke.
		self.permissions = permissions
		self._real_filter = permissions.membership_filter_sql
		permissions.membership_filter_sql = lambda *_a, **_k: "1 = 1"
		self.named = frozenset(_CORPUS)

	def tearDown(self) -> None:
		self.permissions.membership_filter_sql = self._real_filter

	def _read(self, expression: str = "", limit: int = 200) -> list[dict]:
		return self.gate._oversight_room_messages(self.named, expression=expression, limit=limit)

	def _message_statements(self) -> list[str]:
		return [sql for sql, _ in self.db.statements if "tabChat Message" in sql]

	def test_every_named_room_contributes_a_verbatim_message(self) -> None:
		"""The defect, pinned by behaviour. This assertion is why the stub exists.

		On the pre-fix code this returns 200 rows that are all `room-busy`, and the two other
		rooms are simply absent — with no error, and with T2/T3 still answering around it.
		"""
		rows = self._read()
		self.assertEqual(
			{row["room"] for row in rows},
			set(_CORPUS),
			"a room named in the read came back with no verbatim message at all. `seq` is a "
			"per-room counter, so one `order by seq desc limit 200` over a room set gives "
			"every slot to the busiest room, and the auditor sees a result that looks "
			"complete while containing none of the quiet room's messages.",
		)

	def test_the_fan_out_binds_one_statement_per_room(self) -> None:
		"""Stops the 'optimisation' back to a single statement, which is the regression."""
		self._read()
		bound = [values.get("room") for _sql, values in self.db.statements if "tabChat Message" in _sql]
		self.assertEqual(sorted(bound), sorted(_CORPUS))

	def test_every_statement_carries_the_scope_and_the_delete_filter(self) -> None:
		"""Every branch, not just one.

		`test_chat_rawsql_guard` proves the fragment is *named* in this function — its own
		docstring says so. With a fan-out that is no longer the same claim as "applied to each
		read", because a later edit can drop it from one branch and leave the name in place.
		"""
		self._read()
		statements = self._message_statements()
		self.assertEqual(len(statements), len(_CORPUS))
		for sql in statements:
			self.assertIn("1 = 1", sql, "the membership fragment is missing from a branch")
			self.assertIn("`is_deleted` = 0", sql)
			self.assertIn("order by `seq` desc", sql)

	def test_the_rows_are_grouped_by_room_and_ascend_within_one(self) -> None:
		"""No cross-room total order is invented, and each block reads in the order it happened."""
		rows = self._read()
		order = [row["room"] for row in rows]
		self.assertEqual(order, sorted(order), "a room's block is interrupted by another room's")
		for room in _CORPUS:
			seqs = [row["seq"] for row in rows if row["room"] == room]
			self.assertEqual(seqs, sorted(seqs), f"{room} does not ascend by seq")

	def test_a_room_with_more_to_give_than_its_share_says_so(self) -> None:
		"""And a room that fitted entirely does not — the marker means "cut", not "read"."""
		rows = self._read()
		marked = {row["room"] for row in rows if row.get(self.gate._TAIL_CUT)}
		self.assertEqual(marked, {"room-busy"})
		busy = [row for row in rows if row["room"] == "room-busy"]
		self.assertTrue(
			busy[0].get(self.gate._TAIL_CUT),
			"the marker belongs on the OLDEST kept row, which renders first — 'there is more "
			"above this' is meaningless at the bottom of a block",
		)

	def test_a_cap_of_zero_reads_nothing_rather_than_one_per_room(self) -> None:
		"""A caller asking for no verbatim tier is not a caller being starved by the floor."""
		self.assertEqual(self._read(limit=0), [])
		self.assertEqual(self._message_statements(), [])

	def test_the_quota_divides_the_cap_and_never_reaches_zero(self) -> None:
		shares = self.gate._per_room_limits(frozenset({"a", "b", "c"}), 200)
		self.assertEqual(sum(shares.values()), 200, "the remainder is dropped rather than dealt")
		self.assertEqual(shares, {"a": 67, "b": 67, "c": 66})
		self.assertEqual(self.gate._per_room_limits(frozenset({"only"}), 200), {"only": 200})
		self.assertEqual(self.gate._per_room_limits(frozenset(), 200), {})
		self.assertEqual(self.gate._per_room_limits(frozenset({"a", "b"}), 0), {})
		starved = self.gate._per_room_limits(frozenset({f"r{i}" for i in range(30)}), 20)
		self.assertTrue(all(share >= 1 for share in starved.values()), "the floor of one failed")


class TranscriptReadTest(unittest.TestCase):
	"""The transcript path. Tombstones stay, the order ascends, and every page is recorded.

	`retrieve_for_oversight` cannot serve this: it returns a tail, becomes a search on a
	non-empty expression, and hands its rows to a budget whose rung 4 discards the MIDDLE of
	the thread. Each of those is right for assembling a model's context and disqualifying for
	a record. What is asserted here is the properties that make this one a record.
	"""

	def setUp(self) -> None:
		from erpnext_enhancements.chat import audit, permissions
		from erpnext_enhancements.chat.retrieval import gate

		self.gate = gate
		self.db = _FakeDB()
		sys.modules["frappe"].db = self.db
		sys.modules["frappe"].session.user = "auditor@example.com"

		self.permissions = permissions
		self.audit = audit
		self._saved = {
			"filter": permissions.membership_filter_sql,
			"oversight": permissions._has_oversight,
			"enabled": gate._assert_retrieval_enabled,
			"record": audit.record_or_refuse,
		}
		permissions.membership_filter_sql = lambda *_a, **_k: "1 = 1"
		permissions._has_oversight = lambda _user: True
		gate._assert_retrieval_enabled = lambda: None
		self.recorded: list[dict] = []
		audit.record_or_refuse = lambda **kw: (self.recorded.append(kw), "CRA-0001")[1]

	def tearDown(self) -> None:
		self.permissions.membership_filter_sql = self._saved["filter"]
		self.permissions._has_oversight = self._saved["oversight"]
		self.gate._assert_retrieval_enabled = self._saved["enabled"]
		self.audit.record_or_refuse = self._saved["record"]

	def _read(self, **kw):
		return self.gate.retrieve_transcript(
			room=kw.pop("room", "room-quiet"),
			reason=kw.pop("reason", "reviewing the Jones complaint"),
			**kw,
		)

	def test_a_deleted_message_keeps_its_place_in_the_transcript(self) -> None:
		"""The one property that separates this from every tier above it.

		The retrieval tiers exclude ``is_deleted`` so an ordinary read is not a tombstone
		expansion. A transcript has the opposite obligation: a conversation with the deleted
		messages quietly removed is a *misleading* transcript, and the gap is where an
		investigation is most likely to be looking.
		"""
		page = self._read()
		self.assertTrue(
			[row for row in page.rows if row.get("is_deleted")],
			"the transcript dropped its tombstones, so the record it hands an auditor is "
			"missing exactly the messages somebody chose to remove",
		)

	def test_it_ascends_by_seq(self) -> None:
		seqs = [row["seq"] for row in self._read().rows]
		self.assertEqual(seqs, sorted(seqs))

	def test_it_pages_backwards_on_seq_and_never_offset(self) -> None:
		first = self._read(limit=10)
		older = self._read(limit=10, before_seq=first.first_seq)
		self.assertTrue(older.rows)
		self.assertLess(older.last_seq, first.first_seq)
		for _sql, values in self.db.statements:
			self.assertIn("before_seq", values)

	def test_it_says_whether_there_is_more_above(self) -> None:
		"""Otherwise "the conversation starts here" and "your page ended" look identical."""
		self.assertTrue(self._read(limit=5).has_more_before)
		self.assertFalse(self._read(room="room-tiny", limit=50).has_more_before)

	def test_every_page_writes_exactly_one_audit_row(self) -> None:
		self._read(limit=10)
		self._read(limit=10, before_seq=20)
		self.assertEqual(len(self.recorded), 2)
		for row in self.recorded:
			self.assertEqual(row["purpose"], "transcript")
			self.assertEqual(row["actor_type"], "Admin")
			self.assertEqual(row["rooms"][0]["room"], "room-quiet")
			self.assertTrue(row["reason"])

	def test_the_audit_row_records_the_range_actually_read(self) -> None:
		"""`§4.D.2` asks for the range. "They looked at that room" is not an audit record."""
		page = self._read(limit=10)
		recorded = self.recorded[-1]["rooms"][0]
		self.assertEqual(recorded["first_seq"], page.first_seq)
		self.assertEqual(recorded["last_seq"], page.last_seq)
		self.assertEqual(recorded["messages_read"], len(page.rows))

	def test_the_pages_of_one_sitting_can_be_correlated(self) -> None:
		self._read(limit=5, request_id="sitting-7")
		self._read(limit=5, before_seq=10, request_id="sitting-7")
		self.assertEqual({row["request_id"] for row in self.recorded}, {"sitting-7"})

	def test_it_refuses_without_the_oversight_role(self) -> None:
		self.permissions._has_oversight = lambda _user: False
		with self.assertRaises(self.gate.RetrievalRefused):
			self._read()
		self.assertEqual(self.recorded, [], "a refused read still wrote an audit row")

	def test_it_refuses_a_reason_too_short_to_be_one(self) -> None:
		with self.assertRaises(self.gate.RetrievalRefused):
			self._read(reason="because")
		self.assertEqual(self.recorded, [])

	def test_it_refuses_to_read_an_unnamed_room(self) -> None:
		with self.assertRaises(self.gate.RetrievalRefused):
			self._read(room="   ")

	def test_the_page_size_is_capped_however_much_is_asked_for(self) -> None:
		"""A caller-supplied limit sizes a statement; it must not be able to size it unbounded."""
		page = self._read(room="room-busy", limit=100000)
		self.assertLessEqual(len(page.rows), self.gate.MAX_TRANSCRIPT_PAGE)


class CrossRoomSearchTest(unittest.TestCase):
	"""The third oversight read: hits across the named rooms, narrowed, not summarised.

	`retrieve_for_oversight` returns a ranked, budgeted assembly — right for handing a model
	context and wrong for an investigator who wants *every* message matching a narrowing.
	`retrieve_transcript` returns one room in full. This is the gap between them.
	"""

	def setUp(self) -> None:
		from erpnext_enhancements.chat import audit, permissions
		from erpnext_enhancements.chat.retrieval import gate

		self.gate = gate
		self.db = _FakeDB()
		sys.modules["frappe"].db = self.db
		sys.modules["frappe"].session.user = "auditor@example.com"
		self.permissions, self.audit = permissions, audit
		self._saved = {
			"filter": permissions.membership_filter_sql,
			"oversight": permissions._has_oversight,
			"enabled": gate._assert_retrieval_enabled,
			"record": audit.record_or_refuse,
		}
		permissions.membership_filter_sql = lambda *_a, **_k: "1 = 1"
		permissions._has_oversight = lambda _user: True
		gate._assert_retrieval_enabled = lambda: None
		self.recorded: list[dict] = []
		audit.record_or_refuse = lambda **kw: (self.recorded.append(kw), "CRA-0002")[1]

	def tearDown(self) -> None:
		self.permissions.membership_filter_sql = self._saved["filter"]
		self.permissions._has_oversight = self._saved["oversight"]
		self.gate._assert_retrieval_enabled = self._saved["enabled"]
		self.audit.record_or_refuse = self._saved["record"]

	def _search(self, **kw):
		return self.gate.search_transcripts(
			rooms=kw.pop("rooms", sorted(_CORPUS)),
			reason=kw.pop("reason", "reviewing the Jones complaint"),
			**kw,
		)

	def _statements(self):
		return [(sql, v) for sql, v in self.db.statements if "tabChat Message" in sql]

	def test_it_searches_each_named_room_separately(self) -> None:
		"""Per room for the reason `_per_room_limits` exists: `seq` is a per-room counter, so
		one ordering across a set hands every slot to the busiest room."""
		self._search()
		bound = [v.get("room") for _sql, v in self._statements()]
		self.assertEqual(sorted(bound), sorted(_CORPUS))

	def test_every_named_room_contributes_hits(self) -> None:
		hits = self._search()
		self.assertEqual(set(hits.per_room), set(_CORPUS))
		self.assertEqual({row["room"] for row in hits.rows}, set(_CORPUS))

	def test_the_filters_reach_the_statement_as_bound_values(self) -> None:
		"""Keyed on what was ASKED, which is the fake's whole contract."""
		self._search(
			filters=self.gate.MessageFilters(
				sender="ada@example.com",
				origin="Google",
				from_date="2026-01-01",
				to_date="2026-06-30",
				with_attachments=True,
			)
		)
		for sql, values in self._statements():
			self.assertEqual(values["sender"], "ada@example.com")
			self.assertEqual(values["origin"], "Google")
			self.assertEqual(values["from_date"], "2026-01-01")
			self.assertIn("`has_attachments` = 1", sql)
			# A filter, never an ordering. The two clocks disagree by the site offset, which is
			# survivable when narrowing and not when sequencing.
			self.assertIn("order by `seq` desc", sql)
			self.assertNotIn("order by `creation`", sql)
			self.assertNotIn("order by coalesce", sql)

	def test_an_unfiltered_search_binds_nothing_extra(self) -> None:
		"""Absent filters must not become `= ''`, which matches nothing and looks like no hits."""
		self._search()
		for _sql, values in self._statements():
			for key in ("sender", "origin", "from_date", "to_date"):
				self.assertNotIn(key, values)

	def test_deleted_rows_are_matched_and_returned(self) -> None:
		"""A deleted message that matches is a fact an investigator needs. The body is withheld
		by the serialiser; dropping the row would make the search disagree with the transcript
		beside it."""
		hits = self._search()
		self.assertTrue([r for r in hits.rows if r.get("is_deleted")])
		for _sql, _values in self._statements():
			self.assertNotIn("`is_deleted` = 0", _sql)

	def test_the_audit_row_has_a_child_per_room_that_produced_a_hit(self) -> None:
		"""A search returning hits from twelve rooms is twelve non-participant reads."""
		hits = self._search()
		self.assertEqual(len(self.recorded), 1)
		recorded = self.recorded[0]
		self.assertEqual(recorded["purpose"], "search")
		self.assertEqual(recorded["actor_type"], "Admin")
		self.assertEqual({r["room"] for r in recorded["rooms"]}, set(hits.per_room))
		self.assertEqual(recorded["message_count"], len(hits.rows))

	def test_a_room_that_matched_nothing_is_not_recorded_as_read(self) -> None:
		"""The same rule `_write_audit` applies: one child row per room actually read."""
		hits = self._search(rooms=["room-tiny", "room-empty"])
		self.assertNotIn("room-empty", hits.per_room)
		self.assertNotIn("room-empty", {r["room"] for r in self.recorded[0]["rooms"]})

	def test_it_refuses_the_things_the_other_oversight_reads_refuse(self) -> None:
		with self.assertRaises(self.gate.RetrievalRefused):
			self._search(reason="because")
		with self.assertRaises(self.gate.RetrievalRefused):
			self._search(rooms=[])
		with self.assertRaises(self.gate.RetrievalRefused):
			self._search(rooms=[f"r{i}" for i in range(self.gate.MAX_OVERSIGHT_ROOMS + 1)])
		self.permissions._has_oversight = lambda _user: False
		with self.assertRaises(self.gate.RetrievalRefused):
			self._search()
		self.assertEqual(self.recorded, [], "a refused search still wrote an audit row")


class TheRoomSetIsRefusedRatherThanTrimmed(unittest.TestCase):
	"""An auditor's named rooms are never silently narrowed.

	`retrieve_for_oversight` used to pass its caller-supplied set through `_cap_rooms`, which
	sorts and slices. With `max_rooms_per_retrieval` set, an auditor who named twelve rooms got
	some smaller number of them and no indication which — and the audit row stayed honest,
	because it records rooms actually *read*. So the log was right and the person was not,
	which is the same shape as the per-room `seq` defect: a result that looks complete and is
	not.

	The cap's own field description says it applies "to the room set the gate derived from
	membership, never to a set a caller supplied". The oversight path is the one path where the
	caller supplies it.
	"""

	def test_the_oversight_entry_point_does_not_trim_its_room_set(self) -> None:
		# Unparsed from the AST, not read as source. Comments never reach the AST, and the
		# comment in that function has to NAME `_cap_rooms` in order to explain why it is gone
		# — a text scan would be satisfied only by deleting the explanation. This file has now
		# been bitten by that shape four times in one release series; the AST form is the fix.
		fn = _func("retrieve_for_oversight")
		stripped = ast.Module(body=list(fn.body), type_ignores=[])
		if (
			stripped.body
			and isinstance(stripped.body[0], ast.Expr)
			and isinstance(stripped.body[0].value, ast.Constant)
		):
			stripped.body = stripped.body[1:]
		self.assertNotIn(
			"_cap_rooms",
			ast.unparse(stripped),
			"retrieve_for_oversight trims the rooms the auditor named. `_cap_rooms` sorts and "
			"slices; on this path that returns a subset of a set somebody typed, with nothing "
			"anywhere saying so. Refuse instead.",
		)

	def test_it_refuses_above_the_bound_rather_than_returning_a_subset(self) -> None:
		fn = _func("retrieve_for_oversight")
		body = ast.dump(fn)
		self.assertIn("MAX_OVERSIGHT_ROOMS", body, "the bound is no longer enforced at the gate")
		raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
		self.assertGreaterEqual(
			len(raises), 4, "a refusal was removed; the room bound must raise, not narrow"
		)

	def test_the_gate_enforces_it_itself_rather_than_trusting_the_viewer(self) -> None:
		"""A gate that relies on its caller having checked stops working on its second caller."""
		from erpnext_enhancements.chat.retrieval import gate

		self.assertIsInstance(gate.MAX_OVERSIGHT_ROOMS, int)
		self.assertGreater(gate.MAX_OVERSIGHT_ROOMS, 0)

	def test_the_two_surfaces_agree_on_what_is_expressible(self) -> None:
		"""The viewer refuses above its cap and so does the gate. Two different numbers would
		mean a read the page accepts and the gate rejects, or worse, the reverse."""
		from erpnext_enhancements.chat.retrieval import gate

		viewer = _module_constant(VIEWER, "MAX_ROOMS_PER_READ")
		self.assertEqual(viewer, gate.MAX_OVERSIGHT_ROOMS)

	def test_the_derived_path_still_caps(self) -> None:
		"""`_cap_rooms` is right where it was always meant to be — on the set derived from
		somebody's own membership, where narrowing is a cost control and not a lie."""
		self.assertIn("_cap_rooms", GATE.read_text(encoding="utf-8"))


class TheFakeItself(unittest.TestCase):
	"""A fake that cannot fail proves nothing.

	The same control idiom as `test_chat_gate_source_scan.TestTheAnalyserItself`: hand the fake
	the statement the gate used to write and assert it reproduces the defect. Without this, a
	green run cannot be told apart from "the fake did not recognise the query", which is the
	failure mode that makes a stubbed suite worthless the day someone refactors.
	"""

	def test_it_reproduces_the_global_seq_order_defect(self) -> None:
		rooms = "', '".join(sorted(_CORPUS))
		old_statement = (
			"select `name`, `room`, `seq` from `tabChat Message` "
			f"where `room` in ('{rooms}') and 1 = 1 and `is_deleted` = 0 "
			"order by `seq` desc limit %(limit)s"
		)
		rows = _FakeDB().sql(old_statement, {"limit": 200}, as_dict=True)
		self.assertEqual(len(rows), 200)
		self.assertEqual(
			{row["room"] for row in rows},
			{"room-busy"},
			"the fake no longer reproduces the defect, so every assertion resting on it is "
			"passing for a reason nobody has checked",
		)

	def test_it_answers_a_bound_room_rather_than_the_in_clause(self) -> None:
		"""The property that makes it a database rather than a mock of this implementation."""
		rows = _FakeDB().sql(
			"select `seq` from `tabChat Message` where `room` = %(room)s and `room` in ('x')",
			{"room": "room-tiny", "limit": 10},
			as_dict=True,
		)
		self.assertEqual({row["room"] for row in rows}, {"room-tiny"})


class TheTwoCapsAgree(unittest.TestCase):
	"""The floor of one in `_per_room_limits` is unreachable today. This says so out loud."""

	def test_the_room_cap_cannot_starve_a_named_room(self) -> None:
		from erpnext_enhancements.chat.retrieval import gate

		rooms = _module_constant(VIEWER, "MAX_ROOMS_PER_READ")
		self.assertLessEqual(
			rooms,
			gate.MAX_THREAD_MESSAGES,
			"a read may now name more rooms than the verbatim cap has slots, so the "
			"guarantee that every named room contributes a message holds only because of "
			"the floor of one — raise one constant or lower the other deliberately, not by "
			"accident",
		)


if __name__ == "__main__":
	unittest.main()
