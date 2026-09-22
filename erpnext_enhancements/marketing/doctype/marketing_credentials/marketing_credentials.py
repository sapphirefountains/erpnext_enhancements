# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Marketing Credentials — the OAuth apps and tokens for the ad connectors.

A separate Single from Marketing Settings on purpose: Settings is readable by Sales
Manager, and this holds client secrets, a Google Ads developer token and live access and
refresh tokens. System Manager only.

Tokens are written by ``core/oauth.py`` through the Connect flow and never typed in, which
is why their fields are hidden. Nothing here validates: every field is optional until the
platform it belongs to is connected, and a Single whose ``validate`` refuses a blank is the
page that cannot be saved on the day somebody first opens it.
"""

from frappe.model.document import Document


class MarketingCredentials(Document):
	pass
