# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a cleaning task's three states mean (v1.598.0, D2 of Phase D, TASK-2026-02487).

A task is **done**, **N/A** (not needed on this visit, such as a winter-only heater check in
October), or **not done**. A task left not done should say why in its ``notes``: Lisa, reading
the Myers report on 2026-10-12, "It only states that one cartridge wasn't cleaned out; why
wasn't it?" The Visit Wizard asks for that reason at wrap-up.

One copy of the rule, frappe-free, so the record's submit check and completion figure, the
review digest and its tests all read the same thing. The wizard's ``VZ_ANSWERED`` and
``vz_task_state`` mirror it in JavaScript.
"""

DONE = "done"
NOT_APPLICABLE = "na"
NOT_DONE = "open"


def task_state(row):
	"""``"done"``, ``"na"`` or ``"open"``. Done wins over N/A, as the record's ``validate``
	settles a row ticked both ways."""
	if row.get("is_done"):
		return DONE
	if row.get("not_applicable"):
		return NOT_APPLICABLE
	return NOT_DONE


def task_answered(row):
	"""The technician said something about the task: done, N/A, or a note (the reason it was
	not done). Counts towards completion and satisfies a required task."""
	return task_state(row) != NOT_DONE or bool((row.get("notes") or "").strip())


def task_unexplained(row):
	"""Not done, and nothing said about why."""
	return not task_answered(row)
