"""Give the live NDA, Architect and Employee-Contractor agreements the maintenance agreement's theme.

Nik, 2026-10-13: "do the contract builder and building for all contract types, not just the
ones we have so far. Style wise they should follow the same theme as the current Maintenance
Services Agreement." These three were converted from Word (DOC-0033, DOC-0101, DOC-0137) with
their numbering lost, so each now carries:

* **the title block, plain-language summary and information table** the maintenance, rental
  and owner agreements open with;
* **the Word copy's numbering restored** as numbered sections with hanging clause numbers
  (``ct-clause``). The NDA's eight paragraphs and the architect agreement's ten sections are
  numbered as their own cross-references ("this Paragraph 6", "Sections 5 ... and 10") say they
  were; the employee-contractor agreement, which had none, is numbered in order;
* **an input for everything it prints**: the NDA's blank entity type, the architect
  agreement's notice and Statement of Work blanks, and the client name and city/state/zip rows;
* **the other party's initials** beside the clauses that carry its risk, and the Electronic
  Signatures sentence saying each set is made separately;
* and a ``sig()`` SIGNATURES block, so all three can be e-signed (``esign.api`` refuses a
  template without ``sig(``).

``seed_contract_templates`` is insert-only, so the new files never reach a site that already
has the records. Same discipline as ``refresh_rental_owner_agreement_layout``, per template:

* the body is replaced only when it is exactly the one this repo shipped (SHA-256 of the
  file as it stood before this release, which prod's bodies matched byte for byte on
  2026-10-13); a body edited on the site is logged and left alone;
* contracts signed on paper are frozen first, so their reprint keeps the wording they were
  signed with;
* nothing changes while a signing link for that template is out (prod had no contracts of
  these three types on 2026-10-13).

No terms are reset: every new field is blank on existing contracts, so an issued contract
prints the same blank line it printed before. Never raises.
"""

import frappe

from erpnext_enhancements.patches.update_maintenance_agreement_terms import (
	body_sha256,
	freeze_contract_copies,
	live_requests,
)

TEMPLATE_FILES = {
	"nda": "nondisclosure_agreement.html",
	"architect": "architect_agreement.html",
	"employee_contractor": "employee_contractor_agreement.html",
}

#: Shipped bodies this patch may replace, per template key: the files as converted from Word.
REPLACEABLE_BODY_SHA256 = {
	"nda": {"7c45c45576a934382375beb489414bff3b953c6eb73a8cfa555db6c4dd202bcc"},
	"architect": {"e2959ccd34f077c1b2f503a77992988d3bdd4bfe259aaeb3f1bd838c31c44d7f"},
	"employee_contractor": {"3147a13d6963506bab5cff96d0d888c6ec634520d64e6710e149856d2c9f849a"},
}

TITLES = {
	"nda": "Mutual Non-Disclosure Agreement",
	"architect": "Architect Agreement",
	"employee_contractor": "Employee-Contractor Agreement",
}


def shipped_body(template_key):
	path = frappe.get_app_path("erpnext_enhancements", "templates", "contracts", TEMPLATE_FILES[template_key])
	with open(path, encoding="utf-8") as handle:
		return handle.read()


def decide(template_key, live_body, new_body):
	"""``"replace"``, ``"current"`` or ``"diverged"`` — what to do with one live body."""
	if live_body == new_body:
		return "current"
	if body_sha256(live_body) in REPLACEABLE_BODY_SHA256.get(template_key, set()):
		return "replace"
	return "diverged"


def execute():
	for template_key in TEMPLATE_FILES:
		try:
			_refresh(template_key)
		except Exception:
			# A patch that raises aborts `bench migrate`, which is the deploy.
			frappe.log_error(
				title=f"{TITLES[template_key]} layout patch failed", message=frappe.get_traceback()
			)


def _refresh(template_key):
	live = frappe.db.get_value("Contract Template", template_key, "body")
	if live is None:
		return  # no such template on this site; the seed inserts the repo copy
	new_body = shipped_body(template_key)
	verdict = decide(template_key, live, new_body)
	if verdict == "current":
		return
	title = TITLES[template_key]
	if verdict == "diverged":
		frappe.log_error(
			title=f"{title} layout: template not updated",
			message=(
				f"Contract Template '{template_key}' has been edited on this site, so the numbered layout, "
				"the field-bound blanks, the initials and the e-signature block were not applied. Merge "
				f"templates/contracts/{TEMPLATE_FILES[template_key]} into it by hand."
			),
		)
		return
	freeze_contract_copies(template_key)
	pending = live_requests(template_key)
	if pending:
		frappe.log_error(
			title=f"{title} layout: template not updated yet",
			message=(
				"These signing links are still out: " + ", ".join(pending) + ". Run "
				"erpnext_enhancements.patches.refresh_general_agreement_layout.execute again once they "
				"are signed or voided."
			),
		)
		return
	frappe.db.set_value("Contract Template", template_key, "body", new_body, update_modified=False)
	frappe.db.commit()
