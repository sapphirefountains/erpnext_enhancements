"""Server module for the QuickBooks Online Dashboard page.

The dashboard is a client-rendered Frappe desk page (see the sibling .js). This module
is deliberately empty: Frappe only calls a Page's ``get_context`` when the page ships an
``.html`` template, and this one does not. All live data is loaded from the browser via
the ``api.get_dashboard_status`` RPC.
"""
