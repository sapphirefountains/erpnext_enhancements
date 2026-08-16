# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Triton Cost — what the assistant consumed, by day and model.

TASK-2026-01321 asks for *"a report or chart over it so 'what did Triton cost last week' is
answerable without writing SQL"*. Everything needed was already being recorded on
``Triton Invocation Log``; nothing could read it without a console.

--------------------------------------------------------------------------------------
It aggregates, and that is a privacy decision rather than a formatting one
--------------------------------------------------------------------------------------

``Triton Invocation Log`` carries ``asked_by``, ``room``, ``mention_message`` and
``thread_root``. A per-row report over it is a record of **which employee asked the assistant
what, and when** — and that is precisely why the doctype sits on the AI tool denylist, whose
entry says so in as many words: *"a behavioural record of employees [that] belongs behind the
same door as the conversation."*

The cost question is aggregate, so this report answers it with aggregates and **never selects
``asked_by``, ``room``, ``mention_message`` or ``thread_root`` at all.** Those columns are not
filtered out downstream — they are never read, which is a property somebody can check by
reading the query rather than by trusting a redaction step.

The consequence is stated rather than discovered: **this report cannot answer "who used it
most".** That is deliberate. A per-person breakdown is a different report with a different
justification and a different approver, and building it as a filter on this one would smuggle
the decision in as a convenience.

--------------------------------------------------------------------------------------
What the numbers mean
--------------------------------------------------------------------------------------

* ``prompt`` / ``completion`` are billed tokens. ``cached`` is the portion of the prompt served
  from context cache — it is a **subset of the prompt count, not an addition to it**, and it is
  the number that moves when caching is working. Shown separately for that reason.
* ``context`` is what retrieval put in front of the model, which is the lever this app controls.
  A rising context with flat completions means retrieval is getting more expensive without
  producing more answer.
* ``truncated`` counts turns where the context was cut, and ``rungs`` is the worst degradation
  rung reached. Both are quality signals hiding inside a cost report: cost falling while
  truncation rises is not a saving.
* ``miss %`` is citations the model invented over citations it emitted. The invocation log's own
  docstring calls a rising miss rate a **prompt regression signal** rather than a UI bug, and
  this is where it becomes visible without a query.

Timings are averages, and averages hide the tail. ``slowest`` is carried alongside precisely so
a day whose mean looks fine but whose worst turn took ninety seconds does not read as healthy.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "day", "label": _("Day"), "fieldtype": "Date", "width": 100},
		{"fieldname": "model_used", "label": _("Model"), "fieldtype": "Data", "width": 180},
		{"fieldname": "turns", "label": _("Turns"), "fieldtype": "Int", "width": 80},
		{"fieldname": "errors", "label": _("Errors"), "fieldtype": "Int", "width": 80},
		{"fieldname": "prompt_tokens", "label": _("Prompt"), "fieldtype": "Int", "width": 110},
		{"fieldname": "completion_tokens", "label": _("Completion"), "fieldtype": "Int", "width": 110},
		{
			"fieldname": "cached_content_tokens",
			"label": _("Cached (of prompt)"),
			"fieldtype": "Int",
			"width": 130,
		},
		{"fieldname": "context_tokens", "label": _("Context"), "fieldtype": "Int", "width": 110},
		{"fieldname": "truncated", "label": _("Truncated"), "fieldtype": "Int", "width": 100},
		{"fieldname": "worst_rung", "label": _("Worst rung"), "fieldtype": "Data", "width": 110},
		{"fieldname": "citations", "label": _("Citations"), "fieldtype": "Int", "width": 100},
		{"fieldname": "citation_miss_pct", "label": _("Miss %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "avg_total_ms", "label": _("Avg ms"), "fieldtype": "Int", "width": 90},
		{"fieldname": "slowest_ms", "label": _("Slowest ms"), "fieldtype": "Int", "width": 100},
	]


def get_data(filters):
	conditions = []
	values = {}

	if filters.get("from_date"):
		conditions.append("date(l.creation) >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("date(l.creation) <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("origin"):
		conditions.append("l.origin = %(origin)s")
		values["origin"] = filters.origin
	if filters.get("model_used"):
		conditions.append("l.model_used = %(model_used)s")
		values["model_used"] = filters.model_used

	where = (" where " + " and ".join(conditions)) if conditions else ""

	# Column list is the whole security story of this report: no `asked_by`, no `room`, no
	# `mention_message`, no `thread_root`. See the module docstring.
	rows = frappe.db.sql(
		f"""
		select
			date(l.creation)                                    as day,
			coalesce(nullif(l.model_used, ''), 'unknown')       as model_used,
			count(*)                                            as turns,
			sum(case when l.status = 'Error' then 1 else 0 end) as errors,
			sum(coalesce(l.prompt_tokens, 0))                   as prompt_tokens,
			sum(coalesce(l.completion_tokens, 0))               as completion_tokens,
			sum(coalesce(l.cached_content_tokens, 0))           as cached_content_tokens,
			sum(coalesce(l.context_tokens, 0))                  as context_tokens,
			sum(case when l.context_truncated = 1 then 1 else 0 end) as truncated,
			max(coalesce(l.degradation_rung, ''))               as worst_rung,
			sum(coalesce(l.citation_count, 0))                  as citations,
			sum(coalesce(l.citation_miss_count, 0))             as citation_misses,
			avg(coalesce(l.total_ms, 0))                        as avg_total_ms,
			max(coalesce(l.total_ms, 0))                        as slowest_ms
		from `tabTriton Invocation Log` l
		{where}
		group by date(l.creation), coalesce(nullif(l.model_used, ''), 'unknown')
		order by day desc, turns desc
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		misses = cint(row.pop("citation_misses", 0))
		resolved = cint(row.get("citations"))
		attempted = misses + resolved
		# Miss rate over what the model TRIED to cite, not over turns: a day with one heavily
		# cited answer and a day with fifty uncited ones are not comparable per-turn.
		#
		# The denominator is `attempted`, and the guard is on `attempted` rather than on
		# `resolved`. Guarding on `resolved` reads naturally and is wrong in the one case that
		# matters most: a day where the model invented EVERY citation has resolved = 0, and
		# would report a 0% miss rate — the worst possible day rendering as the best.
		row["citation_miss_pct"] = flt(100.0 * misses / attempted, 2) if attempted else 0.0
		row["avg_total_ms"] = cint(row.get("avg_total_ms"))

	return rows
