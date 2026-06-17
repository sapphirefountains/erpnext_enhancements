# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Single doctype holding configuration for the Accounting Document Intake
pipeline: the Triton extraction-service connection, intake-channel toggles, and
Google Drive filing targets (including the configurable Shared Drive + parent
folder for per-supplier folders). Secrets use Password fields; the Triton
gateway falls back to ``Triton Settings`` when left blank."""

from frappe.model.document import Document


class AccountingIntakeSettings(Document):
	pass
