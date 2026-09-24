# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shipped work reports back to the board (WI-079 slice 4, ADR 0016 §5). Hourly.

A release that closes feedback work carries one line anywhere in its CHANGELOG section::

    Refs: ER-2026-00012, TASK-2026-00345, TASK-2026-00346

Every hour this job reads the installed ``CHANGELOG.md``, finds the release sections it has
not processed yet, and hands each ``TASK-…`` id to :func:`task_writer.mark_shipped`, which
moves the Task to ``Pending Review`` with a comment naming the release. Only a Task id moves a
Task, so a release that ships part of a request names the Tasks it shipped and leaves the rest
alone. An ``ER-…`` id is a cross-check: when the line names one or more, a Task that belongs
to any other request is skipped, so a mistyped Task id cannot move another request's work.
Any other id on the line (a ``WI-079``) is ignored.

What counts as a Refs line is deliberately strict (:func:`refs_in`): ``Refs:`` at the start of
the line, after at most three spaces and an optional list marker, outside any code fence or
HTML comment. The CHANGELOG documents the convention with examples written in code spans and
fences, and ``tests/test_feedback_release_sync.py`` runs the parser over the real file so
that no example can ever move a real Task.

--------------------------------------------------------------------------------------
Only what is actually installed
--------------------------------------------------------------------------------------

The file on disk is not proof that its code is live. The deploy resets the checkout before
``bench migrate`` runs (``infra/cloudbuild-deploy.yaml``), and a migrate that aborts leaves
the new file behind with the old code half-installed: the v1.395.0 incident in
``CLAUDE.md``. So the ceiling is the version ``tabInstalled Application`` records for this
app. Frappe v16 rewrites that table near the end of every migrate that gets that far
("Updating installed applications...", ``frappe/migrate.py`` ``post_schema_updates``, which
calls ``frappe.get_single("Installed Applications").update_versions()``; line 198 on
``version-16``). A section above that version is never acted on: it waits for the migrate
that makes it true.

--------------------------------------------------------------------------------------
Why replay is harmless, and what the marker is for
--------------------------------------------------------------------------------------

``mark_shipped`` only moves a Task that this pipeline created, only from ``Open``,
``Working`` or ``Overdue``, and never to ``Completed``. Replaying the whole history
therefore changes nothing that has already moved. The marker,
``Product Feedback Settings.release_sync_last_version``, keeps each run to the new sections.

**It has no default, deliberately.** A new field on a Single never gets its default into the
existing row (``CLAUDE.md``), and here that gap is the behavior wanted: no row means "never
run", which means "process everything". It is read with ``frappe.db.get_single_value`` and
written with ``set_single_value``, never through ``tabSingles``.

The marker moves to the installed version only when every ``mark_shipped`` in the run came
back ``marked`` or ``skipped``. Otherwise it moves only as far as the last release the run
finished before its first failure (or stays, when that is none of them), one Error Log
records the run's failures (messages only, never frame locals), and the next hour retries
from there. A run makes at most :data:`MAX_MARKS_PER_RUN` save attempts: a ``skipped`` result
is not one, so a replay of releases already marked costs reads, never budget, and a run cut
short by the cap is followed by one that gets further. When a run is both capped and failing,
the Error Log says so, and names the last release it finished.

A lost run costs an hour, not a transition. The deploy's ``FLUSHDB`` destroys queued jobs
(``CLAUDE.md``), and nothing here is queued: the next hourly run finds the same sections
still above the marker.

The pure parts, :func:`changelog_versions`, :func:`refs_in` and :func:`version_key`, make no
``frappe`` call, and nothing in the module calls ``frappe`` at import.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import os
import re
from typing import Any

import frappe

APP = "erpnext_enhancements"
SETTINGS = "Product Feedback Settings"
MARKER_FIELD = "release_sync_last_version"

#: ``mark_shipped`` save attempts per run (``marked`` or ``failed``; a ``skipped`` result does
#: not count). A release names a handful of Tasks; this is the fuse for a first run over a long
#: history, not a quota anything is expected to reach.
MAX_MARKS_PER_RUN = 500

#: Real names: ``TASK-2026-01487`` (ERPNext's ``TASK-.YYYY.-.#####``) and
#: ``ER-2026-458194`` (``ER-{YYYY}-{#####}``, whose counter already runs to six digits on
#: production). Five digits or more, so neither series outgrows the pattern.
_TASK_ID = re.compile(r"(?<![\w-])TASK-\d{4}-\d{5,}(?![\w-])")
_ER_ID = re.compile(r"(?<![\w-])ER-\d{4}-\d{5,}(?![\w-])")

#: ``Refs:``, case-sensitive, at the start of a line after at most three spaces and an
#: optional list marker (``-``, ``*``, ``+``, ``1.``, ``1)``). Deliberately strict, see
#: :func:`refs_in`: a backtick, ``**`` or four spaces in front of it and it is not a Refs line.
_REFS_LINE = re.compile(r"^ {0,3}(?:(?:[-*+]|\d{1,9}[.)])(?: {1,4}|\t))?Refs:(.*)$")

#: A code fence: three or more backticks or tildes. Indented any amount, because a fence
#: inside a list item is indented with it; a line that looks like a fence is never a Refs line
#: and treating one too many as a fence only ever skips lines.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")

#: An inline code span, so that a ``<!--`` written inside backticks opens no comment.
_CODE_SPAN = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SECTION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - \S+[ \t]*$", re.M)
_ANY_H2 = re.compile(r"^## ", re.M)


# ------------------------------------------------------------------------------ pure parts


def version_key(version: Any) -> tuple[int, int, int] | None:
	"""``(major, minor, patch)`` as ints, or ``None`` for anything that is not ``x.y.z``.

	Compared as a tuple of ints, never as text: ``"1.99.0" < "1.100.0"`` is false as strings.
	"""
	match = _VERSION.match(str(version or "").strip())
	if not match:
		return None
	return int(match.group(1)), int(match.group(2)), int(match.group(3))


def changelog_versions(text: str) -> list[tuple[str, str]]:
	"""``(version, section text)`` for every ``## [x.y.z] - date`` section, in file order.

	A section ends at the next ``## `` heading of any kind, so ``[Unreleased]`` is never part of
	a release. ``code_anchors.changelog_sections`` reads the same headings for the planner.
	"""
	text = text or ""
	matches = list(_SECTION_HEADING.finditer(text))
	out = []
	for index, match in enumerate(matches):
		end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
		body = text[match.end() : end]
		stop = _ANY_H2.search(body)
		if stop:
			body = body[: stop.start()]
		out.append((match.group(1), body.strip("\n")))
	return out


def refs_in(section_text: str) -> list[dict[str, Any]]:
	"""Every ``Refs:`` line in a section: ``{"line", "requests", "tasks"}`` each, in order.

	``line`` is normalized to ``Refs: <ids in order>``, which is what the shipped comment
	quotes. A Refs line with no ER or TASK id on it is dropped.

	**Strict on purpose.** The CHANGELOG documents this convention with examples, and an
	example that parsed would move real Tasks. So a Refs line is ``Refs:`` at the start of its
	line, after at most three spaces and an optional list marker, and nothing else is:

	* a line that starts with a backtick or ``**`` (the house style for an identifier or a
	  label) is not one, which is what keeps every example written in a code span inert;
	* four spaces or a tab in front is an indented code block in Markdown, so not one;
	* a line inside a fenced block is an example. A fence opens with three or more backticks or
	  tildes and closes only on the same character repeated at least as many times, so a
	  ```` ``` ```` inside a ```` ```` ```` block does not end it;
	* a line inside an HTML comment (``<!--`` … ``-->``) is not rendered, so not one either.
	  As in CommonMark, ``<!--`` at the start of a line hides everything through the line that
	  holds ``-->``; one that opens mid-line is a comment only if ``-->`` follows within its
	  paragraph, and otherwise literal text that hides nothing. A ``<!--`` inside a code span
	  opens nothing.

	The brief (``brief.py``) prints its line bare, in a block of its own, with a note to paste
	it without backticks or bold, and ``tests/test_feedback_brief.py`` feeds that block
	through this function.
	"""
	out = []
	lines = (section_text or "").splitlines()
	fence: tuple[str, int] | None = None  # the open fence's character and length
	in_comment = False
	index = 0
	while index < len(lines):
		raw = lines[index]
		index += 1
		if fence:
			if _closes_fence(raw, fence):
				fence = None
			continue
		if in_comment:
			if "-->" in raw:
				in_comment = False
			continue
		opened = _opens_fence(raw)
		if opened:
			fence = opened
			continue
		visible = raw
		masked = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), raw)
		start = masked.find("<!--")
		if start != -1:
			# Only what comes before a comment can be a Refs line.
			visible = raw[:start]
			last = masked.rfind("<!--")
			if masked.find("-->", last + 4) == -1:
				if len(raw[:start]) <= 3 and not raw[:start].strip(" "):
					# `<!--` opening its line starts an HTML block, hidden through the line
					# that holds `-->`, or to the end of the section.
					in_comment = True
				else:
					index = _past_inline_comment(lines, index)
		match = _REFS_LINE.match(visible)
		if not match:
			continue
		rest = match.group(1)
		requests = list(dict.fromkeys(_ER_ID.findall(rest)))
		tasks = list(dict.fromkeys(_TASK_ID.findall(rest)))
		if not requests and not tasks:
			continue
		ids = [m.group(0) for m in re.finditer(f"{_ER_ID.pattern}|{_TASK_ID.pattern}", rest)]
		out.append(
			{
				"line": "Refs: " + ", ".join(dict.fromkeys(ids)),
				"requests": requests,
				"tasks": tasks,
			}
		)
	return out


def _past_inline_comment(lines: list[str], index: int) -> int:
	"""Where reading resumes after a ``<!--`` that opened mid-line and did not close on it.

	Inline, a comment is one only if it closes within its paragraph, so it hides the lines up to
	and including the one holding ``-->`` when that comes before a blank line. Otherwise the
	``<!--`` was literal text, nothing is hidden, and a Refs line after it still counts.
	"""
	for ahead in range(index, len(lines)):
		if not lines[ahead].strip():
			return index
		if "-->" in lines[ahead]:
			return ahead + 1
	return index


def _opens_fence(line: str) -> tuple[str, int] | None:
	"""``(character, length)`` when ``line`` opens a fenced block, else ``None``.

	A backtick fence's info string may not contain a backtick (CommonMark), so a line such as
	```` ```code``` ```` is inline code, not a fence.
	"""
	match = _FENCE.match(line)
	if not match:
		return None
	marker, info = match.group(1), match.group(2)
	if marker[0] == "`" and "`" in info:
		return None
	return marker[0], len(marker)


def _closes_fence(line: str, fence: tuple[str, int]) -> bool:
	"""Only the same character, at least as many of it, and nothing after but whitespace."""
	match = _FENCE.match(line)
	if not match or match.group(2).strip():
		return False
	marker = match.group(1)
	return marker[0] == fence[0] and len(marker) >= fence[1]


def pending_sections(
	sections: list[tuple[str, str]],
	last_processed: str | None,
	installed: str,
) -> list[tuple[str, str]]:
	"""The sections with ``last_processed < version <= installed``, oldest first.

	An absent or unreadable ``last_processed`` means everything up to ``installed``. A heading
	that is not ``x.y.z`` is never pending.
	"""
	ceiling = version_key(installed)
	if ceiling is None:
		return []
	floor = version_key(last_processed)
	keyed = []
	for position, (version, body) in enumerate(sections or []):
		key = version_key(version)
		if key is None or key > ceiling or (floor is not None and key <= floor):
			continue
		# The file is newest first; `position` keeps a repeated heading in a stable order.
		keyed.append((key, -position, version, body))
	keyed.sort()
	return [(version, body) for _key, _position, version, body in keyed]


# ------------------------------------------------------------------------------ the job


def sync_shipped_tasks() -> dict[str, Any]:
	"""The hourly job (``hooks.py`` ``scheduler_events["hourly"]``). Never raises."""
	try:
		return _run()
	except Exception as exc:
		_log_failures(["the run itself: " + _describe(exc)], installed="?")
		return {"status": "error"}


def _run() -> dict[str, Any]:
	installed = installed_version()
	if not installed:
		return {"status": "no installed version"}
	marker = _read_marker()
	if marker is _UNREADABLE:
		return {"status": "marker unreadable"}
	if marker and version_key(marker) >= version_key(installed):
		# Nothing new is installed, which is every hour but the first after a deploy. The
		# file is not even read.
		return {"status": "up to date", "installed": installed, "from": marker, "to": marker, "sections": 0}
	text = _read_changelog()
	if not text:
		return {"status": "no changelog", "installed": installed}

	sections = pending_sections(changelog_versions(text), marker, installed)
	from erpnext_enhancements.product_feedback import task_writer

	# Save attempts only: a `skipped:` result is a few reads, and a replay after a failed run is
	# made almost entirely of them. Counting skips let one Task that can never save pin the
	# marker and spend every run's budget on the same replayed skips, starving the releases
	# behind them for good.
	calls = 0
	capped = False
	completed_through = None  # the last section the run finished
	clean_through = None  # the last section finished before the first failure
	failures: list[str] = []
	tally = {"marked": 0, "skipped": 0, "failed": 0}

	for version, body in sections:
		done: set[str] = set()
		for refs in refs_in(body):
			for task in refs["tasks"]:
				if task in done:
					continue
				if calls >= MAX_MARKS_PER_RUN:
					capped = True
					break
				done.add(task)
				result = task_writer.mark_shipped(task, version, refs["line"], refs["requests"])
				if result.startswith("skipped:"):
					tally["skipped"] += 1
					continue
				calls += 1
				if result == "marked":
					tally["marked"] += 1
					# Each transition stands on its own: a run cut short keeps what it did.
					frappe.db.commit()
				else:
					tally["failed"] += 1
					failures.append(f"{task} in {version}: {result.removeprefix('failed:')}")
			if capped:
				break
		if capped:
			break
		completed_through = version
		if not failures:
			clean_through = version

	# A clean, whole run moves the marker to the installed version. Anything less moves it only
	# as far as the last release finished before the first failure: the failing release and
	# everything after it are replayed next hour, and the ones before it never are again.
	target = installed if not (failures or capped) else clean_through
	advanced_to = target if target and _write_marker(target, marker) else None
	if failures:
		note = ""
		if capped and (completed_through or marker):
			reached = completed_through or marker
			note = f"run capped at {MAX_MARKS_PER_RUN} calls; releases after {reached} not reached"
		elif capped:
			note = (
				f"run capped at {MAX_MARKS_PER_RUN} calls inside the oldest pending release; it and the "
				"releases after it not finished"
			)
		_log_failures(failures, installed, note)

	return {
		"status": "ok" if not failures else "failed",
		"installed": installed,
		"from": marker or "",
		"to": advanced_to or marker or "",
		"sections": len(sections),
		"capped": capped,
		**tally,
	}


def installed_version() -> str:
	"""The version the last migrate that got far enough recorded for this app, or ``""``.

	One row per app in ``tabInstalled Application`` (the child table of the
	``Installed Applications`` Single). Should there ever be two, the lower wins: acting
	below the true version costs an hour, acting above it could mark work that is not live.
	"""
	rows = frappe.db.sql(
		"""
		select app_version
		from `tabInstalled Application`
		where app_name = %s and parent = %s
		""",
		(APP, "Installed Applications"),
	)
	versions = [str(row[0]).strip() for row in rows or [] if row and version_key(row[0])]
	return min(versions, key=version_key) if versions else ""


def _read_changelog() -> str:
	"""The installed ``CHANGELOG.md``, at the repository root beside the app package, as
	``code_anchors`` and ``codemap`` read it."""
	path = os.path.join(os.path.dirname(frappe.get_app_path(APP)), "CHANGELOG.md")
	try:
		with open(path, encoding="utf-8") as handle:
			return handle.read()
	except OSError:
		return ""


#: What ``_read_marker`` returns when the Single could not be read at all, as opposed to read
#: and found empty. A run stops on it. Treating it as "never run" would replay everything, which
#: is harmless, but a replay that then failed would write back a marker below the stored one.
_UNREADABLE = object()


def _read_marker() -> Any:
	"""The last release processed, ``None`` for "process everything", or ``_UNREADABLE``.

	``get_single_value``, never ``tabSingles`` directly (``CLAUDE.md``). No row, an empty value
	or a value that is not ``x.y.z`` all mean "never run". A read that raises is different: the
	stored value is unknown, so the run stops and the next hour tries again.
	"""
	try:
		value = frappe.db.get_single_value(SETTINGS, MARKER_FIELD, cache=False)
	except Exception:
		return _UNREADABLE
	value = str(value or "").strip()
	return value if version_key(value) else None


def _write_marker(version: str, current: str | None) -> bool:
	"""Move the marker forward to ``version``; never back. ``True`` when it moved.

	``update_modified=False``: the settings page stays saveable for a person who opened it
	before this ran, instead of failing with "document has been modified". If their save
	then writes the old value back, the next run replays those releases, which is harmless.
	"""
	new = version_key(version)
	old = version_key(current)
	if new is None or (old is not None and new <= old):
		return False
	# What is stored now, not only what this run started from: never write below it.
	try:
		stored = version_key(str(frappe.db.get_single_value(SETTINGS, MARKER_FIELD, cache=False) or ""))
	except Exception:
		return False
	if stored is not None and new <= stored:
		return False
	frappe.db.set_single_value(SETTINGS, MARKER_FIELD, version, update_modified=False)
	frappe.db.commit()
	return True


def _log_failures(failures: list[str], installed: str, note: str = "") -> None:
	"""One Error Log per run, with an explicit message.

	Passing the message is what keeps frame locals out: without one, v16's ``log_error`` fills
	it from ``get_traceback(with_context=True)``, which prints every local variable. ``note``
	is a last line about the run itself: that the cap cut it short, and where.
	"""
	try:
		shown = failures[:50]
		more = len(failures) - len(shown)
		message = "\n".join(shown) + (f"\n… and {more} more" if more else "")
		if note:
			message += "\n" + note
		frappe.log_error(
			title=(
				f"Release sync: {len(failures)} failure(s), installed {installed}"
				+ (", capped" if note else "")
			)[:140],
			message=message,
		)
	except Exception:
		pass


def _describe(exc: BaseException) -> str:
	message = " ".join(str(exc or "").split())[:300]
	return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
