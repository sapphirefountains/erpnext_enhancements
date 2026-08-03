"""Bench-free unit tests for the Training AI drafting endpoints.

Stubs a minimal ``frappe`` (no site/bench) so ``api.training_ai`` runs under plain
unittest. The stub is installed in ``setUpModule`` (execution time), not at
import, so it never fools the bench-only suites' ``import frappe`` skip-guards.
The Vertex client is stubbed the same way — every test drives the module by
setting the exact text the model "returned", because the failure modes worth
guarding are all about what the module does with an untrustworthy string.

What is guarded here:

  * **checkpoints refuse without a timed transcript.** Asked for a timestamp it
    cannot derive, a model invents a confident and plausible one, and a
    checkpoint at the wrong second is indistinguishable from a badly written one
    until a learner complains. Prose pasted into the transcript field is *not* a
    timed transcript, and that is the case that actually happens;
  * **the model's reply is parsed strictly** — prose, markdown fences, a bare
    string, a partial object and a half-built question all yield zero suggestions
    and a message, never a question with no stem;
  * **an ungrounded stem is dropped**, because a question about material the
    lesson never covered is worse than no question at all;
  * **drafting writes nothing and names no reviewer.** ``ai_reviewed_by`` is half
    of the gate in ``training_author._unreviewed_ai_questions``; a draft that
    arrives pre-reviewed defeats it entirely;
  * **acceptance does name one**, on both kinds, together with ``ai_generated`` —
    the pair the gate reads. A disputed result has to trace to a named human.

Run: python -m unittest erpnext_enhancements.tests.test_training_ai
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

training_ai = None

#: Everything the stub serves and records, reset by each test.
STATE = {}

MODEL_ID = "gemini-3.1-pro-preview"

LESSON = "TRN-LSN-000001"

# Long enough to clear MIN_SOURCE_CHARS, and deliberately full of distinctive
# terms so the grounding check has something real to match against.
LESSON_BODY = (
	"<p>The isolation valve sits on the return manifold and must be closed before the pump "
	"strainer basket is opened. Closing it first stops the basin draining back through the "
	"suction line and flooding the vault. Reopen it only once the strainer lid is hand-tight "
	"and the union has been checked for weep.</p>"
)

VTT = """WEBVTT

1
00:00:04.000 --> 00:00:11.500
The isolation valve sits on the return manifold.

2
00:00:11.500 --> 00:00:20.000
Close it before you open the pump strainer basket.

3
00:00:20.000 --> 00:00:31.000
Reopen the valve once the strainer lid is hand-tight.
"""


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"model_text": "",
			"calls": [],
			"inserted": [],
			"saved": [],
			"errors": [],
			"roles": ["Training Author"],
			"settings": {"training_enabled": 1, "ai_assist_enabled": 1},
			"version_docstatus": 0,
			"asset_transcript": "",
			"checkpoints": [],
			"name_counter": 0,
			"hash_counter": 0,
		}
	)


# ----------------------------------------------------------------- frappe stub


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _StubDoc(_Dict):
	"""A document that records what was written to it rather than a database."""

	def check_permission(self, permtype="read"):
		return True

	def append(self, table, row):
		self.setdefault(table, []).append(_Dict(row))
		return self[table]

	def insert(self, **kwargs):
		STATE["name_counter"] += 1
		self["name"] = self.get("name") or f"NEW-{STATE['name_counter']:04d}"
		STATE["inserted"].append(self)
		return self

	def save(self, **kwargs):
		STATE["saved"].append(self)
		return self


def _lesson_doc():
	return _StubDoc(
		name=LESSON,
		doctype="Training Lesson",
		lesson_title="Isolating the pump",
		summary="Why the isolation valve comes first.",
		course_version="TRN-CV-00001",
		transcript="",
		has_quiz=0,
		quiz_questions=[],
		blocks=[
			_Dict(
				block_key="blk1",
				block_type="Video",
				heading="Isolation",
				content=LESSON_BODY,
				caption="",
				video_asset="TRN-VID-00001",
				video_duration_seconds=120,
			),
			_Dict(
				block_key="blk2",
				block_type="Rich Text",
				heading="Notes",
				content=LESSON_BODY,
				caption="",
				video_asset="",
				video_duration_seconds=0,
			),
		],
	)


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _StubDoc(doctype)
	if doctype == "Training Lesson":
		return STATE.setdefault("lesson", _lesson_doc())
	if doctype in ("Triton Settings", "Training Settings"):
		return _StubDoc(STATE["settings"])
	raise Exception(f"unexpected get_doc({doctype!r}, {name!r})")


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	rows = STATE["checkpoints"] if doctype == "Training Checkpoint" else []
	if pluck:
		return [row.get(pluck) for row in rows]
	return [_Dict(row) for row in rows]


def _db_get_value(doctype, name, fieldname, **kwargs):
	if doctype == "Training Course Version" and fieldname == "docstatus":
		return STATE["version_docstatus"]
	if doctype == "Training Video Asset" and fieldname == "transcript":
		return STATE["asset_transcript"]
	return None


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = _get_doc
	frappe.get_all = _get_all
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.session = _Dict(user="author@example.com")
	frappe.flags = _Dict()
	frappe.parse_json = json.loads
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.enqueue = lambda *a, **k: None

	def _generate_hash(length=10):
		STATE["hash_counter"] += 1
		return f"h{STATE['hash_counter']:0{max(length - 1, 1)}d}"

	frappe.generate_hash = _generate_hash

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.msgprint = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a[0] if a else "")
	frappe.get_traceback = lambda: "traceback"

	frappe.db = types.SimpleNamespace(
		get_value=_db_get_value,
		set_value=lambda *a, **k: None,
		get_single_value=lambda *a, **k: None,
		exists=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		commit=lambda: None,
		has_column=lambda *a, **k: True,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v: float(v or 0)
	utils.today = lambda: "2026-08-01"
	utils.now_datetime = lambda: "2026-08-01 12:00:00"
	utils.sanitize_html = lambda s: s or ""
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document

	frappe.__dict__["_"] = lambda s: s
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	# The Vertex client, stubbed. Records what it was asked and replays whatever
	# the test decided the model "returned" — including the malformed replies,
	# which are the whole point of the strict-parsing tests.
	gemini = types.ModuleType("erpnext_enhancements.api.gemini")
	gemini.MODEL_ID = MODEL_ID

	def _generate(prompt, system_instruction, settings, feature="unknown"):
		STATE["calls"].append({"prompt": prompt, "system": system_instruction, "feature": feature})
		if isinstance(STATE["model_text"], Exception):
			raise STATE["model_text"]
		return STATE["model_text"], ""

	gemini.generate_content_with_vertex_ai = _generate
	sys.modules["erpnext_enhancements.api.gemini"] = gemini


def setUpModule():
	global training_ai
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.api import training_ai as module

	training_ai = module


# ------------------------------------------------------------------- fixtures


def _option(text, correct=0):
	return {"text": text, "is_correct": correct}


def _good_question(stem="Which valve is closed before opening the strainer basket?"):
	return {
		"question": stem,
		"type": "Single Choice",
		"options": [_option("The isolation valve", 1), _option("The bleed valve")],
		"explanation": "It stops the basin draining back through the suction line.",
		"grounding_quote": "The isolation valve sits on the return manifold.",
	}


def _reply(*questions):
	return json.dumps({"suggestions": list(questions)})


def _draft(*questions, count=5):
	STATE["model_text"] = _reply(*questions)
	return training_ai.draft_quiz_questions(LESSON, count=count)


class _Base(unittest.TestCase):
	def setUp(self):
		_reset_state()


# ------------------------------------------------------ the transcript refusal


class TestCheckpointsRefuseWithoutTimings(_Base):
	"""The single constraint the whole checkpoint feature rests on.

	Without cue timings a model produces a timestamp that looks right and is not,
	and nothing downstream can tell. So the endpoint refuses instead of guessing.
	"""

	def _suggest(self):
		STATE["model_text"] = _reply(dict(_good_question(), at_seconds=12))
		return training_ai.suggest_checkpoints(LESSON, "blk1", count=3)

	def test_no_transcript_at_all_is_refused(self):
		with self.assertRaises(Exception) as caught:
			self._suggest()
		self.assertIn("timed transcript", str(caught.exception))

	def test_the_refusal_says_how_to_fix_it(self):
		"""An author who is told 'no' and not told what to upload files a ticket."""
		with self.assertRaises(Exception) as caught:
			self._suggest()
		self.assertIn(".vtt", str(caught.exception))

	def test_prose_pasted_into_the_transcript_is_not_a_timed_transcript(self):
		"""The case that actually happens: a transcript is present, so the author
		expects it to work, and it carries no timings whatsoever."""
		STATE["lesson"] = _lesson_doc()
		STATE["lesson"]["transcript"] = LESSON_BODY * 3
		with self.assertRaises(Exception) as caught:
			self._suggest()
		self.assertIn("timed transcript", str(caught.exception))

	def test_the_model_is_never_even_asked(self):
		"""Refusing after paying for a generation would still be refusing, but it
		would also mean the prompt was built from timings that do not exist."""
		with self.assertRaises(Exception):
			self._suggest()
		self.assertEqual(STATE["calls"], [])

	def test_a_vtt_on_the_video_asset_is_accepted(self):
		STATE["asset_transcript"] = VTT
		result = self._suggest()
		self.assertEqual(len(result["suggestions"]), 1)

	def test_a_vtt_on_the_lesson_is_accepted_as_a_fallback(self):
		STATE["lesson"] = _lesson_doc()
		STATE["lesson"]["transcript"] = VTT
		result = self._suggest()
		self.assertEqual(len(result["suggestions"]), 1)

	def test_a_non_video_block_is_refused_before_the_transcript_check(self):
		STATE["asset_transcript"] = VTT
		with self.assertRaises(Exception) as caught:
			training_ai.suggest_checkpoints(LESSON, "blk2", count=1)
		self.assertIn("Video", str(caught.exception))


class TestVttParsing(_Base):
	def test_reads_hours_minutes_and_seconds(self):
		cues = training_ai._parse_vtt("WEBVTT\n\n01:02:03.500 --> 01:02:09.000\nhello\n")
		self.assertEqual(len(cues), 1)
		self.assertEqual(int(cues[0]["start"]), 3723)
		self.assertEqual(cues[0]["text"], "hello")

	def test_reads_the_short_minute_second_form(self):
		cues = training_ai._parse_vtt("00:12.000 --> 00:19.000\nhello\n")
		self.assertEqual(int(cues[0]["end"]), 19)

	def test_plain_prose_yields_no_cues(self):
		self.assertEqual(training_ai._parse_vtt("There are no timings in this at all."), [])

	def test_a_reversed_cue_is_discarded_rather_than_reversed(self):
		"""A cue ending before it starts is a corrupt file. Silently swapping the
		two would place a checkpoint at a time nobody wrote down."""
		self.assertEqual(training_ai._parse_vtt("00:30.000 --> 00:10.000\nhello\n"), [])


# ----------------------------------------------------------- strict parsing


class TestStrictParsing(_Base):
	"""A model that did not answer in JSON gets zero suggestions and a message.

	Never a half-built question: the author is reviewing a list, and one plausible
	looking entry with no correct answer is exactly what gets accepted by someone
	skim-reading twelve of them.
	"""

	def _draft_raw(self, text):
		STATE["model_text"] = text
		return training_ai.draft_quiz_questions(LESSON, count=5)

	def test_prose_yields_nothing(self):
		result = self._draft_raw("Sure! Here are five questions about isolation valves.")
		self.assertEqual(result["suggestions"], [])
		self.assertTrue(result["message"])

	def test_markdown_fences_yield_nothing(self):
		"""Deliberately not unwrapped. A parser that hunts for JSON inside prose
		also finds it inside a half-written apology, and the caller cannot tell."""
		fenced = "```json\n" + _reply(_good_question()) + "\n```"
		result = self._draft_raw(fenced)
		self.assertEqual(result["suggestions"], [])
		self.assertTrue(result["message"])

	def test_an_empty_reply_yields_nothing(self):
		result = self._draft_raw("   ")
		self.assertEqual(result["suggestions"], [])
		self.assertTrue(result["message"])

	def test_a_bare_json_string_yields_nothing(self):
		result = self._draft_raw('"no questions today"')
		self.assertEqual(result["suggestions"], [])
		self.assertTrue(result["message"])

	def test_truncated_json_yields_nothing(self):
		result = self._draft_raw(_reply(_good_question())[:-12])
		self.assertEqual(result["suggestions"], [])
		self.assertTrue(result["message"])

	def test_a_bare_list_is_accepted(self):
		"""Unambiguous and common. Every item is validated regardless, so nothing
		is being trusted here that the wrapped form would not also be trusted for."""
		result = self._draft_raw(json.dumps([_good_question()]))
		self.assertEqual(len(result["suggestions"]), 1)

	def test_a_half_built_question_is_dropped_and_the_rest_survive(self):
		"""Malformed *items* are dropped one by one; only a malformed *response*
		costs the whole batch."""
		result = self._draft_raw(
			_reply(
				{"question": "Which valve?", "type": "Single Choice"},
				_good_question(),
			)
		)
		self.assertEqual(len(result["suggestions"]), 1)

	def test_a_stemless_question_is_dropped(self):
		item = _good_question()
		item["question"] = "   "
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_a_question_with_no_correct_option_is_dropped(self):
		item = _good_question()
		item["options"] = [_option("The isolation valve"), _option("The bleed valve")]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_a_question_where_everything_is_correct_is_dropped(self):
		item = _good_question()
		item["options"] = [_option("The isolation valve", 1), _option("The bleed valve", 1)]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_single_choice_with_two_correct_options_is_dropped(self):
		item = _good_question()
		item["options"] = [
			_option("The isolation valve", 1),
			_option("The bleed valve", 1),
			_option("The check valve"),
		]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_one_option_is_dropped(self):
		item = _good_question()
		item["options"] = [_option("The isolation valve", 1)]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_seven_options_are_dropped(self):
		item = _good_question()
		item["options"] = [_option(f"Valve {n}", 1 if n == 0 else 0) for n in range(7)]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_duplicate_option_text_is_dropped(self):
		"""A learner cannot tell two identical options apart, and the doctype
		refuses to save them — better to never suggest it."""
		item = _good_question()
		item["options"] = [_option("The isolation valve", 1), _option("the Isolation Valve")]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_an_unknown_question_type_is_dropped(self):
		item = _good_question()
		item["type"] = "Essay"
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

	def test_true_false_must_be_spelled_true_and_false(self):
		"""The Training Question controller refuses any other pair rather than
		guessing which one means true — so a suggestion of Yes/No never inserts."""
		item = _good_question()
		item["type"] = "True-False"
		item["options"] = [_option("Yes", 1), _option("No")]
		self.assertEqual(self._draft_raw(_reply(item))["suggestions"], [])

		item["options"] = [_option("True", 1), _option("False")]
		self.assertEqual(len(self._draft_raw(_reply(item))["suggestions"]), 1)

	def test_a_string_is_correct_is_read_rather_than_crashing(self):
		"""`cint("true")` raises. A model writing "true" for 1 is a formatting slip,
		not a reason to lose the batch."""
		item = _good_question()
		item["options"] = [_option("The isolation valve", "true"), _option("The bleed valve", "false")]
		result = self._draft_raw(_reply(item))
		self.assertEqual(result["suggestions"][0]["options"][0]["is_correct"], 1)
		self.assertEqual(result["suggestions"][0]["options"][1]["is_correct"], 0)

	def test_html_is_stripped_out_of_the_stem(self):
		item = _good_question()
		item["question"] = "<b>Which valve</b> is closed before the strainer basket?"
		result = self._draft_raw(_reply(item))
		self.assertNotIn("<b>", result["suggestions"][0]["question"])

	def test_more_suggestions_than_asked_for_are_capped(self):
		items = [_good_question(f"Which valve number {n} isolates the strainer basket?") for n in range(6)]
		result = self._draft_raw(json.dumps({"suggestions": items}))
		self.assertEqual(len(result["suggestions"]), 5)

		STATE["model_text"] = json.dumps({"suggestions": items})
		self.assertEqual(len(training_ai.draft_quiz_questions(LESSON, count=2)["suggestions"]), 2)

	def test_a_vertex_failure_is_reported_not_leaked(self):
		STATE["model_text"] = RuntimeError("502 from Vertex")
		with self.assertRaises(Exception) as caught:
			training_ai.draft_quiz_questions(LESSON, count=1)
		self.assertNotIn("502", str(caught.exception))
		self.assertTrue(STATE["errors"])


# ----------------------------------------------------------------- grounding


class TestGrounding(_Base):
	"""A stem sharing no content word with the lesson was not drawn from it.

	Cheap, and aimed at one real failure: a model answering from its own knowledge
	of the subject rather than from the lesson in front of it. The learner is then
	assessed on material the course never taught.
	"""

	def test_an_ungrounded_question_is_dropped(self):
		item = _good_question("Which chlorine tablet dissolves fastest in a saltwater cell?")
		item["options"] = [_option("Trichlor", 1), _option("Dichlor")]
		STATE["model_text"] = _reply(item)
		result = training_ai.draft_quiz_questions(LESSON, count=5)
		self.assertEqual(result["suggestions"], [])
		self.assertTrue(result["message"])

	def test_a_grounded_question_survives_alongside_it(self):
		ungrounded = _good_question("Which chlorine tablet dissolves fastest in a saltwater cell?")
		ungrounded["options"] = [_option("Trichlor", 1), _option("Dichlor")]
		STATE["model_text"] = _reply(ungrounded, _good_question())
		result = training_ai.draft_quiz_questions(LESSON, count=5)
		self.assertEqual(len(result["suggestions"]), 1)
		self.assertIn("isolation", result["suggestions"][0]["options"][0]["text"].lower())

	def test_a_lesson_too_thin_to_ground_anything_is_refused(self):
		STATE["lesson"] = _lesson_doc()
		for block in STATE["lesson"]["blocks"]:
			block["content"] = ""
			block["heading"] = ""
		with self.assertRaises(Exception) as caught:
			training_ai.draft_quiz_questions(LESSON, count=3)
		self.assertIn("not enough", str(caught.exception))
		self.assertEqual(STATE["calls"], [])


# ---------------------------------------------- drafting persists nothing


class TestDraftingWritesNothing(_Base):
	"""The half of the review gate that lives here.

	``training_author._unreviewed_ai_questions`` blocks *submit for review* while
	any question is ``ai_generated`` with no ``ai_reviewed_by``. A drafting call
	that wrote a row — or worse, wrote one already stamped reviewed — would walk
	straight past it.
	"""

	def test_draft_quiz_questions_inserts_nothing(self):
		result = _draft(_good_question())
		self.assertEqual(len(result["suggestions"]), 1)
		self.assertEqual(STATE["inserted"], [])
		self.assertEqual(STATE["saved"], [])

	def test_suggest_checkpoints_inserts_nothing(self):
		STATE["asset_transcript"] = VTT
		STATE["model_text"] = _reply(dict(_good_question(), at_seconds=12))
		training_ai.suggest_checkpoints(LESSON, "blk1", count=1)
		self.assertEqual(STATE["inserted"], [])
		self.assertEqual(STATE["saved"], [])

	def test_no_suggestion_arrives_pre_reviewed(self):
		result = _draft(_good_question())
		self.assertNotIn("ai_reviewed_by", json.dumps(result))
		self.assertNotIn("ai_generated", json.dumps(result))

	def test_the_drafting_functions_never_name_the_field(self):
		"""Belt and braces, and the assertion that survives a rewrite: the source
		of the two drafting functions may not mention ai_reviewed_by at all."""
		source = Path(training_ai.__file__).read_text(encoding="utf-8")
		for name in ("draft_quiz_questions", "suggest_checkpoints"):
			start = source.index(f"def {name}")
			end = source.find("\ndef ", start + 1)
			self.assertNotIn("ai_reviewed_by", source[start : end if end != -1 else len(source)])


class TestGates(_Base):
	def test_drafting_is_refused_when_the_switch_is_off(self):
		STATE["settings"]["ai_assist_enabled"] = 0
		with self.assertRaises(Exception):
			_draft(_good_question())
		self.assertEqual(STATE["calls"], [])

	def test_drafting_is_refused_when_training_itself_is_off(self):
		"""``is_enabled`` requires the master switch too — a module that ships
		dormant must not make a paid API call on a site that never turned it on."""
		STATE["settings"]["training_enabled"] = 0
		with self.assertRaises(Exception):
			_draft(_good_question())
		self.assertEqual(STATE["calls"], [])

	def test_a_non_author_is_refused(self):
		STATE["roles"] = ["Employee"]
		with self.assertRaises(Exception):
			_draft(_good_question())

	def test_usage_is_booked_against_the_training_features(self):
		"""The feature name is what keeps AI Model Usage accounting readable; a
		default of 'unknown' would fold training in with email drafting."""
		_draft(_good_question())
		self.assertEqual(STATE["calls"][-1]["feature"], "training_quiz_draft")

		STATE["asset_transcript"] = VTT
		STATE["model_text"] = _reply(dict(_good_question(), at_seconds=12))
		training_ai.suggest_checkpoints(LESSON, "blk1", count=1)
		self.assertEqual(STATE["calls"][-1]["feature"], "training_checkpoint_draft")


# ------------------------------------------------------------ checkpoint timing


class TestCheckpointTiming(_Base):
	def setUp(self):
		super().setUp()
		STATE["asset_transcript"] = VTT

	def _suggest(self, *items, count=3):
		STATE["model_text"] = _reply(*items)
		return training_ai.suggest_checkpoints(LESSON, "blk1", count=count)

	def test_the_timestamp_is_anchored_to_the_end_of_the_quoted_cue(self):
		"""Asking before the answer has been said is worse than not asking. The
		model's own number is overridden by where its quote actually appears."""
		item = dict(_good_question(), at_seconds=3)
		item["grounding_quote"] = "Close it before you open the pump strainer basket."
		result = self._suggest(item)
		self.assertEqual(result["suggestions"][0]["at_seconds"], 20)

	def test_a_timestamp_in_no_cue_at_all_is_dropped(self):
		"""Nothing is being said at second 90 of a 31-second transcript, so a
		question placed there was invented rather than derived."""
		item = dict(_good_question(), at_seconds=90)
		item["grounding_quote"] = "not a sentence anywhere in this transcript"
		self.assertEqual(self._suggest(item)["suggestions"], [])

	def test_an_unquoted_timestamp_inside_a_cue_is_kept(self):
		item = dict(_good_question(), at_seconds=15)
		item["grounding_quote"] = ""
		self.assertEqual(self._suggest(item)["suggestions"][0]["at_seconds"], 15)

	def test_the_suggestion_carries_its_block_key(self):
		"""Acceptance takes a flat list and has no other way to know which video
		these belong to — the seam that would otherwise be a name nobody owned."""
		self.assertEqual(
			self._suggest(dict(_good_question(), at_seconds=12))["suggestions"][0]["block_key"], "blk1"
		)

	def test_a_checkpoint_in_the_closing_seconds_is_dropped(self):
		"""Matches the controller's own tail rule, so acceptance cannot throw on
		something this endpoint said was fine."""
		STATE["lesson"] = _lesson_doc()
		STATE["lesson"]["blocks"][0]["video_duration_seconds"] = 25
		item = dict(_good_question(), at_seconds=20)
		item["grounding_quote"] = "Close it before you open the pump strainer basket."
		self.assertEqual(self._suggest(item)["suggestions"], [])

	def test_a_checkpoint_landing_on_an_existing_one_is_dropped(self):
		STATE["checkpoints"] = [{"at_seconds": 19}]
		item = dict(_good_question(), at_seconds=15)
		item["grounding_quote"] = ""
		self.assertEqual(self._suggest(item)["suggestions"], [])

	def test_two_suggestions_at_the_same_moment_do_not_both_survive(self):
		first = dict(_good_question(), at_seconds=15)
		first["grounding_quote"] = ""
		second = dict(_good_question("Which valve is on the return manifold?"), at_seconds=16)
		second["grounding_quote"] = ""
		self.assertEqual(len(self._suggest(first, second)["suggestions"]), 1)


# ------------------------------------------------------------------ acceptance


class TestAcceptance(_Base):
	"""The other half of the gate. This call *is* the human review."""

	def _accept_quiz(self, *items):
		return training_ai.accept_ai_suggestions(LESSON, "quiz", list(items) or [_good_question()])

	def test_a_quiz_question_is_stamped_with_both_provenance_fields(self):
		result = self._accept_quiz()
		self.assertEqual(result["created"], 1)
		question = STATE["inserted"][0]
		self.assertEqual(question["ai_generated"], 1)
		self.assertEqual(question["ai_reviewed_by"], "author@example.com")

	def test_the_model_and_the_grounding_quote_are_recorded(self):
		self._accept_quiz()
		question = STATE["inserted"][0]
		self.assertEqual(question["ai_model"], MODEL_ID)
		self.assertIn("isolation valve", question["ai_source"])

	def test_the_question_joins_the_lesson_pool(self):
		self._accept_quiz()
		lesson = STATE["saved"][0]
		self.assertEqual(len(lesson["quiz_questions"]), 1)
		self.assertEqual(lesson["quiz_questions"][0]["question"], STATE["inserted"][0]["name"])

	def test_options_survive_with_their_correctness(self):
		self._accept_quiz()
		options = STATE["inserted"][0]["options"]
		self.assertEqual([o["is_correct"] for o in options], [1, 0])
		self.assertEqual(options[0]["option_text"], "The isolation valve")

	def test_a_checkpoint_is_stamped_too(self):
		item = dict(_good_question(), at_seconds=20, block_key="blk1")
		result = training_ai.accept_ai_suggestions(LESSON, "checkpoint", [item])
		self.assertEqual(result["created"], 1)
		checkpoint = STATE["inserted"][0]
		self.assertEqual(checkpoint["doctype"], "Training Checkpoint")
		self.assertEqual(checkpoint["ai_generated"], 1)
		self.assertEqual(checkpoint["ai_reviewed_by"], "author@example.com")
		self.assertEqual(checkpoint["at_seconds"], 20)
		self.assertEqual(checkpoint["block_key"], "blk1")

	def test_a_checkpoint_without_a_block_key_is_refused_loudly(self):
		item = dict(_good_question(), at_seconds=20)
		with self.assertRaises(Exception) as caught:
			training_ai.accept_ai_suggestions(LESSON, "checkpoint", [item])
		self.assertIn("block", str(caught.exception).lower())

	def test_a_checkpoint_without_a_timestamp_is_refused_loudly(self):
		item = dict(_good_question(), block_key="blk1")
		with self.assertRaises(Exception):
			training_ai.accept_ai_suggestions(LESSON, "checkpoint", [item])

	def test_an_unusable_suggestion_is_refused_rather_than_skipped(self):
		"""Silently dropping one of five accepted questions is the same class of
		bug as an autosave allowlist eating a field: it reports success."""
		broken = {"question": "Which valve?", "type": "Single Choice"}
		with self.assertRaises(Exception):
			self._accept_quiz(broken, _good_question())
		self.assertEqual(STATE["saved"], [])

	def test_an_author_edited_stem_is_still_accepted(self):
		"""Grounding is a drafting heuristic. Re-running it at acceptance would
		reject the author's own rewrite in the model's name."""
		item = _good_question("Rewritten by hand into something else entirely.")
		self.assertEqual(self._accept_quiz(item)["created"], 1)

	def test_a_json_string_payload_is_accepted(self):
		"""The builder posts through frappe.call, so the list arrives serialized."""
		result = training_ai.accept_ai_suggestions(LESSON, "quiz", json.dumps([_good_question()]))
		self.assertEqual(result["created"], 1)

	def test_an_empty_payload_is_refused(self):
		with self.assertRaises(Exception):
			training_ai.accept_ai_suggestions(LESSON, "quiz", [])

	def test_an_unknown_kind_is_refused(self):
		with self.assertRaises(Exception):
			training_ai.accept_ai_suggestions(LESSON, "flashcard", [_good_question()])

	def test_nothing_can_be_accepted_into_a_published_version(self):
		"""Training Checkpoint is a separate top-level document with no guard of
		its own — without this, acceptance would append a question to a live
		course that learners are already being graded against."""
		STATE["version_docstatus"] = 1
		with self.assertRaises(Exception) as caught:
			self._accept_quiz()
		self.assertIn("frozen", str(caught.exception))
		self.assertEqual(STATE["inserted"], [])

	def test_acceptance_still_works_once_the_ai_switch_is_off(self):
		"""An author holding suggestions when a manager flips the switch must
		still be able to keep or discard them."""
		STATE["settings"]["ai_assist_enabled"] = 0
		self.assertEqual(self._accept_quiz()["created"], 1)


if __name__ == "__main__":
	unittest.main()
