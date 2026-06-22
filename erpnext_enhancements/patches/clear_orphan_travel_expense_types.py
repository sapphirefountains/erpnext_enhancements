"""One-time migration patch (post_model_sync; listed in patches.txt).

Travel Settings has six ``Link -> Expense Claim Type`` fields. ``Expense Claim
Type`` is an HRMS doctype, and HRMS is an *optional* dependency of this app. On
a site without HRMS a stored value in any of those fields makes Frappe core's
``getdoc`` raise ``DoesNotExistError`` (404) while resolving the link title —
which bricks the Travel Settings form so badly you cannot even open it to clear
the value by hand.

This clears those six fields whenever the ``Expense Claim Type`` doctype is
absent, restoring the form on deploy. Fully idempotent: a no-op once cleared,
and it never touches anything when HRMS *is* installed (the values are valid
there). Uses ``set_single_value`` to avoid the very link validation that would
otherwise choke on the missing doctype.
"""

import frappe

EXPENSE_TYPE_FIELDS = (
	"flight_expense_type",
	"hotel_expense_type",
	"ground_expense_type",
	"misc_expense_type",
	"per_diem_expense_type",
	"mileage_expense_type",
)


def execute():
	# HRMS present -> the values are legitimate, leave them alone.
	if frappe.db.exists("DocType", "Expense Claim Type"):
		return
	# Travel Settings not migrated yet on this site -> nothing to clear.
	if not frappe.db.exists("DocType", "Travel Settings"):
		return

	for fieldname in EXPENSE_TYPE_FIELDS:
		if frappe.db.get_single_value("Travel Settings", fieldname):
			frappe.db.set_single_value("Travel Settings", fieldname, None)
