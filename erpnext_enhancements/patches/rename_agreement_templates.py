"""Design Agreement, Events Agreement; hide the Employee-Contractor Agreement and the NDA (v1.783.0).

Nikolas, 2026-11-03: "Change Architect Agreement to Design Agreement. Rental Agreement change to
Events Agreement. For now hide Employee Contractor Agreement and Mutual Non-disclosure
agreements."

``seed_contract_templates`` is insert-only, so the repo's new titles never reach a site that
already has the records. This patch, on each existing Contract Template by ``template_key``:

* **architect** → title ``Design Agreement``; **rental** → title ``Events Agreement``. The
  live body's title block and the two sentences that name the agreement get the same words
  (exact-string replacements, each applied only where the old wording is still there), so the
  printed heading agrees with the picker. The ``template_key``, the record name and the
  naming series (``SF-RA-``) are unchanged: code keys on them, not on the title.
* **employee_contractor** and **nda** → ``enabled = 0``. ``get_contract_types`` and the
  Builder's start screen list enabled templates only, and ``create_contract`` refuses a disabled
  one; the contracts already issued on them are untouched and keep working.

Idempotent (a second run finds nothing to change) and it **cannot raise**: a patch that raises
aborts ``bench migrate``, which on this repo is the deploy. Each template is its own savepoint;
one that fails is logged by key and the others still change.
"""

import frappe

#: template_key -> (new title, [(old wording, new wording), ...] applied to the live body)
RENAMES = {
	"architect": (
		"Design Agreement",
		[
			("<h3><b>ARCHITECT AGREEMENT</b></h3>", "<h3><b>DESIGN AGREEMENT</b></h3>"),
			("This Architect Agreement (the ", "This Design Agreement (the "),
			("the terms of this Architect Agreement,", "the terms of this Design Agreement,"),
		],
	),
	"rental": (
		"Events Agreement",
		[
			("<h3><b>RENTAL AGREEMENT</b></h3>", "<h3><b>EVENTS AGREEMENT</b></h3>"),
			(
				"This Rental Agreement (&quot;Agreement&quot;)",
				"This Events Agreement (&quot;Agreement&quot;)",
			),
			("the terms of this Rental Agreement.", "the terms of this Events Agreement."),
		],
	),
}

#: Hidden for now: not offered by any picker, existing contracts untouched.
DISABLE = ("employee_contractor", "nda")


def rename_body(body, replacements):
	"""The body with each old wording swapped for the new where it is still present."""
	for old, new in replacements:
		if old in (body or ""):
			body = body.replace(old, new)
	return body


def execute():
	for key, (title, replacements) in RENAMES.items():
		_apply(key, lambda doc: _rename(doc, title, replacements))
	for key in DISABLE:
		_apply(key, _disable)


def _rename(doc, title, replacements):
	changed = {}
	if doc.title != title:
		changed["title"] = title
	body = rename_body(doc.body, replacements)
	if body != doc.body:
		changed["body"] = body
	return changed


def _disable(doc):
	return {"enabled": 0} if doc.enabled else {}


def _apply(key, decide):
	savepoint = f"rename_agreement_templates_{key}"
	frappe.db.savepoint(savepoint)
	try:
		if not frappe.db.exists("Contract Template", key):
			return
		doc = frappe.get_doc("Contract Template", key)
		changed = decide(doc)
		if not changed:
			return
		# Field writes, as refresh_rental_owner_agreement_layout does: no controller run for a
		# change of wording that touches no tag and no binding.
		for fieldname, value in changed.items():
			frappe.db.set_value("Contract Template", key, fieldname, value, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(
			title=f"rename_agreement_templates: {key} not updated",
			message=frappe.get_traceback(),
		)
