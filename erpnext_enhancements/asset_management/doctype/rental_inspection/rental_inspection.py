# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Rental Inspection — the pre-shipping and return checklist.

ER-2026-312370: "we would like a form to track that all items are accounted for and a
visual inspection of various components ... for any damage."

--------------------------------------------------------------------------------------
Where the enforcement lives, and why it is not on the booking
--------------------------------------------------------------------------------------

The original work breakdown proposed blocking the Asset Booking from moving to
"Dispatched" without a pre-shipping sheet and to "Returned" without a return sheet.
**Asset Booking has no status field**, and no such values exist anywhere in this app. It
is submittable, and it is submitted when the booking is *made* — days before anything
ships — so there is no later lifecycle event on it to gate. The Asset's
``custom_rental_status`` is no substitute either: it is derived from the clock by
``update_asset_status`` and nobody sets it by hand.

The inspection, though, has a real sign-off moment, and that is what is gated here.

--------------------------------------------------------------------------------------
Draft is cheap, submit is strict
--------------------------------------------------------------------------------------

The completeness rules are ``before_submit``, not ``validate``. A crew member walking a
crate fills the sheet over several minutes and saves as they go; refusing to save a
half-filled draft would repeat exactly the mistake that made ER-2026-420503 ("I want to
input the minimal essential information so I can just get it started"). What must not
happen is a *signed* sheet that is silently incomplete.

That is the failure this file is built against: an inspection with unanswered rows that
submits clean reads as "everything came back fine" — a record asserting a complete
return on no evidence, and the strongest document in the room the day a customer
disputes a damage charge. Refusing submit is the point of the whole feature.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

#: Conditions that mean something is wrong with the component itself.
ADVERSE_CONDITIONS = ("Damaged", "Missing")


class RentalInspection(Document):
    def validate(self):
        """Lifecycle hook: keep the roll-ups true on every save, draft included.

        Deliberately cheap. The gates that can refuse a document are in ``before_submit``.
        """
        self.set_findings()

    def before_submit(self):
        """Lifecycle hook: refuse a sign-off that does not actually say anything."""
        self.validate_every_row_answered()
        self.validate_adverse_rows_explained()

    def on_submit(self):
        """Lifecycle hook: put any finding where someone will actually meet it."""
        self.report_findings_to_booking()

    # ------------------------------------------------------------------ roll-ups

    def set_findings(self):
        """Recompute ``has_damage`` / ``has_shortfall`` from the rows.

        Both are read-only fields rather than something a user asserts, so that "did this
        rental come back short" is a query rather than a reading exercise across child
        rows.

        A blank ``qty_accounted`` is **not** treated as a shortfall: at draft it means
        "not counted yet", which is a different fact from "counted, and short". Submit
        is where the difference stops being allowed to persist.
        """
        damage = False
        shortfall = False

        for row in self.items or []:
            if row.condition in ADVERSE_CONDITIONS:
                damage = True
            if row.qty_accounted is None or row.qty_accounted == "":
                continue
            if flt(row.qty_accounted) < flt(row.qty_expected):
                shortfall = True

        self.has_damage = 1 if damage else 0
        self.has_shortfall = 1 if shortfall else 0

    # ------------------------------------------------------------------ submit gates

    def validate_every_row_answered(self):
        """Every row needs a condition and a count before this can be signed off."""
        unanswered = [row.idx for row in self.items or [] if not row.condition]
        if unanswered:
            frappe.throw(
                _("Rows {0} have no condition recorded. Every component has to be looked at.").format(
                    ", ".join(f"#{idx}" for idx in unanswered)
                ),
                frappe.ValidationError,
            )

        uncounted = [
            row.idx
            for row in self.items or []
            if row.qty_accounted is None or row.qty_accounted == ""
        ]
        if uncounted:
            frappe.throw(
                _("Rows {0} have not been counted. Enter 0 if the component is not there.").format(
                    ", ".join(f"#{idx}" for idx in uncounted)
                ),
                frappe.ValidationError,
            )

    def validate_adverse_rows_explained(self):
        """Damaged or Missing needs a note.

        "Missing", on its own, is where the trail goes cold three weeks later when
        somebody asks whether it was ever in the crate.
        """
        unexplained = [
            row.idx
            for row in self.items or []
            if row.condition in ADVERSE_CONDITIONS and not (row.notes or "").strip()
        ]
        if unexplained:
            frappe.throw(
                _("Rows {0} are marked Damaged or Missing with no note. Say what you found.").format(
                    ", ".join(f"#{idx}" for idx in unexplained)
                ),
                frappe.ValidationError,
            )

    # ------------------------------------------------------------------ reporting

    def report_findings_to_booking(self):
        """Comment on the linked Asset Booking when anything came back wrong.

        The booking is where anyone looking into a rental starts, and a finding filed
        only on the inspection is a finding nobody meets.

        Deliberately **not** an NCR or a Task. Quality's NCR path carries its own triage
        and paging (v1.453.0) and is about product non-conformance; a scuffed rental
        panel entering that queue would be noise in someone else's process. If comments
        turn out not to be enough, escalation can be added knowing why — adding it now
        would be guessing.
        """
        if not (self.has_damage or self.has_shortfall) or not self.asset_booking:
            return

        lines = []
        for row in self.items or []:
            if row.condition in ADVERSE_CONDITIONS:
                lines.append(f"<li>{frappe.utils.escape_html(row.component)} — {row.condition}"
                             f": {frappe.utils.escape_html((row.notes or '').strip())}</li>")
            elif row.qty_accounted is not None and flt(row.qty_accounted) < flt(row.qty_expected):
                lines.append(
                    f"<li>{frappe.utils.escape_html(row.component)} — "
                    f"{flt(row.qty_accounted):g} of {flt(row.qty_expected):g} accounted for</li>"
                )

        if not lines:
            return

        frappe.get_doc(
            {
                "doctype": "Comment",
                "comment_type": "Comment",
                "reference_doctype": "Asset Booking",
                "reference_name": self.asset_booking,
                "content": (
                    f"<p><b>{self.direction} inspection {self.name} recorded a problem.</b></p>"
                    f"<ul>{''.join(lines)}</ul>"
                ),
            }
        ).insert(ignore_permissions=True)
