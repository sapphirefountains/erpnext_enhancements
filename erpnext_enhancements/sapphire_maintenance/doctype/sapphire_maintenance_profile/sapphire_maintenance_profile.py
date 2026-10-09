"""Controller for the Sapphire Maintenance Profile doctype.

One per Project (``project`` is unique): stores site-level briefing data shown to
technicians before a visit — ``safety_instructions`` and ``access_codes`` —
plus a read-only ``customer`` fetched from the project. Surfaced in the
Maintenance Record form's dashboard widget via
``sapphire_maintenance_record.get_dashboard_context``.

The "Visit crew and length" section (P1.8) is the site default for multi-person visits:
``default_crew`` (who goes with the default technician), ``visit_hours`` and ``visit_full_day``.
The scheduler copies them onto each drafted visit (``tasks._draft_maintenance_record`` via
``api.maintenance_dispatch.default_crew_for``), and the planners project future visits with them.
``validate`` tidies the crew the same way a visit's is tidied (``sapphire_maintenance.visit_crew``).
"""

import frappe
from frappe.model.document import Document


class SapphireMaintenanceProfile(Document):
	def validate(self):
		from erpnext_enhancements.sapphire_maintenance.visit_crew import validate_crew

		validate_crew(self, table="default_crew", lead_field="default_technician", hours_field="visit_hours")
