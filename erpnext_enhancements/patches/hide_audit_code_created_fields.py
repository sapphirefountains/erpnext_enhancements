"""Hide the 16 code-created Custom Fields the UI field audit graded Hide (v1.799.1).

Why
---
The UI field audit (``docs/audits/ui-field-audit-2026-11.md``, "Hiding plan") graded 129 Custom
Fields Hide. Nik's rule, 2026-11-08: no Custom Field is ever deleted; a field nothing uses gets
``hidden: 1`` and keeps its column and its data. 108 of the 129 are fixture-managed, and the
fixture edit in ``fixtures/custom_field.json`` hides those on the next ``bench migrate``. The
other 21 were never in the fixtures: setup code, an old migration or another app created them on
the site, so no JSON edit can reach them. This patch is how 16 of them get hidden.

The other five are breaks and stay visible: Account's and Lead's ``primary_contact_section``
(Section Break) and ``primary_contact_col_break`` (Column Break), and User's
``work_environment`` (Section Break). A Section Break with ``hidden = 1`` hides its whole section
and a Column Break its whole column (``section.js`` toggles the control on ``df.hidden``), so the
standard fields that follow the break would vanish with it. Frappe collapses a section or column
whose own fields are all hidden, so hiding the fields is enough.

Which fields
------------
* **Account** (the chart-of-accounts DocType, not the Customer this site relabels "Account"):
  the Customer's Primary Contact section landed on it by mistake on 2026-04-20. 0 of 392
  accounts hold a value. ``primary_contact``, ``primary_contact_job_title``,
  ``primary_contact_phone``, ``primary_contact_email`` and the ``address_and_contact`` HTML
  field. Nothing in ``setup/custom_fields.py`` re-creates them (Account is not in its matrix).
* **Lead**: the same five, from the 2026-04 contact migration. 0 of 227 leads hold a value, and
  Lead is not in the unified-tab matrix either.
* **Item** ``custom_pump_curve``: the pre-v1.758 place for pump-curve points, 0 of 1,574 rows
  after ``migrate_item_pump_curves`` moved them to Pump Curve records.
  ``water_engineering/setup.py`` re-creates this one on every migrate, so it passes
  ``hidden: 1`` from this release on; without that the next deploy would unhide it.
* **Assignment Rule** ``assign_condition_json`` and ``unassign_condition_json``: Helpdesk
  leftovers; Helpdesk is uninstalled and its three rules are disabled.
* **CRM Deal** ``erpnext_customer``: a frappe CRM leftover whose table no longer exists.
* **UTM Campaign** ``crm_campaign``: a frappe CRM leftover, 0 rows.
* **User** ``twitter``: an orphan of an uninstalled app, 0 of 48 users.

What it does
------------
For each ``(doctype, fieldname)`` it looks the Custom Field row up with ``frappe.db.exists``; when
the row exists and is not already hidden it sets ``hidden = 1`` with ``frappe.db.set_value`` (the
row's ``modified`` stamp is not touched). Then it clears the cached meta of every doctype it
changed, so the forms redraw without the fields. A missing row is a no-op, so the patch is safe on
a site that never had the field, and safe to run twice.

Nothing is deleted: no ``delete_doc``, no column dropped, no value moved. A hidden field stays
readable server-side and comes back with one edit.

It never raises. A patch that raises aborts ``bench migrate``, which on this repo is the deploy
(v1.395.0); a failure is written to the Error Log and the patch returns.
"""

import frappe

#: ``(doctype, fieldname)`` pairs: the Hide-graded Custom Fields that are not in the fixtures,
#: breaks excluded (see the module docstring).
FIELDS_TO_HIDE = (
	("Account", "primary_contact"),
	("Account", "primary_contact_job_title"),
	("Account", "primary_contact_phone"),
	("Account", "primary_contact_email"),
	("Account", "address_and_contact"),
	("Lead", "primary_contact"),
	("Lead", "primary_contact_job_title"),
	("Lead", "primary_contact_phone"),
	("Lead", "primary_contact_email"),
	("Lead", "address_and_contact"),
	("Item", "custom_pump_curve"),
	("Assignment Rule", "assign_condition_json"),
	("Assignment Rule", "unassign_condition_json"),
	("CRM Deal", "erpnext_customer"),
	("UTM Campaign", "crm_campaign"),
	("User", "twitter"),
)


def execute() -> None:
	try:
		hide_code_created_fields()
	except Exception:
		frappe.log_error(title="hide_audit_code_created_fields: failed")


def hide_code_created_fields(fields=FIELDS_TO_HIDE) -> list[str]:
	"""Set ``hidden = 1`` on each existing, not-yet-hidden Custom Field; return the names changed."""
	changed = []
	touched_doctypes = set()
	for doctype, fieldname in fields:
		name = frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname})
		if not name:
			continue
		if frappe.db.get_value("Custom Field", name, "hidden"):
			continue
		frappe.db.set_value("Custom Field", name, "hidden", 1, update_modified=False)
		changed.append(name)
		touched_doctypes.add(doctype)

	for doctype in sorted(touched_doctypes):
		frappe.clear_cache(doctype=doctype)

	print(f"hide_audit_code_created_fields: hid {len(changed)} of {len(fields)} Custom Field(s)")
	return changed
