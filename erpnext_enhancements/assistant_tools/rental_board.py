"""rental_board — the event-rental operation right now (read-only), v1.567.0.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class RentalBoard(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "rental_board"  # must match module filename
        self.description = (
            "Live board for Sapphire Fountains' fountain EVENT RENTALS. Returns every rental delivering, "
            "out on site, or coming back within the next `days` days (default 7): status (Tentative = "
            "a hold, Confirmed, Out, Returned), customer, event, delivery and take-down times, the "
            "fountains and accessories booked, the crew, whether the customer gave site details, and "
            "the deposit and balance invoices (draft / unpaid / paid). Also lists holds that lapse "
            "within 3 days and fountains out of service. Use it for 'what is going out this weekend', "
            "'what rentals need attention' or 'is the Smith rental paid'. For whether a fountain is "
            "free on given dates use rental_availability instead. Requires read access to Rental Booking."
        )
        self.category = "Event Rentals"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Rental Booking"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to look, 1 to 31. Default 7.",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.asset_management.rental_planner import board_summary

        return {"success": True, "board": board_summary((arguments or {}).get("days") or 7)}


__all__ = ["RentalBoard"]
