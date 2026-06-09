"""Server context for the QuickBooks Online Dashboard page.

The dashboard is a client-rendered Frappe desk page (see the sibling .js); this
controller only supplies page metadata. All live data is loaded from the
browser via the ``api.get_dashboard_status`` RPC.
"""

import frappe


def get_context(context):
	"""Provide page title and disable caching so status data is always fresh."""
	context.no_cache = 1
	context.title = "QuickBooks Online Dashboard"
	return context

