# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Ad Account — one row per connected advertising account.\n\nIdentity is (platform, external_id) and both are set_only_once, because the record name\nis derived from them: letting either change would orphan every campaign and metric already\nfiled under the old name. Connection state is written by the connectors in\nmarketing.platforms; nothing here decides anything."""

from frappe.model.document import Document


class AdAccount(Document):
	pass
