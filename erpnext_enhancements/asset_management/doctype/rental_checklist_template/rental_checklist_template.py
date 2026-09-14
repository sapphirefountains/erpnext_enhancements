# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Rental Checklist Template — what is supposed to be in the crate.

ER-2026-312370 asked for a pre-shipping and return checklist for rental fountains. The
obvious place to read the component list from would be the Asset Booking, and the
original work breakdown assumed exactly that. It does not work: an ``Asset Booking``
reserves **one** ``asset`` for a time window and has no items child table at all. There
is nowhere else either — nothing in this app records what a given fountain ships with.

So this doctype is that missing record, and everything else in the feature reads from
it. Two levels, resolved most-specific-first by
``api.booking.resolve_checklist_template``:

* a template naming an **Asset** — this particular fountain, with whatever it has
  accumulated;
* a template naming an **Asset Category** — every fountain of a type, so a newly bought
  unit is inspectable on day one rather than after somebody remembers to write a list.

Only one active template may exist per asset and per category, because "which list did
the crew use" is not a question a damage dispute should have to ask.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class RentalChecklistTemplate(Document):
    def validate(self):
        """Lifecycle hook: the template must be usable and unambiguous."""
        self.validate_scope()
        self.validate_items()
        self.validate_single_active()

    def validate_scope(self):
        """A template that names neither an Asset nor a category can never be found.

        ``resolve_checklist_template`` looks up by asset and then by category; a row with
        neither is unreachable by any lookup and would sit in the list looking maintained.
        """
        if not self.asset and not self.asset_category:
            frappe.throw(
                _("Set either an Asset or an Asset Category, so this template can be found."),
                frappe.ValidationError,
            )

    def validate_items(self):
        """Reject an empty template, and reject the same component listed twice.

        Empty is the dangerous one. A checklist with no rows submits clean and reads as
        "everything accounted for" — a signed record asserting a complete return, on no
        evidence. Refusing it here is why ``generate_inspection`` can trust what it gets.
        """
        if not self.items:
            frappe.throw(_("Add at least one component."), frappe.ValidationError)

        seen = {}
        for row in self.items:
            key = (row.component or "").strip().casefold()
            if not key:
                continue
            if key in seen:
                frappe.throw(
                    _("Row #{0}: {1} is already listed in row #{2}.").format(
                        row.idx, frappe.bold(row.component), seen[key]
                    ),
                    frappe.ValidationError,
                )
            seen[key] = row.idx

    def validate_single_active(self):
        """At most one active template per Asset, and per Asset Category.

        Not a unique index: a template may be deactivated and replaced, so the constraint
        is on the *active* subset, which an index cannot express.
        """
        if not self.is_active:
            return

        for fieldname, label in (("asset", _("Asset")), ("asset_category", _("Asset Category"))):
            value = self.get(fieldname)
            if not value:
                continue
            clash = frappe.db.exists(
                "Rental Checklist Template",
                {fieldname: value, "is_active": 1, "name": ["!=", self.name]},
            )
            if clash:
                frappe.throw(
                    _("{0} {1} already has an active template: {2}. Deactivate it first.").format(
                        label, frappe.bold(value), clash
                    ),
                    frappe.ValidationError,
                )
