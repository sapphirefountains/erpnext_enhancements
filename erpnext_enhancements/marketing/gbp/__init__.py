"""Google Business Profile: a read-only connector for the listing's daily figures and reviews (v1.756.0).

Ships **off and unconnected**: ``Marketing Settings.google_business_profile_enabled`` is 0, and
Google answers every call with a quota of 0 until it approves the project's API access
(``docs/marketing-connectors-runbook.md`` has the application). Same conventions as ``core/``:
OAuth through ``core/oauth.py``, every request through ``core/client.Transport``, GET only.
"""
