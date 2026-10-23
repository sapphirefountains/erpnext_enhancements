"""One-time migration patch (post_model_sync; listed in patches.txt).

Shows the ToDo's Due Date again, on the form and in the list (v1.739.0, Nik 2026-10-23).

Three Property Setters hid ToDo's native ``date`` ("Due Date") on production, and none of them is
in this repo:

=============================  ==================  =====
Property Setter                Property            Value
=============================  ==================  =====
``ToDo-date-hidden``           ``hidden``          1
``ToDo-date-reqd``             ``reqd``            0
``ToDo-date-in_list_view``     ``in_list_view``    0
=============================  ==================  =====

All three are ``is_system_generated`` and were written on 2025-12-10 by
``patches/migrate_todo_dates.py`` (commit 4ecc1432, ``make_property_setter``), which moved ToDo
onto two custom datetime fields for the old calendar views. "Remove Google Calendar" (42bd7e7d)
deleted that patch, and ``remove_google_calendar_fields`` the fields, but nothing undid the
hiding, so for ten months a ToDo's form and list had no due date at all -- while
My Work buckets every assignment by exactly that field, and its Snooze writes it. The fixture
filter skips system-generated setters, so they could not be managed from fixtures either.

Deleting them restores the native field: visible, in the list view, and not mandatory (v16
``todo.json`` declares ``in_list_view`` 1 and no ``reqd``). The form says under it when the date is
only the day the ToDo was made, which My Work reads as no due date
(``public/js/global_enhancements/my_work.js``).

Idempotent: deletes only what is present. Never raises: a migrate that raises is a failed deploy,
and a ToDo form with its Due Date still hidden is not worth one.
"""

import frappe

HIDING = (
	"ToDo-date-hidden",
	"ToDo-date-reqd",
	"ToDo-date-in_list_view",
)


def execute():
	for name in HIDING:
		try:
			if frappe.db.exists("Property Setter", name):
				frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
		except Exception:
			frappe.log_error(
				title=f"show_todo_due_date: could not delete {name}", message=frappe.get_traceback()
			)

	# Property Setters are baked into the cached Meta; without this the hidden field survives in
	# cache until the next unrelated change to ToDo.
	frappe.clear_cache(doctype="ToDo")
