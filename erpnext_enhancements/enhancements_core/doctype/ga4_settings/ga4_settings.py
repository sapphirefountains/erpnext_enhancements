# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the GA4 Settings Single doctype.

Credentials/config for the Google Analytics 4 + Search Console integration
(``issingle``): ``ga4_property_id``, ``gsc_property_url`` and an attached service
account ``credentials_json``. Consumed by ``api.analytics`` (get_ga4_data /
get_gsc_data), which powers the GA4 Dashboard desk page.

No custom controller logic.
"""

# import frappe
from frappe.model.document import Document

class GA4Settings(Document):
	pass
