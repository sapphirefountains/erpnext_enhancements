"""The heartbeat wire format, pinned on both sides.

The player and the progress store use **deliberately different** shapes, and only
``api.training._normalise_beat`` knows both:

* the wire sends run-length-encoded ranges under the key ``ranges``;
* ``training.progress`` stores the same runs under the key ``intervals``.

Both are half-open ``[start, end]``. ``_normalise_beat`` used to read the wire's
second element as a *length* — and this file used to assert that it should, which
is precisely why the disagreement survived. Restating one side's assumption is not
a contract test. :class:`TestTheWireShapeMatchesTheProducer` checks the server
against ``rle()``, the function that actually produces the numbers.

Getting the translation wrong is **silent and total**. Mismatched keys mean zero
seconds are ever credited, so no video lesson can be completed and nothing
raises — the feature simply never works. That is exactly what happened when these
two halves were first written against each other: the player emitted ``ranges``,
``seeks: {forward, backward}`` and a nested ``discount.hidden``, while progress
read ``intervals``, an integer ``seeks`` and a top-level ``hidden``.

So this suite asserts the translation *and* re-asserts the shape each side
actually uses, so drift on either side fails here rather than in production.

Run: python -m unittest erpnext_enhancements.tests.test_training_heartbeat_wire
"""

import ast
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

normalise = None

VIDEO_JS = REPO_ROOT / "erpnext_enhancements/public/js/training/video.js"
PROGRESS_PY = REPO_ROOT / "erpnext_enhancements/training/progress.py"


def _rle_body():
	"""The source of ``rle()`` alone, so assertions cannot wander into its callers.

	Brace-matched rather than regex-bounded: ``rle`` contains nested blocks, and a
	non-greedy match to the first ``}`` stops inside the ``for`` loop — which would
	quietly hide the very last ``out.push`` from every check below.
	"""
	src = VIDEO_JS.read_text(encoding="utf-8")
	start = src.index("function rle(")
	depth = 0
	for i in range(src.index("{", start), len(src)):
		if src[i] == "{":
			depth += 1
		elif src[i] == "}":
			depth -= 1
			if depth == 0:
				return src[start : i + 1]
	raise AssertionError("rle() has unbalanced braces")


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.session = types.SimpleNamespace(user="tester@example.com")
	frappe.flags = types.SimpleNamespace()
	frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.parse_json = lambda v: v
	frappe.db = types.SimpleNamespace(
		get_value=lambda *a, **k: None, get_single_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None, exists=lambda *a, **k: None, count=lambda *a, **k: 0,
	)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.get_cached_doc = lambda *a, **k: None
	frappe.only_for = lambda *a, **k: None
	frappe.generate_hash = lambda length=10: "h" * length
	frappe.cache = lambda: types.SimpleNamespace(
		get_value=lambda *a, **k: None, set_value=lambda *a, **k: None, delete_value=lambda *a, **k: None
	)
	frappe.__dict__["_"] = lambda s: s

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0) if not isinstance(v, dict) else 0
	utils.flt = lambda v: float(v or 0)
	utils.now_datetime = lambda: "2026-08-02 12:00:00"
	utils.nowdate = lambda: "2026-08-02"
	utils.today = utils.nowdate
	utils.add_days = lambda d, n: d
	utils.getdate = lambda d=None: d
	utils.sanitize_html = lambda s: s or ""
	utils.escape_html = lambda s: s
	utils.formatdate = lambda d, *a, **k: str(d)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
		}
	)


def setUpModule():
	global normalise
	_install_frappe_stub()
	from erpnext_enhancements.api.training import _normalise_beat

	normalise = _normalise_beat


class TestRangesBecomeIntervals(unittest.TestCase):
	"""Wire ``[start, end]`` -> stored ``[start, end]``. A rename, not a maths.

	It used to be a maths, and it was wrong: the second element was read as a
	*length*. The tests below said so too, which is why nobody noticed — they
	restated the server's assumption instead of checking it against the client
	that produces the values. See :class:`TestTheWireShapeMatchesTheProducer`.
	"""

	def test_single_range(self):
		out = normalise({"ranges": [[10, 15]]})
		self.assertEqual(out["intervals"], [[10, 15]])

	def test_several_ranges(self):
		out = normalise({"ranges": [[0, 30], [95, 180]]})
		self.assertEqual(out["intervals"], [[0, 30], [95, 180]])

	def test_a_run_that_does_not_start_at_zero(self):
		"""The case that broke production, kept as its own test.

		A run starting at 0 reads the same either way — end and length coincide —
		so every learner who played from the beginning looked fine, and the defect
		only appeared on the first beat after a pause. Fifteen seconds from 17 is
		``[17, 32]``. Read as a length it became ``[17, 49]``: thirty-two seconds,
		nearly all of them never watched.
		"""
		out = normalise({"ranges": [[17, 32]]})
		self.assertEqual(out["intervals"], [[17, 32]])
		start, end = out["intervals"][0]
		self.assertEqual(end - start, 15, "credited seconds must equal seconds watched")

	def test_empty_range_is_dropped(self):
		"""A zero-width run credits nothing; keeping it would add a degenerate
		interval that merge logic then has to defend against."""
		self.assertEqual(normalise({"ranges": [[10, 10]]})["intervals"], [])

	def test_inverted_range_is_dropped_not_repaired(self):
		"""``end < start`` is a client bug. Swapping it would invent seconds."""
		self.assertEqual(normalise({"ranges": [[30, 10]]})["intervals"], [])

	def test_malformed_range_is_skipped_not_fatal(self):
		out = normalise({"ranges": [[10, 15], "nonsense", [1, 2, 3], [20, 24]]})
		self.assertEqual(out["intervals"], [[10, 15], [20, 24]])

	def test_the_wire_key_does_not_survive(self):
		"""progress reads `intervals`; leaving `ranges` behind invites somebody to
		start reading the wrong one."""
		self.assertNotIn("ranges", normalise({"ranges": [[1, 2]]}))

	def test_an_explicit_intervals_key_wins(self):
		"""Lets a caller (or a test) speak the internal shape directly."""
		out = normalise({"ranges": [[0, 99]], "intervals": [[5, 6]]})
		self.assertEqual(out["intervals"], [[5, 6]])

	def test_no_ranges_key_is_left_alone(self):
		self.assertNotIn("intervals", normalise({"lesson_key": "a"}))


class TestCountersAreFlattened(unittest.TestCase):
	def test_only_forward_seeks_are_counted(self):
		"""Rewatching is legitimate. Counting backward seeks against a learner
		would penalise exactly the behaviour the feature wants to encourage."""
		out = normalise({"seeks": {"forward": 3, "backward": 9}})
		self.assertEqual(out["seeks"], 3)

	def test_missing_forward_reads_as_zero(self):
		self.assertEqual(normalise({"seeks": {"backward": 4}})["seeks"], 0)

	def test_an_integer_seeks_passes_through(self):
		self.assertEqual(normalise({"seeks": 2})["seeks"], 2)

	def test_hidden_is_lifted_out_of_discount(self):
		out = normalise({"discount": {"hidden": 12, "blur": 3, "muted": 40}})
		self.assertEqual(out["hidden"], 12)

	def test_discount_does_not_survive(self):
		self.assertNotIn("discount", normalise({"discount": {"hidden": 1}}))

	def test_top_level_hidden_wins(self):
		out = normalise({"hidden": 7, "discount": {"hidden": 99}})
		self.assertEqual(out["hidden"], 7)


class TestPassthrough(unittest.TestCase):
	def test_identity_keys_are_untouched(self):
		out = normalise({"lesson_key": "lk", "block_key": "bk", "duration": 600})
		self.assertEqual(out["lesson_key"], "lk")
		self.assertEqual(out["block_key"], "bk")
		self.assertEqual(out["duration"], 600)

	def test_input_is_not_mutated(self):
		"""The caller pops identity keys off its own copy; mutating theirs would
		couple two unrelated bits of cleanup."""
		payload = {"ranges": [[1, 2]], "seeks": {"forward": 1}}
		normalise(payload)
		self.assertIn("ranges", payload)


class TestTheWireShapeMatchesTheProducer(unittest.TestCase):
	"""Check the translation against ``rle()``, not against its own docstring.

	This is the test that was missing, and its absence cost a release. The suite
	above pinned ``[start, length]`` and the class below asserted the player still
	emits a key *named* ``ranges`` — so both halves were checked and neither was
	checked against the other. The server was free to disagree with the client
	about what the numbers meant, and did, and nothing failed.

	``rle()`` is JavaScript, so it cannot be called from here. What it can do is
	state its own contract, which it does in a worked example directly above the
	code::

	    // [3,4,5,9,10] -> [[3,6],[9,11]] - half-open, matching progress_json's `iv`

	That example is machine-checked three ways below: that it exists at all, that
	it is arithmetically consistent with half-open ``[start, end]`` (so it cannot
	go stale while still passing), that the emitting line really pushes an end
	rather than a length, and that ``_normalise_beat`` credits exactly the seconds
	it names — no more, which is the failure that shipped.
	"""

	@staticmethod
	def _worked_example():
		"""``(input_seconds, emitted_pairs)`` from rle()'s comment."""
		src = VIDEO_JS.read_text(encoding="utf-8")
		match = re.search(r"//\s*(\[[\d,\s]+\])\s*->\s*(\[\[.*?\]\])", src)
		if not match:
			return None, None
		return ast.literal_eval(match.group(1)), ast.literal_eval(match.group(2))

	def test_the_example_is_actually_found(self):
		"""Guard first. A scanner that matches nothing passes everything, which is
		how three earlier assertions in this module came to be worthless."""
		seconds, pairs = self._worked_example()
		self.assertIsNotNone(seconds, "rle()'s worked example is gone; the tests below are blind")
		self.assertTrue(seconds and pairs)

	def test_the_example_is_consistent_with_half_open_start_end(self):
		"""The comment cannot drift away from the code it documents."""
		seconds, pairs = self._worked_example()
		covered = []
		for start, end in pairs:
			covered.extend(range(start, end))
		self.assertEqual(
			covered,
			list(seconds),
			f"rle()'s example {seconds} -> {pairs} does not read as half-open "
			f"[start, end]; if the player's format really changed, this file and "
			f"_normalise_beat both have to change with it",
		)

	def test_every_emitting_line_pushes_an_end_not_a_length(self):
		"""Belt and braces: the comment could be right and the code wrong.

		*Every* push, not any push. ``rle()`` closes a run in two places — one when
		it hits a gap, one for the final run — and asserting that the pattern
		merely appears somewhere let a mutation change the second site and still
		pass. Presence is not coverage; that mistake has now been made in three
		separate suites in this module.
		"""
		body = _rle_body()
		pushes = re.findall(r"out\.push\(\[([^\]]*)\]\)", body)
		self.assertGreaterEqual(len(pushes), 2, "rle() no longer closes runs the way this reads")
		for args in pushes:
			self.assertEqual(
				args.replace(" ", ""),
				"start,prev+1",
				f"rle() emits `[{args}]`; if that is a length rather than an end, "
				f"_normalise_beat has to stop reading an end",
			)

	def test_normalise_credits_exactly_the_seconds_the_player_named(self):
		"""The whole contract, end to end, in one assertion.

		Not ``>=``: over-crediting is the bug that shipped. ``[[17, 32]]`` — fifteen
		seconds — was banked as thirty-two, and after four beats the intervals had
		swallowed a 90-second video whole.
		"""
		seconds, pairs = self._worked_example()
		intervals = normalise({"ranges": [list(p) for p in pairs]})["intervals"]
		credited = []
		for start, end in intervals:
			credited.extend(range(start, end))
		self.assertEqual(sorted(credited), sorted(seconds))
		self.assertEqual(
			len(credited), len(seconds), "credited seconds must equal watched seconds"
		)


class TestBothSidesStillSpeakTheseShapes(unittest.TestCase):
	"""Re-read the source either side of the translation.

	If the player renames `ranges`, or progress starts reading something other
	than `intervals`, the translation above becomes a no-op that silently credits
	nothing. These catch that at the only moment it is cheap to catch.

	Names only. Checking that the two sides agree on what the values *mean* is
	:class:`TestTheWireShapeMatchesTheProducer`, and the distinction is the entire
	lesson of v1.236.0 — these assertions all passed while coverage was wrong.
	"""

	def test_player_still_emits_ranges_and_structured_counters(self):
		src = VIDEO_JS.read_text(encoding="utf-8")
		self.assertRegex(src, r"\branges\s*:", "player no longer emits `ranges`")
		self.assertRegex(src, r"seeks\s*:\s*\{", "player no longer emits structured `seeks`")
		self.assertRegex(src, r"\bdiscount\s*:", "player no longer emits `discount`")

	def test_progress_still_reads_intervals_seeks_hidden(self):
		src = PROGRESS_PY.read_text(encoding="utf-8")
		for key in ("intervals", "seeks", "hidden", "duration", "lesson_key", "block_key"):
			self.assertIn(
				f'payload.get("{key}")', src, f"progress no longer reads payload[{key!r}]"
			)

	def test_progress_does_not_read_the_wire_keys(self):
		"""If it started reading `ranges` directly the translation would double up."""
		src = PROGRESS_PY.read_text(encoding="utf-8")
		self.assertNotIn('payload.get("ranges")', src)
		self.assertNotIn('payload.get("discount")', src)


class TestTransportMapPointsAtRealEndpoints(unittest.TestCase):
	"""Every transport method must name an endpoint that actually exists.

	The player only knows the transport's *function names*; ``www/training.html``
	maps those to whitelisted methods. A wrong name there is a 404 at runtime and
	nothing catches it earlier, because both halves are individually valid.

	Three were wrong on the first Phase-2 build: ``start_quiz`` and ``media_url``
	did not exist (the endpoints are ``get_quiz`` and ``get_media_url``), and
	``heartbeatBeacon`` was called by video.js but absent from the map entirely,
	so the pagehide flush would have thrown on ``undefined``. Phase 3's authoring
	preview implements this same set, so the list has to be right.
	"""

	HTML = REPO_ROOT / "erpnext_enhancements/www/training.html"
	API = REPO_ROOT / "erpnext_enhancements/api/training.py"
	JS_DIR = REPO_ROOT / "erpnext_enhancements/public/js/training"

	def _method_map(self):
		html = self.HTML.read_text(encoding="utf-8")
		start = html.index("var METHOD = {")
		block = html[start : html.index("};", start)]
		return dict(re.findall(r'(\w+)\s*:\s*"(\w+)"', block))

	def _whitelisted(self):
		api = self.API.read_text(encoding="utf-8")
		return set(re.findall(r"@frappe\.whitelist\(\)\s*\ndef\s+(\w+)", api))

	def _transport_calls(self):
		used = set()
		for path in self.JS_DIR.glob("*.js"):
			used |= set(re.findall(r"transport\.(\w+)", path.read_text(encoding="utf-8")))
		return used

	def test_the_map_parses(self):
		"""Guards every other assertion here — an empty map would make them all
		vacuously true."""
		self.assertGreater(len(self._method_map()), 4)
		self.assertGreater(len(self._whitelisted()), 4)

	def test_every_mapped_method_exists(self):
		whitelisted = self._whitelisted()
		broken = {k: v for k, v in self._method_map().items() if v not in whitelisted}
		self.assertEqual(broken, {}, f"transport methods naming no such endpoint: {broken}")

	def test_every_transport_call_is_mapped(self):
		missing = sorted(self._transport_calls() - set(self._method_map()))
		self.assertEqual(missing, [], f"player calls transport.{missing} with no map entry")


BLOCKS_JS = REPO_ROOT / "erpnext_enhancements/public/js/training/blocks.js"


class TestTheDocumentVocabulary(unittest.TestCase):
	"""PDF and Downloadable File blocks speak a dialect nothing here had learned.

	`blocks.js` sends `played` where video.js sends `ranges`, and `claimed` where it
	sends `claimed_seconds`. Same encodings, different words — and this function had
	only ever implemented one of the two vocabularies, so a document beat produced no
	`intervals`, credited nothing, and the dwell tracking recorded silence
	(TASK-2026-01178).

	The cost was not only the lost dwell. `record_heartbeat` cross-checks the
	claimed total against the seconds it derives from the ranges — the check that
	would have caught the v1.235.0 half-open misread three releases earlier — and
	for document blocks it was comparing against a key that was never sent, so it
	silently checked nothing.
	"""

	def test_played_becomes_intervals(self):
		self.assertEqual(normalise({"played": [[0, 20]]})["intervals"], [[0, 20]])

	def test_played_is_dropped_once_translated(self):
		out = normalise({"played": [[0, 20]]})
		self.assertNotIn("played", out)

	def test_ranges_still_wins_when_both_arrive(self):
		"""Nothing sends both today. If something starts to, the video vocabulary is
		the one with the server-verified duration behind it."""
		out = normalise({"ranges": [[0, 5]], "played": [[0, 20]]})
		self.assertEqual(out["intervals"], [[0, 5]])

	def test_claimed_becomes_claimed_seconds(self):
		self.assertEqual(normalise({"claimed": 20})["claimed_seconds"], 20)

	def test_claimed_is_dropped_once_translated(self):
		self.assertNotIn("claimed", normalise({"claimed": 20}))

	def test_an_explicit_claimed_seconds_is_not_overwritten(self):
		out = normalise({"claimed": 20, "claimed_seconds": 5})
		self.assertEqual(out["claimed_seconds"], 5)

	def test_kind_rides_through_untouched(self):
		"""The normaliser's job is names, not policy. Which block type gets which
		treatment is `record_heartbeat`'s decision."""
		self.assertEqual(normalise({"kind": "doc"})["kind"], "doc")

	def test_ack_is_coerced_to_an_int(self):
		self.assertEqual(normalise({"ack": True})["ack"], 1)
		self.assertEqual(normalise({"ack": 0})["ack"], 0)

	def test_a_beat_without_ack_does_not_invent_one(self):
		"""Absent has to stay absent: player.js reads `response.ack != null` and a
		fabricated 0 would put every video block behind an acknowledgement gate."""
		self.assertNotIn("ack", normalise({"ranges": [[0, 5]]}))

	def test_a_plain_integer_seeks_count_survives(self):
		"""video.js sends {forward, backward}; blocks.js sends 0."""
		self.assertEqual(normalise({"seeks": 0})["seeks"], 0)


class TestBlocksJsStillSendsWhatIsRead(unittest.TestCase):
	"""Pins the producer, so this cannot drift back apart in silence."""

	def _doc_beat_source(self):
		src = BLOCKS_JS.read_text(encoding="utf-8")
		start = src.index("function beat(extra)")
		return src[start : start + 700]

	def test_the_document_beat_sends_the_keys_the_server_translates(self):
		body = self._doc_beat_source()
		for key in ("kind:", "played:", "claimed:", "duration:", "block_key:"):
			self.assertIn(key, body, f"blocks.js stopped sending {key}")

	def test_the_acknowledgement_is_sent_as_ack(self):
		self.assertIn("ack: 1", BLOCKS_JS.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
