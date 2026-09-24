# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Refuse a new Item for two naming defects, and only those two.

Wired as the ``Item`` → ``validate`` doc_event in ``hooks.py``. Nik decided the scope on
2026-09-24 (TASK-2026-02238; POL-0602 v1.0, effective 2026-10-01):

* **Refuse** saving a *new* Item when its code matches an existing Item's code once case
  and punctuation are ignored, or when its name is just its code.
* **Advise** on everything else, exactly as before — the Item form's headline, the Item
  Naming Audit report, the KPI and the MCP tool are unchanged.

Why only two, when the rules module has a whole STOP severity: the STOP set includes
``name_category_unapproved``, and several category words are still waiting on a ruling
(TASK-2026-02215: PLMB, BRUSH, BOTTLE). Refusing every STOP would refuse legitimate new
items until each ruling lands. The two chosen findings need no ruling to be right.
:data:`item_naming_rules.BLOCKING_CODES` is the list and :func:`item_naming_rules.blocking_findings`
decides it; nothing here makes a naming judgement.

--------------------------------------------------------------------------------------
Only a person at a screen is refused
--------------------------------------------------------------------------------------

The guard runs only inside a **web request**, and never during an import, migrate, install,
patch, test run or setup wizard. The reason is who can act on the message. A person saving
an Item in the Desk, through the REST API or through an MCP tool can read "rename it" and
rename it. A background job cannot: the QuickBooks sync creates Items from QuickBooks
records on the scheduler, and a refusal there parks the record for manual review with
nobody told why. The flags cover the bulk paths that run inside a request (Data Import sets
``in_import``); the request check covers every job.

A caller that creates Items **inside** a request, from a name it generated from the code,
sets ``doc.flags.ignore_naming_guard`` — the configured-product Item in
``product_configurator.erp_integration`` and the QuickBooks upsert (whose per-entity Sync
button runs inline) do. A caller where a person approves a new Item keeps the guard:
``accounting_intake.review`` is the one, and says so where it inserts.

Only ``is_new()``. An existing Item is never refused by this module, whatever its name —
the catalogue has hundreds of records that predate the SOP, and refusing an edit to one of
them would stop somebody fixing its stock UOM because of a name they did not write.
"""

import html

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

#: ``frappe.flags`` that mean the save is part of a bulk or system operation rather than a
#: person creating one Item. Data Import runs in a request and sets ``in_import``.
SKIP_FLAGS = ("in_import", "in_migrate", "in_install", "in_patch", "in_test", "in_setup_wizard")

#: Set on an Item's ``flags`` by an in-request caller that generates the name from the code.
IGNORE_FLAG = "ignore_naming_guard"


def validate_new_item(doc, method=None):
	"""``Item`` → ``validate``. Throws for a new Item with a blocking naming finding.

	Runs after ERPNext's own ``Item.validate`` (doc_events always follow the controller
	method), so a blank ``item_name`` has already been filled from ``item_code`` by then —
	which is why a blank name is refused here as "just the code". The existing codes are
	read once, with ``get_all`` so that a collision counts whoever can see the other record.
	"""
	if not should_check(doc):
		return
	code = (doc.get("item_code") or doc.get("name") or "").strip()
	if not code:
		return
	existing = frappe.get_all("Item", pluck="name")
	findings = rules.blocking_findings(code, doc.get("item_name"), existing)
	if findings:
		frappe.throw(refusal_message(code, findings), title=_("Item naming"))


def should_check(doc):
	"""True when this save is a person creating a new Item. See the module docstring."""
	if not doc.is_new():
		return False
	if getattr(getattr(doc, "flags", None), IGNORE_FLAG, None):
		return False
	flags = frappe.flags
	if any(getattr(flags, name, None) for name in SKIP_FLAGS):
		return False
	return bool(getattr(frappe.local, "request", None))


def refusal_message(code, findings):
	"""Plain-English HTML for the refusal. Every value interpolated is escaped.

	One paragraph per finding, then one line saying that nothing else stops the save —
	otherwise a person who has just been refused reasonably assumes the whole naming
	checklist is now enforced, and starts fixing findings that do not matter to the save.
	"""
	safe_code = html.escape(code)
	parts = []
	for finding in findings or ():
		if finding.get("code") == rules.DUPLICATE_CODE_NORMALISED:
			matches = ", ".join(f"<b>{html.escape(str(m))}</b>" for m in finding.get("matches") or ())
			parts.append(
				_(
					"Item Code <b>{0}</b> is the same as {1} once capitals, spaces and punctuation "
					"are ignored. If it is the same part, use that Item instead of creating a second "
					"one. If it really is a different part, give it a code that tells the two apart."
				).format(safe_code, matches)
			)
		elif finding.get("code") == rules.NAME_EQUALS_CODE:
			parts.append(
				_(
					"The Item Name is just the Item Code (<b>{0}</b>), so nobody searching by "
					"description will find this Item. Give it a descriptive name in the Item Naming "
					"Schema's order: CATEGORY, SUB-CATEGORY, KEY FEATURE, MATERIAL, SIZE, RATING, "
					"PACKAGING, for example <i>ELBOW, 90, SOC, PVC, 2&quot; SCH80</i>. A blank Item "
					"Name counts too, because ERPNext copies the Item Code into it."
				).format(safe_code)
			)
	parts.append(
		_(
			"Only these two naming problems stop a new Item being saved (POL-0602). "
			"<b>Naming → Check naming</b> on the Item form lists everything else, as advice."
		)
	)
	return "<br><br>".join(parts)
