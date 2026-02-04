# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_hours, get_datetime

@frappe.whitelist()
def create_composite_booking(asset, rental_start, rental_end, location=None):
    rental_start = get_datetime(rental_start)
    rental_end = get_datetime(rental_end)

    if rental_start >= rental_end:
        frappe.throw(_("Rental Start must be before Rental End"))

    # Calculate offsets
    # Travel: 1 hour before Rental Start
    travel_start = add_hours(rental_start, -1)
    travel_end = rental_start

    # Maintenance: 1 hour after Rental End
    maintenance_start = rental_end
    maintenance_end = add_hours(rental_end, 1)

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
