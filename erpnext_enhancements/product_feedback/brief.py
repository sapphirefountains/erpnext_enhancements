# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Claude Code brief for one confirmed Enhancement Request (WI-079 slice 4, ADR 0016 §5).

Triton plans a request; Claude Code builds it. Until now the hand-off between the two was a
person reading the request, the Task board and the code, and retyping what they found into a
session. The brief is that hand-off written once, by ERPNext, from records it already holds:
the request's own words, the Tasks the reviewer confirmed, the duplicates the breakdown
flagged, and the code anchors slice 3 builds for the planner.

--------------------------------------------------------------------------------------
The shape
--------------------------------------------------------------------------------------

Markdown in this repository's work-item shape (``work-items/*.md``), because that is what a
coding session here already knows how to execute: a ``#`` title, then ``## Why``,
``## Scope``, ``## Acceptance criteria``, ``## Explicitly NOT in this work item`` (the heading
every WI and the ``work-item`` skill use), and last ``## Data``, one fenced ``json`` block with
the request id, the Task ids, the anchors, the design notes and the exact ``Refs:`` line. The
acceptance criteria end with that line on purpose: a release whose CHANGELOG section carries
it is what ``release_sync`` reads to move these Tasks to ``Pending Review``, so the brief
closes its own loop.

**The Refs line is printed bare, in a fenced block of its own**, under a note to paste it
without backticks or bold. ``release_sync.refs_in`` is strict on purpose: it ignores a line
that starts with a backtick or ``**``, because that is how the CHANGELOG writes its own
examples of the convention. A Refs line shown inside a code span, as this repository's house
style would write it, would be copied that way and then silently ignored.

**Closed leaves stay off it.** A leaf that is ``Completed``, ``Canceled``, ``Cancelled`` or
``Invoiced`` is listed under Scope, marked "(canceled)", "(completed)" and so on, and gets no
acceptance box and no place on the Refs line. ERPNext's overdue job turns this site's
``Canceled`` into ``Overdue``, so a closed leaf on a copied Refs line could be reopened as
shipped; ``task_writer.mark_shipped`` guards the same case from its side.

--------------------------------------------------------------------------------------
What it leaves out
--------------------------------------------------------------------------------------

**Who asked.** The requester's identity is not needed to build anything, and the brief goes
to a coding session. It is never read here, and the group Task's origin note, which names
the requester and the approver (``task_writer._origin_note``), is removed from its
description.

**The document.** ``context_docname`` is never read, and the page path goes through
:func:`code_anchors.parse_path`, the rule the Triton payload uses: no query string, no
record segment. ``/desk/item/PUMP-001?x=1`` is ``/desk/item``.

**Screenshots and the capture context file.** ADR 0016 §4: the brief names files; it never
embeds a screenshot.

--------------------------------------------------------------------------------------
Pure, and bounded
--------------------------------------------------------------------------------------

:func:`render_brief` touches no ``frappe`` state and this module imports no ``frappe``, so
``tests/test_feedback_brief.py`` runs it bench-free. ``api.feedback.claude_code_brief`` is
the thin wrapper that gathers the inputs. Task descriptions arrive as the Text Editor's
HTML and are reduced to plain text here, with the standard library, so the test covers it.

The whole brief stays at or under :data:`MAX_BRIEF_CHARS`. Over that it drops, in order, the
anchors' CHANGELOG excerpts, the anchors' README and the anchors' field lists, and says so
under ``## Data`` and in the anchors' own ``truncated`` list. Past those three it drops the
anchors, cuts each description (halving down to 120 characters), drops the Task descriptions
under Scope, and cuts each Task subject, and the note names every step it took. Those steps
fit the most one confirmed proposal can create (50 leaves under 50 groups, 140-character
subjects, with duplicates and anchors; tested). A last step makes the cap a guarantee for any
input at all: the brief is cut at a line boundary and ends by saying so, and that it is
incomplete.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import copy
import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

# `parse_path` is pure; `code_anchors` imports frappe at module level, so a bench-free test of
# this module needs only an empty `frappe` placeholder, never a stub with behavior.
from erpnext_enhancements.product_feedback.code_anchors import parse_path, serialized_size

#: The whole brief, Markdown and JSON together.
MAX_BRIEF_CHARS = 60000

#: The sections, in the work-item order and with the work items' own out-of-scope heading.
#: ``tests/test_feedback_brief.py`` pins it.
HEADINGS = (
	"## Why",
	"## Scope",
	"## Acceptance criteria",
	"## Explicitly NOT in this work item",
	"## Data",
)

#: The note under the acceptance item. The line itself follows in a fenced block of its own.
PASTE_NOTE = (
	"Paste this line into the release's CHANGELOG section exactly as it is, on its own line, "
	"without backticks or bold."
)

#: A leaf in one of these statuses is done with: marked in Scope, and kept off the acceptance
#: boxes and the Refs line. The value is the mark.
CLOSED_STATUSES = {
	"Completed": "completed",
	"Canceled": "canceled",
	"Cancelled": "canceled",
	"Invoiced": "invoiced",
}

#: What is dropped, in this order, while the brief is over the cap: ``(anchors key, what the
#: note says was dropped)``. The key is also what goes into the anchors' ``truncated`` list,
#: which already uses these names (``code_anchors.fit_to_budget``).
_CUTS = (
	("changelog", "the anchors' CHANGELOG excerpts"),
	("readme", "the anchors' module README"),
	("fields", "the anchors' field lists"),
)

#: Past the three named cuts and the anchors, each free-text block is cut to this many
#: characters, halving down to the floor.
_TEXT_CAPS = (8000, 4000, 2000, 1000, 500, 250, 120)

#: Past the descriptions, each Task subject (Scope, acceptance boxes, duplicates, Data) is cut
#: to this many characters. A subject can be 140.
_SUBJECT_CAPS = (80, 40)

#: The acceptance criterion every Bug gets.
BUG_CRITERION = "The steps to reproduce no longer reproduce it"

#: A value that contains markup is HTML from a Text Editor field; anything else is plain text
#: (a model-written Task description) and is left as it is. No whitespace between ``<`` and
#: the tag name, as ``HTMLParser`` itself requires (``starttagopen`` is ``<[a-zA-Z]``): text
#: such as ``check a < b`` is not markup, and Frappe stores it as typed.
_LOOKS_LIKE_HTML = re.compile(r"</?[A-Za-z!]")

#: Marks text that came from inside ``<pre>``, whose indentation is kept. A control character
#: no Text Editor value carries; removed before the text leaves :func:`_html_to_text`.
_PRE = "\x00"

#: The line ``task_writer._origin_note`` writes into every group Task: it names the requester
#: and the approver, and neither belongs in the brief.
_ORIGIN_NOTE = re.compile(r"^Raised from ER-\S+\s+—\s+an enhancement request filed by\b")

_BLOCK_TAGS = frozenset(
	{
		"p",
		"div",
		"ul",
		"ol",
		"table",
		"tr",
		"pre",
		"blockquote",
		"section",
		"article",
		"header",
		"footer",
		"hr",
		"h1",
		"h2",
		"h3",
		"h4",
		"h5",
		"h6",
	}
)


# ------------------------------------------------------------------------------ entry point


def render_brief(
	request: dict[str, Any],
	tasks: list[dict[str, Any]],
	duplicates: list[dict[str, Any]],
	anchors: dict[str, Any],
	design_notes: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
	"""``(markdown, data)`` for one request. Pure: no I/O, no ``frappe``.

	``request`` needs ``name``, ``title``, ``request_type``, ``impact``, ``description``,
	``steps_to_reproduce``, ``context_url`` and ``context_doctype``; any other key (a
	``requested_by``, a ``context_docname``) is ignored. ``tasks`` are the Tasks the request
	created, each with ``name``, ``subject``, ``description``, ``parent_task``, ``project``,
	``is_group`` and ``status``, plus ``parent_subject`` when the parent is not one of them.
	``duplicates`` are the breakdown's candidates (``task``, ``task_subject``, ``confidence``,
	``why``). ``anchors`` is ``code_anchors.build_anchors`` output or ``{}``; it is copied,
	never changed. ``data`` is the object the ``## Data`` block holds.
	"""
	anchors = copy.deepcopy(anchors) if isinstance(anchors, dict) else {}
	cuts: list[str] = []
	fit = _Fit()

	def render() -> tuple[str, dict[str, Any]]:
		return _render(request or {}, tasks or [], duplicates or [], anchors, design_notes, cuts, fit)

	def over() -> bool:
		return len(markdown) > MAX_BRIEF_CHARS

	markdown, data = render()
	for key, label in _CUTS:
		if not over():
			break
		if _drop(anchors, key):
			cuts.append(label)
			markdown, data = render()

	if over() and anchors:
		anchors = {}
		cuts.append("the anchors")
		markdown, data = render()

	for cap in _TEXT_CAPS:
		if not over():
			break
		fit.text_cap = cap
		_replace_cut(cuts, "each description", f"each description, cut to {cap:,} characters")
		markdown, data = render()

	if over():
		# Every other description stays, cut to the floor; the Task descriptions go entirely.
		fit.scope_descriptions = False
		_replace_cut(
			cuts,
			"each description",
			f"every other description, cut to {fit.text_cap or _TEXT_CAPS[-1]:,} characters",
		)
		cuts.append("the Task descriptions under Scope")
		markdown, data = render()

	for cap in _SUBJECT_CAPS:
		if not over():
			break
		fit.subject_cap = cap
		_replace_cut(cuts, "each Task subject", f"each Task subject, cut to {cap:,} characters")
		markdown, data = render()

	if over():
		markdown = _hard_cut(markdown, data["refs"])
	return markdown, data


class _Fit:
	"""How far :func:`render_brief` has had to shrink the brief so far."""

	def __init__(self) -> None:
		#: Characters each free-text block is cut to; ``None`` is whole.
		self.text_cap: int | None = None
		#: ``False`` once the Task descriptions under Scope are dropped.
		self.scope_descriptions = True
		#: Characters each Task subject is cut to; ``None`` is whole.
		self.subject_cap: int | None = None


def _replace_cut(cuts: list[str], prefix: str, label: str) -> None:
	"""Put ``label`` where the cut starting ``prefix`` was, or at the end."""
	for index, cut in enumerate(cuts):
		if cut.startswith(prefix):
			cuts[index] = label
			return
	cuts.append(label)


def _hard_cut(markdown: str, refs: str) -> str:
	"""The last resort: cut at a line boundary, close an open fence, and say so.

	Reached only past what one confirmed proposal can create: its ceiling, 50 leaves under 50
	groups with 140-character subjects, fits with the steps before this one (tested), and so do
	100 of each. It keeps the cap a guarantee rather than a hope for any input. The note under
	``## Data`` may be past the cut, so the ending carries the facts itself.
	"""

	def tail(missing: str) -> str:
		return (
			"\n\n[… The brief stops here. Even with the anchors dropped, every description cut or "
			f"dropped and every subject cut to {_SUBJECT_CAPS[-1]} characters, it would run past "
			f"{MAX_BRIEF_CHARS:,} characters, so the rest is missing, {missing} included. Take the "
			"Task list from the Enhancement Request itself.]\n"
		)

	longest = tail("the Refs line and the Data block")
	room = MAX_BRIEF_CHARS - len(longest) - len("\n```")
	cut = markdown.rfind("\n", 0, room)
	head = markdown[: cut if cut > 0 else room]
	if sum(1 for line in head.split("\n") if line.startswith("```")) % 2:
		head += "\n```"
	# The copy block is whole or absent: the cut falls on a line boundary, so a Refs line is never
	# half there. Say which, since the reader's next step differs.
	whole = f"\n```text\n{refs}\n```" in head
	return head + (tail("the Data block") if whole else longest)


def refs_line(request_name: str, task_names: list[str]) -> str:
	"""The line a release's CHANGELOG section carries to mark these Tasks shipped.

	``Refs: ER-2026-00012, TASK-2026-00345, TASK-2026-00346``. Only the Task ids move Tasks;
	the request id is a cross-check, so ``task_writer.mark_shipped`` skips a Task on the line
	that belongs to another request. Duplicates are dropped, order kept.
	"""
	ids = [str(name).strip() for name in [request_name, *(task_names or [])] if str(name or "").strip()]
	return "Refs: " + ", ".join(dict.fromkeys(ids))


# ------------------------------------------------------------------------------ sections


def _render(
	request: dict[str, Any],
	tasks: list[dict[str, Any]],
	duplicates: list[dict[str, Any]],
	anchors: dict[str, Any],
	design_notes: list[dict[str, Any]] | None,
	cuts: list[str],
	fit: _Fit,
) -> tuple[str, dict[str, Any]]:
	name = _one_line(request.get("name"))
	title = _one_line(request.get("title")) or "(untitled)"

	rows: list[dict[str, Any]] = []
	seen: set[str] = set()
	for task in tasks:
		if not isinstance(task, dict):
			continue
		row = _task_row(task, fit.subject_cap)
		if row["name"] and row["name"] not in seen:
			seen.add(row["name"])
			rows.append(row)
	groups, leaves = _split(rows)
	# A closed leaf is listed under Scope and nowhere else: no box, no place on the Refs line.
	open_leaves = [leaf for leaf in leaves if not leaf["closed"]]
	refs = refs_line(name, [leaf["name"] for leaf in open_leaves])

	lines = [f"# {name}: {title}" if name else f"# {title}", ""]
	lines += [
		f"A brief for a Claude Code session, written by ERPNext from Enhancement Request {name or '(unnamed)'} "
		"after its Tasks were confirmed. The Tasks are the scope. The Data block at the end carries "
		"their ids and the code this request points at.",
		"",
	]
	lines += _why(request, fit.text_cap)
	lines += _scope(rows, groups, leaves, fit)
	lines += _acceptance(request, open_leaves, refs)
	lines += _not_in_scope(duplicates, fit)

	data = {
		"request": name,
		"tasks": [
			{
				"name": row["name"],
				"subject": row["subject"],
				"parent": row["parent_task"],
				"project": row["project"],
			}
			for row in rows
		],
		"anchors": anchors,
		# Slice 5 (Design Review) fills this with the accepted design notes, by element code.
		# Until then the wrapper passes [] and the block says so by being empty.
		"design_notes": list(design_notes or []),
		"refs": refs,
	}
	lines += _data(data, cuts)
	return "\n".join(lines).rstrip() + "\n", data


def _why(request: dict[str, Any], text_cap: int | None) -> list[str]:
	lines = ["## Why", ""]
	facts = []
	if _one_line(request.get("request_type")):
		facts.append(f"- **Type:** {_one_line(request.get('request_type'))}")
	if _one_line(request.get("impact")):
		facts.append(f"- **Impact:** {_one_line(request.get('impact'))}")
	path, kind = parse_path(request.get("context_url"))
	if kind:
		facts.append(f"- **Page:** `{path}`")
	if _one_line(request.get("context_doctype")):
		facts.append(f"- **Doctype:** {_one_line(request.get('context_doctype'))}")
	if facts:
		lines += [*facts, ""]

	description = _plain(request.get("description"), text_cap)
	lines += ["**What was reported**", ""]
	lines += [*(_quote(description) if description else ["> (no description)"]), ""]

	steps = _plain(request.get("steps_to_reproduce"), text_cap)
	if steps:
		lines += ["**Steps to reproduce**", "", *_quote(steps), ""]
	return lines


def _scope(
	rows: list[dict[str, Any]],
	groups: list[dict[str, Any]],
	leaves: list[dict[str, Any]],
	fit: _Fit,
) -> list[str]:
	"""The Tasks, each leaf under its group Task when it has one.

	Buckets keep the order the Tasks arrived in. A leaf whose parent is not one of this
	request's Tasks (an existing epic the model nested it under) gets that parent as its
	heading, named by ``parent_subject``. A closed Task is marked, "(canceled)" and so on.
	"""
	text_cap = fit.text_cap

	def described(row: dict[str, Any]) -> str:
		return _plain(row["description"], text_cap) if fit.scope_descriptions else ""

	lines = ["## Scope", ""]
	group_names = {group["name"] for group in groups}
	by_name = {row["name"]: row for row in rows}
	order: list[str | None] = []
	buckets: dict[str | None, list[dict[str, Any]]] = {}

	for row in rows:
		if row["name"] in group_names:
			key: str | None = row["name"]
		else:
			key = row["parent_task"] or None
		if key not in buckets:
			buckets[key] = []
			order.append(key)
		if row["name"] not in group_names:
			buckets[key].append(row)

	# The ungrouped leaves first, with no heading over them.
	if None in buckets:
		order.remove(None)
		order.insert(0, None)

	for key in order:
		members = buckets[key]
		if key is not None:
			group = by_name.get(key)
			if group is not None:
				heading = f"### {_label(group)}"
				if group["closed"]:
					heading += f" ({group['closed']})"
				lines += [heading, ""]
				description = described(group)
				if description:
					lines += [description, ""]
			else:
				subject = next((m["parent_subject"] for m in members if m["parent_subject"]), "")
				heading = f"{key} — {subject}" if subject else key
				lines += [f"### {heading} (an existing Task, not one this request created)", ""]
		for leaf in members:
			bullet = f"- **{_label(leaf)}**"
			if leaf["project"]:
				bullet += f" ({leaf['project']})"
			if leaf["closed"]:
				bullet += f" ({leaf['closed']})"
			lines += [bullet, ""]
			description = described(leaf)
			if description:
				lines += [("  " + line) if line else "" for line in description.split("\n")] + [""]
	if not leaves:
		lines += ["No leaf Tasks: this request created only group Tasks.", ""]
	return lines


def _acceptance(request: dict[str, Any], open_leaves: list[dict[str, Any]], refs: str) -> list[str]:
	"""One box per open leaf, the Bug line, and the Refs line, bare in a block of its own.

	The block sits at column 0 so that what is copied out of it is the line and nothing else:
	no indentation, no backticks, no bold, each of which ``release_sync.refs_in`` refuses on
	purpose.
	"""
	lines = ["## Acceptance criteria", ""]
	lines += [f"- [ ] {_label(leaf)}" for leaf in open_leaves]
	if _one_line(request.get("request_type")) == "Bug":
		lines.append(f"- [ ] {BUG_CRITERION}")
	lines += [
		"- [ ] The CHANGELOG entry for the release carries the Refs line below, so the release sync "
		f"marks these Tasks shipped. {PASTE_NOTE}",
		"",
		"```text",
		refs,
		"```",
	]
	return [*lines, ""]


def _not_in_scope(duplicates: list[dict[str, Any]], fit: _Fit) -> list[str]:
	lines = [HEADINGS[3], ""]
	for row in duplicates:
		if not isinstance(row, dict):
			continue
		task = _one_line(row.get("task"))
		if not task:
			continue
		subject = _cut(_one_line(row.get("task_subject")), fit.subject_cap)
		confidence = _one_line(row.get("confidence"))
		why = _one_line(_plain(row.get("why"), fit.text_cap))
		text = f"- {task} — {subject}" if subject else f"- {task}"
		text += ". The breakdown flagged it as a possible duplicate"
		text += f" ({confidence} confidence)" if confidence else ""
		text += f": {why}" if why else "."
		lines.append(text)
	lines.append("- Anything not listed under Scope.")
	return [*lines, ""]


def _data(data: dict[str, Any], cuts: list[str]) -> list[str]:
	lines = ["## Data", ""]
	if cuts:
		lines += [
			f"Shortened to stay under {MAX_BRIEF_CHARS:,} characters. Dropped or cut: "
			+ "; ".join(cuts)
			+ ".",
			"",
		]
	return [*lines, "```json", _json_block(data), "```"]


def _json_block(data: dict[str, Any]) -> str:
	"""The data object, one top-level key per line and one Task per line, values compact.

	Indenting the anchors (up to 40,000 characters compact) would add a third again to the
	brief for nothing a reader needs; one line per key keeps it scannable. ``json.loads`` of
	the block gives ``data`` back exactly.
	"""
	parts = []
	for key, value in data.items():
		if key == "tasks" and value:
			inner = ",\n".join("  " + _compact(task) for task in value)
			parts.append(f" {json.dumps(key)}: [\n{inner}\n ]")
		else:
			parts.append(f" {json.dumps(key)}: {_compact(value)}")
	return "{\n" + ",\n".join(parts) + "\n}"


def _compact(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------------------ tasks


def _task_row(task: dict[str, Any], subject_cap: int | None = None) -> dict[str, Any]:
	return {
		"name": _one_line(task.get("name")),
		"subject": _cut(_one_line(task.get("subject")), subject_cap),
		"description": task.get("description") or "",
		"parent_task": _one_line(task.get("parent_task")),
		"parent_subject": _cut(_one_line(task.get("parent_subject")), subject_cap),
		"project": _one_line(task.get("project")),
		"is_group": bool(_int(task.get("is_group"))),
		# "" for a Task still in play, else the mark Scope shows: "canceled", "completed", ...
		"closed": CLOSED_STATUSES.get(_one_line(task.get("status")), ""),
	}


def _split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	"""``(groups, leaves)``. A group is a Task flagged ``is_group`` or the parent of another
	Task in the list; every other Task is a leaf and gets an acceptance criterion."""
	names = {row["name"] for row in rows}
	parents = {row["parent_task"] for row in rows if row["parent_task"] in names}
	groups = [row for row in rows if row["is_group"] or row["name"] in parents]
	group_names = {row["name"] for row in groups}
	return groups, [row for row in rows if row["name"] not in group_names]


def _label(row: dict[str, Any]) -> str:
	return f"{row['name']} — {row['subject']}" if row["subject"] else row["name"]


# ------------------------------------------------------------------------------ anchors


def _drop(anchors: dict[str, Any], key: str) -> bool:
	"""Remove one part of the anchors, and record it in their ``truncated`` list."""
	dropped = False
	if key == "fields":
		for doctype in anchors.get("doctypes") or []:
			if isinstance(doctype, dict) and doctype.pop("fields", None) is not None:
				dropped = True
	else:
		dropped = anchors.pop(key, None) is not None
	if dropped:
		truncated = anchors.get("truncated")
		if not isinstance(truncated, list):
			truncated = anchors["truncated"] = []
		if key not in truncated:
			truncated.append(key)
		if "chars" in anchors:
			# `chars` is part of what it measures, as in `code_anchors.fit_to_budget`.
			for _ in range(3):
				size = serialized_size(anchors)
				if anchors["chars"] == size:
					break
				anchors["chars"] = size
	return dropped


# ------------------------------------------------------------------------------ text


def _plain(value: Any, cap: int | None = None) -> str:
	"""A Text Editor value as plain text: markup gone, entities decoded, origin note removed.

	Plain text (no markup) is kept as it is, line breaks included. ``cap`` cuts at a line
	boundary and says it did.
	"""
	text = "" if value is None else str(value)
	text = text.replace("\r\n", "\n").replace("\r", "\n").replace(_PRE, "")
	if not text.strip():
		return ""
	if _LOOKS_LIKE_HTML.search(text):
		text = _html_to_text(text)
	lines = [line.rstrip() for line in text.split("\n")]
	lines = [line for line in lines if not _ORIGIN_NOTE.match(line.strip())]
	# Newlines only at the ends: the first line of a <pre> block keeps its indentation.
	text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
	if cap and len(text) > cap:
		cut = text.rfind("\n", 0, cap)
		text = text[: cut if cut > 0 else cap].rstrip() + "\n[… cut to fit the brief]"
	return text


class _TextExtractor(HTMLParser):
	"""Text from HTML: blocks become lines, list items become ``- `` bullets, scripts vanish."""

	def __init__(self) -> None:
		super().__init__(convert_charrefs=True)
		self.parts: list[str] = []
		self._skip = 0
		self._pre = 0

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if tag in ("script", "style"):
			self._skip += 1
		elif tag == "br":
			self.parts.append("\n")
		elif tag == "li":
			self.parts.append("\n- ")
		elif tag in _BLOCK_TAGS:
			if tag == "pre":
				self._pre += 1
			self.parts.append("\n\n")

	def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if tag in ("br", "hr"):
			self.parts.append("\n")

	def handle_endtag(self, tag: str) -> None:
		if tag in ("script", "style"):
			self._skip = max(0, self._skip - 1)
		elif tag in _BLOCK_TAGS:
			# Not `li`: the next item's own newline separates it, so a list stays tight.
			if tag == "pre":
				self._pre = max(0, self._pre - 1)
			self.parts.append("\n")

	def handle_data(self, data: str) -> None:
		if self._skip:
			return
		if self._pre:
			# Every line of it carries the mark, so :func:`_html_to_text` keeps its indentation.
			self.parts.append(_PRE + data.replace("\n", "\n" + _PRE))
		else:
			self.parts.append(re.sub(r"\s+", " ", data))


def _html_to_text(markup: str) -> str:
	"""Plain text, one line per block. Lines are trimmed, except those from a ``<pre>``, whose
	leading whitespace is the code's indentation."""
	try:
		parser = _TextExtractor()
		parser.feed(markup)
		parser.close()
		text = "".join(parser.parts)
	except Exception:
		# HTMLParser is lenient and should not raise; if it ever does, tags go by pattern.
		text = unescape(re.sub(r"<[^>]*>", " ", markup))
	lines = [line.replace(_PRE, "").rstrip() if _PRE in line else line.strip() for line in text.split("\n")]
	return "\n".join(lines)


def _quote(text: str) -> list[str]:
	"""The requester's words as a block quote, so any Markdown in them stays inside it."""
	return [f"> {line}" if line else ">" for line in text.split("\n")]


def _one_line(value: Any) -> str:
	return " ".join(str(value or "").split())


def _cut(text: str, cap: int | None) -> str:
	"""``text`` at most ``cap`` characters, ending in ``…`` when it was cut."""
	if not cap or len(text) <= cap:
		return text
	return text[: cap - 1].rstrip() + "…"


def _int(value: Any) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0
