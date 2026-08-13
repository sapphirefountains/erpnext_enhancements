# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Marketing Raw Payload — the append-only verbatim response archive.\n\nExists so a mapper can be debugged against what a platform actually sent rather than what we\nbelieve it sends; the QuickBooks equivalent has repeatedly earned its keep. Pruned on the\nretention window in Marketing Settings, because it grows fast and is a debugging aid rather\nthan a data store."""

from frappe.model.document import Document


class MarketingRawPayload(Document):
	pass
