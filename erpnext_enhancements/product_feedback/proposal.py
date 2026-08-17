# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Parsing and validating what the model proposed — **pure functions, no I/O, no Frappe**.

Stdlib only, which puts this module in the bench-free CI tier — the only tier in this repo
with automatic regression protection (``CLAUDE.md``: there is no Frappe integration-test
job). ``tests/test_feedback_breakdown_parse.py`` is the guard.

--------------------------------------------------------------------------------------
The parsing rules, and why each one is strict
--------------------------------------------------------------------------------------

Lifted wholesale from ``api/training_ai.py``, which solved this problem for quiz questions
and wrote down what it cost:

* **A malformed *response* yields nothing.** One JSON object with a ``tasks`` list, or an
  empty breakdown and a reason saying so. Prose, markdown fences and half-built objects are
  not salvaged. A tolerant parser is how a task with a subject and no description reaches a
  reviewer who is skim-reading.
* **A malformed *item* is dropped on its own.** One bad row must not cost the other eleven.
* **Every dropped row is reported**, in :attr:`Breakdown.dropped`. A parser that silently
  discards half the model's output looks identical to a model that only produced half.

--------------------------------------------------------------------------------------
Two rules that are security properties rather than tidiness
--------------------------------------------------------------------------------------

**The model names a *target*, never a Project.** Each task carries ``target`` — the string
``"erpnext"`` or ``"triton"`` — and this module maps that to a Project id from the caller's
allowlist. A model that emitted ``"project": "PRJ-00123"`` could otherwise write work onto a
live customer job; with this shape the worst it can do is name a target that is not in the
map, and the row is dropped. The set of writable Projects is a fact about the settings, not
about the response.

**A ``parent_task`` or a duplicate ``task`` must be one we sent.** Both are checked against
``known_tasks`` — the exact rows ERPNext put in the request payload. A model asked to cite a
task id will invent a plausible one (``TASK-2026-99999``) rather than admit it has none, and
an invented parent silently reparents nothing while an invented duplicate sends a reviewer
looking for a task that does not exist.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

#: ``Task.priority`` as ERPNext declares it. A value outside this set becomes MEDIUM rather
#: than failing the row: the model getting the vocabulary wrong is not a reason to lose a
#: task a human would have kept.
VALID_PRIORITIES: Final[tuple[str, ...]] = ("Low", "Medium", "High", "Urgent")
DEFAULT_PRIORITY: Final[str] = "Medium"

VALID_CONFIDENCE: Final[tuple[str, ...]] = ("High", "Medium", "Low")
DEFAULT_CONFIDENCE: Final[str] = "Medium"

#: Field length caps. Generous, and applied so a runaway generation cannot write a megabyte
#: into a Data column and fail the insert after the reviewer has already confirmed.
MAX_SUBJECT_CHARS: Final[int] = 200
MAX_GROUP_SUBJECT_CHARS: Final[int] = 200
MAX_DESCRIPTION_CHARS: Final[int] = 20000
MAX_SUMMARY_CHARS: Final[int] = 2000
MAX_MODEL_CHARS: Final[int] = 140
MAX_WHY_CHARS: Final[int] = 500

#: Nobody estimates a single task at more than a working month, and a model that returns
#: 10000 has misread the unit rather than made a judgement.
MAX_EXPECTED_HOURS: Final[float] = 200.0

#: More than this and the duplicate banner stops being a hint and becomes a second list to
#: read. The reviewer only needs the ones worth checking.
MAX_DUPLICATES: Final[int] = 6


@dataclass(frozen=True)
class ProposedTask:
	"""One row of the proposal. Not a ``Task`` — nothing here has been written anywhere."""

	subject: str
	project: str
	description: str = ""
	priority: str = DEFAULT_PRIORITY
	expected_hours: float = 0.0
	parent_task: str = ""
	group_subject: str = ""
	#: 1-based position of another row in the *kept* list, or 0 for none. Already remapped
	#: from the model's original numbering and already guaranteed acyclic.
	depends_on_idx: int = 0


@dataclass(frozen=True)
class DuplicateCandidate:
	"""An existing Task the model thinks already covers this request."""

	task: str
	task_subject: str
	confidence: str = DEFAULT_CONFIDENCE
	why: str = ""


@dataclass(frozen=True)
class Breakdown:
	"""Everything a parsed response yielded, including what it lost on the way."""

	summary: str = ""
	model: str = ""
	tasks: tuple[ProposedTask, ...] = ()
	duplicates: tuple[DuplicateCandidate, ...] = ()
	#: Human-readable reasons, one per dropped item. Written to ``breakdown_error`` when the
	#: result is otherwise usable, so a thin proposal explains itself.
	dropped: tuple[str, ...] = ()

	@property
	def is_empty(self) -> bool:
		return not self.tasks


def parse_breakdown(
	body: Any,
	*,
	target_projects: Mapping[str, str],
	known_tasks: Mapping[str, Mapping[str, Any]],
	max_tasks: int,
) -> Breakdown:
	"""Turn Triton's response into a validated :class:`Breakdown`.

	Args:
		body: the decoded JSON response. Anything that is not a dict yields an empty
			breakdown — see the module docstring.
		target_projects: ``{"erpnext": "PRJ-00580", "triton": "PRJ-00755"}``, already narrowed
			to the targets the reviewer permitted. A task naming a key that is absent is
			dropped, which is how a reviewer's "ERPNext only" override is enforced.
		known_tasks: ``{task_name: {"subject", "project", "is_group"}}`` — exactly the rows
			ERPNext sent. Nothing outside this map may be referenced.
		max_tasks: hard clamp, applied after validation.

	Never raises. A caller in a background job would only turn the exception back into the
	same empty result, and an exception here would lose the ``dropped`` reasons with it.
	"""
	dropped: list[str] = []

	if not isinstance(body, Mapping):
		return Breakdown(dropped=("Triton returned a body that is not a JSON object.",))

	raw_tasks = body.get("tasks")
	if not isinstance(raw_tasks, Sequence) or isinstance(raw_tasks, (str, bytes)):
		return Breakdown(
			summary=_text(body.get("summary"), MAX_SUMMARY_CHARS),
			model=_text(body.get("model"), MAX_MODEL_CHARS),
			dropped=("Triton returned no 'tasks' list.",),
		)

	kept: list[tuple[int, ProposedTask]] = []
	for position, raw in enumerate(raw_tasks, start=1):
		parsed, reason = _parse_task(raw, position, target_projects=target_projects, known_tasks=known_tasks)
		if parsed is None:
			dropped.append(reason)
			continue
		kept.append((position, parsed))

	if len(kept) > max_tasks:
		dropped.append(
			f"Kept the first {max_tasks} of {len(kept)} proposed tasks "
			f"(Product Feedback Settings.max_proposed_tasks)."
		)
		kept = kept[:max_tasks]

	tasks = _resolve_dependencies(kept, dropped)
	duplicates = _parse_duplicates(body.get("duplicates"), known_tasks=known_tasks, dropped=dropped)

	return Breakdown(
		summary=_text(body.get("summary"), MAX_SUMMARY_CHARS),
		model=_text(body.get("model"), MAX_MODEL_CHARS),
		tasks=tasks,
		duplicates=duplicates,
		dropped=tuple(dropped),
	)


# ------------------------------------------------------------------------------- tasks


def _parse_task(
	raw: Any,
	position: int,
	*,
	target_projects: Mapping[str, str],
	known_tasks: Mapping[str, Mapping[str, Any]],
) -> tuple[ProposedTask | None, str]:
	"""One row, or ``(None, reason)``. The reason is user-facing; name the row by position."""
	if not isinstance(raw, Mapping):
		return None, f"Task {position}: not an object."

	subject = _text(raw.get("subject"), MAX_SUBJECT_CHARS)
	if not subject:
		return None, f"Task {position}: no subject."

	target = _text(raw.get("target"), 32).lower()
	project = target_projects.get(target, "")
	if not project:
		permitted = ", ".join(sorted(target_projects)) or "none"
		return None, f"Task {position} ({subject}): target {target or '(missing)'} is not one of {permitted}."

	parent_task = _text(raw.get("parent_task"), 140)
	if parent_task:
		known = known_tasks.get(parent_task)
		if known is None:
			# Not fatal to the row. An invented parent means "put it at the top level", which
			# is what a human would do with the same suggestion.
			parent_task = ""
		elif not known.get("is_group"):
			parent_task = ""
		elif (known.get("project") or "") != project:
			# A parent on the other board would make the Task tree cross projects, which
			# ERPNext's own dependency rules already refuse elsewhere.
			parent_task = ""

	return (
		ProposedTask(
			subject=subject,
			project=project,
			description=_text(raw.get("description"), MAX_DESCRIPTION_CHARS),
			priority=_choice(raw.get("priority"), VALID_PRIORITIES, DEFAULT_PRIORITY),
			expected_hours=_hours(raw.get("expected_hours")),
			parent_task=parent_task,
			group_subject=_text(raw.get("group_subject"), MAX_GROUP_SUBJECT_CHARS),
			depends_on_idx=_int(raw.get("depends_on_index")),
		),
		"",
	)


def _resolve_dependencies(
	kept: Sequence[tuple[int, ProposedTask]],
	dropped: list[str],
) -> tuple[ProposedTask, ...]:
	"""Remap ``depends_on_idx`` onto the kept list, then break every cycle.

	The model numbers its rows in the order it emitted them. Dropping a malformed row and
	clamping to ``max_tasks`` both shift those numbers, so an index resolved naively points at
	the wrong task — silently, and in a way that looks like the model got the ordering wrong.

	Cycles are broken rather than reported as fatal: ERPNext's own ``add_task_dependency``
	refuses a cycle at insert time, so a proposal carrying one would fail *after* the reviewer
	confirmed it, which is the worst moment to find out.
	"""
	if not kept:
		return ()

	old_to_new = {old: new for new, (old, _) in enumerate(kept, start=1)}
	staged: list[ProposedTask] = []
	for new_index, (_, task) in enumerate(kept, start=1):
		dep = old_to_new.get(task.depends_on_idx, 0)
		if dep == new_index:
			dep = 0
		staged.append(_replace_dep(task, dep))

	for index in range(1, len(staged) + 1):
		if _walks_into_a_cycle(staged, index):
			dropped.append(
				f"Task {index} ({staged[index - 1].subject}): dependency dropped, it formed a cycle."
			)
			staged[index - 1] = _replace_dep(staged[index - 1], 0)

	return tuple(staged)


def _walks_into_a_cycle(tasks: Sequence[ProposedTask], start: int) -> bool:
	"""Does following ``depends_on_idx`` from ``start`` come back to ``start``?

	Bounded by the list length, so a chain that is merely long terminates rather than looping.
	"""
	seen: set[int] = set()
	cursor = tasks[start - 1].depends_on_idx
	while cursor:
		if cursor == start:
			return True
		if cursor in seen or not (1 <= cursor <= len(tasks)):
			return False
		seen.add(cursor)
		cursor = tasks[cursor - 1].depends_on_idx
	return False


def _replace_dep(task: ProposedTask, dep: int) -> ProposedTask:
	"""``dataclasses.replace`` without the import, since only one field ever moves."""
	return ProposedTask(
		subject=task.subject,
		project=task.project,
		description=task.description,
		priority=task.priority,
		expected_hours=task.expected_hours,
		parent_task=task.parent_task,
		group_subject=task.group_subject,
		depends_on_idx=dep,
	)


# -------------------------------------------------------------------------- duplicates


def _parse_duplicates(
	raw_duplicates: Any,
	*,
	known_tasks: Mapping[str, Mapping[str, Any]],
	dropped: list[str],
) -> tuple[DuplicateCandidate, ...]:
	"""Duplicate rows, restricted to tasks we actually sent and de-duplicated themselves."""
	if not isinstance(raw_duplicates, Sequence) or isinstance(raw_duplicates, (str, bytes)):
		return ()

	out: list[DuplicateCandidate] = []
	seen: set[str] = set()
	for position, raw in enumerate(raw_duplicates, start=1):
		if not isinstance(raw, Mapping):
			dropped.append(f"Duplicate {position}: not an object.")
			continue
		name = _text(raw.get("task"), 140)
		known = known_tasks.get(name)
		if known is None:
			# The invented-task-id case. Worth reporting: it is the signal that the model is
			# guessing rather than reading the list it was given.
			dropped.append(f"Duplicate {position}: {name or '(no task)'} was not in the tasks we sent.")
			continue
		if name in seen:
			continue
		seen.add(name)
		out.append(
			DuplicateCandidate(
				task=name,
				task_subject=_text(known.get("subject"), MAX_SUBJECT_CHARS),
				confidence=_choice(raw.get("confidence"), VALID_CONFIDENCE, DEFAULT_CONFIDENCE),
				why=_text(raw.get("why"), MAX_WHY_CHARS),
			)
		)
		if len(out) >= MAX_DUPLICATES:
			break
	return tuple(out)


# ----------------------------------------------------------------------------- scalars


def _text(value: Any, limit: int) -> str:
	"""A trimmed, length-capped string. Anything that is not a string becomes ``""``.

	Deliberately not ``str(value)``: coercing a dict here is how ``{'text': ...}`` ends up
	rendered as a Python repr in a Task subject.
	"""
	if not isinstance(value, str):
		return ""
	return value.strip()[:limit]


def _choice(value: Any, options: Sequence[str], fallback: str) -> str:
	"""Case-insensitive match against a fixed vocabulary, else ``fallback``."""
	if not isinstance(value, str):
		return fallback
	lowered = value.strip().lower()
	for option in options:
		if option.lower() == lowered:
			return option
	return fallback


def _hours(value: Any) -> float:
	"""A non-negative float, capped. Unparseable or absurd becomes ``0.0``."""
	if isinstance(value, bool) or not isinstance(value, (int, float, str)):
		return 0.0
	try:
		hours = float(value)
	except (TypeError, ValueError):
		return 0.0
	if hours != hours or hours in (float("inf"), float("-inf")):  # NaN / inf
		return 0.0
	if hours <= 0:
		return 0.0
	return round(min(hours, MAX_EXPECTED_HOURS), 2)


def _int(value: Any) -> int:
	"""A non-negative int. Unparseable becomes ``0``, which means "no dependency"."""
	if isinstance(value, bool) or not isinstance(value, (int, float, str)):
		return 0
	try:
		number = int(float(value))
	except (TypeError, ValueError):
		return 0
	return number if number > 0 else 0
