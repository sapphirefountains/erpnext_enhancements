"""Migrated Address Server Script, wired via ``hooks.py`` doc_events["Address"].

Hook wiring (see ``hooks.py``):
  * ``before_save`` -> :func:`set_full_address`

Originally a Frappe "Server Script" stored only in the site DB; now versioned
with the app.
"""

# Address Server Script migrated to native doc_events.


def set_full_address(doc, method=None):
	"""Source Server Script: "Address - Set Full Address" (Address, Before Save).

	Build a single comma-joined address string into custom_full_address.
	"""
	address_parts = [
		doc.address_line1,
		doc.address_line2,
		doc.city,
		doc.state,
		doc.pincode,
	]
	doc.custom_full_address = ", ".join(part for part in address_parts if part)
