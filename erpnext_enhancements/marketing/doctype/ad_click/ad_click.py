# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Ad Click — one Google Ads click, named by its gclid.

Decision D on TASK-2026-01570: when a Lead carries a ``custom_gclid`` but no ``utm_id``,
this row says which campaign the click belonged to. Google keeps ``click_view`` for 90 days
only, so ``core/sync.py`` stores every click it sees and ``prune`` keeps a row past
``CLICK_RETENTION_DAYS`` only while a Lead carries its gclid.

Written only by the sync (``sync.upsert_click``); nothing here needs behaviour.
"""

from frappe.model.document import Document


class AdClick(Document):
	pass
