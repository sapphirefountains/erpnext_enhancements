"""Marketing Spend — the monthly per-channel ad/marketing spend paste.

The one piece of genuine manual entry for Marketing KPIs: a marketing lead pastes
each channel's monthly total here (interim until Google Ads / Meta connectors
exist). The Marketing snapshot joins it with lead volume to compute Cost Per Lead.
One row per (channel, month), enforced by the ``SPEND-{month}-{channel}`` autoname.
"""

from frappe.model.document import Document


class MarketingSpend(Document):
	pass
