# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

class AssetBooking(Document):
    def validate(self):
        self.check_overlap()

    def on_update(self):
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def on_submit(self):
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def on_cancel(self):
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def after_delete(self):
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def check_overlap(self):
        if not self.asset or not self.from_datetime or not self.to_datetime:
            return

        overlap = frappe.db.exists(
            "Asset Booking",
            {
                "asset": self.asset,
                "name": ["!=", self.name],
                "docstatus": ["<", 2],
                "from_datetime": ["<", self.to_datetime],
                "to_datetime": [">", self.from_datetime]
            }
        )

        if overlap:
            frappe.throw(_("Asset is already booked during this period by {0}").format(overlap), frappe.ValidationError)

def update_asset_status(asset_name):
    if not asset_name:
        return

    now = frappe.utils.now_datetime()

    # Find active booking
    active_booking = frappe.db.get_value("Asset Booking", {
        "asset": asset_name,
        "docstatus": ["<", 2],
        "from_datetime": ["<=", now],
        "to_datetime": [">=", now]
    }, ["booking_type", "location"], as_dict=True)

    status = "Available"
    location = None

    if active_booking:
        booking_type = active_booking.booking_type
        location = active_booking.location

        if booking_type == "Rental":
            status = "Rented"
        elif booking_type == "Travel":
            status = "In Transit"
        elif booking_type == "Maintenance":
            status = "Maintenance"

    # Update Asset
    # Use ignore_permissions to ensure the update succeeds even if the user lacks write access to Asset
    # (e.g., they can book but not manage assets)
    # Note: frappe.ignore_permissions is a context manager available in recent versions.
    # If using an older version, explicit flags might be needed. assuming recent.
    # Using frappe.set_user or similar is also an option but context manager is cleaner.

    # Fallback for set_value with ignore_permissions check
    # frappe.db.set_value ignores permissions unless check_permissions=True is passed in some versions,
    # but to be safe we wrap it.

    # Actually, frappe.db.set_value typically bypasses permissions in server-side scripts unless mapped to a controller call.
    # But just in case:
    frappe.db.set_value("Asset", asset_name, {
        "custom_rental_status": status,
        "custom_current_event_location": location
    })

@frappe.whitelist()
def check_availability(asset, from_datetime, to_datetime, ignore_booking=None):
    if not asset or not from_datetime or not to_datetime:
        return {"available": False, "message": "Missing arguments"}

    filters = {
        "asset": asset,
        "docstatus": ["<", 2],
        "from_datetime": ["<", to_datetime],
        "to_datetime": [">", from_datetime]
    }

    if ignore_booking:
        filters["name"] = ["!=", ignore_booking]

    overlap = frappe.db.exists("Asset Booking", filters)

    if overlap:
        return {"available": False, "message": f"Asset is booked: {overlap}"}

    return {"available": True}

@frappe.whitelist()
def get_events(start, end, filters=None):
    from frappe.desk.calendar import get_event_conditions

    if isinstance(filters, str):
        filters = frappe.parse_json(filters)

    conditions = get_event_conditions("Asset Booking", filters)

    query = """
        SELECT
            name, from_datetime, to_datetime, booking_type, asset
        FROM
            `tabAsset Booking`
        WHERE
            ((from_datetime BETWEEN %(start)s AND %(end)s)
            OR (to_datetime BETWEEN %(start)s AND %(end)s)
            OR (from_datetime < %(start)s AND to_datetime > %(end)s))
            {conditions}
    """.format(conditions=conditions)

    data = frappe.db.sql(query, {"start": start, "end": end}, as_dict=True)

    events = []
    for d in data:
        color = "#3498db" # Default Blue
        if d.booking_type == "Travel":
            color = "#f1c40f" # Yellow
        elif d.booking_type == "Maintenance":
            color = "#e74c3c" # Red

        events.append({
            "name": d.name,
            "from_datetime": d.from_datetime,
            "to_datetime": d.to_datetime,
            "title": f"{d.asset} ({d.booking_type})",
            "color": color,
            "allDay": 0
        })

    return events
