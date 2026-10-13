"""Server module for the Stripe Payments dashboard page.

A client-rendered Frappe desk page (see the sibling .js). This module is deliberately
empty: Frappe only calls a Page's ``get_context`` when the page ships an ``.html``
template, and this one does not. Live data loads from the browser via the
``stripe_payments.core.api.get_dashboard_status`` RPC.
"""
