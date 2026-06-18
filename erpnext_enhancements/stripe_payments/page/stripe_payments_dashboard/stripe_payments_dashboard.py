"""Server context for the Stripe Payments dashboard page.

A client-rendered Frappe desk page (see the sibling .js); this controller only
supplies page metadata. Live data loads from the browser via the
``stripe_payments.core.api.get_dashboard_status`` RPC.
"""

import frappe


def get_context(context):
	"""Provide page title and disable caching so status data is always fresh."""
	context.no_cache = 1
	context.title = "Stripe Payments"
	return context
