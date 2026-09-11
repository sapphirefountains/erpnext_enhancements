# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Inbound call routing — the ERPNext half: read the config, compile it, explain it.

The *decision* is in :mod:`call_routing_match`, which is dependency-free and copied into
Triton verbatim. This module is the binding to Frappe around it, and it does three things:

* :func:`get_routing_payload` — the block ``api.telephony.get_telephony_routing`` hands the
  Triton gateway. Triton already fetches that endpoint on a 60-second cache, so this rides
  an existing request rather than adding one.
* :func:`compile_rules` — turns editable records into a plan Triton can execute with no
  further lookups. Employees become E.164 numbers, users become Twilio Client identities,
  the Holiday List becomes a list of dates.
* :func:`preview` — answers "who would ring, and why" for a hypothetical call. Backed by a
  button on the settings form.

**Why compile rather than let Triton read the records.** Triton would need an ERPNext round
trip per Employee, inside a Twilio webhook, to turn a rule into a phone number. Resolving
here means the whole plan arrives in one already-cached fetch, and it means a target that
cannot be resolved is reported *at configuration time*, in :func:`compile_rules`'
``warnings``, instead of being discovered as a phone that did not ring.

That last point is not hypothetical. On 2026-09-11 exactly **2 of 20** Employee records had
a ``cell_number`` at all, and the ones that did stored it as bare digits (``"7025214969"``).
An Employee target is therefore much more likely to resolve to nothing than you would
guess, which is why the warnings are surfaced on the form, in the preview, and in the
payload rather than merely logged.

Indentation is tabs, matching the rest of this module's non-doctype code.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, get_system_timezone, getdate, now_datetime

from erpnext_enhancements.ai_governance import call_routing_match as match

# Imported rather than reimplemented: this must be the *same* identity string the desk
# softphone registers in get_softphone_token, or a rule silently rings an identity that
# nobody's browser is listening on.
from erpnext_enhancements.api.telephony import _softphone_identity

SETTINGS_DOCTYPE = "Call Routing Settings"
RULE_DOCTYPE = "Call Routing Rule"

# The ring-duration bounds live in call_routing_match because they are part of the contract
# Triton shares, not an ERPNext-side preference. Re-exported here so the rule controller can
# import them without dragging in this module's frappe and twilio dependencies.
DEFAULT_RING_SECONDS = match.DEFAULT_RING_SECONDS
MAX_RING_SECONDS = match.MAX_RING_SECONDS


def get_settings() -> dict[str, Any]:
	"""Effective settings with every fallback applied — the only reader in this module.

	Same shape of care as ``product_feedback_settings.get_settings``: a Single that has
	never been saved has no rows in ``tabSingles`` at all, so every field reads ``None``
	whatever the JSON declares as its default. Hence ``paused`` rather than ``enabled``
	(the absent-row state has to be the running state) and hence the fallbacks living here
	in code rather than relying on a seed patch having run.
	"""
	row: dict[str, Any] = {}
	try:
		row = frappe.db.get_singles_dict(SETTINGS_DOCTYPE) or {}
	except Exception:
		row = {}

	ring = cint(row.get("default_ring_seconds")) or DEFAULT_RING_SECONDS
	return {
		"paused": bool(cint(row.get("paused"))),
		"default_forward_number": (row.get("default_forward_number") or "").strip(),
		"default_ring_seconds": max(5, min(MAX_RING_SECONDS, ring)),
		"voicemail_message": (row.get("voicemail_message") or "").strip(),
		"holiday_list": (row.get("holiday_list") or "").strip(),
	}


def holiday_dates(holiday_list: str) -> list[str]:
	"""ISO dates from ``holiday_list`` for this year and next, or ``[]``.

	Sent in the payload so Triton can answer "is today a holiday" without an ERPNext call
	on the webhook path. Two years is enough for a cache that refreshes every 60 seconds
	and keeps the payload small. A missing or unreadable list degrades to "no holidays",
	never to an error — the same direction ``utils/working_days._holiday_checker`` takes.
	"""
	if not holiday_list:
		return []
	try:
		today = getdate(now_datetime())
		rows = frappe.get_all(
			"Holiday",
			filters={
				"parent": holiday_list,
				"parenttype": "Holiday List",
				"holiday_date": ["between", [f"{today.year}-01-01", f"{today.year + 1}-12-31"]],
			},
			fields=["holiday_date"],
			limit_page_length=0,
		)
		return sorted({str(getdate(r.holiday_date)) for r in rows if r.get("holiday_date")})
	except Exception:
		return []


def _compile_targets(rule_name: str, targets: Any, warnings: list[str]) -> list[dict[str, str]]:
	"""Resolve one rule's target rows into dial legs, appending to ``warnings``.

	A target that cannot be resolved is dropped and explained rather than passed through
	as an empty string: an empty ``<Number>`` is a Twilio error on a live call, and a
	silently-dropped leg is a phone that mysteriously stops ringing.
	"""
	out: list[dict[str, str]] = []
	for row in targets or []:
		kind = (row.get("target_type") or "").strip()
		value = (row.get("target_value") or "").strip()

		if kind == "Voicemail":
			out.append({"type": match.TARGET_VOICEMAIL})
		elif kind == "Account Manager":
			out.append({"type": match.TARGET_ACCOUNT_MANAGER})
		elif kind == "Employee":
			if not value:
				warnings.append(f"{rule_name}: an Employee target has no employee selected")
				continue
			emp = frappe.db.get_value(
				"Employee", value, ["employee_name", "cell_number", "status"], as_dict=True
			)
			if not emp:
				warnings.append(f"{rule_name}: Employee {value} no longer exists")
				continue
			number = match.to_e164(emp.get("cell_number"))
			if not number:
				warnings.append(
					f"{rule_name}: {emp.get('employee_name') or value} has no usable Cell Number, "
					"so this target will not ring"
				)
				continue
			if (emp.get("status") or "") != "Active":
				warnings.append(
					f"{rule_name}: {emp.get('employee_name') or value} is {emp.get('status')}, "
					"not Active — still dialed, but check this is intended"
				)
			out.append({"type": match.TARGET_NUMBER, "value": number, "label": emp.get("employee_name") or value})
		elif kind == "Softphone User":
			if not value:
				warnings.append(f"{rule_name}: a Softphone User target has no user selected")
				continue
			if not frappe.db.get_value("User", value, "enabled"):
				warnings.append(f"{rule_name}: user {value} is disabled, so their softphone will not ring")
				continue
			out.append({"type": match.TARGET_CLIENT, "value": _softphone_identity(value), "label": value})
		else:
			warnings.append(f"{rule_name}: unknown target type {kind!r}")

	if len(out) > match.MAX_DIAL_LEGS:
		warnings.append(
			f"{rule_name}: {len(out)} targets, but Twilio rings at most {match.MAX_DIAL_LEGS} "
			"endpoints on one call — the last ones will not be dialed"
		)
		out = out[: match.MAX_DIAL_LEGS]
	return out


def compile_rules() -> dict[str, Any]:
	"""Every enabled rule, in priority order, resolved into dial legs.

	Returns ``{"rules": [...], "warnings": [...]}``. Disabled rules are skipped here
	rather than sent with a flag, so the payload only ever contains rules that can fire.
	"""
	warnings: list[str] = []
	compiled: list[dict[str, Any]] = []
	try:
		names = frappe.get_all(
			RULE_DOCTYPE,
			filters={"enabled": 1},
			pluck="name",
			order_by="priority asc, name asc",
			limit_page_length=0,
		)
	except Exception:
		# No DocType yet (a site mid-migrate), or no permission. No rules is a valid,
		# safe answer: Triton falls back to the default forward number.
		return {"rules": [], "warnings": []}

	for name in names:
		try:
			doc = frappe.get_doc(RULE_DOCTYPE, name)
		except Exception:
			continue
		targets = _compile_targets(name, [t.as_dict() for t in (doc.get("targets") or [])], warnings)
		also_ring = cint(doc.get("also_ring_softphones"))
		if not targets and not also_ring:
			warnings.append(f"{name}: no reachable targets — this rule will be skipped at call time")
		numbers = [
			line.strip()
			for line in (doc.get("caller_numbers") or "").replace(",", "\n").splitlines()
			if line.strip()
		]
		compiled.append(
			{
				"name": name,
				"priority": cint(doc.get("priority")),
				"intent": (doc.get("applies_to_intent") or "Any").strip(),
				"caller_scope": (doc.get("caller_scope") or match.CALLER_ANY).strip(),
				"caller_numbers": numbers,
				"schedule": (doc.get("schedule") or match.SCHEDULE_ANY).strip(),
				"custom_days": (doc.get("custom_days") or "").strip(),
				"days": match.schedule_days(doc.get("schedule"), doc.get("custom_days")),
				"from_time": str(doc.get("from_time") or "") or None,
				"to_time": str(doc.get("to_time") or "") or None,
				"skip_on_holidays": bool(cint(doc.get("skip_on_holidays"))),
				"ring_seconds": cint(doc.get("ring_seconds")),
				"also_ring_softphones": bool(also_ring),
				"targets": targets,
			}
		)
	return {"rules": compiled, "warnings": warnings}


def get_routing_payload() -> dict[str, Any]:
	"""The ``routing`` block for ``get_telephony_routing``.

	Never raises. This rides on an endpoint whose existing keys (the desk softphone
	identities and the branded caller ID) Triton has depended on since 1.23.0, and a
	routing-config problem must not take those down with it.
	"""
	try:
		settings = get_settings()
		rules = compile_rules()
		return {
			"schema_version": match.SCHEMA_VERSION,
			"paused": settings["paused"],
			"timezone": get_system_timezone(),
			"default_forward_number": match.to_e164(settings["default_forward_number"]),
			"default_ring_seconds": settings["default_ring_seconds"],
			"voicemail_message": settings["voicemail_message"],
			"holidays": holiday_dates(settings["holiday_list"]),
			"rules": rules["rules"],
			"warnings": rules["warnings"],
		}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Call routing payload failed")
		# schema_version 0 is not SCHEMA_VERSION, so Triton reads this as "configuration
		# unavailable" and uses its own fallback rather than acting on a half-built plan.
		return {"schema_version": 0, "paused": True, "rules": []}


def build_facts(when=None, from_number: str = "", intent: str = "General", known_caller: bool = False,
                holidays: Any = None) -> dict[str, Any]:
	"""The facts :func:`call_routing_match.decide` matches against.

	``when`` is site-local wall-clock — ``now_datetime()`` already is, and this is a Utah
	business rule about Utah office hours, so site-local is the correct frame rather than
	UTC. Triton builds the same dict from ``pytz`` and the ``timezone`` in the payload;
	keeping the timezone handling on this side of the boundary is what lets the matcher
	itself stay identical in both repos.
	"""
	when = when or now_datetime()
	holiday_set = set(holidays or [])
	return {
		"weekday": when.weekday(),
		"minutes": when.hour * 60 + when.minute,
		"intent": (intent or "General").strip() or "General",
		"from_number": from_number or "",
		"known_caller": bool(known_caller),
		"is_holiday": str(getdate(when)) in holiday_set,
	}


def preview(from_number: str = "", intent: str = "General", when=None) -> dict[str, Any]:
	"""Who would ring for this call, and why — the answer the settings form shows.

	Resolves the caller read-only: ``_get_caller_info`` creates a Customer and Contact for
	an unrecognised number by default, and a *preview* that quietly creates CRM records
	every time somebody tries a number would be its own bug.
	"""
	from erpnext_enhancements.api.telephony import _get_caller_info

	routing = get_routing_payload()
	known = False
	if from_number:
		try:
			known = bool((_get_caller_info(from_number, create_if_missing=False) or {}).get("customer"))
		except Exception:
			known = False

	facts = build_facts(
		when=when,
		from_number=from_number,
		intent=intent,
		known_caller=known,
		holidays=routing.get("holidays"),
	)
	plan = match.decide(routing, facts)
	return {
		"facts": facts,
		"plan": plan,
		"warnings": routing.get("warnings") or [],
		"softphone_identities": _current_softphone_identities(),
	}


def _current_softphone_identities() -> list[str]:
	"""The desk identities the normal fan-out would ring, for the preview's benefit.

	Shown alongside the plan so "include softphones" is a list of names rather than a
	boolean the reader has to go and interpret somewhere else.
	"""
	try:
		from erpnext_enhancements.api.telephony import LEGACY_SOFTPHONE_IDENTITY, _softphone_users

		users = _softphone_users(frappe.get_cached_doc("Triton Settings"))
		return [_softphone_identity(u) for u in users] if users else [LEGACY_SOFTPHONE_IDENTITY]
	except Exception:
		return []
