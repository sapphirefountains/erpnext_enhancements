# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Building a subcontractor scorecard from what the database actually knows — WI-075 sub-phase N.

The judgement is all in :mod:`erpnext_enhancements.quality.scorecard`, which imports no ``frappe``
and is therefore the part CI runs. This module asks the questions that one cannot.

How a measure reaches a *named* subcontractor
----------------------------------------------

This is the constraint that shaped every query below, and it is worth stating plainly because the
obvious readings are wrong.

``Scope Acceptance Criterion.responsible_party`` is a **Select** — Sapphire / Subcontractor /
Customer / Third Party. It says *a* subcontractor was responsible; it cannot say *which*. And
``Project Quality Inspection`` carries no supplier at all: an inspection covers a milestone, not a
company.

So the only path from a quality event to a named subcontractor is
``Non Conformance.custom_supplier``. Every measure here either reads that field or reads
something reachable from it, and where a measure had to be *defined* to make that possible,
:data:`MEASURE_NOTES` says so on the scorecard rather than leaving a reader to assume a stronger
attribution than exists.

``first_pass_yield`` is the one that needed defining. "Inspections this subcontractor passed
first time" is not answerable, because no inspection names a subcontractor. What is answerable is
**of the inspections on projects this subcontractor was engaged on in the period, the proportion
that raised no non-conformance against them** — and that is what it computes and what its note
says. A weaker claim, stated, beats a stronger one implied.

What this produces today
------------------------

Nothing judgeable, on every subcontractor, and that is the correct output. Measured on production
2026-09-14: every quality doctype holds zero rows, no ``Project Contract`` is a subcontractor
master agreement (all sixteen are ``maintenance`` with ``party_type = Customer``), and rework
hours and certificates of insurance are recorded nowhere at all. Each scorecard will read
*Not Measurable*.

That is the whole point. A vendor scorecard is the one artifact here that gets printed and
carried into a negotiation, and the failure mode is not a crash — it is a page asserting that
every subcontractor is flawless.

Driven daily, not monthly, on purpose
-------------------------------------

:func:`sweep` runs from ``scheduler_events["daily"]`` and builds any **closed** month that has no
scorecard yet, rather than firing once on the first of the month. A prod deploy ``FLUSHDB``s the
queue redis on ``:11000`` and silently destroys every pending background job; a monthly job
caught by a deploy that morning means no scorecards that month, with nothing to notice. A daily
job that fills gaps is self-healing, and the same reasoning already governs the Critical-NCR
sweep in sub-phase G.
"""

import frappe
from frappe.utils import add_months, get_first_day, get_last_day, getdate, now_datetime

from erpnext_enhancements.quality import msa, scorecard
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

DOCTYPE = "Subcontractor Scorecard"

#: How many months back :func:`sweep` will fill in. Enough to recover from a deploy that ate a
#: month's jobs, short enough that a dormant site does not build a year of empty scorecards the
#: first time somebody ticks the switch.
BACKFILL_MONTHS = 3

#: Ceiling on one sweep, so a first run on a busy site cannot monopolise the scheduler.
SWEEP_LIMIT = 200


# --- Period -------------------------------------------------------------------------------------


def period_for(on_date):
	"""The ``(start, end, label)`` of the calendar month containing ``on_date``."""
	day = getdate(on_date)
	start = get_first_day(day)
	end = get_last_day(day)
	return start, end, start.strftime("%Y-%m")


def closed_periods(today=None, months=BACKFILL_MONTHS):
	"""The most recent fully-elapsed months, newest first.

	A scorecard is never built for the month in progress: half a month of evidence scored against
	a whole month's thresholds reports every subcontractor as improving, every time, until the
	month ends.
	"""
	day = getdate(today or now_datetime())
	out = []
	for back in range(1, int(months) + 1):
		out.append(period_for(add_months(get_first_day(day), -back)))
	return out


# --- Who gets one -------------------------------------------------------------------------------


def _suppliers_with_project_orders(start, end):
	"""Suppliers we placed a project purchase order with inside the period.

	``ifnull(nullif(poi.project, ''), po.project)`` is the union from sub-phase L: either project
	field alone drops orders, silently, and 40 of 148 live pending lines carry no row project.
	"""
	rows = frappe.db.sql(
		"""
		select distinct po.supplier
		  from `tabPurchase Order Item` poi
		  join `tabPurchase Order` po on po.name = poi.parent
		 where po.docstatus = 1
		   and po.transaction_date between %(start)s and %(end)s
		   and ifnull(nullif(poi.project, ''), po.project) <> ''
		""",
		{"start": start, "end": end},
		as_dict=True,
	)
	return {row.supplier for row in rows if row.supplier}


def _suppliers_with_ncrs(start, end):
	if not frappe.db.has_column("Non Conformance", "custom_supplier"):
		return {}
	rows = frappe.db.sql(
		"""
		select custom_supplier as supplier, count(*) as n
		  from `tabNon Conformance`
		 where ifnull(custom_supplier, '') <> ''
		   and ifnull(custom_raised_on, creation) between %(start)s and %(end)s
		 group by custom_supplier
		""",
		{"start": start, "end": end},
		as_dict=True,
	)
	return {row.supplier: row.n for row in rows}


def _suppliers_with_agreements():
	"""Suppliers covered by a signed master agreement, whenever it was signed.

	Included even with no activity in the period: an agreement in force is itself a thing to
	report on, and a subcontractor who did no work this month has not stopped being under
	contract. Returns ``{}`` on this site today — all sixteen contracts are ``maintenance``
	agreements with customers and the ``msa`` template has never been used.
	"""
	rows = frappe.db.sql(
		"""
		select name, party, signed_on, contract_date, msa_term_months,
		       msa_expires_on, msa_expiry_effective
		  from `tabProject Contract`
		 where template_key = 'msa' and party_type = 'Supplier'
		   and ifnull(party, '') <> '' and docstatus < 2
		""",
		as_dict=True,
	)
	return {row.party: row for row in rows}


def population(start, end):
	"""``{supplier: [reasons]}`` for one period.

	Derived from behaviour rather than from ``supplier_group``, which cannot separate a
	subcontractor from a stationery vendor — 908 of the 1181 suppliers sit in the group
	``Labels``.
	"""
	ordered = _suppliers_with_project_orders(start, end)
	ncrs = _suppliers_with_ncrs(start, end)
	agreements = _suppliers_with_agreements()

	out = {}
	for supplier in set(ordered) | set(ncrs) | set(agreements):
		reasons = scorecard.engagement_reasons(
			supplier in ordered, ncrs.get(supplier, 0), supplier in agreements
		)
		if reasons:
			out[supplier] = reasons
	return out


# --- The measures ---------------------------------------------------------------------------------


def _ncr_rows(supplier, start, end):
	"""Every non-conformance attributed to this subcontractor in the period."""
	if not frappe.db.has_column("Non Conformance", "custom_supplier"):
		return []
	return frappe.db.sql(
		"""
		select name, ifnull(custom_severity, '') as severity,
		       ifnull(custom_raised_on, date(creation)) as raised_on,
		       ifnull(custom_inspection_source_key, '') as source_key,
		       ifnull(custom_project, '') as project,
		       ifnull(custom_quality_action, '') as action,
		       ifnull(custom_recovery_claimed, 0) as claimed,
		       ifnull(custom_recovery_recovered, 0) as recovered,
		       subject
		  from `tabNon Conformance`
		 where custom_supplier = %(supplier)s
		   and ifnull(custom_raised_on, date(creation)) between %(start)s and %(end)s
		""",
		{"supplier": supplier, "start": start, "end": end},
		as_dict=True,
	)


def _hold_point_keys(source_keys):
	"""Which of these criterion keys are hold points on their locked Scope of Work.

	``Inspection Result`` does not carry ``is_hold_point`` — it carries ``source_key``, the
	stable criterion key — so the flag is read back from the criterion it froze a copy of. That
	is provenance, not a live re-read of the standard: the result row keeps its own frozen copy
	of what it was judged against.
	"""
	keys = [k for k in source_keys if k]
	if not keys:
		return set()
	rows = frappe.db.get_all(
		"Scope Acceptance Criterion",
		filters={"criterion_key": ("in", keys), "is_hold_point": 1},
		fields=["criterion_key"],
	)
	return {row.criterion_key for row in rows}


def _inspection_sample(supplier, start, end):
	"""``(inspections_on_their_projects, inspections_with_no_NCR_against_them)``.

	See the module docstring: no inspection names a subcontractor, so first-pass yield is defined
	as the proportion of inspections *on projects this subcontractor was engaged on* that raised
	no non-conformance against them. The scorecard prints that definition in the measure note
	rather than letting a reader assume a stronger attribution than exists.
	"""
	projects = frappe.db.sql(
		"""
		select distinct ifnull(nullif(poi.project, ''), po.project) as project
		  from `tabPurchase Order Item` poi
		  join `tabPurchase Order` po on po.name = poi.parent
		 where po.docstatus = 1 and po.supplier = %(supplier)s
		   and ifnull(nullif(poi.project, ''), po.project) <> ''
		""",
		{"supplier": supplier},
		as_dict=True,
	)
	names = [row.project for row in projects if row.project]
	if not names:
		return 0, 0

	inspections = frappe.db.get_all(
		"Project Quality Inspection",
		filters={
			"project": ("in", names),
			"inspection_date": ("between", [start, end]),
			"docstatus": 1,
		},
		fields=["name"],
	)
	if not inspections:
		return 0, 0

	flagged = frappe.db.get_all(
		"Non Conformance",
		filters={
			"custom_supplier": supplier,
			"custom_inspection": ("in", [row.name for row in inspections]),
		},
		fields=["custom_inspection"],
	)
	blamed = {row.custom_inspection for row in flagged}
	return len(inspections), len([r for r in inspections if r.name not in blamed])


def _days_to_close(actions):
	"""Average days from raising a corrective action to it being verified closed."""
	if not actions:
		return []
	rows = frappe.db.get_all(
		"Quality Action",
		filters={"name": ("in", actions), "custom_closed_on": ("is", "set")},
		fields=["name", "date", "custom_closed_on"],
	)
	spans = []
	for row in rows:
		if not (row.date and row.custom_closed_on):
			continue
		spans.append((getdate(row.custom_closed_on) - getdate(row.date)).days)
	return spans


def _agreement_state(supplier, agreements, end):
	"""The master agreement's expiry state at the end of the period, via sub-phase L's rules."""
	row = agreements.get(supplier)
	if not row:
		return msa.STATE_UNKNOWN
	expiry = row.get("msa_expiry_effective") or msa.expires_on(
		row.get("signed_on") or row.get("contract_date"),
		row.get("msa_term_months"),
		row.get("msa_expires_on"),
	)
	state, _days = msa.expiry_state(expiry, end)
	return state


def _has_rows(doctype):
	"""Whether an instrument holds any rows at all on this site. Cached per request."""
	cache = frappe.local.__dict__.setdefault("_ee_scorecard_source_rows", {})
	if doctype in cache:
		return cache[doctype]
	try:
		present = bool(frappe.db.sql(f"select name from `tab{doctype}` limit 1"))
	except Exception:
		present = False
	cache[doctype] = present
	return present


def measures_for(supplier, start, end, agreements=None):
	"""``(measure_rows, evidence_rows)`` for one subcontractor and period."""
	agreements = agreements if agreements is not None else _suppliers_with_agreements()

	ncrs = _ncr_rows(supplier, start, end)
	hold_keys = _hold_point_keys([row.source_key for row in ncrs])
	hold_failures = [row for row in ncrs if row.source_key in hold_keys]
	critical = [row for row in ncrs if row.severity == "Critical"]
	spans = _days_to_close([row.action for row in ncrs if row.action])
	attempted, first_time = _inspection_sample(supplier, start, end)

	claimed = sum(float(row.claimed or 0) for row in ncrs)
	recovered = sum(float(row.recovered or 0) for row in ncrs)

	values = {
		"ncrs_raised": (len(ncrs), len(ncrs)),
		"critical_ncrs": (len(critical), len(ncrs)),
		"hold_point_failures": (len(hold_failures), len(ncrs)),
		"first_pass_yield": (scorecard.ratio(first_time, attempted), attempted),
		"days_to_close": (scorecard.average(spans), len(spans)),
		"recovery_rate": (scorecard.ratio(recovered, claimed) if claimed else None, len(ncrs)),
		"rework_hours": (None, 0),
		"agreement_currency": (_agreement_state(supplier, agreements, end), 1),
		"insurance_currency": (None, 0),
	}

	rows = []
	for key, label, uom, _direction, source, threshold, note in scorecard.MEASURES:
		value, sample = values.get(key, (None, 0))
		doctype = None if source == scorecard.SOURCE_NONE else source
		verdict = scorecard.coverage(source, _has_rows(doctype) if doctype else False)
		rows.append(
			{
				"measure_key": key,
				"label": label,
				"value_raw": "" if value is None else str(value),
				"value_display": _display(value, uom),
				"coverage": verdict,
				"met": _verdict_label(key, value, verdict),
				"sample_size": sample,
				"threshold_display": _threshold_display(key, uom, threshold),
				"note": note,
			}
		)

	evidence = []
	for row in ncrs:
		evidence.append(
			{
				"measure_key": "critical_ncrs" if row.severity == "Critical" else "ncrs_raised",
				"document_type": "Non Conformance",
				"document_name": row.name,
				"occurred_on": row.raised_on,
				"summary": (row.subject or "")[:140],
			}
		)
	for row in hold_failures:
		evidence.append(
			{
				"measure_key": "hold_point_failures",
				"document_type": "Non Conformance",
				"document_name": row.name,
				"occurred_on": row.raised_on,
				"summary": "Hold point: " + (row.subject or "")[:120],
			}
		)

	return rows, evidence


def _display(value, uom):
	"""What a person reads. An em dash for no sample — never a zero standing in for one."""
	if value is None or value == "":
		return "—"
	if uom == "%":
		return f"{float(value):g}%"
	if uom == "days":
		return f"{float(value):g} days"
	if uom == "state":
		return str(value)
	return str(value)


def _threshold_display(key, uom, threshold):
	if key in ("agreement_currency", "insurance_currency"):
		return "in force"
	if threshold is None:
		return "—"
	row = scorecard.measure(key)
	direction = row[3] if row else None
	symbol = "<=" if direction == scorecard.LOWER_IS_BETTER else ">="
	suffix = "%" if uom == "%" else (" days" if uom == "days" else "")
	return f"{symbol} {threshold:g}{suffix}"


def _verdict_label(key, value, verdict):
	if verdict != scorecard.COVERAGE_TRACKED:
		return "Not Judged"
	met = scorecard.meets_threshold(key, value)
	if met is None:
		return "Not Judged"
	return "Met" if met else "Not Met"


# --- Writing it ------------------------------------------------------------------------------------


def build(supplier, start, end, label=None, reasons=None, agreements=None):
	"""Create the scorecard for one subcontractor and period, or return the existing one.

	Never overwrites. A scorecard that has been read, adjusted and signed is a record of what was
	known then; rebuilding it on top would silently discard somebody's adjustment and the reason
	they gave for it.
	"""
	start, end = getdate(start), getdate(end)
	label = label or start.strftime("%Y-%m")

	existing = frappe.db.get_value(
		DOCTYPE, {"supplier": supplier, "period_start": start}, "name"
	)
	if existing:
		return existing

	rows, evidence = measures_for(supplier, start, end, agreements)

	doc = frappe.new_doc(DOCTYPE)
	doc.supplier = supplier
	doc.supplier_name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	doc.period_start = start
	doc.period_end = end
	doc.period_label = label
	doc.generated_on = now_datetime()
	doc.engagement_reason = "; ".join(reasons or []) or "engaged in this period"
	for row in rows:
		doc.append("measures", row)
	for row in evidence:
		doc.append("evidence", row)
	doc.insert(ignore_permissions=True)
	return doc.name


def build_period(start, end, label=None, limit=SWEEP_LIMIT):
	"""Build every missing scorecard for one period. Returns the names created."""
	agreements = _suppliers_with_agreements()
	created = []
	for supplier, reasons in population(start, end).items():
		if len(created) >= limit:
			break
		try:
			name = build(supplier, start, end, label, reasons, agreements)
		except Exception:
			# One bad supplier must not stop the rest of the month being scored.
			frappe.log_error(
				title="Subcontractor scorecard build failed",
				message=f"{supplier} {start} to {end}: {frappe.get_traceback()}",
			)
			continue
		created.append(name)
	return created


def sweep():
	"""Daily: build any closed month that has no scorecards yet.

	Daily rather than monthly because a prod deploy ``FLUSHDB``s the queue redis and silently
	destroys pending jobs — a monthly job caught by a deploy is a month with no scorecards and
	nothing to notice. Gated on ``quality_enabled``, which ships off, so this does nothing on
	production today.
	"""
	if not is_enabled():
		return

	for start, end, label in closed_periods():
		if frappe.db.exists(DOCTYPE, {"period_start": start}):
			continue
		created = build_period(start, end, label)
		if created:
			frappe.db.commit()
			return len(created)
	return 0


def refresh_supplier_fields(supplier):
	"""Stamp the latest scorecard onto the Supplier, for people who never open a scorecard.

	Written with ``frappe.db.set_value`` rather than through the document API: this runs across
	every scored supplier and Supplier carries its own ``doc_events``, so saving each one would
	fire the full hook chain for a read-only summary field. The same reasoning WI-057 records for
	Project.

	**The coverage travels with the number.** A Supplier showing ``0`` with no indication that
	nothing was measurable is precisely the misreading this whole sub-phase exists to prevent, so
	the stamped field is the sentence, not the figure.
	"""
	latest = frappe.db.get_value(
		DOCTYPE,
		{"supplier": supplier},
		["name", "score_display", "state", "period_label"],
		order_by="period_start desc",
		as_dict=True,
	)
	if not latest:
		return

	frappe.db.set_value(
		"Supplier",
		supplier,
		{
			"custom_latest_scorecard": latest.name,
			"custom_scorecard_summary": f"{latest.period_label}: {latest.score_display}",
			"custom_scorecard_state": latest.state,
		},
		update_modified=False,
	)


def refresh_all_supplier_fields():
	"""Daily: keep the Supplier summaries in step with the newest scorecards."""
	if not is_enabled():
		return
	suppliers = frappe.db.sql_list(f"select distinct supplier from `tab{DOCTYPE}`")
	for supplier in suppliers[:SWEEP_LIMIT]:
		try:
			refresh_supplier_fields(supplier)
		except Exception:
			frappe.log_error(
				title="Supplier scorecard summary failed",
				message=f"{supplier}: {frappe.get_traceback()}",
			)
	frappe.db.commit()
	return len(suppliers)
