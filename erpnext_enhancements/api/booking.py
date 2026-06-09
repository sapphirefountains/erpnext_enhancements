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
        frappe.log_error("Composite Booking Failed", str(e))
        # Re-raise to ensure the client gets the error and transaction is aborted
        raise e
