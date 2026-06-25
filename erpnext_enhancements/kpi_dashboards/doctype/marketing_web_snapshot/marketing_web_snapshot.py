"""Marketing Web Snapshot — the daily GA4 / Search Console pull, cached.

So the Marketing KPI snapshot can surface web traffic without calling Google in
the snapshot render/aggregation path: a once-a-day job (``snapshots.snapshot_marketing_web``,
invoked at the head of the nightly batch) pulls the 30-day totals and stores
them here; the Marketing aggregator reads the latest OK row. One row per day
(``MWS-{snapshot_date}`` autoname).
"""

from frappe.model.document import Document


class MarketingWebSnapshot(Document):
	pass
