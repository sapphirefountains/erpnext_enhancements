"""Water engineering endpoints that outlived the Water Engineering Wizard (v1.771.0).

Until phase H of the Fountain Designer rebuild this module was the wizard's and the old form's API:
the stateless calculator, the design state reader and the schema-1 save the AI tools used. The wizard
was retired in v1.771.0 and its quick calculator became the Calculator drawer on the Fountain
Designer's Review step; everything that still has a caller now lives in ``api/designer.py``:

* ``check_permission``, ``run_calc``, ``item_pump_curves``, ``pump_curves`` and
  ``nozzle_profile_params`` are re-exported below, **deprecated**, for a Desk tab still running a
  pre-v1.771.0 bundle (``pump_curve_chart.js`` and the page asked for them here). Call
  ``api.designer`` instead; these names go when nothing has asked for them for a release.
* ``design_state``, ``get_design_state``, ``save_inputs``, ``_save_design``, ``get_design_issues``,
  ``acknowledge_issue``, ``run_design_spine``, ``get_pump_candidates``,
  ``get_equipment_candidates`` and ``get_loss_catalog`` are gone: their only callers were the wizard
  page, the old Desk form and the AI tools, which now call ``api.designer`` (``design_status``,
  ``tool_save``, ``run_calc``, ``acknowledge_issue``).

What stays here is the Control Panel Design reader the ``control_panel_status`` AI tool calls.
"""

import frappe
from frappe import _

# Deprecated re-exports (see the docstring). The same function objects, so a whitelisted one stays
# whitelisted at this path too.
from erpnext_enhancements.water_engineering.api.designer import (  # noqa: F401
    check_permission,
    item_pump_curves,
    nozzle_profile_params,
    pump_curves,
    run_calc,
)

CONTROL_DOCTYPE = "Control Panel Design"


def control_panel_state(panel=None, project=None):
    """Read a Control Panel Design's state, or list panels. Core helper (callers
    must have gated read access)."""
    if panel:
        if not frappe.has_permission(CONTROL_DOCTYPE, "read", doc=panel):
            frappe.throw(_("No read permission for {0}").format(panel), frappe.PermissionError)
        doc = frappe.get_doc(CONTROL_DOCTYPE, panel)
        screens = [
            name
            for name, on in (
                ("Main", doc.screen_main),
                ("Run", doc.screen_run),
                ("Maintenance", doc.screen_maintenance),
                ("Status/Off", doc.screen_status),
            )
            if on
        ]
        return {
            "panel": doc.name,
            "project": doc.project,
            "product_family": doc.product_family,
            "nema_rating": doc.nema_rating,
            "controller_hardware": doc.controller_hardware,
            "power": {
                "main_line_voltage": doc.main_line_voltage,
                "phase": doc.phase,
                "amperage_to_panel": doc.amperage_to_panel,
                "main_breaker_size_a": doc.main_breaker_size_a,
            },
            "screens": screens,
            "sizing": {
                "lighting_total_watts": doc.lighting_total_watts,
                "lighting_current_a": doc.lighting_current_a,
                "lighting_relay_count": doc.lighting_relay_count,
                "solenoid_relay_count": doc.solenoid_relay_count,
                "control_transformer_va": doc.control_transformer_va,
            },
            "counts": {
                "pumps": len(doc.pumps or []),
                "io_points": len(doc.io_points or []),
                "interlocks": len(doc.interlocks or []),
                "lights": len(doc.lights or []),
            },
            "interlocks": [
                {"condition": il.condition, "action": il.action, "enabled": bool(il.enabled)}
                for il in doc.interlocks or []
            ],
            "io_points": [
                {"point_name": io.point_name, "io_type": io.io_type, "signal": io.signal, "device": io.device}
                for io in doc.io_points or []
            ],
        }
    filters = {"project": project} if project else {}
    rows = frappe.get_list(
        CONTROL_DOCTYPE,
        filters=filters,
        fields=["name", "product_family", "project"],
        order_by="modified desc",
        limit=20,
    )
    return {"panels": rows}


@frappe.whitelist()
def get_control_panel_state(panel=None, project=None):
    if not frappe.has_permission(CONTROL_DOCTYPE, "read"):
        frappe.throw(
            _("You do not have access to {0}.").format(CONTROL_DOCTYPE), frappe.PermissionError
        )
    return control_panel_state(panel, project)
