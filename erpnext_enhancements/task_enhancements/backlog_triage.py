"""The one-time assignment backlog triage, with a dry run first and an undo after (WI-081 Phase 1).

On 2026-10-20 production held 1,310 open ToDos. A new inbox would drown in them, and nearly all
were not work anyone was going to do: 286 on Administrator, 225 on Tasks already Completed, 581
"Follow up with ..." reminders for customers that are mostly no longer prospects. This closes those,
by the rules in ``assignments.triage_rule``, and leaves everything else exactly as it is.

How it runs: a System Manager opens **My Work -> Tidy the backlog**, which calls :func:`preview`
(changes nothing) and shows the count, the people and a sample under each rule. **Apply** enqueues
:func:`apply` on the long queue; each ToDo it closes is stamped with the batch in
``ToDo.custom_triage_batch`` (``<batch>|<rule>``), and **Undo** reopens exactly that batch. It is
a button, not a patch, so nothing is closed by a deploy and nobody's list changes before someone
has looked at the numbers.

Things it is careful about:

* **It closes quietly.** Through ``frappe.db.set_value`` and then the ToDo's own
  ``update_in_reference``, which rebuilds the record's ``_assign`` list. No "Assignment of X
  removed by Y" comment lands on a thousand Customer timelines, and no "your assignment was
  removed" notification goes to anyone. That is the difference from :func:`auto_close.close_todo`,
  which is for one record finishing, where the timeline line is the point.
* **The stamp moves ``modified``.** The customer inactivity reminder reads a closed follow-up's
  ``modified`` as the last time anyone followed up (``script_migrations/customer.py``); without it,
  every prospect's reminder the triage cancelled would be written again the next morning.
* **A record whose doctype no longer exists counts as missing.** HD Ticket is one: a ToDo on
  "HD Ticket 1" survived the Helpdesk app's removal.
* **``custom_prospect`` is read only where the column exists**, like every custom-field read in a
  path that can run before this app's fields do.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext_enhancements.task_enhancements import assignments

FIELD = "custom_triage_batch"

RULE_LABELS = {
	"record_missing": "The record it was about no longer exists",
	"record_finished": "The record is finished (it closes as Done)",
	"nobody_owns": "Assigned to Administrator or Guest, whose list nobody reads",
	"not_a_prospect": "A generated customer follow-up for someone who is no longer a prospect",
	"stale": "Nobody has touched it or its record in 90 days",
}

SAMPLE_SIZE = 5


def new_batch_name():
	return "triage-" + now_datetime().strftime("%Y%m%d-%H%M%S")


def _open_todos():
	return frappe.get_all(
		"ToDo",
		filters={"status": "Open"},
		fields=[
			"name",
			"allocated_to",
			"assigned_by",
			"reference_type",
			"reference_name",
			"description",
			"date",
			"creation",
			"modified",
		],
		limit_page_length=0,
	)


def _reference_facts(todos):
	"""{(doctype, name): {"exists", "finished", "modified", "prospect"}} for every record named."""
	by_type = {}
	for row in todos:
		if row.reference_type and row.reference_name:
			by_type.setdefault(row.reference_type, set()).add(str(row.reference_name))
	facts = {}
	for doctype, names in by_type.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		if meta.issingle:
			for name in names:
				facts[(doctype, name)] = {
					"exists": True,
					"finished": False,
					"modified": None,
					"prospect": None,
				}
			continue
		field = assignments.finished_field(doctype)
		fields = ["name", "modified", "docstatus"]
		if field and field not in fields and frappe.db.has_column(doctype, field):
			fields.append(field)
		prospect = doctype == "Customer" and frappe.db.has_column("Customer", "custom_prospect")
		if prospect:
			fields.append("custom_prospect")
		names = list(names)
		for start in range(0, len(names), 500):
			for rec in frappe.get_all(
				doctype, filters={"name": ["in", names[start : start + 500]]}, fields=fields
			):
				finished = int(rec.docstatus or 0) == 2 or (
					bool(field) and assignments.finished_value(doctype, rec.get(field))
				)
				facts[(doctype, str(rec.name))] = {
					"exists": True,
					"finished": finished,
					"modified": rec.modified,
					"prospect": bool(rec.custom_prospect) if prospect else None,
				}
	return facts


def classify():
	"""Every open ToDo with the rule that closes it (or ``None``), as ``[(row, rule)]``."""
	todos = _open_todos()
	facts = _reference_facts(todos)
	now = now_datetime()
	out = []
	for row in todos:
		if row.reference_type and row.reference_name:
			row["ref"] = facts.get((row.reference_type, str(row.reference_name))) or {"exists": False}
		else:
			row["ref"] = None
		out.append((row, assignments.triage_rule(row, now)))
	return out


def preview():
	"""What :func:`apply` would close, per rule: count, the status it gets, who holds them, and a
	sample. Also how many stay open, and how many of those still have nobody assigned."""
	classified = classify()
	rules = {
		rule: {
			"label": _(label),
			"status": assignments.TRIAGE_STATUS[rule],
			"count": 0,
			"people": {},
			"sample": [],
		}
		for rule, label in RULE_LABELS.items()
	}
	kept = kept_unassigned = 0
	for row, rule in classified:
		if not rule:
			kept += 1
			if not row.allocated_to:
				kept_unassigned += 1
			continue
		entry = rules[rule]
		entry["count"] += 1
		who = row.allocated_to or _("Nobody")
		entry["people"][who] = entry["people"].get(who, 0) + 1
		if len(entry["sample"]) < SAMPLE_SIZE:
			entry["sample"].append(
				{
					"name": row.name,
					"who": who,
					"what": assignments.truncate(assignments.plain_text(row.description), 100),
					"reference": f"{row.reference_type} {row.reference_name}" if row.reference_type else "",
					"created": str(assignments.as_date(row.creation)),
				}
			)
	for entry in rules.values():
		entry["people"] = sorted(entry["people"].items(), key=lambda kv: -kv[1])
	return {
		"open": len(classified),
		"closing": sum(e["count"] for e in rules.values()),
		"kept": kept,
		"kept_unassigned": kept_unassigned,
		"rules": rules,
		"ready": frappe.db.has_column("ToDo", FIELD),
	}


def _set(name, values):
	frappe.db.set_value("ToDo", name, values)
	todo = frappe.get_doc("ToDo", name)
	# A ToDo on a doctype that no longer exists (HD Ticket) has no _assign to rebuild, and
	# update_in_reference would raise on its meta.
	if todo.reference_type and frappe.db.exists("DocType", todo.reference_type):
		todo.update_in_reference()


def apply(batch, user=None):
	"""Close everything :func:`preview` lists, stamped with ``batch``. Background job."""
	if not frappe.db.has_column("ToDo", FIELD):
		frappe.throw(_("The ToDo field {0} is missing; run bench migrate first.").format(FIELD))
	counts = {}
	done = 0
	for row, rule in classify():
		if not rule:
			continue
		try:
			_set(row.name, {"status": assignments.TRIAGE_STATUS[rule], FIELD: f"{batch}|{rule}"})
			counts[rule] = counts.get(rule, 0) + 1
			done += 1
			if done % 100 == 0:
				frappe.db.commit()
		except Exception:
			frappe.log_error(
				title="My Work: triage could not close a ToDo",
				message=f"{row.name}\n{frappe.get_traceback()}",
			)
	frappe.db.commit()
	if user:
		frappe.publish_realtime("ee_my_work_triage_done", {"batch": batch, "counts": counts}, user=user)
	return counts


def undo(batch):
	"""Reopen every ToDo ``batch`` closed that is still closed. Returns how many."""
	if not batch or not frappe.db.has_column("ToDo", FIELD):
		return 0
	names = frappe.get_all(
		"ToDo",
		filters={FIELD: ["like", f"{batch}|%"], "status": ["in", ["Closed", "Cancelled"]]},
		pluck="name",
		limit_page_length=0,
	)
	for name in names:
		_set(name, {"status": "Open", FIELD: None})
	frappe.db.commit()
	return len(names)


def batches():
	"""``[{"batch", "count"}]`` for every batch with ToDos still closed under it, newest first."""
	if not frappe.db.has_column("ToDo", FIELD):
		return []
	rows = frappe.get_all(
		"ToDo",
		filters={FIELD: ["is", "set"], "status": ["in", ["Closed", "Cancelled"]]},
		fields=[FIELD],
		limit_page_length=0,
	)
	counts = {}
	for row in rows:
		batch = (row.get(FIELD) or "").split("|", 1)[0]
		if batch:
			counts[batch] = counts.get(batch, 0) + 1
	return [{"batch": b, "count": counts[b]} for b in sorted(counts, reverse=True)]
