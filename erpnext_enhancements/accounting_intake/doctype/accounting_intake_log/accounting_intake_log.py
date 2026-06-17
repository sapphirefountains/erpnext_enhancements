# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Audit log for every Accounting Document Intake action (ingest, dedup,
extract, propose, item review, approve, post, file). Rows are written via
``accounting_intake.audit.log_intake``; Failed rows carry a JSON ``payload``
(method + kwargs) that the nightly retry job re-enqueues — the same contract as
``Drive Sync Log``."""

from frappe.model.document import Document


class AccountingIntakeLog(Document):
	pass
