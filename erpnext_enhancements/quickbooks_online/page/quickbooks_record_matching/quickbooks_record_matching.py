"""Server context for the QuickBooks Record Matching page.

A client-rendered desk page (see the sibling .js); this controller only supplies page
metadata. Every row comes from ``core.api.get_match_queue`` /
``core.api.get_parked_transactions``, and every decision goes through
``core.api.decide_match`` / ``decide_matches`` / ``confirm_match``, all gated on the
QBO operator roles.
"""

import frappe


def get_context(context):
	"""Provide the page title and disable caching so the queue is always current."""
	context.no_cache = 1
	context.title = "QuickBooks Record Matching"
	return context
