# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The schedule of values and the pay application, as arithmetic. No frappe.

Two jobs, both in **integer cents** so a total partitions exactly and twelve months of applications
never drift a cent from the contract:

1. :func:`sov_rows` projects an Estimate's priced lines onto schedule-of-values rows, grouped by
   phase and cost type (the 2026-11-04 rule, editable afterwards) or one row per line. Every row is a
   sum of whole cents of the estimate's own line prices, so the rows sum to the estimate exactly: the
   projection is a **partition**, and :func:`sov_rows` asserts it with ``==``, not a tolerance. The
   estimate's lines already carry their share of contingency, insurance and fee (overhead
   distributed across lines, the default presentation), so nothing is apportioned here.
2. :func:`price_application` is the AIA G703 continuation sheet and the G702's nine lines.

The G703, per line *i* of application *n* (TASK-2026-02125)::

    C scheduled value         the line's value on the schedule (a change order row carries its own)
    D from previous           = work completed on line i in applications 1..n-1  (NOT their column G)
    E this period             = C x this period's percent       (the only monthly input, Nik 2026-11-06)
    F stored materials        a BALANCE on hand now, not an accumulation; off unless the job turns it on
    G completed and stored    = D + E + F
    H percent                 = G / C        (None when C is 0: never 0%, never a huge number)
    I balance to finish       = C - G
    retainage on work         = what earlier applications held on this line + E x work % in force now
    retainage on stored       = F x stored %   (stored is a balance, so its retainage is too)

**Column D is the earlier applications' work completed, not their column G.** Stored material
moves into completed work as it is installed, so carrying column G forward counts it twice. The
template workbook's "E = prior H" makes exactly that mistake.

The G702 (TASK-2026-02126)::

    1 original contract sum        the schedule's contract sum
    2 net change by change orders  sum of C over the change-order rows on this application
    3 contract sum to date         1 + 2
    4 completed and stored to date total of G
    5 retainage                    5a on work + 5b on stored (two rows: two rates)
    6 earned less retainage        4 - 5
    7 less previous certificates   what the earlier applications were CERTIFIED for, summed
    8 current payment due          6 - 7
    9 balance to finish, with retainage   3 - 6

Line 7 sums the amounts **certified**, not the amounts due (agreed 2026-11-04): when a general
contractor certifies less than was asked, the shortfall is carried into the next application's line
8 by construction. The totals row sums each column on its own; no total is derived from another.

**Retainage** (D16): each line's rates resolve line override -> application override -> the
schedule's rate in force -> 0 when the job holds no retainage, and the rate actually used is stored
on the line. The schedule may step down (10% until 50% complete, then 5%): the step in force is
chosen by the **cumulative percent complete at the START of the period**, i.e. columns D over the
contract sum to date before this period's work, so a month's own progress never changes the rate it
is billed at.

**Keep holding (Nik, 2026-11-19).** Retainage on work is cumulative: each application holds what the
one before it held on the line, plus this period's work at the rate in force now. So when retainage
steps down from 10% to 5% at 50% complete, the work billed before the step stays held at 10% until
final release, and only work after it is held at 5%; a step-down, or an override, never releases
earlier retainage. (Re-rating all work to date, ``(D + E) x rate``, would have let the application
that crosses the step pay out half of everything held so far.) A line whose work goes down releases
retainage at today's rate, never below zero. Retainage on stored materials stays ``F x rate``,
because stored is a balance.

**Finishing a line.** A percent of C seldom lands on the exact cents left. When the percent typed is
the percent remaining (to two places), E is the balance itself, so a line marked finished always
closes to zero.

Store or derive: the controller stores every number here on the application, because a certified
G702 is a record that must not move when the schedule is later revised. Nothing is derived on read.
"""

from erpnext_enhancements.estimating import estimate_math as em

#: Cost types in schedule order, and how a grouped row names them.
TYPE_ORDER = {t: i for i, t in enumerate(em.COST_TYPES)}
TYPE_LABEL = {
	"Material": "materials",
	"Equipment": "equipment",
	"Labor": "labor",
	"Subcontract": "subcontracted work",
	"Expense": "expenses",
}


# ---------------------------------------------------------------------------- money


def cents(value):
	"""Dollars (float, str or None) to whole cents, banker's-rounded the way frappe rounds."""
	return int(round(em.round_money(value) * 100))


def dollars(value_cents):
	return value_cents / 100


def share(base_cents, pct):
	"""``pct`` percent of ``base_cents``, in whole cents, banker's-rounded."""
	if not base_cents or not pct:
		return 0
	return cents(base_cents * float(pct) / 10000)


def percent(part_cents, whole_cents):
	"""``part / whole`` as a percent to two places, or None when ``whole`` is 0."""
	if not whole_cents:
		return None
	return em.round_money(part_cents * 100 / whole_cents, 2)


# ------------------------------------------------------------------- the projection


def _desc(text):
	return (text or "").strip()


def sov_rows(phases, lines, rule="phase_cost_type", total=None):
	"""Schedule-of-values rows from an Estimate's phases and priced lines.

	``lines`` are dicts with ``idx``, ``phase_number``, ``cost_type``, ``description``, ``amount``
	and ``by_others``. ``rule`` is ``"phase_cost_type"`` (the default: one row per phase and cost
	type) or ``"line"`` (one row per estimate line). Rows come back in phase order, then cost type,
	then the estimate's own order, so the same estimate always gives the same schedule whatever order
	its lines arrive in.

	Returns ``(rows, check)``: rows are ``{"item_no", "phase_number", "description", "cost_type",
	"scheduled_value", "by_others", "source_lines"}``; ``check`` is ``{"sum", "lines_sum",
	"total", "difference"}`` in dollars, where ``difference`` is ``total - sum`` (0.0 when no total is
	given). A "by others" line is its own row, flagged and worth nothing.
	"""
	labels = {em.phase_number(p.get("phase_number")): _desc(p.get("label")) for p in phases or []}
	ordered = sorted(
		lines or [],
		key=lambda l: (
			em.phase_number(l.get("phase_number")),
			TYPE_ORDER.get(l.get("cost_type"), len(TYPE_ORDER)),
			int(l.get("idx") or 0),
		),
	)
	groups = {}
	order = []
	for line in ordered:
		number = em.phase_number(line.get("phase_number"))
		if line.get("by_others"):
			key = ("by_others", number, int(line.get("idx") or 0))
		elif rule == "line":
			key = ("line", number, int(line.get("idx") or 0))
		else:
			key = ("group", number, line.get("cost_type") or "")
		if key not in groups:
			groups[key] = []
			order.append(key)
		groups[key].append(line)

	rows = []
	counters = {}
	lines_sum = 0
	for key in order:
		members = groups[key]
		number = key[1]
		top = em.top_phase(number)
		group_no = top // 100 if top else 0
		counters[group_no] = counters.get(group_no, 0) + 1
		item_no = f"{group_no}.{counters[group_no]}" if group_no else str(counters[group_no])
		by_others = key[0] == "by_others"
		value = 0 if by_others else sum(cents(m.get("amount")) for m in members)
		lines_sum += value
		if len(members) == 1:
			description = _desc(members[0].get("description")) or "Line"
		else:
			label = labels.get(number) or em_label(number)
			description = f"{label}: {TYPE_LABEL.get(key[2], key[2].lower())}"
		rows.append(
			{
				"item_no": item_no,
				"phase_number": number,
				"description": description[:140],
				"cost_type": members[0].get("cost_type") or "",
				"scheduled_value": dollars(value),
				"by_others": 1 if by_others else 0,
				"source_lines": ",".join(str(int(m.get("idx") or 0)) for m in members),
			}
		)
	row_sum = sum(cents(r["scheduled_value"]) for r in rows)
	# The partition: the rows are the estimate's lines regrouped, so they sum to them exactly.
	assert row_sum == lines_sum, (row_sum, lines_sum)
	total_cents = cents(total) if total is not None else row_sum
	return rows, {
		"sum": dollars(row_sum),
		"lines_sum": dollars(lines_sum),
		"total": dollars(total_cents),
		"difference": dollars(total_cents - row_sum),
	}


def em_label(number):
	from erpnext_enhancements.estimating import rules

	return rules.phase_label(number, {})


# ---------------------------------------------------------------------- retainage


def step_in_force(steps, pct_complete_at_start):
	"""The retainage step whose ``from_percent_complete`` is the highest one at or below the job's
	percent complete at the start of the period, or None when no step has been reached."""
	reached = [
		s
		for s in steps or []
		if float(s.get("from_percent_complete") or 0) <= float(pct_complete_at_start or 0) + 1e-9
	]
	if not reached:
		return None
	return max(reached, key=lambda s: float(s.get("from_percent_complete") or 0))


def resolve_rates(schedule, application, pct_complete_at_start):
	"""The job-level retainage rates for this application: ``(work_pct, stored_pct, source)``.

	``schedule`` carries ``retainage_applies``, ``retainage_pct``, ``stored_retainage_pct`` and
	``steps``; ``application`` carries the override strings ``work_retainage_override`` and
	``stored_retainage_override`` (blank inherits, 0 is a real rate). ``source`` says where the work
	rate came from, for the form to show.
	"""
	if not schedule.get("retainage_applies"):
		return 0.0, 0.0, "none"
	work = float(schedule.get("retainage_pct") or 0)
	stored = float(schedule.get("stored_retainage_pct") or 0)
	source = "schedule"
	step = step_in_force(schedule.get("steps"), pct_complete_at_start)
	if step is not None:
		work = float(step.get("work_pct") or 0)
		stored = float(step.get("stored_pct") or 0)
		source = f"step from {em.round_money(step.get('from_percent_complete') or 0, 2):g}%"
	work_override = em.parse_rate(application.get("work_retainage_override"))
	stored_override = em.parse_rate(application.get("stored_retainage_override"))
	if work_override is not None:
		work, source = work_override, "application"
	if stored_override is not None:
		stored = stored_override
	return work, stored, source


# ------------------------------------------------------------------ the application


def price_application(schedule, application, lines, previous):
	"""Price one pay application. Returns ``{"lines": [...], "totals": {...}}`` in dollars.

	``schedule``: ``contract_sum``, ``stored_materials`` and the retainage fields of
	:func:`resolve_rates`. ``application``: ``less_previous_certificates`` and the two overrides.
	``lines``: ``line_key``, ``scheduled_value``, ``this_period_pct``, ``stored``, ``by_others``,
	``is_change_order``, ``work_retainage_override``, ``stored_retainage_override``.
	``previous``: per line key, from the application before, either ``{"work": its D + E,
	"retainage_work": the retainage on work it held}`` or a bare number (work completed, with no
	retainage history: an opening balance, held at today's rate).
	"""
	stored_on = bool(schedule.get("stored_materials"))
	prepared = []
	for line in lines or []:
		c = cents(line.get("scheduled_value"))
		entry = (previous or {}).get(line.get("line_key"))
		if isinstance(entry, dict):
			d = cents(entry.get("work"))
			held = entry.get("retainage_work")
			held = None if held is None else cents(held)
		else:
			d, held = cents(entry or 0), None
		prepared.append((line, c, d, held))

	contract_to_date = sum(p[1] for p in prepared)
	start_pct = percent(sum(p[2] for p in prepared), contract_to_date) or 0
	job_work, job_stored, source = resolve_rates(schedule, application, start_pct)

	out = []
	for line, c, d, held in prepared:
		by_others = bool(line.get("by_others"))
		pct = 0.0 if by_others else float(line.get("this_period_pct") or 0)
		remaining = percent(c - d, c)
		if pct and remaining is not None and em.round_money(pct, 2) == remaining:
			e = c - d  # finishing the line closes it to the cent
		else:
			e = share(c, pct)
		f = cents(line.get("stored")) if stored_on and not by_others else 0
		g = d + e + f
		work_pct = em.parse_rate(line.get("work_retainage_override"))
		stored_pct = em.parse_rate(line.get("stored_retainage_override"))
		if not schedule.get("retainage_applies"):
			work_pct, stored_pct = 0.0, 0.0
		work_pct = job_work if work_pct is None else work_pct
		stored_pct = job_stored if stored_pct is None else stored_pct
		# Keep holding (Nik, 2026-11-19): what was held stays held at the rate it was held at, and only this
		# period's work is held at the rate in force now. A step-down never releases earlier retainage.
		if held is None:
			held = share(d, work_pct)  # an opening balance with no history: held at today's rate
		ret_work = held + share(e, work_pct)
		if c >= 0 and ret_work < 0:
			ret_work = 0
		ret_stored = share(f, stored_pct)
		out.append(
			{
				"line_key": line.get("line_key"),
				"scheduled_value": dollars(c),
				"from_previous": dollars(d),
				"retainage_previous": dollars(held),
				"this_period_pct": pct,
				"this_period": dollars(e),
				"stored": dollars(f),
				"total_completed": dollars(g),
				"percent_complete": percent(g, c),
				"balance": dollars(c - g),
				"work_completed_to_date": dollars(d + e),
				"work_retainage_pct": work_pct,
				"stored_retainage_pct": stored_pct,
				"retainage_work": dollars(ret_work),
				"retainage_stored": dollars(ret_stored),
				"retainage": dollars(ret_work + ret_stored),
				"_c": c,
				"_e": e,
				"_g": g,
				"_rw": ret_work,
				"_rs": ret_stored,
				"_co": bool(line.get("is_change_order")),
			}
		)

	original = cents(schedule.get("contract_sum"))
	change = sum(r["_c"] for r in out if r["_co"])
	completed = sum(r["_g"] for r in out)
	ret_work = sum(r["_rw"] for r in out)
	ret_stored = sum(r["_rs"] for r in out)
	earned = completed - ret_work - ret_stored
	prev_cert = cents(application.get("less_previous_certificates"))
	totals = {
		"original_contract_sum": dollars(original),
		"net_change_orders": dollars(change),
		"contract_sum_to_date": dollars(original + change),
		"scheduled_total": dollars(contract_to_date),
		"from_previous_total": dollars(sum(cents(r["from_previous"]) for r in out)),
		"this_period_total": dollars(sum(r["_e"] for r in out)),
		"stored_total": dollars(sum(cents(r["stored"]) for r in out)),
		"total_completed_stored": dollars(completed),
		"balance_total": dollars(sum(r["_c"] - r["_g"] for r in out)),
		"retainage_work": dollars(ret_work),
		"retainage_stored": dollars(ret_stored),
		"total_retainage": dollars(ret_work + ret_stored),
		"total_earned_less_retainage": dollars(earned),
		"less_previous_certificates": dollars(prev_cert),
		"current_payment_due": dollars(earned - prev_cert),
		"balance_to_finish": dollars(original + change - earned),
		"percent_complete": percent(completed, original + change),
		"percent_complete_at_start": start_pct,
		"work_retainage_pct": job_work,
		"stored_retainage_pct": job_stored,
		"retainage_source": source,
	}
	for r in out:
		for k in ("_c", "_e", "_g", "_rw", "_rs", "_co"):
			r.pop(k)
	return {"lines": out, "totals": totals}


# ----------------------------------------------------------------------- refusals


def refusals(context, priced):
	"""Every reason this application cannot be certified, all at once, in the order a person fixes
	them (the ``change_orders.blocking_reasons`` shape). ``[]`` when it may be certified.

	``context``: ``schedule_locked``, ``schedule_keys`` (line keys on the schedule now),
	``previous`` (``None`` or ``{"name", "docstatus", "period_to"}``), ``period_from``,
	``period_to`` (ISO date strings), ``stored_materials``, ``amount_certified`` and ``lines``
	(the input lines, aligned with ``priced["lines"]``).
	"""
	out = []
	if not context.get("schedule_locked"):
		out.append(("SCHEDULE_NOT_LOCKED", "Lock the schedule of values before certifying against it."))
	previous = context.get("previous")
	if previous and previous.get("docstatus") != 1:
		out.append(
			(
				"PREVIOUS_NOT_CERTIFIED",
				f"Application {previous.get('name')} comes first: certify it before this one.",
			)
		)
	start, end = context.get("period_from"), context.get("period_to")
	if not end:
		out.append(("PERIOD_MISSING", "Give the period this application runs to."))
	elif start and start > end:
		out.append(("PERIOD_ORDER", "The period ends before it starts."))
	if previous and previous.get("period_to") and start and start <= previous["period_to"]:
		out.append(
			(
				"PERIOD_OVERLAPS",
				f"The period starts on or before {previous['period_to']}, when application "
				f"{previous.get('name')} ended.",
			)
		)

	keys = set(context.get("schedule_keys") or [])
	gone, over, negative, zero_work, stored_off, bad_rate, others = [], [], [], [], [], [], []
	below_zero, stored_negative = [], []
	for line, row in zip(context.get("lines") or [], priced.get("lines") or []):
		name = f"{line.get('item_no') or ''} {line.get('description') or ''}".strip()
		if line.get("line_key") not in keys:
			gone.append(name)
		c, g, e = cents(row["scheduled_value"]), cents(row["total_completed"]), cents(row["this_period"])
		if (c >= 0 and g > c) or (c < 0 and g < c):
			over.append(name)
		if c > 0 and e < 0 and not (line.get("adjustment_note") or "").strip():
			negative.append(name)
		if c >= 0 and g < 0:
			below_zero.append(name)
		if cents(row["stored"]) < 0:
			stored_negative.append(name)
		if c == 0 and (g or e or float(line.get("this_period_pct") or 0)) and not line.get("by_others"):
			zero_work.append(name)
		if line.get("by_others") and float(line.get("this_period_pct") or 0):
			others.append(name)
		if not context.get("stored_materials") and cents(line.get("stored")):
			stored_off.append(name)
		for pct in (row["work_retainage_pct"], row["stored_retainage_pct"]):
			if pct < 0 or pct > 100:
				bad_rate.append(name)
				break

	def listed(names):
		shown = ", ".join(names[:8])
		return shown + (f" and {len(names) - 8} more" if len(names) > 8 else "")

	if gone:
		out.append(("LINE_NOT_ON_SCHEDULE", f"No longer on the schedule of values: {listed(gone)}."))
	if over:
		out.append(("OVER_SCHEDULED", f"Billed past the scheduled value: {listed(over)}."))
	if below_zero:
		out.append(
			("COMPLETED_BELOW_ZERO", f"Completed to date would fall below zero: {listed(below_zero)}.")
		)
	if stored_negative:
		out.append(("STORED_NEGATIVE", f"Stored materials cannot be negative: {listed(stored_negative)}."))
	if negative:
		out.append(
			("NEGATIVE_WITHOUT_NOTE", f"A negative this-period amount needs a note: {listed(negative)}.")
		)
	if zero_work:
		out.append(("WORK_ON_ZERO_LINE", f"Work billed on a line scheduled at nothing: {listed(zero_work)}."))
	# The controller sets these two back to 0 before pricing, so through the Desk they never fire; the
	# engine keeps them for any other caller that hands it raw lines.
	if others:
		out.append(("BY_OTHERS_BILLED", f"Lines by others are never billed: {listed(others)}."))
	if stored_off:
		out.append(("STORED_WHILE_OFF", f"Stored materials are off for this job: {listed(stored_off)}."))
	if bad_rate:
		out.append(("RETAINAGE_OUT_OF_RANGE", f"Retainage must be 0 to 100%: {listed(bad_rate)}."))

	totals = priced.get("totals") or {}
	due = cents(totals.get("current_payment_due"))
	if due < 0:
		out.append(
			(
				"PAYMENT_DUE_NEGATIVE",
				"The payment due is negative: this period takes back more than it bills. Lower the "
				"retainage change or raise a deduction change order instead.",
			)
		)
	certified = context.get("amount_certified")
	if certified is not None:
		certified = cents(certified)
		if certified < 0:
			out.append(("CERTIFIED_NEGATIVE", "The amount certified cannot be negative."))
		elif certified > due:
			out.append(("CERTIFIED_OVER_DUE", "The amount certified is more than the payment due."))
	return out


def next_previous_certificates(previous):
	"""Line 7 of the next application: what this one's line 7 was, plus what it was certified for."""
	if not previous:
		return 0.0
	return dollars(
		cents(previous.get("less_previous_certificates")) + cents(previous.get("amount_certified"))
	)
