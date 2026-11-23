# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Business Profile Review. A plain model: every row is written by the nightly pull
(``marketing/gbp/sync.py``), and no rule lives here."""

from frappe.model.document import Document


class BusinessProfileReview(Document):
	pass
