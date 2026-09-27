# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TripDocument(Document):
	"""One file on a trip: a boarding pass, a hotel confirmation, a rental agreement, a bill of
	lading, or a file for the whole trip (site map, safety plan, insurance certificate, job
	packet). ``booking_group`` ties it to a booking (the same id as that booking's rows; blank
	= the whole trip) and ``traveler`` to one person (blank = everyone on the booking, or the
	whole crew).

	Not a receipt: a receipt is money, and stays on its cost row's Receipt field, which the
	trip views never show. Plan a Trip writes these rows (``planner.merge_documents``), and
	anyone who can open the trip sees them on the views and ``/itinerary``. All validation
	lives in the planner."""

	pass
