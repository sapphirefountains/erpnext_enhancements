"""Bench-free unit tests for the Address coordinate gate.

Stubs a minimal ``frappe`` (no site/bench) so
``script_migrations.address.validate_coordinates`` runs under plain unittest.
The stub is installed in ``setUpModule`` (execution time), not at import, so it
never fools the bench-only suites' ``import frappe`` skip-guards.

Why this is worth a suite of its own: ``custom_latitude``/``custom_longitude``
became user-editable in v1.207.0, and every map in the app trusts that point
*over* the address text. So these two fields are the one place a typo silently
relocates a job site, and both failures this guards save cleanly today:

  * **half a pair** — a filled Latitude with an empty Longitude reads as "no
    point at all" to every consumer, so the record looks saved and located and
    is neither;
  * **out of range** — almost always a transposed pair, which is a
    valid-looking number that lands the site in the wrong hemisphere.

It also pins the sentinel: 0.0 means *absent* everywhere in this codebase, so
an untouched Address must pass through the gate silently rather than being
rejected for having no coordinates.

Run: python -m unittest erpnext_enhancements.tests.test_address_coordinates
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

address = None


class StubValidationError(Exception):
	pass


def setUpModule():
	global address

	frappe = types.ModuleType("frappe")

	def _throw(msg, exc=None, title=None):
		raise StubValidationError(msg)

	frappe.throw = _throw
	frappe.ValidationError = StubValidationError
	frappe._ = lambda msg: msg

	utils = types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		try:
			out = float(value)
		except (TypeError, ValueError):
			return 0.0
		return round(out, precision) if precision is not None else out

	utils.flt = flt
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	from erpnext_enhancements.script_migrations import address as address_module

	address = address_module


class StubAddress:
	"""A doc with only the attributes the hook reads."""

	def __init__(self, **fields):
		self.address_line1 = "1907 Indiana Ave"
		self.address_line2 = None
		self.city = "Salt Lake City"
		self.state = "UT"
		self.pincode = "84104"
		self.custom_full_address = None
		# DB-shaped: the Float columns are NOT NULL DEFAULT 0, so an Address
		# that never had a point reads back as 0.0, never None.
		self.custom_latitude = 0.0
		self.custom_longitude = 0.0
		self.custom_location_source = ""
		for key, value in fields.items():
			setattr(self, key, value)


class TestValidateCoordinates(unittest.TestCase):
	def test_no_coordinates_passes_silently(self):
		# The overwhelming majority of Addresses. Must not throw, and must not
		# invent a provenance for a point that does not exist.
		doc = StubAddress()
		address.validate_coordinates(doc)
		self.assertEqual(doc.custom_latitude, 0.0)
		self.assertEqual(doc.custom_longitude, 0.0)
		self.assertEqual(doc.custom_location_source, "")

	def test_valid_pair_is_kept(self):
		doc = StubAddress(custom_latitude=40.889402, custom_longitude=-111.880771)
		address.validate_coordinates(doc)
		self.assertEqual(doc.custom_latitude, 40.889402)
		self.assertEqual(doc.custom_longitude, -111.880771)

	def test_latitude_without_longitude_throws(self):
		# Saves cleanly without this gate, then reads as "no point" everywhere —
		# the user sees a successful save and a site that is not located.
		doc = StubAddress(custom_latitude=40.889402)
		with self.assertRaises(StubValidationError):
			address.validate_coordinates(doc)

	def test_longitude_without_latitude_throws(self):
		doc = StubAddress(custom_longitude=-111.880771)
		with self.assertRaises(StubValidationError):
			address.validate_coordinates(doc)

	def test_latitude_out_of_range_throws(self):
		for bad in (91.0, -90.5, 181.0):
			with self.subTest(latitude=bad):
				doc = StubAddress(custom_latitude=bad, custom_longitude=-111.880771)
				with self.assertRaises(StubValidationError):
					address.validate_coordinates(doc)

	def test_longitude_out_of_range_throws(self):
		for bad in (181.0, -180.5):
			with self.subTest(longitude=bad):
				doc = StubAddress(custom_latitude=40.889402, custom_longitude=bad)
				with self.assertRaises(StubValidationError):
					address.validate_coordinates(doc)

	def test_transposed_pair_is_caught(self):
		# The realistic mistake: a Utah longitude is not a valid latitude, so the
		# range check is what stops the site landing in the wrong hemisphere.
		doc = StubAddress(custom_latitude=-111.880771, custom_longitude=40.889402)
		with self.assertRaises(StubValidationError):
			address.validate_coordinates(doc)

	def test_precision_is_trimmed_to_six_places(self):
		doc = StubAddress(custom_latitude=40.8894021234, custom_longitude=-111.8807719876)
		address.validate_coordinates(doc)
		self.assertEqual(doc.custom_latitude, 40.889402)
		self.assertEqual(doc.custom_longitude, -111.880772)

	def test_blank_source_is_left_blank(self):
		# The hook must NOT stamp a provenance on a point that arrives without
		# one. It cannot tell an API write from a pre-v1.207.0 row being re-saved
		# for an unrelated reason, and those legacy rows all hold Google points
		# with a blank source — stamping them "Manual" would migrate the whole
		# table into the one state where clear-on-edit stops firing, silently and
		# on a read-only field. Blank is read as Google everywhere, which is the
		# safe direction for a point of unknown origin.
		doc = StubAddress(custom_latitude=40.889402, custom_longitude=-111.880771)
		address.validate_coordinates(doc)
		self.assertEqual(doc.custom_location_source, "")

	def test_existing_source_is_not_overwritten(self):
		doc = StubAddress(
			custom_latitude=40.889402,
			custom_longitude=-111.880771,
			custom_location_source="Google",
		)
		address.validate_coordinates(doc)
		self.assertEqual(doc.custom_location_source, "Google")

	def test_missing_custom_fields_do_not_raise(self):
		# doc_events fire during ERPNext's own test bootstrap, before this app's
		# custom fields exist. The hook must read through getattr and no-op.
		class Bare:
			address_line1 = "1907 Indiana Ave"

		address.validate_coordinates(Bare())

	def test_none_coordinates_are_treated_as_absent(self):
		doc = StubAddress(custom_latitude=None, custom_longitude=None)
		address.validate_coordinates(doc)
		self.assertEqual(doc.custom_location_source, "")


if __name__ == "__main__":
	unittest.main()
