"""A visit by the name it has now or the one it had before v1.610.0.

``patches/rename_hash_named_visits.py`` renamed the visits created before v1.597.0 from random
names (``4lvpg04h6h``) to visit numbers (``PRJ-00179-2026-V001``) and kept each old name in
``legacy_name``. The old names are still in review emails, the kiosk's links and the work kept
on technicians' phones, so the Visit Wizard's endpoints (``api.maintenance_visit``) and the
Visit Review page (``api.maintenance_review``) look a visit up through here.

Nothing but ``frappe`` is imported, so the bench-free suites that stub the record's controller
can still load both callers.
"""

import frappe

DOCTYPE = "Sapphire Maintenance Record"


def current_visit_name(name):
	"""The visit's name now, for its current name or its ``legacy_name``; None for neither.

	No permission check: callers load the visit and check it (``resolve_visit`` is the
	whitelisted form that does).
	"""
	name = str(name or "").strip()
	if not name:
		return None
	if frappe.db.exists(DOCTYPE, name):
		return name
	try:
		# A site migrating to v1.610.0 runs code before the column exists (CLAUDE.md: defensive
		# hooks are load-bearing).
		if not frappe.db.has_column(DOCTYPE, "legacy_name"):
			return None
	except Exception:
		return None
	# order_by=None: get_value's default orders by creation, and returns the newest match.
	return frappe.db.get_value(DOCTYPE, {"legacy_name": name}, "name", order_by=None) or None
