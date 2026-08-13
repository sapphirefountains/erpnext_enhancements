# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Marketing Sync Log — one row per connector run.\n\nPlain record. Sales Manager can read it, so nothing written here may contain a token, a\nsecret or an Authorization header."""

from frappe.model.document import Document


class MarketingSyncLog(Document):
	pass
