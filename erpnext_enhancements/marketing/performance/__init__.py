"""The Marketing Performance page and the daily data behind it (v1.756.0).

Nik, 2026-10-29: one page in place of the GA4 Dashboard and the Marketing Dashboard widgets,
with Google Analytics 4 and Search Console stored by day instead of asked for on every load,
and inbound calls, Google Business Profile, ad spend and social page stats beside them. The
module map is in ``marketing/README.md``; the rules the page draws from are in ``rules.py``,
which is pure and tested bench-free.
"""
