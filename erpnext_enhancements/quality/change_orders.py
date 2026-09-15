# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Change orders: numbering, status and the sign of money — WI-075 sub-phase K.

A change order is the single most likely piece of scope to be sold and then forgotten, which is
why the programme's own principle cuts both ways here. *If an inspection item does not trace back
to something contracted, question why it exists* — and its converse, which is the one that costs
money: **something contracted that nothing inspects.** A change order adds contracted scope after
the Scope of Work was locked, so unless the generator is taught to look, its criteria reach no
inspection at all and nobody finds out until the walkthrough.

Four decisions live here, and each one prevents a wrong number rather than a crash.

**The number is per project, and it is not a naming series.** A series counter is global, so the
third change order on one job would be `CO-047`. `next_number` takes the numbers already used on
*that* project. Two people creating one at the same moment still collide on the document name;
the controller catches that and retries, and it catches **both** `DuplicateEntryError` and
`UniqueValidationError`, because in this framework a primary-key collision and a unique-index
collision raise different exceptions and catching only one makes the retry fail open.

**Status is derived, never typed.** A submittable document already has a state — `docstatus` —
and a second stored status beside it is a fact that can disagree with the record it summarises.
:func:`derive_status` computes it from `docstatus` and the approval stamps, so it cannot drift.

**Money carries its own sign, and the type is derived from it.** A credit change order — the
customer removing scope — is negative. The tempting shape is a positive magnitude plus an
Addition/Credit Select, and the moment those two disagree a credit is totalled as an addition.
There is one number; :func:`impact_type` reads its sign.

**An amended change order is refused outright.** Frappe's amend path appends `-1` to the name,
producing `PRJ-00580-CO-003-1` — a second commercial instrument with almost the same name as the
first. A wrong change order is voided and re-raised, not edited into a near-twin.

Imports nothing — and it lives in ``quality/`` rather than beside the DocType in
``project_enhancements/`` for exactly one reason: that package's ``__init__`` imports ``frappe``
at module scope, so anything under it is unreachable from the bench-free test tier. The same
reason put ``quality/scope_criteria.py`` here while its consumer, ``Project Scope of Work``, sits
over there. A constant nobody can import is a constant nobody checks.
"""

STATUS_DRAFT = "Draft"
STATUS_PENDING = "Pending Approval"
STATUS_LOCKED = "Locked"
STATUS_EXECUTED = "Executed"
#: One "l" — the house spelling for a cancelled state throughout this app.
STATUS_CANCELED = "Canceled"

STATUSES = (STATUS_DRAFT, STATUS_PENDING, STATUS_LOCKED, STATUS_EXECUTED, STATUS_CANCELED)

IMPACT_ADDITION = "Addition"
IMPACT_CREDIT = "Credit"
IMPACT_NONE = "No Cost"

#: Who caused the change. Attribution is the thing `docs/KPI_DASHBOARD_DESIGN.md` says is
#: impossible today, and it is the whole reason a change order is a record rather than a
#: contract revision: a revision count cannot tell a customer's addition from our own error.
CAUSES = (
	"Customer Request",
	"Sapphire Error",
	"Subcontractor",
	"Site Condition",
	"Design Error",
	"Code or Inspector",
)

#: Causes that are our own. Counted separately because "how much change did we cause" is a
#: different question from "how much did the job change", and only the first is a quality signal.
INTERNAL_CAUSES = ("Sapphire Error", "Design Error")


def next_number(used_numbers):
	"""The next change-order number for one project.

	Takes the numbers already used **on that project**, not a global counter: a naming series
	would make the third change order on one job `CO-047`, which tells a customer reading it
	how busy the rest of the company has been and nothing about their own job.

	Gaps are preserved rather than filled. If `CO-002` was voided, the next one is `CO-004` —
	reusing 3 would put two different documents behind one number in anybody's email.
	"""
	highest = 0
	for value in used_numbers or []:
		try:
			number = int(value)
		except (TypeError, ValueError):
			continue
		highest = max(highest, number)
	return highest + 1


def co_name(project, number):
	"""``PRJ-00580-CO-003``. Zero-padded so a list sorts the way a person reads it."""
	return f"{project}-CO-{int(number):03d}"


def derive_status(docstatus, pm_approved=False, customer_approved=False, executed_on=None):
	"""The status, computed from what the document actually is.

	Never stored from a form field. A submittable document already carries a state in
	``docstatus``; a second status that somebody can type is a fact that can disagree with the
	record it claims to summarise, and on a commercial instrument that disagreement is the
	argument.
	"""
	if int(docstatus or 0) == 2:
		return STATUS_CANCELED
	if int(docstatus or 0) == 1:
		return STATUS_EXECUTED if executed_on else STATUS_LOCKED
	return STATUS_PENDING if (pm_approved or customer_approved) else STATUS_DRAFT


def impact_type(cost_impact):
	"""``Addition`` / ``Credit`` / ``No Cost`` from the sign of one signed number.

	Derived rather than chosen, because a magnitude plus a type is two fields that can disagree
	about the same fact — and the first time they do, a credit is totalled as an addition and the
	change-order value on a dashboard is simply wrong.
	"""
	try:
		value = float(cost_impact or 0)
	except (TypeError, ValueError):
		return IMPACT_NONE
	if value > 0:
		return IMPACT_ADDITION
	if value < 0:
		return IMPACT_CREDIT
	return IMPACT_NONE


def blocking_reasons(doc):
	"""Why this change order may not be submitted yet, in the order a person would fix them.

	Submit is the lock: after it the change order is contractual and its criteria start appearing
	on inspections. So the gates are approval gates, not tidiness gates.
	"""
	reasons = []
	if not _get(doc, "project"):
		reasons.append("It is not attached to a project.")
	if not (_get(doc, "cause") or "").strip():
		reasons.append("Nobody has said what caused it, so it cannot be attributed later.")
	if not (_get(doc, "client_request_description") or "").strip():
		reasons.append("There is no description of what changed.")
	if not _get(doc, "pm_approved"):
		reasons.append("The project manager has not approved it.")
	if not _get(doc, "customer_approved"):
		reasons.append("The customer has not approved it.")
	return reasons


def unassigned_added_criteria(criteria):
	"""Added criteria naming no milestone — sold, and inspected by nothing.

	The same reporting `quality.merge.unassigned_criteria` does for the Scope of Work, and it
	matters more here: a change order is the scope most likely to be agreed in a hurry and
	remembered by nobody.
	"""
	return [
		(_get(row, "criterion_key", ""), _get(row, "criterion", ""))
		for row in criteria or []
		if not (_get(row, "inspect_at_milestone", "") or "").strip()
	]


def totals(rows):
	"""``{addition, credit, net, schedule_days, internal_count, count}`` across change orders.

	`addition` and `credit` are reported separately as positive figures, and `net` is their sum
	with signs intact. A single net number hides a job that added fifty thousand and credited
	forty-eight, which is a different job from one that barely changed.
	"""
	addition = credit = net = 0.0
	schedule = 0
	internal = 0
	rows = rows or []
	for row in rows:
		try:
			value = float(_get(row, "cost_impact", 0) or 0)
		except (TypeError, ValueError):
			value = 0.0
		net += value
		if value > 0:
			addition += value
		elif value < 0:
			credit += -value
		try:
			schedule += int(_get(row, "schedule_impact_days", 0) or 0)
		except (TypeError, ValueError):
			pass
		if _get(row, "cause") in INTERNAL_CAUSES:
			internal += 1
	return {
		"addition": round(addition, 2),
		"credit": round(credit, 2),
		"net": round(net, 2),
		"schedule_days": schedule,
		"internal_count": internal,
		"count": len(rows),
	}


def _get(obj, field, default=None):
	if obj is None:
		return default
	if isinstance(obj, dict):
		return obj.get(field, default)
	return getattr(obj, field, default)
