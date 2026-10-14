"""Flag the historical Opportunities for the first time (v1.629.0, WI-024).

Nik, 2026-10-13: an Opportunity created before 2025-08-01 that no person has worked on in 90
days is *historical*. It raises no "Closed Won but no linked Project" warning and is left out
of the KPIs. The rule, the activity test and every call site are documented in
``crm_enhancements/historical.py``; this patch only runs that module's own nightly job once,
so the warnings are quiet from the first morning after the deploy rather than the second.

**It creates the field first.** ``Opportunity.custom_is_historical`` is a fixture Custom Field,
and patches run *before* fixture sync, so on the deploy that introduces it the column does not
exist yet when this executes. The record is inserted from ``fixtures/custom_field.json``
itself (the ``backfill_stage_changed_on`` / ``seed_planner_templates`` precedent), so it is
the exact fixture definition, and fixture sync then adopts the same record by name.

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the deploy.
``refresh_historical_flags`` already logs and returns on its own failures; the field creation
is wrapped here. If the field cannot be created the patch logs and returns, and the nightly
job does the work once fixture sync has made the column. Safe twice: the second run changes
nothing it did not change the first time. Re-run with
``bench --site <site> execute erpnext_enhancements.patches.set_historical_opportunities.execute``.
"""

import json

import frappe

FIELD_NAME = "Opportunity-custom_is_historical"


def _ensure_field():
	"""Insert the fixture's Custom Field if it is missing. True when the column exists after."""
	from erpnext_enhancements.crm_enhancements.historical import has_field

	if has_field():
		return True
	if not frappe.db.exists("Custom Field", FIELD_NAME):
		path = frappe.get_app_path("erpnext_enhancements", "fixtures", "custom_field.json")
		with open(path, encoding="utf-8") as handle:
			record = next((row for row in json.load(handle) if row.get("name") == FIELD_NAME), None)
		if record is None:
			return False
		frappe.get_doc(dict(record)).insert(ignore_permissions=True)
	return has_field()


def execute():
	try:
		ready = _ensure_field()
	except Exception:
		frappe.log_error(
			title="Historical opportunities: field not created",
			message=frappe.get_traceback(),
		)
		print("set_historical_opportunities: could not create the field; the nightly job will flag them.")
		return
	if not ready:
		print("set_historical_opportunities: Opportunity.custom_is_historical is missing; nothing flagged.")
		return

	from erpnext_enhancements.crm_enhancements.historical import refresh_historical_flags

	counts = refresh_historical_flags()
	print(
		"set_historical_opportunities: {flagged} flagged, {unflagged} un-flagged, {unchanged} unchanged{error}.".format(
			error=" (FAILED: see Error Log)" if counts.get("error") else "",
			**{key: counts.get(key, 0) for key in ("flagged", "unflagged", "unchanged")},
		)
	)
