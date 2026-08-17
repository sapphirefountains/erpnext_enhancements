# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What survives coming back from the model, and what does not.

``product_feedback/proposal.py`` is the seam between a language model and a live project
board. Everything downstream of it — the reviewer's screen, the confirm call, the ``Task``
rows — treats its output as trustworthy, so this is where the trust is actually established.

The rules under test, and why each one is strict, are argued in that module's docstring. The
two worth restating here because they are security properties rather than tidiness:

* **A task names a `target`, never a Project.** The model chooses from a closed set of keys
  and this module maps them. A model that could emit ``"project": "PRJ-00123"`` could write
  work onto a live customer job.
* **A `parent_task` or a duplicate `task` must be one we sent.** A model asked to cite a task
  id will invent a plausible one rather than admit it has none.

**pytest, not unittest** — plain ``def test_*`` functions. ``python -m unittest`` silently
collects nothing from these and reports success, which is how the QuickBooks suite ran nowhere
for weeks. This file needs its own ``python -m pytest`` step in ``ci.yml``.

Run: python -m pytest erpnext_enhancements/tests/test_feedback_breakdown_parse.py -q
"""

from erpnext_enhancements.product_feedback.proposal import (
	MAX_EXPECTED_HOURS,
	parse_breakdown,
)

BOTH = {"erpnext": "PRJ-00580", "triton": "PRJ-00755"}
ERPNEXT_ONLY = {"erpnext": "PRJ-00580"}

KNOWN = {
	"TASK-1": {"subject": "Kiosk epic", "project": "PRJ-00580", "is_group": True},
	"TASK-2": {"subject": "Clock-in drops GPS", "project": "PRJ-00580", "is_group": False},
	"TASK-3": {"subject": "Triton epic", "project": "PRJ-00755", "is_group": True},
}


def task(**overrides):
	base = {"subject": "Do the thing", "target": "erpnext"}
	base.update(overrides)
	return base


def parse(body, targets=None, known=None, max_tasks=10):
	return parse_breakdown(
		body,
		target_projects=targets if targets is not None else BOTH,
		known_tasks=KNOWN if known is None else known,
		max_tasks=max_tasks,
	)


# --------------------------------------------------------------- a malformed response


def test_a_body_that_is_not_an_object_yields_nothing():
	for body in (None, "some prose", [], 42, True):
		result = parse(body)
		assert result.is_empty
		assert result.dropped


def test_a_response_with_no_tasks_list_yields_nothing_but_keeps_the_summary():
	result = parse({"summary": "I did not understand.", "model": "gemini-x", "tasks": "none"})
	assert result.is_empty
	assert result.summary == "I did not understand."
	assert result.model == "gemini-x"
	assert "no 'tasks' list" in " ".join(result.dropped)


def test_prose_where_the_task_list_should_be_is_not_salvaged():
	result = parse({"tasks": "1. fix it 2. ship it"})
	assert result.is_empty


# ------------------------------------------------------------------ a malformed item


def test_one_bad_row_does_not_cost_the_others():
	result = parse({"tasks": [task(subject="A"), "not an object", task(subject="C")]})
	assert [t.subject for t in result.tasks] == ["A", "C"]
	assert any("not an object" in reason for reason in result.dropped)


def test_a_row_with_no_subject_is_dropped_and_reported():
	result = parse({"tasks": [task(subject="   "), task(subject="B")]})
	assert [t.subject for t in result.tasks] == ["B"]
	assert any("no subject" in reason for reason in result.dropped)


def test_every_drop_is_reported():
	"""A parser that silently discards half the output looks like a model that produced half."""
	result = parse({"tasks": [task(), {}, {}, "x"]})
	assert len(result.dropped) == 3


# --------------------------------------------------------------------- the target gate


def test_a_task_may_not_name_a_project():
	"""The security property. `project` in the payload is ignored; `target` decides."""
	result = parse({"tasks": [task(project="PRJ-00123", target="erpnext")]})
	assert result.tasks[0].project == "PRJ-00580"


def test_an_unknown_target_is_dropped():
	result = parse({"tasks": [task(target="customer-board"), task(target="erpnext")]})
	assert len(result.tasks) == 1
	assert any("is not one of" in reason for reason in result.dropped)


def test_a_missing_target_is_dropped():
	result = parse({"tasks": [{"subject": "orphan"}]})
	assert result.is_empty


def test_the_reviewers_override_is_enforced():
	"""'ERPNext only' at approval time must actually exclude Triton rows."""
	result = parse(
		{"tasks": [task(target="erpnext", subject="A"), task(target="triton", subject="B")]},
		targets=ERPNEXT_ONLY,
	)
	assert [t.subject for t in result.tasks] == ["A"]


def test_target_matching_is_case_insensitive():
	result = parse({"tasks": [task(target="ERPNext")]})
	assert result.tasks[0].project == "PRJ-00580"


# -------------------------------------------------------------------- parent and dupes


def test_an_invented_parent_is_dropped_but_the_task_is_kept():
	result = parse({"tasks": [task(parent_task="TASK-2026-99999")]})
	assert result.tasks[0].parent_task == ""


def test_a_non_group_parent_is_refused():
	"""Nesting under a leaf would make the NestedSet tree wrong."""
	result = parse({"tasks": [task(parent_task="TASK-2")]})
	assert result.tasks[0].parent_task == ""


def test_a_parent_on_the_other_board_is_refused():
	result = parse({"tasks": [task(target="erpnext", parent_task="TASK-3")]})
	assert result.tasks[0].parent_task == ""


def test_a_valid_group_parent_survives():
	result = parse({"tasks": [task(target="erpnext", parent_task="TASK-1")]})
	assert result.tasks[0].parent_task == "TASK-1"


def test_an_invented_duplicate_is_dropped_and_reported():
	result = parse(
		{
			"tasks": [task()],
			"duplicates": [{"task": "TASK-2026-99999", "confidence": "High", "why": "made up"}],
		}
	)
	assert result.duplicates == ()
	assert any("was not in the tasks we sent" in reason for reason in result.dropped)


def test_a_real_duplicate_carries_the_subject_we_sent():
	result = parse(
		{"tasks": [task()], "duplicates": [{"task": "TASK-2", "confidence": "high", "why": "same"}]}
	)
	assert result.duplicates[0].task_subject == "Clock-in drops GPS"
	assert result.duplicates[0].confidence == "High"


def test_duplicates_are_deduplicated():
	result = parse(
		{
			"tasks": [task()],
			"duplicates": [{"task": "TASK-2", "why": "a"}, {"task": "TASK-2", "why": "b"}],
		}
	)
	assert len(result.duplicates) == 1


# ------------------------------------------------------------------------- dependencies


def test_indices_are_remapped_after_a_row_is_dropped():
	"""The subtle one. Dropping row 2 shifts every number the model wrote after it."""
	result = parse(
		{
			"tasks": [
				task(subject="A"),
				{"subject": "", "target": "erpnext"},  # dropped
				task(subject="C", depends_on_index=1),
			]
		}
	)
	assert [t.subject for t in result.tasks] == ["A", "C"]
	# "C" depended on the row that is now index 1, i.e. "A" — not on the dropped row.
	assert result.tasks[1].depends_on_idx == 1


def test_a_dependency_on_a_dropped_row_becomes_none():
	result = parse(
		{
			"tasks": [
				task(subject="A", depends_on_index=2),
				{"subject": "", "target": "erpnext"},  # dropped, was index 2
			]
		}
	)
	assert result.tasks[0].depends_on_idx == 0


def test_a_self_dependency_is_removed():
	result = parse({"tasks": [task(depends_on_index=1)]})
	assert result.tasks[0].depends_on_idx == 0


def test_a_two_row_cycle_is_broken_and_reported():
	result = parse(
		{"tasks": [task(subject="A", depends_on_index=2), task(subject="B", depends_on_index=1)]}
	)
	assert 0 in [t.depends_on_idx for t in result.tasks]
	assert any("cycle" in reason for reason in result.dropped)


def test_a_three_row_cycle_is_broken():
	result = parse(
		{
			"tasks": [
				task(subject="A", depends_on_index=2),
				task(subject="B", depends_on_index=3),
				task(subject="C", depends_on_index=1),
			]
		}
	)
	# Whatever it breaks, following the chain from every row must terminate.
	for start in range(1, len(result.tasks) + 1):
		seen, cursor, steps = set(), result.tasks[start - 1].depends_on_idx, 0
		while cursor and steps <= len(result.tasks):
			assert cursor not in seen
			seen.add(cursor)
			cursor = result.tasks[cursor - 1].depends_on_idx
			steps += 1


def test_a_long_chain_is_not_mistaken_for_a_cycle():
	result = parse(
		{
			"tasks": [
				task(subject="A", depends_on_index=0),
				task(subject="B", depends_on_index=1),
				task(subject="C", depends_on_index=2),
				task(subject="D", depends_on_index=3),
			]
		}
	)
	assert [t.depends_on_idx for t in result.tasks] == [0, 1, 2, 3]
	assert not any("cycle" in reason for reason in result.dropped)


def test_an_out_of_range_index_becomes_none():
	result = parse({"tasks": [task(depends_on_index=99)]})
	assert result.tasks[0].depends_on_idx == 0


# ------------------------------------------------------------------------------ scalars


def test_the_clamp_is_applied_and_reported():
	result = parse({"tasks": [task(subject=str(n)) for n in range(20)]}, max_tasks=5)
	assert len(result.tasks) == 5
	assert any("Kept the first 5" in reason for reason in result.dropped)


def test_absurd_hours_are_capped_rather_than_dropping_the_row():
	result = parse({"tasks": [task(expected_hours=100000)]})
	assert result.tasks[0].expected_hours == MAX_EXPECTED_HOURS


def test_unparseable_hours_become_zero():
	for value in ("soon", None, {}, [], float("nan"), float("inf"), -4, True):
		result = parse({"tasks": [task(expected_hours=value)]})
		assert result.tasks[0].expected_hours == 0.0


def test_numeric_strings_are_accepted():
	result = parse({"tasks": [task(expected_hours="3.5")]})
	assert result.tasks[0].expected_hours == 3.5


def test_an_unknown_priority_falls_back_rather_than_dropping_the_row():
	result = parse({"tasks": [task(priority="Critical")]})
	assert result.tasks[0].priority == "Medium"


def test_a_known_priority_survives_any_casing():
	result = parse({"tasks": [task(priority="uRgEnT")]})
	assert result.tasks[0].priority == "Urgent"


def test_a_non_string_subject_is_not_coerced():
	"""`str(value)` here is how `{'text': ...}` ends up rendered as a repr in a Task subject."""
	result = parse({"tasks": [task(subject={"text": "hello"})]})
	assert result.is_empty


def test_long_fields_are_truncated_not_refused():
	result = parse({"tasks": [task(subject="x" * 5000, description="y" * 50000)]})
	assert len(result.tasks[0].subject) == 200
	assert len(result.tasks[0].description) == 20000


def test_a_clean_response_survives_intact():
	"""The control. Every assertion above is about rejection; this one proves acceptance."""
	result = parse(
		{
			"summary": "The kiosk drops GPS on clock-in.",
			"model": "gemini-3.1-pro-preview",
			"tasks": [
				task(
					subject="Clock-in loses the GPS fix",
					target="erpnext",
					description="<p><b>Open.</b> The watcher is torn down early.</p>",
					priority="High",
					expected_hours=4,
					parent_task="TASK-1",
					depends_on_index=0,
				),
				task(subject="Add a regression test", target="erpnext", depends_on_index=1),
			],
			"duplicates": [{"task": "TASK-2", "confidence": "Medium", "why": "same area"}],
		}
	)
	assert not result.dropped
	assert len(result.tasks) == 2
	assert result.tasks[0].parent_task == "TASK-1"
	assert result.tasks[1].depends_on_idx == 1
	assert result.duplicates[0].task == "TASK-2"
	assert result.summary.startswith("The kiosk")
