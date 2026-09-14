# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Asset booking helper endpoint.

Whitelisted API for creating a "composite" Asset Booking: a single rental
request expands into three back-to-back Asset Booking documents (Travel,
Rental, Maintenance) so the asset is reserved for setup/teardown around the
rental window. Invoked from client scripts / the booking UI via
``erpnext_enhancements.api.booking.create_composite_booking``.

Security: standard authenticated whitelist; documents are created with normal
permissions (no ``ignore_permissions``). All three inserts run in one logical
transaction — on any failure the DB is rolled back and the exception re-raised
so nothing partial is committed.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime


@frappe.whitelist()
def create_composite_booking(asset, rental_start, rental_end, location=None):
    """Create linked Travel + Rental + Maintenance Asset Bookings for an asset.

    Args:
        asset (str): Asset (id) being booked.
        rental_start, rental_end (str|datetime): Rental window; start must be
            strictly before end (else ``frappe.throw``).
        location (str, optional): Location applied to all three bookings.

    Behaviour: Travel booking spans 1 hour before ``rental_start``; the Rental
    booking covers the requested window; Maintenance spans 1 hour after
    ``rental_end``. All three Asset Booking docs are inserted.

    Returns:
        dict: ``{"status": "success", "bookings": {"travel": ..., "rental":
        ..., "maintenance": ...}}`` with the created document names.

    Side effects: inserts 3 Asset Booking documents. On any exception the whole
    transaction is rolled back, logged to the Error Log, and the exception is
    re-raised so the client sees the failure.
    """
    rental_start = get_datetime(rental_start)
    rental_end = get_datetime(rental_end)

    if rental_start >= rental_end:
        frappe.throw(_("Rental Start must be before Rental End"))

    # Calculate offsets
    # Travel: 1 hour before Rental Start
    travel_start = add_to_date(rental_start, hours=-1)
    travel_end = rental_start

    # Maintenance: 1 hour after Rental End
    maintenance_start = rental_end
    maintenance_end = add_to_date(rental_end, hours=1)

    bookings = {}

    try:
        # Create Travel Booking
        travel_booking = frappe.get_doc({
            "doctype": "Asset Booking",
            "asset": asset,
            "booking_type": "Travel",
            "from_datetime": travel_start,
            "to_datetime": travel_end,
            "location": location
        })
        travel_booking.insert()
        bookings["travel"] = travel_booking.name

        # Create Rental Booking
        rental_booking = frappe.get_doc({
            "doctype": "Asset Booking",
            "asset": asset,
            "booking_type": "Rental",
            "from_datetime": rental_start,
            "to_datetime": rental_end,
            "location": location
        })
        rental_booking.insert()
        bookings["rental"] = rental_booking.name

        # Create Maintenance Booking
        maintenance_booking = frappe.get_doc({
            "doctype": "Asset Booking",
            "asset": asset,
            "booking_type": "Maintenance",
            "from_datetime": maintenance_start,
            "to_datetime": maintenance_end,
            "location": location
        })
        maintenance_booking.insert()
        bookings["maintenance"] = maintenance_booking.name

        return {
            "status": "success",
            "bookings": bookings
        }

    except Exception as e:
        frappe.db.rollback()
        # log_error(message, title): the constant is the title (searchable in the Error Log
        # list), the traceback is the message. These were inverted, so the log title was the
        # truncated exception text and the message was the constant.
        frappe.log_error(frappe.get_traceback(), "Composite Booking Failed")
        # Re-raise to ensure the client gets the error and transaction is aborted
        raise e


#: Checklist directions, matching ``Rental Inspection.direction``.
PRE_SHIPPING = "Pre-shipping"
RETURN = "Return"

#: Mirrors ``rental_inspection.NOT_APPLICABLE``. Duplicated rather than imported to keep
#: this module free of a doctype-controller import; the pair is pinned by a test.
NOT_APPLICABLE = "N/A"


def resolve_checklist_template(asset):
    """Return the active Rental Checklist Template for an asset, or None.

    Most specific wins: a template naming this exact Asset, else one naming its Asset
    Category. ``RentalChecklistTemplate.validate_single_active`` guarantees at most one
    active template at each level, so this never has to choose between two.

    Args:
        asset (str): Asset docname.

    Returns:
        str | None: Template docname.
    """
    if not asset:
        return None

    by_asset = frappe.db.get_value(
        "Rental Checklist Template", {"asset": asset, "is_active": 1}, "name"
    )
    if by_asset:
        return by_asset

    category = frappe.db.get_value("Asset", asset, "asset_category")
    if not category:
        return None

    return frappe.db.get_value(
        "Rental Checklist Template", {"asset_category": category, "is_active": 1}, "name"
    )


def _rows_from_template(template):
    """Checklist rows copied from a template. ``qty_expected`` comes from the template."""
    doc = frappe.get_doc("Rental Checklist Template", template)
    return [
        {
            "component": row.component,
            "qty_expected": row.qty_expected,
            "notes": row.notes,
        }
        for row in doc.items
    ]


def _rows_from_pre_shipping(inspection):
    """Checklist rows copied from a submitted pre-shipping sheet.

    ``qty_expected`` is set to what was actually **counted out** (``qty_accounted``), not
    to what the template said. That is the whole reason a return reads from here rather
    than from the template: "are all items accounted for" is a question about what left
    the yard, and if three of four panels shipped, three coming back is complete.

    Rows the pre-shipping crew marked ``N/A`` are dropped. A category-level template
    lists parts that a given fountain does not carry, and a component this unit never had
    is not something the return crew should be asked about twice — it would arrive with a
    blank ``qty_expected`` and demand a count of a part that does not exist.
    """
    doc = frappe.get_doc("Rental Inspection", inspection)
    return [
        {
            "component": row.component,
            "qty_expected": row.qty_accounted,
            "notes": row.notes,
        }
        for row in doc.items
        if row.condition != NOT_APPLICABLE
    ]


@frappe.whitelist()
def generate_inspection(booking, direction):
    """Create (or return) the draft Rental Inspection for a booking and direction.

    Args:
        booking (str): Asset Booking docname.
        direction (str): ``Pre-shipping`` or ``Return``.

    Returns:
        dict: ``{"inspection": <docname>, "created": bool, "source": <description>}``.

    Behaviour: if a non-cancelled inspection already exists for this booking and
    direction it is returned untouched (``created: False``) rather than a second one
    being made — two sheets for one crate is the ambiguity this feature exists to remove.

    Rows come from the pre-shipping sheet for a return, and from the asset's checklist
    template otherwise. **Throws rather than returning an empty checklist** when neither
    source exists: a sheet with no rows submits clean and reads as "everything accounted
    for", which is the worst output this feature could produce.
    """
    if direction not in (PRE_SHIPPING, RETURN):
        frappe.throw(_("Unknown inspection direction: {0}").format(direction))

    booking_doc = frappe.get_doc("Asset Booking", booking)
    booking_doc.check_permission("read")

    existing = frappe.db.get_value(
        "Rental Inspection",
        {"asset_booking": booking, "direction": direction, "docstatus": ["<", 2]},
        "name",
    )
    if existing:
        return {"inspection": existing, "created": False, "source": _("Already existed")}

    rows, source = _resolve_rows(booking, booking_doc.asset, direction)

    inspection = frappe.get_doc(
        {
            "doctype": "Rental Inspection",
            "asset_booking": booking,
            "direction": direction,
            "asset": booking_doc.asset,
            "inspected_by": frappe.session.user,
            "inspection_datetime": frappe.utils.now_datetime(),
            "row_source": source,
            "items": rows,
        }
    )
    inspection.insert()

    return {"inspection": inspection.name, "created": True, "source": source}


def _resolve_rows(booking, asset, direction):
    """(rows, source description) for a new inspection, or throw if there is no source."""
    if direction == RETURN:
        pre_shipping = frappe.db.get_value(
            "Rental Inspection",
            {"asset_booking": booking, "direction": PRE_SHIPPING, "docstatus": 1},
            "name",
        )
        if pre_shipping:
            return _rows_from_pre_shipping(pre_shipping), _("Pre-shipping sheet {0}").format(
                pre_shipping
            )

    template = resolve_checklist_template(asset)
    if template:
        note = _("Template {0}").format(template)
        if direction == RETURN:
            # Say so on the document. A return reconciled against a catalogue rather
            # than against what shipped is a weaker record, and the person reading it
            # later should not have to work that out from the numbers.
            note = _("Template {0} (no submitted pre-shipping sheet)").format(template)
        return _rows_from_template(template), note

    frappe.throw(
        _(
            "No active Rental Checklist Template for {0}, and no submitted pre-shipping "
            "inspection to reconcile against. Create a template first — an empty checklist "
            "would sign off as complete."
        ).format(asset or _("this asset"))
    )
