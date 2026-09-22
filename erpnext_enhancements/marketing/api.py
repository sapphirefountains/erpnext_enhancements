# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Stable short paths for URLs registered outside this repo.

``erpnext_enhancements.marketing.api.oauth_callback`` is the OAuth redirect URI entered in
the Google Cloud, Meta and LinkedIn app consoles. Those registrations are made by hand and
nobody will remember to update them, so the path stays here whatever happens to
``core/api.py``. The re-exported objects are the whitelisted functions themselves, so
Frappe's whitelist and method checks apply unchanged.
"""

from erpnext_enhancements.marketing.core.api import (
	disconnect,
	oauth_callback,
	start_oauth,
	sync_now,
	test_connection,
)

__all__ = ["disconnect", "oauth_callback", "start_oauth", "sync_now", "test_connection"]
