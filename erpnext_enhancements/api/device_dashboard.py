"""Fleet dashboard payload for the Device Fleet Dashboard desk page.

A DB-only green/amber/red snapshot of the managed-device fleet — counts by
status / platform / ownership, the compliance split, stale self-attestations,
and warranty expiries — modelled on ``api.integrations_health`` (whose pure
tone helpers it reuses) and rendered by
``device_management/page/device_fleet_dashboard``. Device-Manager / System-
Manager only; no outbound calls.
"""

import frappe
from frappe.utils import add_days, today

from erpnext_enhancements.api.device_management import MANAGER_ROLES
from erpnext_enhancements.api.integrations_health import worst_tone
from erpnext_enhancements.device_management.doctype.device_compliance_settings.device_compliance_settings import (
	get_settings,
)

STATUS_ORDER = ["In Stock", "Assigned", "In Repair", "Lost/Stolen", "Retired"]


def _check_access():
	if not MANAGER_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(frappe._("You are not permitted to view the device dashboard."), frappe.PermissionError)


def _count(filters):
	return frappe.db.count("Managed Device", filters)


def _by(field, values=None):
	"""Return [(value, count), …] for a Select/Link field, in ``values`` order
	when given (else by descending count)."""
	rows = frappe.get_all("Managed Device", fields=[field, "count(name) as n"], group_by=field)
	counts = {row.get(field): row.n for row in rows if row.get(field)}
	if values:
		return [(v, counts.get(v, 0)) for v in values]
	return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def _metric(label, value, tone="neutral"):
	return {"label": label, "value": value, "tone": tone}


def _tile(key, label, status, headline, metrics, links=None):
	return {
		"key": key,
		"label": label,
		"status": status,
		"headline": headline,
		"metrics": metrics,
		"links": links or [],
	}


def _list_link(label, filters):
	"""Deep link into a filtered Managed Device list view."""
	from urllib.parse import urlencode

	return {"label": label, "route": "/app/managed-device?" + urlencode(filters)}


@frappe.whitelist()
def get_fleet_health():
	"""Device-Manager-only fleet snapshot (counts, compliance, attestation,
	warranty). DB-only."""
	_check_access()
	settings = get_settings()
	total = _count({})

	tiles = []

	# --- Fleet by status ------------------------------------------------------
	status_counts = dict(_by("status", STATUS_ORDER))
	active_total = total - status_counts.get("Retired", 0)
	status_metrics = [
		_metric(
			s,
			status_counts.get(s, 0),
			"red" if s == "Lost/Stolen" and status_counts.get(s, 0) else "neutral",
		)
		for s in STATUS_ORDER
	]
	tiles.append(
		_tile(
			"fleet",
			"Fleet",
			"red" if status_counts.get("Lost/Stolen", 0) else "green",
			f"{active_total} active · {total} total",
			status_metrics,
			links=[{"label": "All devices", "route": "/app/managed-device"}],
		)
	)

	# --- Compliance -----------------------------------------------------------
	compliant = _count({"compliance_status": "Compliant"})
	non_compliant = _count({"compliance_status": "Non-Compliant"})
	unknown = _count({"compliance_status": "Unknown"})
	provider_managed = _count({"compliance_source": "Provider"})
	comp_tone = "red" if non_compliant else ("amber" if unknown else "green")
	tiles.append(
		_tile(
			"compliance",
			"Compliance",
			comp_tone,
			f"{non_compliant} non-compliant" if non_compliant else ("all clear" if not unknown else f"{unknown} unknown"),
			[
				_metric("Compliant", compliant, "green" if compliant else "neutral"),
				_metric("Non-Compliant", non_compliant, "red" if non_compliant else "neutral"),
				_metric("Unknown", unknown, "amber" if unknown else "neutral"),
				# Phase-1 reality check: everything is self-attested until the
				# Phase-2 provider feed lands.
				_metric("Provider-managed", provider_managed, "green" if provider_managed else "neutral"),
			],
			links=[_list_link("Non-compliant", {"compliance_status": "Non-Compliant"})],
		)
	)

	# --- Attestation freshness ------------------------------------------------
	interval = settings.get("attestation_interval_days") or 90
	cutoff = add_days(today(), -interval)
	attest_filter = {"status": "Assigned"}
	if not settings.get("require_attestation_for_byod"):
		attest_filter["ownership"] = "Company"
	never = _count({**attest_filter, "last_checked_on": ["is", "not set"]})
	overdue = _count({**attest_filter, "last_checked_on": ["<", cutoff]})
	stale = never + overdue
	tiles.append(
		_tile(
			"attestation",
			"Attestation",
			"amber" if stale else "green",
			f"{stale} need re-attestation" if stale else "all current",
			[
				_metric("Never attested", never, "amber" if never else "neutral"),
				_metric(f"Overdue (> {interval}d)", overdue, "amber" if overdue else "neutral"),
			],
		)
	)

	# --- Warranty -------------------------------------------------------------
	lead = settings.get("warranty_reminder_lead_days") or 30
	soon = add_days(today(), lead)
	expiring = _count({"warranty_expiry_date": ["between", [today(), soon]], "status": ["!=", "Retired"]})
	expired = _count({"warranty_expiry_date": ["<", today()], "status": ["!=", "Retired"]})
	warr_tone = "red" if expired else ("amber" if expiring else "green")
	tiles.append(
		_tile(
			"warranty",
			"Warranty",
			warr_tone,
			f"{expired} expired" if expired else (f"{expiring} expiring soon" if expiring else "all in warranty"),
			[
				_metric("Expired", expired, "red" if expired else "neutral"),
				_metric(f"Expiring (≤ {lead}d)", expiring, "amber" if expiring else "neutral"),
			],
		)
	)

	# --- Breakdown tiles (neutral) -------------------------------------------
	tiles.append(
		_tile(
			"platforms",
			"Platforms",
			"neutral",
			f"{len([1 for _v, n in _by('platform') if n])} in use",
			[_metric(p, n) for p, n in _by("platform")] or [_metric("—", 0)],
		)
	)
	ownership = dict(_by("ownership", ["Company", "BYOD"]))
	tiles.append(
		_tile(
			"ownership",
			"Ownership",
			"neutral",
			f"{ownership.get('BYOD', 0)} BYOD",
			[
				_metric("Company", ownership.get("Company", 0)),
				_metric("BYOD", ownership.get("BYOD", 0)),
			],
			links=[_list_link("BYOD devices", {"ownership": "BYOD"})],
		)
	)

	# --- Data hygiene: assigned to an inactive employee ----------------------
	assigned = frappe.get_all(
		"Managed Device",
		filters={"status": "Assigned", "assigned_to_employee": ["is", "set"]},
		fields=["assigned_to_employee"],
	)
	if assigned:
		inactive = set(frappe.get_all("Employee", filters={"status": ["!=", "Active"]}, pluck="name"))
		orphaned = sum(1 for d in assigned if d.assigned_to_employee in inactive)
	else:
		orphaned = 0
	if orphaned:
		tiles.append(
			_tile(
				"reclaim",
				"Reclaim",
				"amber",
				f"{orphaned} held by inactive staff",
				[_metric("Assigned to inactive employee", orphaned, "amber")],
			)
		)

	return {
		"generated_at": str(frappe.utils.now_datetime()),
		"total": total,
		"overall": worst_tone([t["status"] for t in tiles]),
		"tiles": tiles,
	}
