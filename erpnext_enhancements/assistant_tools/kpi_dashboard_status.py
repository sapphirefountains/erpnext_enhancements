"""kpi_dashboard_status — the department KPI cockpit, read-only.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

Two deliberate shapes:

* **No department named** — every department the caller may see, but only the
  values that are *not* Good. Nine departments' full KPI sets is a context bomb:
  a hundred-odd rows of numbers that are fine, burying the four that are not.
  "What needs attention" is the question somebody asks without naming a
  department, so that is what an unqualified call answers.
* **A department named** — that department in full, Good rows included, because
  now they have asked.

``refresh_kpi_dashboard`` is deliberately not exposed. It rebuilds the snapshot
and ``frappe.db.commit()``s, which makes it a write, and this tool is classified
read-only in ``_gate.EXPLICIT_READONLY``. The nightly job and the cockpit's own
Refresh button remain the ways to force a rebuild.

Per-department visibility is ``api/kpi.py::_can_view``'s decision and is not
re-implemented here.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

ATTENTION_STATUSES = ("Watch", "Bad")


class KpiDashboardStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "kpi_dashboard_status"  # must match module filename
        self.description = (
            "Department KPI snapshots from the nightly cockpit build. Called with no "
            "department, it returns every department the user may see but only the "
            "values that are Watch or Bad — the answer to 'what needs attention' "
            "without dumping a hundred healthy numbers. Called with a department "
            "(Finance, Sales, Operations, Marketing, Design, Production, Product, HR, "
            "Executive) it returns that department in full, Good values included. "
            "Every reply carries source_freshness, which says how current each "
            "underlying feed was when the snapshot was built — a KPI computed from a "
            "stale feed is a plausible number that is not true today, and the figure "
            "alone cannot tell you that. Read-only: this never rebuilds a snapshot. "
            "Figures are as of the last build, not live."
        )
        self.category = "Analytics"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "KPI Snapshot"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "description": "One department; omit for a cross-department exceptions view",
                },
                "period": {
                    "type": "string",
                    "enum": ["Daily", "Weekly", "Monthly"],
                    "default": "Daily",
                    "description": "Snapshot period to read",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "KPI Snapshot"):
            return {"error": "KPI Dashboards are not installed on this site."}

        from erpnext_enhancements.api.kpi import get_kpi_dashboard, visible_departments

        period = arguments.get("period") or "Daily"
        allowed = visible_departments()
        if not allowed:
            return {
                "departments": [],
                "note": "You do not have access to any department's KPIs.",
            }

        department = (arguments.get("department") or "").strip()
        if department:
            if department not in allowed:
                # Same refusal `get_kpi_dashboard` would give, said before the call
                # so the message names what the caller *can* see.
                return {
                    "error": f"You do not have permission to view the {department} KPIs.",
                    "visible_departments": allowed,
                }
            result = get_kpi_dashboard(department, period=period)
            return self._one(department, period, result, attention_only=False)

        departments = []
        for name in allowed:
            result = get_kpi_dashboard(name, period=period)
            departments.append(self._one(name, period, result, attention_only=True))
        return {
            "period": period,
            "view": "attention",
            "visible_departments": allowed,
            "departments": departments,
            "note": (
                "Only Watch and Bad values are listed. Ask for a single department to "
                "see its Good values too. Figures are as of each snapshot's build time."
            ),
        }

    def _one(self, department, period, result, attention_only):
        """One department's reply, with freshness attached whatever the shape."""
        if not result.get("available"):
            return {
                "department": department,
                "period": period,
                "available": False,
                "reason": result.get("reason"),
            }
        snapshot = result.get("snapshot")
        if not snapshot:
            return {
                "department": department,
                "period": period,
                "available": True,
                "snapshot": None,
                "reason": result.get("reason"),
            }

        values = snapshot.get("values") or []
        if attention_only:
            values = [v for v in values if v.get("status") in ATTENTION_STATUSES]

        return {
            "department": department,
            "period": period,
            "available": True,
            "snapshot_date": snapshot.get("snapshot_date"),
            "generated_at": snapshot.get("generated_at"),
            "values": values,
            "value_count": len(snapshot.get("values") or []),
            "attention_count": sum(
                1 for v in (snapshot.get("values") or []) if v.get("status") in ATTENTION_STATUSES
            ),
            # `_serialize` does not carry this, and it is the one field that says
            # whether the numbers beside it can be trusted today.
            "source_freshness": self._freshness(department, period, snapshot),
        }

    def _freshness(self, department, period, snapshot):
        """``source_freshness_json`` off the snapshot document, parsed.

        Read separately because `api/kpi.py::_serialize` does not include it —
        the cockpit renders freshness from its own request. A KPI built from a
        feed that last updated a week ago is a plausible number that is not true
        today, and nothing in the value itself says so.
        """
        raw = frappe.db.get_value(
            "KPI Snapshot",
            {
                "department": department,
                "period": period,
                "snapshot_date": snapshot.get("snapshot_date"),
            },
            "source_freshness_json",
        )
        if not raw:
            return None
        try:
            return frappe.parse_json(raw)
        except Exception:
            # Diagnostic metadata must never be the reason a KPI read fails.
            return None


__all__ = ["KpiDashboardStatus"]
