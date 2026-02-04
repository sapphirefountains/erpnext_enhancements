# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

import frappe
import unittest
from frappe.utils import add_hours, now_datetime
from erpnext_enhancements.api.booking import create_composite_booking

class TestAssetBooking(unittest.TestCase):
    def setUp(self):
        # Create a dummy asset if not exists
        if not frappe.db.exists("Asset", "Test Asset 1"):
            doc = frappe.get_doc({
                "doctype": "Asset",
                "asset_name": "Test Asset 1",
                "item_code": "Test Item",
                "is_existing_asset": 1
            })
            doc.insert()
            self.asset = doc.name
        else:
            self.asset = "Test Asset 1"

        # Create a dummy address for location
        if not frappe.db.exists("Address", "Test Address 1"):
            doc = frappe.get_doc({
                "doctype": "Address",
                "address_title": "Test Address 1",
                "address_type": "Billing",
                "address_line1": "123 Test St",
                "city": "Test City",
                "country": "India"
            })
            # Insert with ignore_mandatory/permissions if needed, but usually minimal fields are enough
            # Country 'India' is standard in test seeds usually.
            doc.insert(ignore_permissions=True)
            self.address = doc.name
        else:
            self.address = "Test Address 1"

        # Clean up bookings
        frappe.db.delete("Asset Booking", {"asset": self.asset})

    def test_overlap_validation(self):
        start = now_datetime()
        end = add_hours(start, 2)

        # Create first booking
        b1 = frappe.get_doc({
            "doctype": "Asset Booking",
            "asset": self.asset,
            "booking_type": "Rental",
            "from_datetime": start,
            "to_datetime": end,
            "location": self.address
        })
        b1.insert()

        # Create overlapping booking
        b2 = frappe.get_doc({
            "doctype": "Asset Booking",
            "asset": self.asset,
            "booking_type": "Rental",
            "from_datetime": add_hours(start, 1),
            "to_datetime": add_hours(end, 1)
        })

        self.assertRaises(frappe.ValidationError, b2.insert)

    def test_composite_booking(self):
        start = now_datetime()
        end = add_hours(start, 2)

        # This calls the API
        result = create_composite_booking(self.asset, start, end, location=self.address)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["bookings"]["rental"])
        self.assertTrue(result["bookings"]["travel"])
        self.assertTrue(result["bookings"]["maintenance"])

        # Verify offsets
        travel = frappe.get_doc("Asset Booking", result["bookings"]["travel"])
        # Travel ends at rental start
        self.assertEqual(travel.to_datetime, start)
        self.assertEqual(travel.location, self.address)

        main = frappe.get_doc("Asset Booking", result["bookings"]["maintenance"])
        # Maintenance starts at rental end
        self.assertEqual(main.from_datetime, end)

    def tearDown(self):
        frappe.db.delete("Asset Booking", {"asset": self.asset})
        # cleanup address if strictly needed, but might be reused.
        # usually good practice to clean up what we created if it persists.
        # But 'Test Address 1' check in setUp handles existence.
