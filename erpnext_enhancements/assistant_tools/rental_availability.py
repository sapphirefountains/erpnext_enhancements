"""rental_availability — which rental fountains and accessories are free for given dates (read-only), v1.567.0.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


def _window(start, end):
    """Delivery and take-down datetimes from a date or datetime string each; a bare end date
    means the end of that day, and no end means the end of the start day."""
    from frappe.utils import get_datetime, getdate

    start = str(start or "").strip()
    end = str(end or "").strip() or start.split(" ")[0].split("T")[0]
    delivery = get_datetime(start if (" " in start or "T" in start) else f"{getdate(start)} 00:00:00")
    takedown = get_datetime(end if (" " in end or "T" in end) else f"{getdate(end)} 23:59:59")
    return delivery, takedown


class RentalAvailability(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "rental_availability"  # must match module filename
        self.description = (
            "Checks which of Sapphire Fountains' RENTAL fountains and accessories are free for an event. "
            "Give the start (delivery) date and optionally the end (take-down) date; plain dates cover "
            "whole days. Each fountain is checked against its own calendar including its prep and "
            "cleaning time, other bookings, and out-of-service periods; each accessory pool reports how "
            "many units are free at the busiest moment. Returns free fountains, taken fountains with what "
            "is in the way, and accessory counts. Use it for 'is the tiered fountain free on June 14' or "
            "'what can we offer for the 3rd to the 5th'. It never books anything: bookings are made on the "
            "Rental Planner. Requires read access to Rental Booking."
        )
        self.category = "Event Rentals"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Rental Booking"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "Delivery date or datetime, YYYY-MM-DD or YYYY-MM-DD HH:MM.",
                },
                "end": {
                    "type": "string",
                    "description": "Take-down date or datetime. Omit for a one-day event.",
                },
            },
            "required": ["start"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.asset_management.rental_availability import get_availability

        arguments = arguments or {}
        if not arguments.get("start"):
            return {"success": False, "error": "Give a start date, YYYY-MM-DD."}
        try:
            delivery, takedown = _window(arguments.get("start"), arguments.get("end"))
        except Exception:
            return {"success": False, "error": "Dates must look like 2026-06-14 or 2026-06-14 09:00."}
        if takedown <= delivery:
            return {"success": False, "error": "The end has to be after the start."}
        data = get_availability(str(delivery), str(takedown))
        return {
            "success": True,
            "window": {"from": str(delivery), "to": str(takedown)},
            "free_fountains": [
                {"fountain": f["asset_name"] or f["asset"], "asset": f["asset"], "model": f["item_code"]}
                for f in data["fountains"]
                if f["available"]
            ],
            "taken_fountains": [
                {
                    "fountain": f["asset_name"] or f["asset"],
                    "asset": f["asset"],
                    "in_the_way": [c["label"] for c in f["conflicts"]],
                }
                for f in data["fountains"]
                if not f["available"]
            ],
            "accessories": [
                {"accessory": p["label"], "free": p["available"], "bookable": p["capacity"]} for p in data["pools"]
            ],
        }


__all__ = ["RentalAvailability"]
