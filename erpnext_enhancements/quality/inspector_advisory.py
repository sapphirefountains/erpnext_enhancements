# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Warning that the wrong person is holding the clipboard — and never stopping them.

WI-075 sub-phase H. A direct descendant of :mod:`erpnext_enhancements.training.compliance`,
down to the decorator: gate, then swallow. **A bug in a qualification *warning* must not be able
to fail an inspector's save.**

Why advisory and not a gate, on this site specifically
------------------------------------------------------

Two Senior Technicians, no Masters, one Project Manager. A hard gate would routinely prevent an
inspection being *recorded* rather than prevent unqualified work being done — and an inspection
that happened and was never written down is strictly worse than one written down by somebody the
template did not ask for, because the second at least leaves a trail that can be questioned
later. The decision is in the work item and is not for this file to revisit.

Three halves, deliberately
---------------------------

The **inline** half is a ``msgprint`` the person saving actually reads. The **durable** half is a
timeline comment, enqueued ``after_commit`` because on a brand-new inspection the document does
not exist until the transaction lands. The **escalation** half emails the inspector's manager,
because a warning only the warned person sees is a warning with no consequence.

Repeat saves are deduped on a fingerprint of the findings, so five saves of one unchanged
inspection leave one comment rather than five — the timeline becoming noise is the failure this
whole advisory was supposed to avoid being.

The judgement is not here
--------------------------

Who counts as qualified lives in :mod:`erpnext_enhancements.quality.qualification`, which
imports nothing and is asserted on every push. It has to be: nothing downstream ever fails
because this got the answer wrong, so a warning that is wrong in the quiet direction is
indistinguishable from one that is right.
"""

import functools

import frappe
from frappe.utils import escape_html, nowdate

from erpnext_enhancements.quality import qualification
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: Marker embedded in the comment so a repeat save can find its own previous warning. A marker
#: rather than a field: an advisory does not deserve a column on the record it advises about.
MARKER = "quality-inspector-advisory"


# ----------------------------------------------------------------------- guards


def _log_quietly(where):
	"""Log a swallowed failure, and swallow a failure to log.

	``log_error`` writes a document, and inside a transaction that is already unhappy that write
	can fail too. An advisory that raises while reporting that it raised is the same outage
	twice.
	"""
	try:
		frappe.log_error(
			title="Quality inspector advisory failed",
			message=f"{where}\n{frappe.get_traceback()}",
		)
	except Exception:
		pass


def _should_run():
	flags = frappe.flags
	if flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import:
		return False
	if not is_enabled():
		return False
	try:
		return bool(
			frappe.db.get_single_value("Quality Settings", "advisory_inspector_qualification")
		)
	except Exception:
		return False


def _advisory(fn):
	"""Gate + swallow. Every entry point in this module wears it.

	The gate lives here rather than in each function so "did anyone remember to check the
	setting" has one answer instead of several.
	"""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		try:
			if not _should_run():
				return None
			return fn(*args, **kwargs)
		except Exception:
			_log_quietly(fn.__name__)
		return None

	return wrapper


# ------------------------------------------------------------------ entry point


@_advisory
def warn_unqualified_inspector(doc, method=None):
	"""``Project Quality Inspection`` ``validate`` — warn about who is carrying it out."""
	if int(getattr(doc, "docstatus", 0) or 0) == 2:
		return
	inspector = getattr(doc, "inspector", None)
	template = getattr(doc, "master_template", None)
	if not inspector or not template:
		return

	requirements = _template_requirements(template)
	if not qualification.has_requirements(requirements):
		return

	found = qualification.findings(requirements, _inspector_profile(inspector))
	if not found:
		return

	_msgprint(doc, found)
	frappe.enqueue(
		"erpnext_enhancements.quality.inspector_advisory.record_advisory",
		queue="short",
		enqueue_after_commit=True,
		inspection=doc.name,
		inspector=inspector,
		findings=found,
		fingerprint=qualification.fingerprint(inspector, found),
	)


def record_advisory(inspection=None, inspector=None, findings=None, fingerprint=None):
	"""The durable half: one timeline comment per distinct finding set, then the manager email.

	Runs in a background job, after commit, because on a brand-new inspection the document does
	not exist until the transaction lands — and a comment on a document that is not there yet is
	a silently dropped warning.
	"""
	try:
		if not findings or not frappe.db.exists("Project Quality Inspection", inspection):
			return
		if _already_recorded(inspection, fingerprint):
			return
		doc = frappe.get_doc("Project Quality Inspection", inspection)
		doc.add_comment("Comment", _comment_html(findings, fingerprint))
		_notify_manager(doc, inspector, findings)
	except Exception:
		_log_quietly("record_advisory")


# ------------------------------------------------------------------- resolution


def _template_requirements(template):
	"""What the master template asks for, with the required Position resolved to a dict.

	Resolved here rather than in the pure module because comparing seniority needs
	``job_family`` and ``tier``, and a Link field only carries a name.
	"""
	row = frappe.db.get_value(
		"Project Inspection Template",
		template,
		["template_name", "required_position", "required_course"],
		as_dict=True,
	) or frappe._dict()
	return {
		"template_name": row.get("template_name"),
		"required_position": row.get("required_position"),
		"required_position_doc": _position(row.get("required_position")),
		"required_course": row.get("required_course"),
	}


def _inspector_profile(user):
	"""Who this inspector is, as the pure module wants it.

	``employee`` being ``None`` is meaningful and is not smoothed over: it is the difference
	between "this person is not qualified" and "nothing here can be checked".
	"""
	employee = frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, ["name", "custom_position"], as_dict=True
	)
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"employee": employee.get("name") if employee else None,
		"position": _position(employee.get("custom_position")) if employee else None,
		"valid_courses": _valid_courses(user),
	}


def _position(name):
	"""``{name, job_family, tier}``, or ``None``. Guarded: ``Position`` is this app's DocType and
	these handlers fire during ERPNext's own test bootstrap, before it exists."""
	if not name:
		return None
	try:
		row = frappe.db.get_value("Position", name, ["name", "job_family", "tier"], as_dict=True)
	except Exception:
		return None
	return dict(row) if row else None


def _valid_courses(user):
	"""Courses with a live completion. A completion with no ``expires_on`` never lapses, which is
	how a course with no certificate validity period is meant to behave."""
	try:
		rows = frappe.get_all(
			"Training Completion",
			filters={"user": user, "docstatus": 1, "status": "Valid"},
			fields=["course", "expires_on"],
		)
	except Exception:
		return set()
	today = nowdate()
	return {r["course"] for r in rows if not r.get("expires_on") or str(r["expires_on"]) >= today}


# ------------------------------------------------------------------- delivery


def _msgprint(doc, findings):
	"""The inline half. ``msgprint`` and never ``throw`` — the save continues.

	Worded so it cannot be mistaken for a refusal. A warning that reads like an error gets
	escalated to somebody who then discovers there was nothing to do.
	"""
	frappe.msgprint(
		"<p>{}</p><ul>{}</ul><p class='text-muted small'>{}</p>".format(
			frappe._("This template asks for qualifications {0} does not have on record.").format(
				escape_html(frappe.utils.get_fullname(doc.inspector))
			),
			"".join(f"<li>{escape_html(qualification.summary_line(f))}</li>" for f in findings),
			frappe._("Saved anyway — this is a note, not a refusal. It has been recorded on the inspection."),
		),
		title=frappe._("Inspector qualifications"),
		indicator="orange",
	)


def _comment_html(findings, fingerprint):
	return "<p><b>{}</b></p><ul>{}</ul><!-- {}:{} -->".format(
		frappe._("The template asked for qualifications the inspector does not have on record."),
		"".join(f"<li>{escape_html(qualification.summary_line(f))}</li>" for f in findings),
		MARKER,
		escape_html(fingerprint or ""),
	)


def _already_recorded(inspection, fingerprint):
	if not fingerprint:
		return False
	return bool(
		frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Project Quality Inspection",
				"reference_name": inspection,
				"content": ["like", f"%{MARKER}:{escape_html(fingerprint)}%"],
			},
			limit=1,
		)
	)


def _notify_manager(doc, inspector, findings):
	"""Email the inspector's manager. Silent when there is nobody to tell.

	No invented fallback address: this is an advisory, and mailing a general inbox about a
	qualification gap the record already carries would be noise with no owner. The comment on the
	inspection is the durable trace either way.
	"""
	from erpnext_enhancements import email_style

	manager = _manager_of(inspector)
	if not manager:
		return
	link = frappe.utils.get_url_to_form("Project Quality Inspection", doc.name)
	subject = frappe._("Inspection {0} was carried out by {1}").format(
		doc.name, frappe.utils.get_fullname(inspector)
	)
	frappe.sendmail(
		recipients=[manager],
		subject=subject,
		message=email_style.wrap(
			email_style.callout(
				frappe._("This is a note, not a problem to fix today. The inspection was saved."),
				tone="warning",
			)
			+ email_style.p(
				frappe._("{0} carried out an inspection whose template asks for qualifications they do not have on record.").format(
					frappe.utils.get_fullname(inspector)
				)
			)
			+ email_style.bullets([qualification.summary_line(f) for f in findings])
			+ email_style.kv(
				[
					(frappe._("Project"), doc.project or ""),
					(frappe._("Milestone"), doc.milestone or ""),
					(frappe._("Template"), doc.master_template or ""),
				]
			)
			+ email_style.button(link, frappe._("Open the inspection"))
			+ email_style.button_fallback(link),
			title=subject,
			eyebrow=frappe._("Quality"),
		),
		reference_doctype="Project Quality Inspection",
		reference_name=doc.name,
	)


def _manager_of(user):
	"""``Employee.reports_to`` resolved to a user. The only manager field that actually exists —
	``User`` has none — and it is maintained on 13 of 15 active employees."""
	try:
		employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
		if not employee:
			return None
		manager = frappe.db.get_value("Employee", employee, "reports_to")
		if not manager:
			return None
		return frappe.db.get_value("Employee", manager, "user_id")
	except Exception:
		return None
