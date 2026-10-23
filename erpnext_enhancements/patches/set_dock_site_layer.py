# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Put Buying, Stock, Projects, Opportunities and CRM on everyone's Dock (v1.734.0).

Nik asked on 2026-10-23 for "Buying, Stock, Projects, Opportunities Kanban (CRM)". That morning
he had arranged the site's layer of ERPNext's dock by hand to Buying, Stock, Projects and CRM,
every other ERPNext module hidden. This writes the same layer with the Opportunities Kanban after
Projects, and every entry this app now mounts on ERPNext's rail named as hidden, so the layers
each person gets (``setup/dock_profiles.py``) have something to bring back.

It overwrites the hand-made layer once, on purpose: ``apply_dock_profiles`` keeps any layer a
person saved last, and Nik's would otherwise be kept and this change would never land. After this
run the layer reads as the app's, and every later migrate keeps it current until someone arranges
it again in Manage Dock.

``post_model_sync``, because model sync imports the app's own dock file, whose rows this names.
Cannot raise, since a raising patch aborts ``bench migrate``, which on this repo is the deploy.
Safe twice: the second run finds the layer already as written.
"""

import frappe


def execute():
	try:
		from erpnext_enhancements.setup.dock_profiles import write_site_layer

		print(f"set_dock_site_layer: {write_site_layer(force=True) or 'no ERPNext dock on this site'}")
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="set_dock_site_layer", message=frappe.get_traceback())
