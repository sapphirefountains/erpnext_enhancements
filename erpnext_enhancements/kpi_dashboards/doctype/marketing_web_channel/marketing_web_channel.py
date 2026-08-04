# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Marketing Web Channel — one GA4 acquisition channel on a nightly snapshot.

Child table of `Marketing Web Snapshot`. The snapshot used to store only site
totals (sessions, users, organic clicks/impressions), which answers "is traffic
up" but not "up from where" — and the second question is the one that decides
where money goes.

The GA4 pull already fetched this breakdown: `api.analytics.get_ga4_data` has
requested the `sessionDefaultChannelGroup` dimension all along and returned it as
`acquisition_channels`. Nothing stored it, so it was discarded every night. These
rows are that data, kept.

GA4's own channel grouping is used verbatim rather than mapped onto a local
taxonomy, so the numbers reconcile against the GA4 UI when somebody checks them —
which is the first thing anybody does with a marketing number they dislike.
"""

from frappe.model.document import Document


class MarketingWebChannel(Document):
	pass
