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
Not before the policy takes effect
--------------------------------------------------------------------------------------

The refusal starts on :data:`item_naming_rules.NAMING_GO_LIVE`, POL-0602's effective date,
and not on the day this deploys. The policy is what a person who is refused gets pointed
at, and the naming conventions themselves were still being finalized the week the guard
shipped. The new-items KPI counts from the same date, so the days in which a new Item can
still be saved with its code for a name are exactly the days the KPI does not measure.
Compared against ``nowdate()``, which is site-local, like the ``Item.creation`` stamps the
KPI reads.

--------------------------------------------------------------------------------------
Only a person at a screen is refused
--------------------------------------------------------------------------------------

The guard runs only inside a **web request**, and never during an import, migrate, install,
patch, test run or setup wizard. The reason is who can act on the message. A person saving
an Item in the Desk, through the REST API or through an MCP tool can read "rename it" and
rename it. A background job cannot: the QuickBooks sync creates Items from QuickBooks
records on the scheduler, and a refusal there parks the record for manual review with
nobody told why. Data Import normally runs as a background job, so the request check
already skips it; the ``in_import`` flag covers the times it runs inline (developer mode,
tests). The request check covers every other job.

A caller that creates Items **inside** a request, where the person at the screen cannot
choose the code or the name, sets ``doc.flags.ignore_naming_guard`` — the configured-product
Item in ``product_configurator.erp_integration`` and the QuickBooks upsert (whose per-entity
Sync button runs inline) do. Document Intake's Approve Items keeps the guard, because the
Stock Manager enters the code there; ``accounting_intake.review`` checks the two findings
itself first, so a refusal names the intake line rather than an Item form nobody opened.

**Variants are skipped.** ERPNext derives a variant's code and name from its template
(``erpnext.controllers.item_variant``): a manufacturer variant copies no name at all, so
its name becomes its code, and an attribute variant's name is the template's name plus an
abbreviation. The person pressing *Make Variants* chose neither, and the template they
came from is what the naming rules govern.

Only ``is_new()``. An existing Item is never refused by this module, whatever its name —
the catalogue has hundreds of records that predate the SOP, and refusing an edit to one of
them would stop somebody fixing its stock UOM because of a name they did not write.
"""

import html

import frappe
from frappe import _
from frappe.utils import nowdate

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

#: ``frappe.flags`` that mean the save is part of a bulk or system operation rather than a
#: person creating one Item. Data Import sets ``in_import``; it matters on the rare inline run.
SKIP_FLAGS = ("in_import", "in_migrate", "in_install", "in_patch", "in_test", "in_setup_wizard")

#: Set on an Item's ``flags`` by an in-request caller whose user cannot choose the code or name.
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
	if doc.get("variant_of"):
		return False
	if not in_force():
		return False
	flags = frappe.flags
	if any(getattr(flags, name, None) for name in SKIP_FLAGS):
		return False
	return bool(getattr(frappe.local, "request", None))


def in_force(today=None):
	"""True on and after :data:`item_naming_rules.NAMING_GO_LIVE`. ISO dates compare as text."""
	return str(today or nowdate()) >= rules.NAMING_GO_LIVE


def refusal_message(code, findings):
	"""Plain-English HTML for the refusal. Every value interpolated is escaped.

	One paragraph per finding, then one line saying that nothing else stops the save —
	otherwise a person who has just been refused reasonably assumes the whole naming
	checklist is now enforced, and starts fixing findings that do not matter to the save.
	"""
	parts = [p for p in (finding_message(code, finding) for finding in findings or ()) if p]
	parts.append(
		_(
			"Only two naming problems stop a new Item being saved (POL-0602). Once it is saved, "
			"<b>Naming → Check naming</b> on the Item form lists the rest, as advice."
		)
	)
	return "<br><br>".join(parts)


def finding_message(code, finding):
	"""One refusal paragraph, or "" for a finding this module does not refuse.

	Shared with ``accounting_intake.review``, which reports the same two findings against an
	intake line before it tries the insert.
	"""
	safe_code = html.escape(code)
	if finding.get("code") == rules.DUPLICATE_CODE_NORMALISED:
		matches = ", ".join(f"<b>{html.escape(str(m))}</b>" for m in finding.get("matches") or ())
		return _(
			"Item Code <b>{0}</b> is the same as {1} once capitals, spaces and punctuation "
			"are ignored. If it is the same part, use that Item instead of creating a second "
			"one. If it really is a different part, give it a code that tells the two apart."
		).format(safe_code, matches)
	if finding.get("code") == rules.NAME_EQUALS_CODE:
		return _(
			"The Item Name is just the Item Code (<b>{0}</b>) once capitals, spaces and "
			"punctuation are ignored, so nobody searching by description will find this Item. "
			"Give it a descriptive name in the Item Naming Schema's order: CATEGORY, "
			"SUB-CATEGORY, KEY FEATURE, MATERIAL, SIZE, RATING, PACKAGING, for example "
			"<i>ELBOW, 90, SOC, PVC, 2&quot; SCH80</i>. A blank Item Name counts too, because "
			"ERPNext copies the Item Code into it. If the name is already a proper description, "
			"the code is what needs changing: use the vendor's part number, or the right CON-, "
			"PDT- or SRV- code, rather than a code spelled from the name."
		).format(safe_code)
	return ""
