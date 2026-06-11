# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Connections-dashboard overrides that surface Travel Trips on the
documents trips are taken FOR (wired via ``override_doctype_dashboards``).

Travel Trip points at its business reason through a dynamic link
(``travel_for_doctype`` / ``travel_for_name``), so each host doctype's
dashboard needs both a ``non_standard_fieldnames`` entry and a
``dynamic_links`` entry for frappe's link-count query
(``frappe.desk.notifications.get_open_count`` resolves the pair via
``get_dynamic_link_filters``). Project is handled separately in
``project_enhancements.get_dashboard_data`` using the trip's read-only
``project`` mirror field, and Employee in ``dashboard_overrides.get_data``
via the Trip Traveler child table fallback.
"""

from frappe import _


def _add_travel_group(data, host_doctype):
	data.setdefault("non_standard_fieldnames", {})["Travel Trip"] = "travel_for_name"
	data.setdefault("dynamic_links", {})["travel_for_name"] = [
		host_doctype,
		"travel_for_doctype",
	]
	data.setdefault("transactions", []).append({"label": _("Travel"), "items": ["Travel Trip"]})
	return data


def get_opportunity_dashboard_data(data):
	return _add_travel_group(data or {}, "Opportunity")


def get_lead_dashboard_data(data):
	return _add_travel_group(data or {}, "Lead")


def get_customer_dashboard_data(data):
	return _add_travel_group(data or {}, "Customer")
