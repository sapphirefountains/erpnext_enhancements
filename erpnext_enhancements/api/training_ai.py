# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""AI drafting for the training builder — suggestions, never content.

Answers one question: *how does a model help an author write a quiz without ever
becoming the thing that decided whether somebody passed a compliance course?*

The answer is a hard split, and it is the whole design:

* :func:`draft_quiz_questions` and :func:`suggest_checkpoints` **persist nothing**.
  They return transient suggestions. Neither of them may so much as name
  ``ai_reviewed_by`` — a draft that arrives pre-reviewed walks straight past
  ``training_author._unreviewed_ai_questions``, which is the only thing standing
  between a model's guess and a published answer key.
* :func:`accept_ai_suggestions` is where rows are written, and that call **is** the
  human review. It stamps ``ai_generated``, ``ai_reviewed_by`` and the grounding
  quote together, so a disputed result traces to a named person rather than to a
  model.

Three things here look defensive and are load-bearing:

* **Checkpoint drafting refuses without a timed transcript.** Asked for a
  timestamp it cannot derive, a model produces a confident, plausible, wrong one —
  and a checkpoint at the wrong second is indistinguishable from a badly written
  one until a learner complains. Timings come from WebVTT cues or the endpoint
  throws. That single constraint is the entire integrity story for the feature.
* **The model's output is parsed strictly.** One JSON object, or zero suggestions
  and a message saying so. Prose, markdown fences and half-built objects are not
  salvaged: a tolerant parser is how a question with two correct options and no
  stem reaches an author who is skim-reading. Malformed *items* are dropped
  individually; a malformed *response* yields nothing.
* **Grounding is checked cheaply and on purpose.** A stem sharing no content word
  with the lesson was not drawn from the lesson, whatever the model claims in its
  ``grounding_quote``. Dropping those costs an author a re-roll; keeping one costs
  them a question about material the course never taught.

The option bounds and the checkpoint spacing rules are *imported* from the
doctype controllers that enforce them rather than restated, so a suggestion that
survives validation here also survives ``insert()``. Restating them is how the two
drift and acceptance starts throwing on suggestions this module said were fine.

Both drafting calls are synchronous. Vertex allows 120s and these prompts return
in a few, and the builder has no polling channel to hand a job id to — enqueuing
would mean inventing one seam to save a wait the author is already sitting through.

Indentation is 4 spaces, matching the majority of ``api/``.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.training.doctype.training_checkpoint.training_checkpoint import (
    MIN_GAP_SECONDS,
    MIN_TAIL_SECONDS,
)
from erpnext_enhancements.training.doctype.training_question.training_question import (
    MAX_OPTIONS,
    MIN_OPTIONS,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

AUTHOR_ROLES = ("Training Author", "Training Manager", "System Manager")

# Recorded on the AI Model Usage row so training drafting is accounted for
# separately from email/SMS drafting.
QUIZ_FEATURE = "training_quiz_draft"
CHECKPOINT_FEATURE = "training_checkpoint_draft"

# Checkpoints and quiz questions are both choice questions. Short Answer is
# deliberately absent: it is graded by string match, and a model-invented list of
# accepted spellings fails learners who were right.
CHOICE_TYPES = ("Single Choice", "Multiple Choice", "True-False")

#: Never draft more than this in one call, whatever the caller asks for.
MAX_SUGGESTIONS = 10

#: Below this much prose there is nothing to ground a question in, and the model
#: will happily invent a lesson that does not exist.
MIN_SOURCE_CHARS = 120

#: Prompts are bounded rather than trusted to be small — a forty-block lesson with
#: a full transcript runs to megabytes.
MAX_SOURCE_CHARS = 24000

_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_WS_RE = re.compile(r"\s+")

# `hh:mm:ss.mmm --> hh:mm:ss.mmm`, also accepting `mm:ss` and SRT's comma.
_CUE_RE = re.compile(
    r"(?P<start>(?:\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
    r"\s*-->\s*"
    r"(?P<end>(?:\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
)

# Words that carry no topic. Kept short on purpose: the grounding check only has
# to reject a stem about something else entirely, not score relevance.
_STOPWORDS = frozenset(
    """
    about after again against because been before being below between both cannot
    could does doing down during each from further have having here into itself
    more most only other over same should some such than that their them then
    there these they this those through under until very were what when where
    which while will with would your yours
    """.split()
)


# --------------------------------------------------------------------- guards


def _require_author():
    if not set(AUTHOR_ROLES) & set(frappe.get_roles()):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _require_ai_enabled():
    """Both the master switch and ``ai_assist_enabled`` — see ``is_enabled``.

    Only the *drafting* calls check this. Acceptance does not: an author who has
    suggestions on screen when a manager turns the switch off must still be able
    to keep or discard them, and refusing there would strand rows nowhere.
    """
    if not is_enabled("ai_assist_enabled"):
        frappe.throw(
            _("AI assistance is switched off. A Training Manager can enable it in Training Settings.")
        )


def _lesson(lesson):
    doc = frappe.get_doc("Training Lesson", lesson)
    doc.check_permission("write")
    return doc


def _editable_lesson(lesson):
    """The lesson, refused if its version is published.

    ``Training Lesson`` guards itself on save, but a ``Training Checkpoint`` is a
    separate top-level document with no such guard — without this, accepting a
    checkpoint suggestion would append a question to a live course.
    """
    doc = _lesson(lesson)
    if doc.course_version:
        docstatus = cint(
            frappe.db.get_value("Training Course Version", doc.course_version, "docstatus")
        )
        if docstatus != 0:
            frappe.throw(
                _("{0} is published and its content is frozen. Create a new draft version first.").format(
                    doc.course_version
                )
            )
    return doc


# ------------------------------------------------------------------ text tools


def _plain(value):
    """Tag-free, whitespace-collapsed text. Non-strings become ``""``.

    Local rather than ``frappe.utils.strip_html`` because the grounding check
    compares against exactly this normalisation on both sides; a helper that
    changed underneath it would quietly change which suggestions get dropped.
    """
    if not isinstance(value, str):
        return ""
    text = _TAG_RE.sub(" ", value)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    return _WS_RE.sub(" ", text).strip()


def _content_terms(text):
    """Lowercased topic words — four letters or more, stopwords removed."""
    return {
        word
        for word in _WORD_RE.findall((text or "").lower())
        if len(word) >= 4 and word not in _STOPWORDS
    }


def _is_grounded(stem, terms):
    """True when the stem shares at least one content word with the source.

    Cheap on purpose. It is not a relevance score; it catches the failure that
    actually happens — a model that answered from its own knowledge of the subject
    rather than from the lesson in front of it.
    """
    if not terms:
        return True
    return bool(_content_terms(stem) & terms)


def _lesson_text(doc):
    """Everything written in the lesson, as one block of prose."""
    parts = [doc.lesson_title or "", doc.summary or ""]
    for row in doc.blocks or []:
        parts.append(row.get("heading") or "")
        parts.append(_plain(row.get("content")))
        parts.append(row.get("caption") or "")
    parts.append(_transcript_prose(doc.transcript))
    return "\n".join(part for part in parts if part).strip()


def _transcript_prose(text):
    """Transcript text with any cue timings and numbering stripped out."""
    cues = _parse_vtt(text)
    if cues:
        return " ".join(cue["text"] for cue in cues if cue["text"]).strip()
    return _plain(text)


# ---------------------------------------------------------------------- WebVTT


def _timestamp_seconds(stamp):
    parts = stamp.replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def _parse_vtt(text):
    """Cues as ``[{"start", "end", "text"}]``; empty when there are no timings.

    Empty is the answer that matters. It is what makes
    :func:`suggest_checkpoints` refuse, and a transcript pasted as plain prose —
    the common case, and the one that reads as "there is a transcript, why won't
    it work" — has no timings and must land here rather than be guessed at.
    """
    if not isinstance(text, str) or "-->" not in text:
        return []

    cues = []
    current = None
    for line in text.splitlines():
        match = _CUE_RE.search(line)
        if match:
            start = _timestamp_seconds(match.group("start"))
            end = _timestamp_seconds(match.group("end"))
            if end < start:
                # A reversed cue is a corrupt file, not a cue running backwards.
                current = None
                continue
            current = {"start": start, "end": end, "text": ""}
            cues.append(current)
            continue

        stripped = line.strip()
        if not stripped or current is None:
            continue
        if stripped.upper().startswith("WEBVTT") or stripped.startswith("NOTE"):
            continue
        current["text"] = (current["text"] + " " + _plain(stripped)).strip()

    return cues


def _timed_transcript(lesson_doc, block):
    """Cues for this block's video, from the asset first, then the lesson.

    Asset first because the transcript belongs to the video and one video is
    deliberately reusable across courses; the lesson field is the fallback for a
    course whose author pasted WebVTT there instead.
    """
    asset = block.get("video_asset")
    if asset:
        cues = _parse_vtt(frappe.db.get_value("Training Video Asset", asset, "transcript"))
        if cues:
            return cues
    return _parse_vtt(lesson_doc.transcript)


# ------------------------------------------------------------- model plumbing

_QUIZ_SYSTEM = """You write end-of-lesson assessment questions for a workplace training course at a fountain design, build, service and rental company.

Rules you must follow:
- Answer with ONE JSON object and nothing else. No prose before or after it, and no markdown code fences. A reply that is not parseable JSON is discarded whole.
- Shape: {"suggestions": [{"question": str, "type": "Single Choice"|"Multiple Choice"|"True-False", "options": [{"text": str, "is_correct": 0|1}], "explanation": str, "grounding_quote": str}]}
- Every question must be answerable from the lesson text alone. Never use outside knowledge of the subject.
- "grounding_quote" must be a sentence copied verbatim from the lesson text that the question is drawn from.
- Two to six options. At least one correct, and never all of them. "Single Choice" and "True-False" take exactly one correct option, and a "True-False" question's two options must be spelled exactly True and False.
- Ask about what the lesson teaches, not about its wording, its formatting or its length.
- Write plain text. No HTML, no markdown."""

_CHECKPOINT_SYSTEM = """You write short questions that interrupt a training video partway through, to check the learner was paying attention.

Rules you must follow:
- Answer with ONE JSON object and nothing else. No prose before or after it, and no markdown code fences. A reply that is not parseable JSON is discarded whole.
- Shape: {"suggestions": [{"at_seconds": int, "question": str, "type": "Single Choice"|"Multiple Choice"|"True-False", "options": [{"text": str, "is_correct": 0|1}], "explanation": str, "grounding_quote": str}]}
- "grounding_quote" must be copied verbatim from one transcript cue, and "at_seconds" must be that cue's END time. Never invent a timestamp, and never place a question before the point where the answer is stated.
- Two to six options. At least one correct, and never all of them. "Single Choice" and "True-False" take exactly one correct option, and a "True-False" question's two options must be spelled exactly True and False.
- Keep each question to one sentence. The learner is mid-video.
- Write plain text. No HTML, no markdown."""


def _ask_model(prompt, system_instruction, feature):
    """One Vertex call, with a failure the author can act on.

    Imported lazily and per-call, matching ``api/communication.py``: the Vertex
    client pulls ``requests`` at import, and a drafting helper must not be the
    reason an unrelated import of this module fails.
    """
    try:
        from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai

        settings = frappe.get_doc("Triton Settings")
        text, _thoughts = generate_content_with_vertex_ai(
            prompt, system_instruction, settings, feature=feature
        )
    except Exception:
        frappe.log_error(f"Training AI drafting failed\n{frappe.get_traceback()}", "Training AI")
        frappe.throw(
            _("The AI service did not respond. Nothing has been changed — try again, or write the "
              "question by hand.")
        )
    return text


def _model_id():
    """The model name to stamp on an accepted draft.

    Swallows an import failure and returns ``""``: an author accepting a
    suggestion must not be blocked because the Vertex client is unavailable, and
    the provenance that matters (``ai_generated`` plus a named reviewer) does not
    depend on knowing which model produced it.
    """
    try:
        from erpnext_enhancements.api.gemini import MODEL_ID

        return MODEL_ID
    except Exception:
        return ""


# ------------------------------------------------------------- strict parsing


def _parse_suggestions(text):
    """``(items, message)`` from a model reply. Never raises, never salvages.

    ``items`` is empty and ``message`` is set for anything that is not one JSON
    payload of suggestion objects. Markdown fences are *not* stripped: the moment
    this function starts hunting for JSON inside prose it also starts finding it
    inside a half-written apology, and the caller cannot tell the difference.
    """
    raw = (text or "").strip()
    if not raw:
        return [], _("The AI service returned an empty response, so nothing was drafted.")

    try:
        data = json.loads(raw)
    except ValueError:
        return [], _(
            "The AI service replied with something other than the JSON that was asked for, so "
            "nothing was drafted. Try again."
        )

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("suggestions"), list):
        items = data["suggestions"]
    else:
        return [], _("The AI service replied in an unexpected shape, so nothing was drafted.")

    return [item for item in items if isinstance(item, dict)], ""


def _flag(value):
    """0/1 from whatever the model put in ``is_correct``.

    Deliberately not ``cint``: ``cint("true")`` raises, and a model writing
    ``"true"`` instead of ``1`` is a formatting slip, not a reason to lose the
    whole batch.
    """
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes", "y") else 0
    if isinstance(value, bool):
        return 1 if value else 0
    return 1 if value else 0


def _coerce_options(raw, question_type):
    """Validated option list, or ``None`` if the item cannot be salvaged.

    Enforces exactly what ``TrainingQuestion._validate_options`` and
    ``TrainingCheckpoint._validate_options`` enforce, using their own constants.
    Anything that passes here inserts; anything that fails here would have thrown
    at acceptance, when the author has already decided they want it.
    """
    if not isinstance(raw, list):
        return None

    options = []
    seen = set()
    for row in raw:
        if not isinstance(row, dict):
            return None
        text = _plain(row.get("text"))
        if not text or text.lower() in seen:
            return None
        seen.add(text.lower())
        options.append({"text": text, "is_correct": _flag(row.get("is_correct"))})

    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        return None

    correct = [option for option in options if option["is_correct"]]
    if not correct or len(correct) == len(options):
        return None
    if question_type in ("Single Choice", "True-False") and len(correct) != 1:
        return None
    if question_type == "True-False" and sorted(o["text"].lower() for o in options) != ["false", "true"]:
        return None
    return options


def _coerce_question(item, terms=None):
    """One validated suggestion, or ``None``.

    ``terms`` is the lesson's content vocabulary; pass ``None`` to skip the
    grounding check. Acceptance passes ``None`` on purpose — by then a person has
    read the stem and may well have rewritten it, and re-running a heuristic
    against an author's own edit would reject their work in the model's name.
    """
    stem = _plain(item.get("question"))
    if not stem:
        return None

    question_type = item.get("type") or "Single Choice"
    if question_type not in CHOICE_TYPES:
        return None

    options = _coerce_options(item.get("options"), question_type)
    if options is None:
        return None

    if terms is not None and not _is_grounded(stem, terms):
        return None

    return {
        "id": frappe.generate_hash(length=10),
        "question": stem,
        "type": question_type,
        "options": options,
        "explanation": _plain(item.get("explanation")),
        "grounding_quote": _plain(item.get("grounding_quote")),
    }


# ------------------------------------------------------------- quiz questions


@frappe.whitelist()
def draft_quiz_questions(lesson, count=5):
    """Suggest quiz questions for a lesson. Persists nothing.

    The author reviews these and calls :func:`accept_ai_suggestions` for the ones
    worth keeping. Nothing in this function may write a row or name a reviewer.
    """
    _require_author()
    _require_ai_enabled()

    doc = _lesson(lesson)
    count = max(1, min(cint(count) or 5, MAX_SUGGESTIONS))

    source = _lesson_text(doc)
    if len(source) < MIN_SOURCE_CHARS:
        frappe.throw(
            _("There is not enough written content in this lesson to draft questions from. Add the "
              "lesson text (or a transcript) first — a question drafted from a heading is a question "
              "about nothing.")
        )

    prompt = (
        f"Lesson title: {doc.lesson_title or ''}\n"
        f"Write {count} question(s).\n\n"
        f"Lesson text:\n{source[:MAX_SOURCE_CHARS]}"
    )
    items, message = _parse_suggestions(_ask_model(prompt, _QUIZ_SYSTEM, QUIZ_FEATURE))

    terms = _content_terms(source)
    suggestions = []
    for item in items:
        candidate = _coerce_question(item, terms)
        if candidate:
            suggestions.append(candidate)
        if len(suggestions) >= count:
            break

    if items and not suggestions and not message:
        message = _(
            "Nothing the AI drafted could be traced back to this lesson, so none of it is being "
            "suggested."
        )
    return {"suggestions": suggestions, "message": message}


# ----------------------------------------------------------------- checkpoints


def _block(lesson_doc, block_key):
    for row in lesson_doc.blocks or []:
        if (row.get("block_key") or "") == block_key:
            return row
    frappe.throw(_("No content block on {0} has the key {1}.").format(lesson_doc.name, block_key))


def _anchor_seconds(item, cues):
    """Where this suggestion actually belongs, or ``None`` to drop it.

    Prefers the end of the cue containing the grounding quote — a question asked
    before the answer has been said is worse than no question. Falls back to the
    model's own ``at_seconds`` only when a cue is genuinely running at that
    moment; a timestamp in a silent gap is one the model made up.
    """
    quote = _plain(item.get("grounding_quote")).lower()
    if quote:
        for cue in cues:
            if quote in cue["text"].lower():
                return int(round(cue["end"]))

    at = item.get("at_seconds")
    if isinstance(at, bool) or not isinstance(at, int | float):
        return None
    at = int(at)
    for cue in cues:
        if cue["start"] <= at <= cue["end"]:
            return at
    return None


@frappe.whitelist()
def suggest_checkpoints(lesson, block_key, count=3):
    """Suggest in-video checkpoints for one video block. Persists nothing.

    Refuses outright without a timed transcript. See the module docstring: this is
    the constraint the feature is built around, not a nicety.
    """
    _require_author()
    _require_ai_enabled()

    doc = _lesson(lesson)
    block = _block(doc, block_key)
    if block.get("block_type") != "Video":
        frappe.throw(
            _("Checkpoints only attach to Video blocks; this one is a {0} block.").format(
                block.get("block_type") or _("content")
            )
        )

    cues = _timed_transcript(doc, block)
    if not cues:
        frappe.throw(
            _("This video has no timed transcript, so a checkpoint's timing would be guesswork. "
              "Upload a .vtt subtitle file to the video asset (or paste WebVTT into the lesson "
              "transcript) and try again.")
        )

    count = max(1, min(cint(count) or 3, MAX_SUGGESTIONS))
    duration = cint(block.get("video_duration_seconds"))
    transcript = "\n".join(
        f"[{int(cue['start'])}-{int(cue['end'])}] {cue['text']}" for cue in cues if cue["text"]
    )

    prompt = (
        f"Lesson title: {doc.lesson_title or ''}\n"
        f"Video length: {duration or _('unknown')} seconds.\n"
        f"Write {count} checkpoint question(s).\n\n"
        f"Transcript cues, as [start-end] text:\n{transcript[:MAX_SOURCE_CHARS]}"
    )
    items, message = _parse_suggestions(_ask_model(prompt, _CHECKPOINT_SYSTEM, CHECKPOINT_FEATURE))

    terms = _content_terms(" ".join(cue["text"] for cue in cues))
    # Existing checkpoints count towards the spacing rule, or the first accepted
    # suggestion throws on a clash the author was never shown.
    taken = [
        cint(value)
        for value in frappe.get_all(
            "Training Checkpoint",
            filters={"lesson": doc.name, "block_key": block_key},
            pluck="at_seconds",
        )
    ]

    suggestions = []
    for item in items:
        candidate = _coerce_question(item, terms)
        if not candidate:
            continue
        at = _anchor_seconds(item, cues)
        if at is None or at < 1:
            continue
        if duration and at > duration - MIN_TAIL_SECONDS:
            continue
        if any(abs(at - other) < MIN_GAP_SECONDS for other in taken):
            continue

        taken.append(at)
        candidate["at_seconds"] = at
        # block_key rides on the suggestion because acceptance takes a flat list
        # and has no other way to know which video these belong to.
        candidate["block_key"] = block_key
        suggestions.append(candidate)
        if len(suggestions) >= count:
            break

    if items and not suggestions and not message:
        message = _(
            "None of the AI's checkpoints could be tied to a moment in the transcript, so none of "
            "them are being suggested."
        )
    return {"suggestions": suggestions, "message": message}


# ------------------------------------------------------------------ acceptance


@frappe.whitelist()
def accept_ai_suggestions(lesson, kind, suggestions):
    """Persist reviewed suggestions. **This call is the human review.**

    Everything written here is stamped ``ai_generated`` *and* ``ai_reviewed_by``
    together. The pair is what ``_unreviewed_ai_questions`` reads, so writing one
    without the other either blocks publication forever or lets an unreviewed
    answer key go live.

    Failures here are loud. A suggestion the author has accepted and that then
    turns out to be malformed must say so — silently dropping it is the same
    class of bug as an autosave allowlist eating a field.
    """
    _require_author()
    doc = _editable_lesson(lesson)

    rows = frappe.parse_json(suggestions) if isinstance(suggestions, str) else suggestions
    if not isinstance(rows, list) or not rows:
        frappe.throw(_("There is nothing to accept."))

    # Built once, here, and handed down. Both fields are set together or neither
    # is; a helper that could be called with only one of them is exactly the seam
    # this gate cannot afford.
    stamp = {"ai_generated": 1, "ai_reviewed_by": frappe.session.user}

    if kind == "quiz":
        return _accept_quiz(doc, rows, stamp)
    if kind == "checkpoint":
        return _accept_checkpoints(doc, rows, stamp)
    frappe.throw(_("{0} is not a kind of suggestion this can accept.").format(kind))


def _accept_quiz(lesson_doc, rows, stamp):
    names = []
    for index, row in enumerate(rows, start=1):
        item = _coerce_question(row if isinstance(row, dict) else {}, None)
        if not item:
            frappe.throw(
                _("Suggestion {0} is not a usable question — check it has a stem, two to six options, "
                  "and at least one correct answer that is not all of them.").format(index)
            )

        question = frappe.get_doc(
            {
                "doctype": "Training Question",
                "question_text": item["question"],
                "question_type": item["type"],
                "explanation": item["explanation"],
                "source_lesson": lesson_doc.name,
                "is_bank_question": 0,
                "options": [
                    {"option_text": option["text"], "is_correct": option["is_correct"]}
                    for option in item["options"]
                ],
                "ai_model": _model_id(),
                # The sentence from the lesson the draft came from. Kept so a
                # reviewer six months later can see what it was grounded in.
                "ai_source": item["grounding_quote"],
                **stamp,
            }
        )
        question.insert(ignore_permissions=True)
        names.append(question.name)
        # points is left unset so it falls through to the question's own value —
        # see _split_lesson's `cint(row.points) or cint(question.points) or 1`.
        lesson_doc.append("quiz_questions", {"question": question.name})

    if names:
        # has_quiz is deliberately not flipped. Whether a lesson has a quiz is an
        # authoring decision, and growing the pool never breaks _validate_quiz.
        lesson_doc.save(ignore_permissions=True)
    return {"created": len(names), "names": names}


def _accept_checkpoints(lesson_doc, rows, stamp):
    names = []
    for index, row in enumerate(rows, start=1):
        row = row if isinstance(row, dict) else {}
        item = _coerce_question(row, None)
        if not item:
            frappe.throw(
                _("Suggestion {0} is not a usable checkpoint — check it has a question, two to six "
                  "options, and at least one correct answer that is not all of them.").format(index)
            )

        block_key = (row.get("block_key") or "").strip()
        if not block_key:
            frappe.throw(_("Suggestion {0} does not say which video block it belongs to.").format(index))
        _block(lesson_doc, block_key)

        at = row.get("at_seconds")
        if isinstance(at, bool) or not isinstance(at, int | float) or int(at) < 1:
            frappe.throw(
                _("Suggestion {0} has no timestamp. A checkpoint without one cannot be placed.").format(index)
            )

        checkpoint = frappe.get_doc(
            {
                "doctype": "Training Checkpoint",
                "lesson": lesson_doc.name,
                "block_key": block_key,
                "at_seconds": int(at),
                "question_type": item["type"],
                "question_text": item["question"],
                "explanation": item["explanation"],
                "options": [
                    {"option_text": option["text"], "is_correct": option["is_correct"]}
                    for option in item["options"]
                ],
                # Training Checkpoint carries no ai_model / ai_source fields, so
                # the grounding quote is not stored here. The two provenance flags
                # the review gate reads are, which is what has to be true.
                **stamp,
            }
        )
        checkpoint.insert(ignore_permissions=True)
        names.append(checkpoint.name)

    return {"created": len(names), "names": names}
