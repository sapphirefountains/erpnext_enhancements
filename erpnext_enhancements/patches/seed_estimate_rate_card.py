"""Seed one empty, default "Standard" Estimate Rate Card (v1.836.0, estimating milestone 1).

Every rate is left at zero on purpose: this repository is public, and the company's markups and
overheads are Finance's to type in the Desk (WI-077: Finance owns the rates). An estimate made from an
all-zero card prices at cost and says so in a banner, so nobody mistakes it for a real price.

Insert-only: does nothing when any rate card exists, so a card somebody has filled in is never touched
and a second migrate is a no-op.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Estimate Rate Card"):
		return
	if frappe.db.count("Estimate Rate Card"):
		return
	card = frappe.new_doc("Estimate Rate Card")
	card.rate_card_name = "Standard"
	card.is_default = 1
	card.notes = "Seeded empty. Type the company's markups, overheads, use tax and labor cost per hour here."
	card.insert(ignore_permissions=True)
