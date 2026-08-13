# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Ad Campaign — one row per campaign on a connected account.\n\nNamed from (ad_account, external_id) so a re-pull upserts. platform_status deliberately\nstores the platform's own vocabulary verbatim rather than a mapped Select: a constrained\nlist would reject a status we have never seen and fail an ingest over a label."""

from frappe.model.document import Document


class AdCampaign(Document):
	pass
