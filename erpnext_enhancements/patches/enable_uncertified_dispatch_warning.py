"""Turn the uncertified-dispatch advisory on, now that it can fire (v1.386.0).

``warn_on_uncertified_dispatch`` has been ``0`` on this site since the field was
added, and turning it on before now would have changed nothing: ``_uncertified``
drew findings from three sources — an open assignment, a revoked or expired
completion, and a completion past its date — and **all three require the person
to have already been assigned the course**. With zero assignment rules on the
site, the advisory fired for nobody, ever, and reported clean while doing it.

v1.386.0 gives it a fourth source (a Required course this person's rules say they
owe, never assigned) and gives the site actual assignment rules, so the check now
has something to say. Nik chose "warn only, but make it fire" when asked, and a
switch left off is the check not firing.

**Warn, never block**, and that was the right call: a hard gate on dispatch does
not stop the visit, it moves the visit off the books and into somebody's truck
where nobody can see it.

Writes only over ``0`` — the value this app shipped — and never over a ``1``
somebody already set. It cannot tell a deliberate ``0`` from an untouched one, so
if this is turned back off by hand, note that this patch has already run and will
not re-run: ``tabPatch Log`` records it once.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Training Settings"):
		return

	current = frappe.db.get_value(
		"Singles", {"doctype": "Training Settings", "field": "warn_on_uncertified_dispatch"}, "value"
	)
	# A Single stores one row per field, and a field that has never been saved has
	# NO row at all -- `bench migrate` adds none and load_from_db applies no
	# defaults. So "0" and "missing" both mean off, and both are what this writes
	# over; anything else is somebody's own answer and is left alone.
	if current not in (None, "", "0", 0):
		return

	settings = frappe.get_single("Training Settings")
	settings.warn_on_uncertified_dispatch = 1
	settings.save(ignore_permissions=True)
	print(
		"[erpnext_enhancements] uncertified-dispatch advisory enabled "
		"(warn only; it never blocks a dispatch)"
	)
