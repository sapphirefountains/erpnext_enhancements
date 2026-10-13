# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the public visit sign-off page (``/visit-sign-off``, v1.614.0).

**The filename matters.** Frappe imports a page's controller by the *template's* basename with
hyphens turned into underscores, so ``visit-sign-off.html`` needs ``visit_sign_off.py``. A
hyphenated controller is never imported and ``get_context`` silently never runs
(``scripts/check_www_controllers.py`` fails the build on one).

A client who was not on site when the technician finished opens this from the email
``sapphire_maintenance/sign_off.py`` sends: the service report for the visit, a typed name, a
signature pad, a "the service described above was performed" box, and Sign. The link's token is
the only authority (``sign_off.resolve``). An unknown and a malformed token render identically,
so the page never confirms whether a token exists; expired, replaced and already-signed links,
which the visitor already holds, are told apart as a kindness.

The page adds no history entries: it is one screen, and signing swaps the form for a thank-you in
place, so the browser's Back and Forward keep doing what they did
(``scripts/test_web_flow_history.js visit-sign-off``).
"""

import frappe
from frappe.utils import cint, flt, formatdate

from erpnext_enhancements.sapphire_maintenance import sign_off
from erpnext_enhancements.utils.deploy import get_deploy_version

# A cached page would serve one client's service report, and their signing link, to another.
no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.deploy_version = get_deploy_version()

	ref = frappe.form_dict.get("ref")
	request = sign_off.resolve(ref)
	visit = _visit(request)
	state = _state_for(request, visit)

	context.state = state
	context.report = _report(visit) if state == "signable" else None
	context.boot = {
		# A Guest session carries no CSRF token and frappe does not check one for it. A signed-in
		# staff member opening the link does have one, and their POST would be a 400 without the
		# header. So the session's existing token is emitted, never
		# frappe.sessions.get_csrf_token(), which mints one a Guest session never persists
		# (the same reasoning as www/contract_sign.py).
		"csrf_token": (getattr(frappe.session, "data", None) or {}).get("csrf_token") or "",
		"state": state,
		"ref": ref or "",
	}
	context.expires_on = frappe.utils.format_datetime(request.expires_on) if request and request.expires_on else ""
	context.contact_phone = _contact_phone()

	if state == "signable":
		# Counted in the background: a GET is not committed, so an inline write would be
		# rolled back on the way out.
		frappe.enqueue(
			"erpnext_enhancements.sapphire_maintenance.sign_off.mark_viewed",
			queue="short",
			request_name=request.name,
		)
	return context


def _visit(request):
	if not request:
		return None
	try:
		return frappe.get_doc(sign_off.VISIT, request.visit)
	except Exception:
		return None


def _state_for(request, visit):
	if not request or not visit:
		return "invalid"
	if request.status == "Signed" or visit.get("client_sign_off"):
		return "signed"
	if request.status == "Superseded":
		return "replaced"
	if sign_off.effective_status(request) == "Expired":
		return "expired"
	if visit.docstatus == 2 or not sign_off.is_signable(request):
		return "invalid"
	return "signable"


def _report(visit):
	"""The service report as plain values; the template escapes every one."""
	names = _feature_names(visit)
	per_site = len({row.get("serial_no") for row in _rows(visit) if row.get("serial_no")}) > 1

	def feature(row):
		if not per_site:
			return ""
		serial = row.get("serial_no")
		return names.get(serial) or serial or ""

	readings = [
		{
			"feature": feature(row),
			"reading": row.get("reading") or "",
			"value": _number(row.get("reading_value")),
			"uom": row.get("uom") or "",
			"range": f"{_number(row.get('min_value'))}–{_number(row.get('max_value'))}"
			if row.get("min_value") is not None or row.get("max_value") is not None
			else "",
			"out_of_range": cint(row.get("out_of_range")),
			"notes": row.get("notes") or "",
		}
		for row in visit.get("chemistry_readings") or []
	]
	tasks = [
		{
			"feature": feature(row),
			"task": row.get("task") or "",
			"status": "Done" if row.get("is_done") else "N/A" if row.get("not_applicable") else "Not done",
			# A CSS class suffix, from this fixed set only.
			"tone": "done" if row.get("is_done") else "na" if row.get("not_applicable") else "not-done",
			"notes": row.get("notes") or "",
		}
		for row in visit.get("cleaning_tasks") or []
	]
	checks = [
		{
			"feature": feature(row),
			"item": row.get("question") or "",
			"status": row.get("selection") or "",
			"notes": " — ".join(
				part for part in (row.get("answer"), row.get("other_details") if row.get("selection") == "Other" else "") if part
			),
		}
		for row in visit.get("maintenance_results") or []
	]
	chemicals = [
		{"item": row.get("item_name") or row.get("item") or "", "qty": _number(row.get("qty")), "uom": row.get("uom") or ""}
		for row in visit.get("consumables") or []
		if flt(row.get("qty"))
	]
	follow_ups = [
		{"description": row.get("description") or "", "told": row.get("told") or ""}
		for row in visit.get("follow_ups") or []
	]
	return {
		"number": visit.name,
		"site": sign_off.site_label(visit),
		"account": _customer_name(visit.get("customer")),
		"date": formatdate(visit.get("visit_date") or visit.get("scheduled_visit_date") or visit.creation),
		"technician": sign_off._full_name(visit.get("technician")),
		"features": [names.get(serial) or serial for serial in _covered(visit)],
		"readings": readings,
		"out_of_range": any(row["out_of_range"] for row in readings),
		"tasks": tasks,
		"checks": checks,
		"chemicals": chemicals,
		"notes": visit.get("visit_notes") or "",
		"follow_ups": follow_ups,
		"photo": _photo(visit),
		"per_site": per_site,
	}


def _rows(visit):
	for table in ("chemistry_readings", "cleaning_tasks", "maintenance_results", "consumables"):
		yield from visit.get(table) or []


def _number(value):
	if value is None or value == "":
		return ""
	number = flt(value)
	return f"{number:g}"


def _feature_names(visit):
	try:
		from erpnext_enhancements.sapphire_maintenance.visit_report import feature_names

		return feature_names(visit) or {}
	except Exception:
		return {}


def _covered(visit):
	try:
		from erpnext_enhancements.sapphire_maintenance.visit_report import covered_features

		return covered_features(visit) or []
	except Exception:
		return [visit.serial_no] if visit.get("serial_no") else []


def _photo(visit):
	"""The "fountain as we left it" photo as a data URI, only when it is a File attached to this
	visit (``visit_report.image_data_uri`` checks), or None."""
	try:
		from erpnext_enhancements.sapphire_maintenance.visit_report import image_data_uri

		return image_data_uri(visit.get("fountain_photo"), visit.name)
	except Exception:
		return None


def _customer_name(customer):
	if not customer:
		return ""
	try:
		return frappe.db.get_value("Customer", customer, "customer_name") or customer
	except Exception:
		return customer


def _contact_phone():
	try:
		from erpnext_enhancements.crm_enhancements.fountain_move import get_contact_phone

		return get_contact_phone()
	except Exception:
		return ""
