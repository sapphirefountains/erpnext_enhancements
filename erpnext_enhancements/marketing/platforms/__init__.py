"""One module per ad platform, each exposing the same four reads.

``discover_accounts(transport)``, ``fetch_campaigns(transport, account_id)`` and
``fetch_daily_metrics(transport, account_id, date_from, date_to)`` everywhere;
``fetch_clicks(transport, account_id, day)`` on Google only (decision D). The sync engine
(``core/sync.py``) is written against that contract and nothing platform-specific.
"""

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.platforms import google_ads, linkedin_ads, meta_ads

MODULES = {
	C.PLATFORM_GOOGLE: google_ads,
	C.PLATFORM_META: meta_ads,
	C.PLATFORM_LINKEDIN: linkedin_ads,
}


def module_for(platform):
	return MODULES[platform]
