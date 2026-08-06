# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shared gating for the department dashboard widgets (Custom HTML Blocks).

The Finance Dashboard widgets (``api/finance_dashboard.py``) established the
pattern this module generalises: one whitelisted feed per widget, role-gated to
the department, gated again on a per-widget toggle on ``ERPNext Enhancements
Settings`` (default OFF), returning ``{"enabled": False}`` so the block renders a
muted notice rather than an error.

Two deliberate choices:

**Roles are imported from** ``api/kpi.py``, never re-declared. A widget must not
be visible to someone who cannot already see the same department's KPI Cockpit,
and one table of roles is the only way to keep that true.

**Feeds take no arguments.** Every widget is a fixed view of its department, so
there is nothing to validate and no way for a caller to widen the query. Add a
new widget rather than a filter parameter.

Finance keeps its own copies of these helpers. Its five toggles predate this
registry, live in their own settings section, and are read by
``api/finance_calendar.py`` and ``api/horoscope.py``; nothing here touches them.

``WIDGETS`` is the single source of truth for which toggle backs which widget.
``setup/custom_html_blocks.py`` places the blocks, and
``tests/test_dashboard_widgets.py`` asserts the two agree.
"""

import functools

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.api.kpi import DEPARTMENT_ROLES

SETTINGS_DOCTYPE = "ERPNext Enhancements Settings"

# department -> {widget key: settings fieldname}. Every fieldname here is a Check
# on ERPNext Enhancements Settings defaulting to 0 — a widget nobody has turned
# on renders its "turned off" notice instead of querying.
WIDGETS = {
	"Sales": {
		"speed_to_lead": "sales_speed_to_lead_enabled",
		"stalled_deals": "sales_stalled_deals_enabled",
		"handoff_backlog": "sales_handoff_backlog_enabled",
		"renewal_radar": "sales_renewal_radar_enabled",
	},
	"Operations": {
		"day_board": "operations_day_board_enabled",
		"fleet_health": "operations_fleet_health_enabled",
		"chemistry_alerts": "operations_chemistry_alerts_enabled",
		"labor_capture": "operations_labor_capture_enabled",
	},
	"Design": {
		"wip_board": "design_wip_board_enabled",
		"signoff_queue": "design_signoff_queue_enabled",
		"handoff_readiness": "design_handoff_readiness_enabled",
		"hydraulic_headroom": "design_hydraulic_headroom_enabled",
	},
	"Production": {
		"wip_aging": "production_wip_aging_enabled",
		"milestone_slippage": "production_milestone_slippage_enabled",
		"material_readiness": "production_material_readiness_enabled",
		"hours_variance": "production_hours_variance_enabled",
	},
	"Marketing": {
		"funnel_cascade": "marketing_funnel_cascade_enabled",
		"channel_spend": "marketing_channel_spend_enabled",
		"unsourced_leads": "marketing_unsourced_leads_enabled",
		"source_health": "marketing_source_health_enabled",
	},
	"HR": {
		"training_compliance": "hr_training_compliance_enabled",
		"timesheet_completeness": "hr_timesheet_completeness_enabled",
		"headcount_movement": "hr_headcount_movement_enabled",
		"people_calendar": "hr_people_calendar_enabled",
	},
	"Executive": {
		"scorecard": "executive_scorecard_enabled",
		"cash_runway": "executive_cash_runway_enabled",
		"bookings_backlog": "executive_bookings_backlog_enabled",
		"risk_queue": "executive_risk_queue_enabled",
	},
}


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def can_view(department) -> bool:
	roles = set(frappe.get_roles())
	if "System Manager" in roles:
		return True
	return bool(DEPARTMENT_ROLES.get(department, set()) & roles)


def require_department(department):
	if not can_view(department):
		frappe.throw(_("Not permitted."), frappe.PermissionError)


def widget_enabled(department, widget, settings=None) -> bool:
	"""True when the widget's toggle is on. An unknown widget, or a Check field
	that does not exist yet (a site mid-migrate), reads as OFF."""
	field = WIDGETS.get(department, {}).get(widget)
	if not field:
		return False
	settings = settings or _settings()
	return bool(cint(settings.get(field)))


def widget_feed(department, widget):
	"""Decorator wrapping a feed in the department's role gate + widget toggle.

	The wrapped function is only called once both pass, and its dict is returned
	with ``enabled: True`` merged in — so a block's render path sees exactly one
	shape: ``{"enabled": False}`` or the payload. Stack it *under*
	``@frappe.whitelist()`` so the registered callable is the gated wrapper::

	    @frappe.whitelist()
	    @widget_feed("Sales", "speed_to_lead")
	    def get_speed_to_lead():
	        return {"leads": [...]}
	"""

	def decorator(fn):
		@functools.wraps(fn)
		def wrapper():
			require_department(department)
			if not widget_enabled(department, widget):
				return {"enabled": False}
			payload = fn() or {}
			payload["enabled"] = True
			return payload

		return wrapper

	return decorator


def fetch_all(doctype, **kwargs):
	"""``frappe.get_all`` with permissions off, for feeds already role-gated above.

	Same reasoning as the Task Dashboard and the Finance widgets: the gate is the
	department role, and a shared departmental board must not silently empty out
	because the viewer carries a restrictive User Permission on the doctype.
	"""
	kwargs.setdefault("ignore_permissions", True)
	return frappe.get_all(doctype, **kwargs)
