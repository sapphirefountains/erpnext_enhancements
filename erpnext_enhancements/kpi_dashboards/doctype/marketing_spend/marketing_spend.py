"""Marketing Spend — the monthly per-channel ad/marketing spend paste.

The one piece of genuine manual entry for Marketing KPIs: a marketing lead pastes
each channel's monthly total here (interim until Google Ads / Meta connectors
exist). The Marketing snapshot joins it with lead volume to compute Cost Per Lead.
One row per (channel, month), enforced by the ``SPEND-{month}-{channel}`` autoname.

**As of v1.243.0 rows can also arrive in bulk** via
``kpi_dashboards/marketing_spend_import.py``, which upserts rather than inserts
(the autoname makes a month/channel pair unique, so re-importing a corrected
export would otherwise collide on every row).

``validate`` runs the same normalisation the importer does — canonical channel
name, month snapped to the 1st. Hand-entered and imported rows have to agree, or
the rollup fragments the first time somebody types "google ads" into the form
instead of picking the spelling already in use.

``value_stream`` is optional and deliberately not apportioned: a channel that
serves several streams is left blank, and the Value Stream Performance dashboard
reports that spend as unallocated rather than dividing it arbitrarily.
"""

from frappe.model.document import Document

from erpnext_enhancements.kpi_dashboards.marketing_spend_import import validate_spend


class MarketingSpend(Document):
	def validate(self):
		validate_spend(self)
