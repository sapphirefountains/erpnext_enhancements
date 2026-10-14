"""One-time migration patch (post_model_sync; listed in patches.txt).

Deletes again the 14 Custom Fields an older build's migrate brought back on 2026-07-18 (see
``setup/downgrade_guard.py``, which stops the cause):

- the 12 blank duplicate "Comments" Tab Breaks ``remove_duplicate_comments_tabs`` removed in
  v1.159.2 (Address, Batch, Delivery Note, Lead, Project, Quotation, Serial No, Stock Entry and
  Task's ``custom_comments_tab``; Employee, Purchase Order and Supplier Quotation's
  ``custom_comments``). Each sits empty after the real Comments tab and renders as a second,
  blank "Comments" tab;
- Opportunity's ``custom_lost_reason`` and ``custom_lost_competitor``, which
  ``remove_opportunity_lost_reason`` removed in v1.159.0 in favor of ERPNext's own
  ``lost_reasons`` and ``competitors``. The Desk showed both again, beside the native Lost section.

Those two patches are in ``tabPatch Log`` and will never run again, so this one runs their
``execute()`` once more. Both are idempotent and match nothing once clean.

Data safety, checked on prod 2026-10-14 (read-only): the Tab Breaks carry no data. Four
Opportunities had a ``custom_lost_reason`` (CRM-OPP-2026-00068, -00104, -00124, -00137), all
"Other" and all Lost, and each already has a native ``lost_reasons`` row; ``custom_lost_competitor``
was empty everywhere. Nothing in the current fixtures is anchored on (``insert_after``), sets a
property on, or orders any of the 14. So nothing is migrated and nothing is lost.

Never raises: a failure is written to the Error Log and the migrate goes on, because a raising
patch aborts ``bench migrate``, which on this repo is the deploy.
"""

import importlib

import frappe

PATCHES = (
	"erpnext_enhancements.patches.remove_duplicate_comments_tabs",
	"erpnext_enhancements.patches.remove_opportunity_lost_reason",
)


def execute():
	for path in PATCHES:
		try:
			importlib.import_module(path).execute()
		except Exception:
			frappe.log_error(
				title="Retired customization not removed",
				message=frappe.get_traceback(),
				reference_doctype="Custom Field",
			)
