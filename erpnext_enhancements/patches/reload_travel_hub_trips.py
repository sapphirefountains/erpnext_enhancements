"""Force the Travel hub's "Trips" shortcut and sidebar item to re-sync from the repo.

v1.555.0 turned "My trips" into "Trips" in two places, and both now open the list of everyone's
trips on the web itinerary (``/itinerary?view=trips``) instead of the desk Travel Trip list:

* the workspace shortcut (``travel_management/workspace/travel_management/travel_management.json``)
  is a URL shortcut now — no ``link_to``, no count, no filter;
* the sidebar item (``workspace_sidebar/travel.json``) is a URL item.

Any staff member may open any trip there, money left out; the desk list, form and Report view stay
row-scoped, because on the desk a trip's costs are readable by whoever can read the trip (Nik,
2026-09-28: "Change it from My Trips to just Trips so anyone can see anyone's trips").

Both records are **timestamp-gated** on import, so each file carries a new stamp; this patch is the
half a stamp that loses to a newer row cannot undo. ``reload_travel_hub`` did the same job for the
v1.554.0 revamp, has run on production and will never run again, so this one has a name of its own.
It is that patch's first two steps, unchanged: ``reload_doc(force=True)`` for the workspace, and
``import_file_by_path(force=True)`` for the sidebar, because app-level sidebars are flat files under
``<app>/workspace_sidebar/`` that ``reload_doc`` (which takes a *module*) cannot find.

Deliberately overwrites the desk copy of each: these are app-owned records and the repo is the
source of truth. Someone's own arrangement of a workspace lives in a *private* Workspace, which this
does not touch.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A desk page's
	# layout is not worth a half-finished deploy, so every failure here is logged and swallowed.
	try:
		frappe.reload_doc("travel_management", "workspace", "travel_management", force=True)
	except Exception:
		frappe.log_error(
			title="Workspace reload failed: Travel",
			message=frappe.get_traceback(),
		)
	try:
		from frappe.modules.import_file import import_file_by_path

		import_file_by_path(
			frappe.get_app_path("erpnext_enhancements", "workspace_sidebar", "travel.json"),
			force=True,
		)
	except Exception:
		frappe.log_error(
			title="Workspace sidebar reload failed: Travel",
			message=frappe.get_traceback(),
		)
	try:
		frappe.clear_cache()
	except Exception:
		pass
