"""Migrated Address Server Script, wired via ``hooks.py`` doc_events["Address"].

Hook wiring (see ``hooks.py``):
  * ``before_save`` -> :func:`set_full_address`
  * ``before_save`` -> :func:`validate_coordinates`

Originally a Frappe "Server Script" stored only in the site DB; now versioned
with the app.
"""

import frappe
from frappe import _
from frappe.utils import flt

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


def validate_coordinates(doc, method=None):
	"""Reject a stored point that no map could use, before it reaches the DB.

	``custom_latitude``/``custom_longitude`` became user-editable in v1.207.0 so
	a site with no findable street address -- new construction, a lot number --
	can still be located. That put a keyboard on two fields every map in the app
	trusts *over* the address text, so they need a gate on the way in.

	Two failures are worth throwing for, because both save cleanly today and go
	wrong silently later:

	* **Half a pair.** Every consumer treats a zero axis as "no point at all"
	  (``api.pickup_routing._address_coords``, ``api.travel._poi_address_location``,
	  ``stopLatLng`` in the pick-routing map). So a filled Latitude with an empty
	  Longitude looks saved and located, and is neither.
	* **Out of range.** Usually a transposed pair -- a Utah longitude in the
	  latitude box is a valid-looking number that puts the site in the wrong
	  hemisphere.

	Note 0.0 is the "absent" sentinel throughout, so the equator and the prime
	meridian are not representable. Deliberate: the columns are ``NOT NULL
	DEFAULT 0`` and making them nullable would mean changing every guard in the
	app to gain coordinates nobody here will ever need.
	"""
	# doc_events fire during ERPNext's own test bootstrap, before this app's
	# custom fields exist -- read through getattr, never doc.custom_latitude.
	lat = flt(getattr(doc, "custom_latitude", 0) or 0)
	lng = flt(getattr(doc, "custom_longitude", 0) or 0)

	if not lat and not lng:
		return

	if not lat or not lng:
		frappe.throw(
			_("Enter both Latitude and Longitude, or neither. A single coordinate cannot locate anything, and every map would ignore it."),
			title=_("Incomplete Coordinates"),
		)

	if not -90.0 <= lat <= 90.0:
		frappe.throw(
			_("Latitude must be between -90 and 90 (got {0}). Are the two values the wrong way round?").format(lat),
			title=_("Invalid Latitude"),
		)

	if not -180.0 <= lng <= 180.0:
		frappe.throw(
			_("Longitude must be between -180 and 180 (got {0}). Are the two values the wrong way round?").format(lng),
			title=_("Invalid Longitude"),
		)

	# Precision 6 is ~0.1 m; anything beyond it is noise from a paste or an
	# export, and trimming keeps the stored value matching what the form shows.
	doc.custom_latitude = flt(lat, 6)
	doc.custom_longitude = flt(lng, 6)

	# Deliberately NOT stamping a provenance on a blank one.
	#
	# It is tempting: a point written by an import or the REST API arrives with
	# no source, blank reads as "Google" everywhere, and Google's points are
	# discarded when the address text is edited -- so such a point would quietly
	# vanish on the next edit. But this hook cannot tell that case from the far
	# commoner one, a pre-v1.207.0 row being re-saved for any unrelated reason
	# (ticking Is Primary Address, a party link, a patch). Every one of those
	# holds a Google-derived point and a blank source, so stamping here would
	# migrate the whole existing Address table to "Manual" -- the one value in
	# which clear-on-edit no longer fires -- silently, on a read-only field,
	# without anyone asking. Retarget such a record later and it keeps the old
	# building's coordinates while showing the new address, which is exactly the
	# failure the provenance split exists to prevent.
	#
	# So blank stays blank and is treated as Google: a point of unknown origin is
	# assumed derived from the text and dies with it. Wrong in the safe
	# direction. An importer that wants its points to survive sets
	# custom_location_source = "Manual" itself.
