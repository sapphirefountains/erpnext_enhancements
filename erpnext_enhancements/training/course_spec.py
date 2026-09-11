# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Course Spec — the one shape an AI-authored training course is described in.

A *spec* is a plain dict: a course, an optional list of chapters, and a list of
lessons, each with content blocks and an optional quiz. It is the contract between
the thing that *proposes* a course (a model, via ``assistant_tools.draft_course_spec``,
or a human editing JSON) and the thing that *builds* one
(:func:`erpnext_enhancements.api.training_course_authoring.author_course_from_spec`).

Two rules make the split safe, and they are the whole reason this module exists:

* **The model never writes records; it fills a spec, and the builder is
  deterministic.** So every course the builder produces has the same shape and
  flow, whatever the model wrote — the same code the manual Training Builder runs
  (``create_draft_version`` + ``save_draft_version``) does the writing, and this
  spec is only ever mapped onto that. A malformed spec fails *here*, loudly, before
  a single row is created.
* **A spec can only describe what a model can honestly author.** Blocks that need
  an uploaded image, a PDF, or a registered video asset (``Image`` / ``PDF`` /
  ``Video`` / ``Image Hotspots`` / ``Downloadable File`` / ``External Embed``) are
  **not** in :data:`SPEC_BLOCK_TYPES`: a model cannot supply the media, and a block
  that renders a blank space is worse than one that was never offered. What is left
  is text, callouts, dividers and the three interactive list types.

The option bounds and the choice types are *imported* from the question controller
that enforces them, never restated — the same discipline
``api/training_ai.py`` keeps, and for the same reason: a spec that validates here
must also survive ``insert()``.

Quiz answer keys ride inside the spec (a question names its correct options), and
that is fine *because the spec never reaches a browser*: it is server-side input to
the builder, and the builder writes the key to ``answer_key_json`` at permlevel 1
via ``_split_lesson`` like any hand-authored question. Every question the builder
creates from a spec is stamped ``ai_generated`` with **no** reviewer, so
``_unreviewed_ai_questions`` blocks publication until a human signs it off.

Indentation is tabs, matching the ``training/`` package.
"""

import json

import frappe
from frappe import _

# Option bounds come from the controller that enforces them, so a spec that
# validates here also survives ``insert()`` — the same discipline
# ``api/training_ai.py`` keeps. Imported defensively because this module is pulled
# in by the lightweight FAC tools, whose contract test stubs ``frappe`` without
# ``frappe.model`` (so the controller cannot import there); the fallback mirrors
# the controller's own MIN/MAX (2..6) and is only ever reached under that stub.
try:
	from erpnext_enhancements.training.doctype.training_question.training_question import (
		MAX_OPTIONS,
		MIN_OPTIONS,
	)
except Exception:  # pragma: no cover - only under a frappe-less import stub
	MIN_OPTIONS, MAX_OPTIONS = 2, 6

# Hard caps. A model asked for "a course" will otherwise cheerfully return forty
# lessons; these bound the prompt, the generation cost, and the size of one
# gated write. They are enforced in the schema AND in :func:`validate_course_spec`,
# because the schema guides a model but does not bind a human editing the JSON.
MAX_LESSONS = 20
MAX_BLOCKS_PER_LESSON = 20
MAX_QUESTIONS_PER_LESSON = 20
MAX_CHAPTERS = 12
MAX_CHECKLIST_ITEMS = 30
MAX_FLASHCARDS = 30
MAX_ACCORDION_PANELS = 20

#: Choice questions only. Short Answer is deliberately absent — it is graded by
#: string match, and a model-invented list of accepted spellings fails learners
#: who were right (same call ``api/training_ai.py`` makes).
CHOICE_TYPES = ("Single Choice", "Multiple Choice", "True-False")

#: The block types a model may author: no uploaded media, no registered video.
#: These map 1:1 to Training Content Block ``block_type`` options.
SPEC_BLOCK_TYPES = ("Rich Text", "Callout", "Divider", "Checklist", "Flashcards", "Accordion")

#: Callout tones, matching Training Content Block ``callout_tone`` (stored
#: title-cased on the block; the learner payload lower-cases them).
CALLOUT_TONES = ("Info", "Tip", "Warning", "Danger")

#: Course-level knobs a spec may set. Everything else on Training Course
#: (gates, recertification, sign-off, certificate) is left to the author to tune
#: on the draft — a model should not be silently deciding a passing score.
COURSE_WEIGHTS = ("Optional", "Required")
COURSE_AUDIENCES = ("Internal Staff", "Customers", "Both")


# --------------------------------------------------------------------- schema

# A JSON Schema (standard, lower-case types) used three ways: as the
# ``inputSchema`` of the ``author_training_course`` MCP tool, as the payload of the
# ``get_course_spec_schema`` read tool, and as the generation target described to
# the model in ``draft_course_spec``. Kept declarative so all three stay in step.
COURSE_SPEC_SCHEMA = {
	"type": "object",
	"required": ["course", "lessons"],
	"properties": {
		"course": {
			"type": "object",
			"required": ["course_title"],
			"properties": {
				"course_title": {"type": "string", "description": "What the course is called, as learners see it."},
				"summary": {"type": "string", "description": "One or two sentences on what someone can do after taking it."},
				"category": {"type": "string", "description": "An existing Training Category name, or omit."},
				"weight": {
					"type": "string",
					"enum": list(COURSE_WEIGHTS),
					"description": "Optional = self-serve library; Required = assigned with a due date. Defaults to Optional.",
				},
				"audience": {
					"type": "string",
					"enum": list(COURSE_AUDIENCES),
					"description": "Who may see it. Defaults to Internal Staff. Never put internal compliance content on a Customers course.",
				},
			},
		},
		"chapters": {
			"type": "array",
			"description": "Optional grouping of lessons. A lesson's `chapter` is a 0-based index into this list.",
			"items": {
				"type": "object",
				"required": ["title"],
				"properties": {
					"title": {"type": "string"},
					"description": {"type": "string"},
				},
			},
		},
		"lessons": {
			"type": "array",
			"minItems": 1,
			"description": "The lessons, in order.",
			"items": {
				"type": "object",
				"required": ["lesson_title", "blocks"],
				"properties": {
					"lesson_title": {"type": "string"},
					"chapter": {"type": "integer", "description": "0-based index into `chapters`, or omit for an ungrouped lesson."},
					"summary": {"type": "string"},
					"estimated_minutes": {"type": "integer", "description": "Rough time to complete this lesson."},
					"blocks": {
						"type": "array",
						"description": "The lesson content, in order.",
						"items": {
							"type": "object",
							"required": ["block_type"],
							"properties": {
								"block_type": {"type": "string", "enum": list(SPEC_BLOCK_TYPES)},
								"heading": {"type": "string", "description": "Optional heading shown above the block."},
								"content": {"type": "string", "description": "Body text/HTML. Required for Rich Text and Callout."},
								"callout_tone": {"type": "string", "enum": list(CALLOUT_TONES), "description": "Callout only."},
								"items": {
									"type": "array",
									"items": {"type": "string"},
									"description": "Checklist only: the steps to tick off.",
								},
								"cards": {
									"type": "array",
									"items": {
										"type": "object",
										"required": ["front", "back"],
										"properties": {"front": {"type": "string"}, "back": {"type": "string"}},
									},
									"description": "Flashcards only.",
								},
								"panels": {
									"type": "array",
									"items": {
										"type": "object",
										"required": ["title", "body"],
										"properties": {"title": {"type": "string"}, "body": {"type": "string"}},
									},
									"description": "Accordion only: title + body (body may contain simple HTML).",
								},
							},
						},
					},
					"quiz": {
						"type": "object",
						"description": "An optional end-of-lesson quiz.",
						"properties": {
							"pass_score": {"type": "integer", "description": "Percent needed to pass. 0 inherits the course setting."},
							"questions_to_ask": {"type": "integer", "description": "How many to draw from the pool. 0 = all."},
							"questions": {
								"type": "array",
								"items": {
									"type": "object",
									"required": ["question", "type", "options"],
									"properties": {
										"question": {"type": "string", "description": "The question stem."},
										"type": {"type": "string", "enum": list(CHOICE_TYPES)},
										"explanation": {"type": "string", "description": "Shown after answering — why the answer is what it is."},
										"options": {
											"type": "array",
											"minItems": MIN_OPTIONS,
											"maxItems": MAX_OPTIONS,
											"items": {
												"type": "object",
												"required": ["text", "is_correct"],
												"properties": {
													"text": {"type": "string"},
													"is_correct": {"type": "boolean"},
												},
											},
										},
									},
								},
							},
						},
					},
				},
			},
		},
	},
}


# ------------------------------------------------------------------ coercion


def _cint(value):
	"""``frappe.utils.cint`` for our uses, but import-free so this module still
	loads under the lightweight FAC-tool contract stub (which has no
	``frappe.utils.cint``). Coerces to int, 0 on anything unparseable."""
	try:
		return int(value)
	except (ValueError, TypeError):
		try:
			return int(float(value))
		except (ValueError, TypeError):
			return 0


def _flag(value):
	"""0/1 from whatever a model or a JSON editor put in ``is_correct``.

	Not ``cint`` — ``cint("true")`` raises, and ``"true"`` for ``1`` is a slip,
	not a reason to reject the whole question (same as ``training_ai._flag``)."""
	if isinstance(value, str):
		return 1 if value.strip().lower() in ("1", "true", "yes", "y") else 0
	if isinstance(value, bool):
		return 1 if value else 0
	return 1 if value else 0


def _text(value):
	"""A trimmed string from any scalar; ``""`` for anything else."""
	if isinstance(value, str):
		return value.strip()
	if isinstance(value, int | float) and not isinstance(value, bool):
		return str(value)
	return ""


def _require(condition, message):
	if not condition:
		frappe.throw(message, frappe.ValidationError)


def _validate_options(raw, question_type, where):
	"""The exact option contract ``TrainingQuestion`` enforces, so a spec that
	passes here also survives ``insert()``. Returns a clean option list."""
	_require(
		isinstance(raw, list),
		_("{0}: options must be a list.").format(where),
	)
	options = []
	seen = set()
	for row in raw:
		_require(isinstance(row, dict), _("{0}: each option must be an object.").format(where))
		text = _text(row.get("text"))
		_require(bool(text), _("{0}: an option has no text.").format(where))
		_require(text.lower() not in seen, _("{0}: two options read the same ({1}).").format(where, text))
		seen.add(text.lower())
		options.append({"text": text, "is_correct": _flag(row.get("is_correct"))})

	_require(
		MIN_OPTIONS <= len(options) <= MAX_OPTIONS,
		_("{0}: a question needs between {1} and {2} options.").format(where, MIN_OPTIONS, MAX_OPTIONS),
	)
	correct = [o for o in options if o["is_correct"]]
	_require(correct, _("{0}: no option is marked correct.").format(where))
	_require(len(correct) != len(options), _("{0}: every option is marked correct.").format(where))
	if question_type in ("Single Choice", "True-False"):
		_require(
			len(correct) == 1,
			_("{0}: a {1} question must have exactly one correct option.").format(where, question_type),
		)
	if question_type == "True-False":
		_require(
			sorted(o["text"].lower() for o in options) == ["false", "true"],
			_("{0}: a True-False question's two options must be exactly True and False.").format(where),
		)
	return options


def _validate_question(raw, where):
	_require(isinstance(raw, dict), _("{0}: a question must be an object.").format(where))
	stem = _text(raw.get("question"))
	_require(bool(stem), _("{0}: a question has no text.").format(where))
	qtype = _text(raw.get("type")) or "Single Choice"
	_require(qtype in CHOICE_TYPES, _("{0}: {1} is not a supported question type.").format(where, qtype))
	options = _validate_options(raw.get("options"), qtype, where)
	return {
		"question": stem,
		"type": qtype,
		"explanation": _text(raw.get("explanation")),
		"options": options,
	}


def _validate_block(raw, where):
	_require(isinstance(raw, dict), _("{0}: a block must be an object.").format(where))
	block_type = _text(raw.get("block_type"))
	_require(
		block_type in SPEC_BLOCK_TYPES,
		_("{0}: {1} is not a block type a course can be authored with. Use one of: {2}.").format(
			where, block_type or _("(blank)"), ", ".join(SPEC_BLOCK_TYPES)
		),
	)
	block = {"block_type": block_type, "heading": _text(raw.get("heading"))}

	if block_type in ("Rich Text", "Callout"):
		content = raw.get("content")
		content = content.strip() if isinstance(content, str) else ""
		_require(bool(content), _("{0}: a {1} block has no text.").format(where, block_type))
		block["content"] = content
		if block_type == "Callout":
			tone = _text(raw.get("callout_tone")).title()
			if tone in CALLOUT_TONES:
				block["callout_tone"] = tone
	elif block_type == "Divider":
		pass
	elif block_type == "Checklist":
		items = [_text(i) for i in (raw.get("items") or []) if _text(i)]
		_require(bool(items), _("{0}: a Checklist block has no items.").format(where))
		block["items"] = items[:MAX_CHECKLIST_ITEMS]
	elif block_type == "Flashcards":
		cards = [
			{"front": _text(c.get("front")), "back": _text(c.get("back"))}
			for c in (raw.get("cards") or [])
			if isinstance(c, dict) and _text(c.get("front")) and _text(c.get("back"))
		]
		_require(bool(cards), _("{0}: a Flashcards block has no complete cards.").format(where))
		block["cards"] = cards[:MAX_FLASHCARDS]
	elif block_type == "Accordion":
		panels = [
			{"title": _text(p.get("title")), "body": _text(p.get("body"))}
			for p in (raw.get("panels") or [])
			if isinstance(p, dict) and _text(p.get("title")) and _text(p.get("body"))
		]
		_require(bool(panels), _("{0}: an Accordion block has no complete panels.").format(where))
		block["panels"] = panels[:MAX_ACCORDION_PANELS]

	return block


def _validate_lesson(raw, index, chapter_count):
	where = _("Lesson {0}").format(index + 1)
	_require(isinstance(raw, dict), _("{0} is not an object.").format(where))
	title = _text(raw.get("lesson_title"))
	_require(bool(title), _("{0} has no title.").format(where))

	blocks_raw = raw.get("blocks") or []
	_require(isinstance(blocks_raw, list), _("{0}: blocks must be a list.").format(where))
	_require(bool(blocks_raw), _("{0} has no content blocks.").format(where))
	_require(
		len(blocks_raw) <= MAX_BLOCKS_PER_LESSON,
		_("{0} has more than {1} blocks.").format(where, MAX_BLOCKS_PER_LESSON),
	)
	blocks = [_validate_block(b, _("{0}, block {1}").format(where, i + 1)) for i, b in enumerate(blocks_raw)]

	lesson = {
		"lesson_title": title,
		"summary": _text(raw.get("summary")),
		"estimated_minutes": max(0, _cint(raw.get("estimated_minutes"))),
		"blocks": blocks,
	}

	chapter = raw.get("chapter")
	if chapter is not None and not isinstance(chapter, bool):
		chapter = _cint(chapter)
		_require(
			0 <= chapter < chapter_count,
			_("{0} names chapter {1}, but there are only {2} chapter(s).").format(where, chapter, chapter_count),
		)
		lesson["chapter"] = chapter

	quiz_raw = raw.get("quiz")
	if isinstance(quiz_raw, dict) and quiz_raw.get("questions"):
		questions_raw = quiz_raw.get("questions") or []
		_require(
			len(questions_raw) <= MAX_QUESTIONS_PER_LESSON,
			_("{0} has more than {1} quiz questions.").format(where, MAX_QUESTIONS_PER_LESSON),
		)
		questions = [
			_validate_question(q, _("{0}, question {1}").format(where, i + 1))
			for i, q in enumerate(questions_raw)
		]
		lesson["quiz"] = {
			"pass_score": max(0, min(100, _cint(quiz_raw.get("pass_score")))),
			"questions_to_ask": max(0, _cint(quiz_raw.get("questions_to_ask"))),
			"questions": questions,
		}

	return lesson


def validate_course_spec(raw):
	"""Return a clean, canonical spec, or raise ``ValidationError`` saying why.

	Strict on purpose: this runs at the write boundary
	(``author_course_from_spec``), where a human has already approved the action,
	so a malformed spec must fail with a message that names the lesson and block —
	not be silently patched into something the author did not describe.
	"""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except (ValueError, TypeError):
			frappe.throw(_("The course spec is not valid JSON."), frappe.ValidationError)
	_require(isinstance(raw, dict), _("A course spec must be an object."))

	course_raw = raw.get("course")
	_require(isinstance(course_raw, dict), _("The spec has no `course` object."))
	course_title = _text(course_raw.get("course_title"))
	_require(bool(course_title), _("The course has no title."))

	weight = _text(course_raw.get("weight")).title() or "Optional"
	_require(weight in COURSE_WEIGHTS, _("`weight` must be one of: {0}.").format(", ".join(COURSE_WEIGHTS)))
	audience = _text(course_raw.get("audience")).title() or "Internal Staff"
	# .title() turns "Internal Staff" fine but would mangle nothing here; guard anyway.
	audience = {a.lower(): a for a in COURSE_AUDIENCES}.get(audience.lower(), "Internal Staff")

	course = {
		"course_title": course_title,
		"summary": _text(course_raw.get("summary")),
		"category": _text(course_raw.get("category")),
		"weight": weight,
		"audience": audience,
	}

	chapters_raw = raw.get("chapters") or []
	_require(isinstance(chapters_raw, list), _("`chapters` must be a list."))
	_require(len(chapters_raw) <= MAX_CHAPTERS, _("A course may have at most {0} chapters.").format(MAX_CHAPTERS))
	chapters = []
	for i, ch in enumerate(chapters_raw):
		_require(isinstance(ch, dict), _("Chapter {0} is not an object.").format(i + 1))
		ctitle = _text(ch.get("title"))
		_require(bool(ctitle), _("Chapter {0} has no title.").format(i + 1))
		chapters.append({"title": ctitle, "description": _text(ch.get("description"))})

	lessons_raw = raw.get("lessons") or []
	_require(isinstance(lessons_raw, list) and lessons_raw, _("The course has no lessons."))
	_require(len(lessons_raw) <= MAX_LESSONS, _("A course may have at most {0} lessons.").format(MAX_LESSONS))
	lessons = [_validate_lesson(lsn, i, len(chapters)) for i, lsn in enumerate(lessons_raw)]

	return {"course": course, "chapters": chapters, "lessons": lessons}


def spec_summary(spec):
	"""A compact ``{course_title, chapters, lessons, blocks, questions}`` tally for
	a confirmation card or a preview. Assumes a validated spec."""
	lessons = spec.get("lessons") or []
	blocks = sum(len(lsn.get("blocks") or []) for lsn in lessons)
	questions = sum(len((lsn.get("quiz") or {}).get("questions") or []) for lsn in lessons)
	return {
		"course_title": (spec.get("course") or {}).get("course_title") or "",
		"chapters": len(spec.get("chapters") or []),
		"lessons": len(lessons),
		"blocks": blocks,
		"questions": questions,
	}
