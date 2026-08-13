# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Ad Daily Metric — one row per campaign per day.

The uniqueness that makes restating safe is carried by the **record name**, not by a
unique index: ``autoname`` is ``format:ADM-{campaign}-{metric_date}``, so the same campaign
and date always resolve to the same row. Platforms revise spend and conversion figures for
days that have already closed, so every run re-pulls a trailing window; without a
deterministic name that window would deposit a duplicate set of rows each night and every
spend total downstream would drift upward.

**Which exception a collision raises depends on which key collides, and getting it wrong
makes dedupe fail open.** A clash on the primary key raises ``DuplicateEntryError``; a clash
on a *non-primary* unique index raises ``UniqueValidationError`` instead. This doctype
collides on the primary key, so ``DuplicateEntryError`` is the one to expect — but ingest
code should catch both, because catching only one of them and treating "no exception" as
"inserted cleanly" is how a de-duplicating writer quietly stops de-duplicating.

Prefer ``upsert()`` below to a bare ``insert()`` anywhere in the connectors.
"""

import frappe
from frappe.model.document import Document


class AdDailyMetric(Document):
	pass


def metric_name(campaign: str, metric_date: str) -> str:
	"""The deterministic record name for a (campaign, date) pair.

	Mirrors the ``autoname`` format. Kept here so ingest code never rebuilds that string by
	hand — a second copy of the format is a second thing to forget to change.
	"""
	return f"ADM-{campaign}-{metric_date}"


def upsert(campaign: str, metric_date: str, values: dict) -> str:
	"""Create or update the row for one campaign-day. Returns the record name.

	Restating is the normal case, not the exceptional one, so this is written as
	"update if present" rather than "insert and handle the failure" — the exception path is
	for genuine races only.
	"""
	name = metric_name(campaign, metric_date)

	if frappe.db.exists("Ad Daily Metric", name):
		doc = frappe.get_doc("Ad Daily Metric", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return doc.name

	doc = frappe.get_doc(
		{
			"doctype": "Ad Daily Metric",
			"campaign": campaign,
			"metric_date": metric_date,
			**values,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		# Two workers reached the same campaign-day. The row that won is as good as the one
		# that lost — same source, same day — so adopt it rather than failing the run.
		doc = frappe.get_doc("Ad Daily Metric", name)
		doc.update(values)
		doc.save(ignore_permissions=True)

	return doc.name
