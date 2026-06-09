# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Asset Booking submittable doctype.

An Asset Booking reserves an Asset for a time window (``from_datetime`` ->
``to_datetime``) with a ``booking_type`` of Rental / Travel / Maintenance and an
optional ``location`` (Address). Bookings default to a Calendar view and feed the
``get_events`` calendar/feed below; the API helper
``api.booking.create_composite_booking`` chains Travel + Rental + Maintenance
bookings together (see test_asset_booking.py).

Every mutation re-derives the parent Asset's denormalised status fields via the
background worker ``update_asset_status`` (module function below), and bookings
are prevented from overlapping the same Asset (``check_overlap`` /
``check_availability``).
"""

import frappe
from frappe import _
from frappe.model.document import Document

class AssetBooking(Document):
    def validate(self):
        """Lifecycle hook: block overlapping bookings for the same Asset."""
        self.check_overlap()

    def on_update(self):
        """Lifecycle hook: refresh the Asset's status in the background after save."""
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def on_submit(self):
        """Lifecycle hook: refresh the Asset's status in the background on submit."""
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def on_cancel(self):
        """Lifecycle hook: refresh the Asset's status in the background on cancel."""
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def after_delete(self):
        """Lifecycle hook: refresh the Asset's status in the background after delete."""
        frappe.enqueue('erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def check_overlap(self):
        """Throw a ValidationError if this booking overlaps another for the Asset.

        No-op unless asset and both datetimes are set. Considers any non-cancelled
        (``docstatus < 2``) booking for the same Asset whose window intersects this
        one, excluding the current record.
        """
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
    """Recompute and write an Asset's denormalised rental status + location.

    Background worker enqueued by every Asset Booking lifecycle hook. Finds the
    Asset's currently-active (non-cancelled) booking, if any, and maps its
    ``booking_type`` to a status (Rental->Rented, Travel->In Transit,
    Maintenance->Maintenance; otherwise "Available"). Writes ``custom_rental_status``
    and ``custom_current_event_location`` back onto the Asset via
    ``frappe.db.set_value`` (bypasses Asset write permissions by design — a user
    may be able to book but not manage Assets).

    Args:
        asset_name (str): Asset docname; no-op if falsy.
    """
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
    """Whitelisted: report whether an Asset is free for a time window.

    Called from the Asset Booking form JS as the user fills in asset/dates.

    Args:
        asset (str): Asset docname.
        from_datetime, to_datetime (str): Requested window.
        ignore_booking (str|None): Booking docname to exclude (the current record).

    Returns:
        dict: {"available": bool, "message": str} — ``message`` names the
        conflicting booking when unavailable.
    """
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
    """Whitelisted: calendar feed of Asset Bookings overlapping [start, end].

    Wired as the doctype's calendar data source (default Calendar view). Applies
    any standard desk-calendar ``filters`` and colour-codes events by booking type
    (Travel=yellow, Maintenance=red, otherwise blue).

    Args:
        start, end (str): Calendar viewport bounds.
        filters: Optional desk-calendar filters (JSON string or list).

    Returns:
        list[dict]: Event dicts (name, from/to datetimes, title, color, allDay).
    """
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
