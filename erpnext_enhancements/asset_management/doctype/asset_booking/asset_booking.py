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
from frappe.utils import get_datetime

#: ``frappe.flags`` key naming the Rental Booking currently rewriting its own legs
#: (``asset_management.rental_availability.SYNC_FLAG``; duplicated to keep this controller
#: free of that import at module load).
RENTAL_SYNC_FLAG = "rental_booking_sync"

_STATUS_JOB = 'erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.update_asset_status'


class AssetBooking(Document):
    def validate(self):
        """Lifecycle hook: block overlapping bookings for the same Asset."""
        self.guard_rental_leg()
        self.check_overlap()

    def before_update_after_submit(self):
        """Dates and location may change after submit (a rental moved in place keeps its
        inspections), so the overlap check has to run here too."""
        self.guard_rental_leg()
        self.check_overlap()

    def on_update_after_submit(self):
        frappe.enqueue(_STATUS_JOB, asset_name=self.asset)

    def before_cancel(self):
        self.guard_rental_leg(action=_("canceled"))

    def on_trash(self):
        self.guard_rental_leg(action=_("deleted"))

    def is_synced_by_owner(self):
        return bool(self.rental_booking) and frappe.flags.get(RENTAL_SYNC_FLAG) == self.rental_booking

    def guard_rental_leg(self, action=None):
        """A leg of a Rental Booking is changed through the booking, never by hand.

        Otherwise the booking would still believe it holds a fountain whose calendar says
        otherwise — the double booking this whole arrangement exists to prevent. Edits
        that do not move the leg (a note, the location) are left alone.
        """
        if not getattr(self, "rental_booking", None) or self.is_synced_by_owner():
            return
        if action is None:
            before = self.get_doc_before_save()
            if before is None:
                action = _("created")
            elif any(
                str(before.get(f) or "") != str(self.get(f) or "")
                for f in ("asset", "from_datetime", "to_datetime", "booking_type")
            ):
                action = _("moved")
            else:
                return
        frappe.throw(
            _("This is part of Rental Booking {0} and cannot be {1} here. Change the booking instead.").format(
                self.rental_booking, action
            ),
            title=_("Managed by a rental"),
        )

    def on_update(self):
        """Lifecycle hook: refresh the Asset's status in the background after save."""
        frappe.enqueue('erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def on_submit(self):
        """Lifecycle hook: refresh the Asset's status in the background on submit."""
        frappe.enqueue('erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def on_cancel(self):
        """Lifecycle hook: refresh the Asset's status in the background on cancel."""
        frappe.enqueue('erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def after_delete(self):
        """Lifecycle hook: refresh the Asset's status in the background after delete."""
        frappe.enqueue('erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.update_asset_status', asset_name=self.asset)

    def check_overlap(self):
        """Throw a ValidationError if this booking overlaps another for the Asset.

        No-op unless asset and both datetimes are set. Considers any non-cancelled
        (``docstatus < 2``) booking for the same Asset whose window intersects this
        one, excluding the current record.
        """
        if not self.asset or not self.from_datetime or not self.to_datetime:
            return
        if get_datetime(self.to_datetime) <= get_datetime(self.from_datetime):
            frappe.throw(_("To Datetime has to be after From Datetime."))

        # Row-lock the Asset first, so two bookings saved at the same moment are checked one
        # after the other rather than both reading "free" (v1.562.0).
        frappe.db.sql("select name from `tabAsset` where name = %s for update", (self.asset,))

        # One rental's own legs never collide with each other by construction
        # (rental_rules.leg_windows), but while the booking moves them one at a time, a
        # lengthened Rental leg briefly overlaps the old Turnaround leg it is about to move.
        # So a leg is checked against everything except its own rental. Raw SQL for the
        # coalesce: rental_booking is NULL on every hand-made booking, and NULL != x is not true.
        rows = frappe.db.sql(
            """
            select name from `tabAsset Booking`
            where asset = %(asset)s
                and name != %(name)s
                and docstatus < 2
                and from_datetime < %(to)s
                and to_datetime > %(from)s
                and (%(rental)s = '' or coalesce(rental_booking, '') != %(rental)s)
            order by from_datetime
            limit 1
            """,
            {
                "asset": self.asset,
                "name": self.name or "",
                "from": self.from_datetime,
                "to": self.to_datetime,
                "rental": getattr(self, "rental_booking", None) or "",
            },
        )
        overlap = rows[0][0] if rows else None

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

    # A fountain out of service reads so, unless it is still out at an event — it came
    # back damaged, or broke on site; it becomes Out of Service when that rental ends.
    if status != "Rented" and frappe.db.exists(
        "Asset Out of Service",
        {"asset": asset_name, "status": "Out of Service", "out_from": ["<=", now]},
    ):
        status = "Out of Service"

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


def refresh_asset_statuses():
    """Hourly: recompute rental status/location for assets whose booking window began or
    ended in the last couple of hours.

    ``update_asset_status`` only runs on a booking's own lifecycle hooks, so the denormalised
    ``custom_rental_status`` is correct only when a document mutation happens to coincide with
    the active window: a booking made a week ahead never flips the asset to Rented when its
    window starts, and the status persists after it ends. Recomputing on each window boundary
    crossing closes that — the from/to boundary of an advance booking lands in this window during
    the hour it happens, and ``update_asset_status`` is idempotent so a re-run is free.
    """
    now = frappe.utils.now_datetime()
    window_start = frappe.utils.add_to_date(now, hours=-2)
    rows = frappe.get_all(
        "Asset Booking",
        filters={"docstatus": ["<", 2]},
        or_filters=[
            {"from_datetime": ["between", [window_start, now]]},
            {"to_datetime": ["between", [window_start, now]]},
        ],
        pluck="asset",
    )
    for asset_name in {a for a in rows if a}:
        try:
            update_asset_status(asset_name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Asset status refresh")

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

    query = f"""
        SELECT
            name, from_datetime, to_datetime, booking_type, asset, docstatus,
            customer, rental_booking, rental_leg
        FROM
            `tabAsset Booking`
        WHERE
            docstatus < 2
            AND ((from_datetime BETWEEN %(start)s AND %(end)s)
            OR (to_datetime BETWEEN %(start)s AND %(end)s)
            OR (from_datetime < %(start)s AND to_datetime > %(end)s))
            {conditions}
    """

    data = frappe.db.sql(query, {"start": start, "end": end}, as_dict=True)

    events = []
    for d in data:
        color = "#3498db" # Default Blue
        if d.booking_type == "Travel":
            color = "#f1c40f" # Yellow
        elif d.booking_type == "Maintenance":
            color = "#e74c3c" # Red
        # A tentative rental hold is a draft leg: it blocks the dates, but reads lighter so
        # nobody mistakes a quote for a confirmed event.
        if d.rental_booking and d.docstatus == 0:
            color = "#aed6f1" if d.booking_type == "Rental" else "#f5b7b1"

        label = d.rental_leg if d.rental_booking else d.booking_type
        title = f"{d.asset} ({label})"
        if d.customer:
            title = f"{d.asset}: {d.customer} ({label}{', held' if d.docstatus == 0 else ''})"

        events.append({
            "name": d.name,
            "from_datetime": d.from_datetime,
            "to_datetime": d.to_datetime,
            "title": title,
            "color": color,
            "allDay": 0
        })

    return events
