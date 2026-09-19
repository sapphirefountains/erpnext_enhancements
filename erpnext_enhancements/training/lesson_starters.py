# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Lesson shapes — the same answer as :mod:`templates_gallery`, one level down.

``templates_gallery`` gives away the shape of a *course*. It only helps on the day
you create the course. Every lesson after the first one started from ``+ Lesson``,
which minted ``{lesson_title: "New lesson", blocks: []}`` — a blank page, which is
precisely the thing the starters exist to avoid. So a course built from a starter
was well-shaped for three lessons and blank for the next twenty.

A shape here is **a lesson as the canvas already holds one in memory**: lesson
fields the canvas may write, plus a `blocks` list in the canvas's own edit shape
(``content`` as HTML, ``data`` as a JSON string). The canvas splices it into
``this.lessons`` and its ordinary autosave writes it through
``training_author.save_draft_version`` — the same allowlisted, key-minting,
optimistic-locked path a hand-added block takes.

**That is the design constraint, and it is worth stating as a rule: this module is
not a scaffolder.** It writes nothing, it is never imported by a save path, and
there is no endpoint here that creates a lesson. It hands the browser a starting
value for a structure the browser already builds. A "create lesson from shape"
endpoint would be a second writer of lessons, and the block allowlist plus
``_apply_blocks``'s positional replace are subtle enough with one.

Two things constrain what a shape may contain, both learned the expensive way:

* **A shape may never set ``has_quiz``.** ``TrainingLesson._validate_quiz`` throws
  "marked as having a quiz but no questions are in the pool" the moment
  ``has_quiz`` is 1 with an empty pool — and the canvas cannot put a question in a
  pool without a Training Question to point at, because ``QUIZ_ROW_ALLOWED_FIELDS``
  is ``{question, points, is_required}`` and the body is refused by design. A shape
  that ticked the box would make its own lesson fail on the first autosave, four
  seconds after the author chose it, with a red dialog and no way forward. So a
  shape that wants a quiz says so in ``suggests_quiz`` and the canvas *nudges*; the
  author ticks the box after adding a question. The flag is advice, never a field.

* **Media blocks are allowed here and are not in a Course Spec.** A course starter
  is validated by ``validate_course_spec``, which excludes uploaded media, so
  ``templates_gallery`` writes "add a photo here" in a callout instead. A lesson
  shape goes nowhere near that validator — it goes through ``BLOCK_ALLOWED_FIELDS``,
  which carries ``image``/``file``/``video_asset`` — so a shape can place the empty
  Image or Video block itself and the author only has to attach the file. An empty
  media block saves fine (``_validate_blocks`` stopped throwing on emptiness in
  v1.386.0) and is refused at publish by ``incomplete_blocks``, which is exactly the
  behaviour wanted: the slot is visible, and it cannot ship unfilled.

The prose is instructions to the author — "replace this with…" — for the same
reason ``templates_gallery`` says so: filler is worse than an empty page, because
filler gets published.

**And instructions go only in fields a learner will obviously never see as
finished text.** That is a narrower set than it looks. ``caption`` is not in it:
``blocks.js`` renders it as ``<p class="tr-block-caption">`` under the media *and*
uses it as the ``<img alt>``, so "A photo of the actual machine, labelled" would
have shipped to every learner and to every screen reader on any lesson where the
author attached the photo and did not think to clear the caption — which is the
normal case, because the caption reads as help text in the editor. ``heading`` is
learner-facing too, so a media block here carries a real heading ("The unit") and
nothing else. The empty slot is its own instruction: the canvas renders "No file
attached yet." in place, and ``TrainingLesson.incomplete_blocks`` refuses the
publish while it is empty. A field that is displayed is not a place to leave a
note for yourself.
"""

import copy
import json

import frappe
from frappe import _

# Block types a shape may use. A strict subset of `api/training_author.TC_ADDABLE`
# / the Training Content Block Select, checked by the test suite rather than
# trusted: a typo here is a `_validate_selects` throw on the author's first
# autosave, which is the same unusable-editor failure the quiz flag above causes.
SHAPE_BLOCK_TYPES = frozenset(
	{
		"Rich Text",
		"Callout",
		"Checklist",
		"Flashcards",
		"Accordion",
		"Image",
		"Video",
		"PDF",
		"Divider",
	}
)

#: `callout_tone` verbatim from the doctype's Select, capitalised. Same list and
#: the same reason as `TC_CALLOUT_TONES` in training_canvas.js — a value outside
#: it does not degrade, it throws, and it takes the whole lesson autosave with it.
SHAPE_CALLOUT_TONES = frozenset({"Info", "Tip", "Warning", "Danger"})

SHAPES = {
	"blank": {
		"label": "Blank",
		"blurb": "An empty lesson. Start from nothing.",
		"minutes": 0,
		"suggests_quiz": False,
		"blocks": [],
	},
	"briefing": {
		"label": "Safety briefing",
		"blurb": "The tailgate talk. The hazard, why it matters here, the checks, " "and when to stop.",
		"minutes": 10,
		"suggests_quiz": True,
		"summary": "What to watch for and what to do, before we start.",
		"blocks": [
			{
				"block_type": "Callout",
				"callout_tone": "Danger",
				"heading": "The hazard",
				"content": "<p>Replace this with the one thing most likely to hurt somebody "
				"on this job, said plainly.</p>",
			},
			{
				"block_type": "Rich Text",
				"heading": "Why it matters here",
				"content": "<p>Two or three sentences. What has gone wrong before, or could. "
				"A real near-miss is worth a page of policy.</p>",
			},
			{
				"block_type": "Checklist",
				"heading": "Before you start",
				"items": [
					"Replace with the first check",
					"Replace with the second check",
					"Replace with the third check",
				],
			},
			{
				"block_type": "Callout",
				"callout_tone": "Warning",
				"heading": "Stop the job if",
				"content": "<p>The conditions under which nobody carries on. Name them, so "
				"that stopping is the expected thing rather than somebody's judgment call.</p>",
			},
		],
	},
	"procedure": {
		"label": "Step-by-step procedure",
		"blurb": "A written procedure turned into something with a record attached. "
		"Why it exists, the steps in order, and the document itself.",
		"minutes": 12,
		"suggests_quiz": True,
		"summary": "The procedure, and why each step is there.",
		"blocks": [
			{
				"block_type": "Rich Text",
				"heading": "Why we do it this way",
				"content": "<p>A procedure somebody understands is a procedure somebody "
				"follows when the situation does not quite match. Say what it is protecting "
				"against.</p>",
			},
			{
				"block_type": "Checklist",
				"heading": "The steps, in order",
				"items": [
					"Replace with step one",
					"Replace with step two",
					"Replace with step three",
				],
			},
			{
				"block_type": "Callout",
				"callout_tone": "Tip",
				"heading": "The step people skip",
				"content": "<p>Name it, and say what goes wrong when it is skipped. This is "
				"usually the only part anybody remembers.</p>",
			},
			{
				"block_type": "PDF",
				"heading": "The written procedure",
			},
		],
	},
	"video-lesson": {
		"label": "Video lesson",
		"blurb": "A video with something around it — what to look for going in, and "
		"what should have landed coming out.",
		"minutes": 15,
		"suggests_quiz": True,
		"summary": "Watch this, then check you caught the parts that matter.",
		"blocks": [
			{
				"block_type": "Rich Text",
				"heading": "What to watch for",
				"content": "<p>Two or three things to keep an eye on. A learner told what to "
				"look for watches differently from one told to watch.</p>",
			},
			{
				"block_type": "Video",
				"heading": "The video",
				"required_for_completion": 1,
			},
			{
				"block_type": "Checklist",
				"heading": "What should have landed",
				"items": [
					"Replace with the first takeaway",
					"Replace with the second takeaway",
				],
			},
		],
	},
	"equipment": {
		"label": "Equipment walkthrough",
		"blurb": "One pump, filter or controller — what it does, what it looks like, "
		"and what to do when it stops.",
		"minutes": 12,
		"suggests_quiz": False,
		"summary": "What it does, how to run it, and what to do when it stops.",
		"blocks": [
			{
				"block_type": "Rich Text",
				"heading": "In one paragraph",
				"content": "<p>What it is, where it sits in the system, and what breaks "
				"downstream if it stops.</p>",
			},
			{
				"block_type": "Image",
				"heading": "The unit",
			},
			{
				"block_type": "Accordion",
				"heading": "Common faults",
				"panels": [
					{
						"title": "Replace with a symptom",
						"body": "<p>What it usually means, and what to try.</p>",
					},
					{
						"title": "Replace with another symptom",
						"body": "<p>What it usually means, and what to try.</p>",
					},
				],
			},
			{
				"block_type": "Callout",
				"callout_tone": "Danger",
				"heading": "Do not attempt",
				"content": "<p>The things that need an electrician, the manufacturer, or "
				"somebody senior. Being explicit here is what stops improvisation.</p>",
			},
		],
	},
	"concept": {
		"label": "Concept and practice",
		"blurb": "Teach an idea, drill the vocabulary, then ask them to use it.",
		"minutes": 15,
		"suggests_quiz": True,
		"summary": "The idea, the words for it, and a chance to use both.",
		"blocks": [
			{
				"block_type": "Rich Text",
				"heading": "The idea",
				"content": "<p>Explain it once, plainly, with a worked example from a real "
				"job. Abstract first and concrete later is the wrong order for this.</p>",
			},
			{
				"block_type": "Flashcards",
				"heading": "Words you will hear",
				"cards": [
					{"front": "Replace with a term", "back": "What it means here"},
					{"front": "Replace with another", "back": "What it means here"},
				],
			},
			{
				"block_type": "Checklist",
				"heading": "Try it",
				"items": [
					"Replace with something to go and do",
					"Replace with something to go and check",
				],
			},
		],
	},
}

#: The order the gallery draws them in. Explicit rather than dict order, because
#: "Blank" belongs last on a chooser whose whole purpose is to talk you out of it.
SHAPE_ORDER = ("briefing", "procedure", "video-lesson", "equipment", "concept", "blank")


def _block_to_edit_shape(block):
	"""One shape block in the canvas's edit shape.

	The canvas stores an interactive block's payload as a **JSON string** in
	``data`` (that is what ``BLOCK_ALLOWED_FIELDS`` carries and what
	``_augment_interactive_block`` parses at publish), while a shape is written
	here as real Python lists because a JSON string embedded in a source file is
	unreadable and unreviewable. The conversion belongs in exactly one place, and
	this is it — ``block_key`` is deliberately **not** minted here: keys are
	client-minted ``blk-…`` values so that a shape fetched once and used twice
	cannot produce two blocks sharing a key, which is the one case
	``_apply_blocks`` rewrites silently.
	"""
	out = {key: value for key, value in block.items() if key not in ("items", "cards", "panels")}
	if "items" in block:
		out["data"] = json.dumps({"items": list(block["items"])})
	elif "cards" in block:
		out["data"] = json.dumps({"cards": [dict(card) for card in block["cards"]]})
	elif "panels" in block:
		out["data"] = json.dumps({"panels": [dict(panel) for panel in block["panels"]]})
	return out


def list_shapes():
	"""``[{key, label, blurb, minutes, suggests_quiz, summary, blocks}]``.

	The whole gallery including every block, in one payload: it is a few KB of
	static text, and the alternative — a list call followed by a fetch when the
	author picks one — puts a round trip between the click and the lesson for no
	benefit. The canvas caches it for the life of the page.
	"""
	out = []
	for key in SHAPE_ORDER:
		shape = SHAPES[key]
		out.append(
			{
				"key": key,
				"label": _(shape["label"]),
				"blurb": _(shape["blurb"]),
				"minutes": shape.get("minutes") or 0,
				"suggests_quiz": bool(shape.get("suggests_quiz")),
				"summary": shape.get("summary") or "",
				"block_count": len(shape.get("blocks") or []),
				"blocks": [_block_to_edit_shape(block) for block in shape.get("blocks") or []],
			}
		)
	return out


def shape_for(key):
	"""A **deep copy** of one shape, in the canvas's edit shape.

	Deep-copied for the same reason ``templates_gallery.spec_for`` is: ``SHAPES``
	is module-level state shared by every request in the worker, and handing out
	the live dict would let one author's edit leak into the next author's lesson.
	"""
	shape = SHAPES.get(key)
	if not shape:
		frappe.throw(_("{0} is not a lesson shape this app has.").format(key))
	shape = copy.deepcopy(shape)
	return {
		"key": key,
		"label": _(shape["label"]),
		"minutes": shape.get("minutes") or 0,
		"suggests_quiz": bool(shape.get("suggests_quiz")),
		"summary": shape.get("summary") or "",
		"blocks": [_block_to_edit_shape(block) for block in shape.get("blocks") or []],
	}
