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

Both were invisible to every existing test because nothing called the function. What is
asserted here is the *shape* of the call — that each caller decides out loud — because the
rows themselves need a bench and the wiring is what regressed.
"""

import ast
import pathlib
import unittest

GATE = (
	pathlib.Path(__file__).resolve().parents[1] / "chat" / "retrieval" / "gate.py"
)


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


if __name__ == "__main__":
	unittest.main()
