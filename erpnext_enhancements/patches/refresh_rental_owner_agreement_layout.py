"""Give the live Rental Agreement and Owner Contract the maintenance agreement's treatment.

Nik, 2026-10-13, after the Maintenance Services Agreement was reworked: "do the rental and
owner agreements the same way." So both templates now carry:

* **the Word copy's layout** (as v1.611.1 gave the maintenance agreement): every numbered
  clause hangs its number, titled clauses lead in bold, the owner §2.1 design items are a
  bulleted list and its §4.2 plan options are indented. The owner contract's ``<h2>``
  sub-headings (2.1 Design Scope …) became titled clauses, so the numbering is one series;
* **an input for everything they print** (as v1.611.0): the typed blanks (rental §3.2's
  balance-due days, the owner's revision rounds and rate, construction scope, "Other"
  property type and the §4.2 maintenance plan) now print Project Contract fields;
* **client initials** beside the clauses that carry the client's risk and money;
* and the owner contract's SIGNATURES block is the ``sig()`` one, so it can be e-signed
  (``esign.api`` refuses a template without ``sig(``).

``seed_contract_templates`` is insert-only, so the new files never reach a site that already
has the records. Same discipline as ``refresh_maintenance_agreement_layout``, per template:

* the body is replaced only when it is exactly the one this repo shipped (SHA-256 of the
  v1.611.0 file, which prod's bodies matched byte for byte on 2026-10-13); a body edited on
  the site is logged and left alone;
* contracts signed on paper are frozen first, so their reprint keeps the wording they were
  signed with;
* nothing changes while a signing link for that template is out (prod had one rental draft
  and no owner contracts on 2026-10-13).

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
	"rental": "rental_agreement.html",
	"owner": "owner_contract.html",
}

#: Shipped bodies this patch may replace, per template key: the v1.611.0 files (summary added).
REPLACEABLE_BODY_SHA256 = {
	"rental": {"7274e6bd87eca74f6df9b29cde2a348562261029bb5fe80637510eb5ce354149"},
	"owner": {"43490ef31d7fa831c7af7368c92293b472cb4edc4c43be710fe2de773bdd7079"},
}

TITLES = {"rental": "Rental Agreement", "owner": "Owner Contract"}


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
				f"Contract Template '{template_key}' has been edited on this site, so the Word-copy layout, "
				"the field-bound blanks and the client initials were not applied. Merge "
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
				"erpnext_enhancements.patches.refresh_rental_owner_agreement_layout.execute again once they "
				"are signed or voided."
			),
		)
		return
	frappe.db.set_value("Contract Template", template_key, "body", new_body, update_modified=False)
	frappe.db.commit()
