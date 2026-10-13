"""Give the live Master Subcontractor Agreement and Statement of Work the maintenance agreement's treatment.

Nik, 2026-10-13: "do the contract builder and building for all contract types, not just the ones
we have so far. Style wise they should follow the same theme as the current Maintenance Services
Agreement." So both subcontractor templates now carry:

* **the title block, the plain-language summary and an information table** for the
  subcontractor, as the maintenance agreement opens;
* **the Word copy's layout**: every numbered clause hangs its number (``ct-clause``), titled
  clauses lead in bold, the MSA §5.2 coverages are a bulleted list, and the SOW's ``<h2>``
  sub-headings (2.1 Included Services, 5.1 Compensation Type …) are clauses in one number series;
* **an input for everything they print**: every ``[ ]`` box and typed blank in the Statement of
  Work (project and feature type, included services, exclusions, deliverable due dates, schedule
  days, liquidated damages, compensation type, retention, invoice day, Company-furnished items,
  safety requirements, correction days, the fallback hold points and special terms) prints a
  Project Contract field;
* **subcontractor initials** beside the clauses that carry the subcontractor's risk and money,
  and an Electronic Signatures clause saying each set is made separately;
* and a ``sig()`` SIGNATURES block, so either can be e-signed (``esign.api`` refuses a template
  without ``sig(``).

``seed_contract_templates`` is insert-only, so the new files never reach a site that already
has the records. This patch is also what finally brings the **v1.461.0 Statement of Work** to
production: that release's §9.4 (the contracted hold points from a locked Scope of Work, printed
in place of six checkboxes) changed only the repo file, and prod still had the original v1.5.0
body on 2026-10-13. Same discipline as ``refresh_rental_owner_agreement_layout``, per template:

* the body is replaced only when it is exactly one this repo shipped (SHA-256): the MSA's v1.5.0
  body, and the SOW's v1.5.0 or v1.461.0 body; a body edited on the site is logged and left
  alone;
* contracts signed on paper are frozen first, so their reprint keeps the wording they were
  signed with;
* nothing changes while a signing link for that template is out (prod had no MSA or SOW
  contracts at all on 2026-10-13).

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
	"msa": "master_subcontractor_agreement.html",
	"sow": "statement_of_work.html",
}

#: Shipped bodies this patch may replace, per template key.
REPLACEABLE_BODY_SHA256 = {
	# v1.5.0, unchanged since; prod's body matched it byte for byte on 2026-10-13.
	"msa": {"89e019714fa8f428351fd8bd8e7431737f99b3151757ce0ccc665d1c7dacd778"},
	"sow": {
		# v1.5.0, which prod still had on 2026-10-13.
		"326db01b342e34bca128572d0aa2d6760fccff8121a8404ecb26e324624334d6",
		# v1.461.0 (§9.4's contracted hold points), on any site seeded after it.
		"e7045f53dd14c5c3eb133b8f706fe65894551e5e3fb3f06ae8bce0b073906627",
	},
}

TITLES = {"msa": "Master Subcontractor Agreement", "sow": "Statement of Work"}


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
				"the field-bound blanks, the subcontractor initials and the e-signature block were not "
				f"applied. Merge templates/contracts/{TEMPLATE_FILES[template_key]} into it by hand."
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
				"erpnext_enhancements.patches.refresh_subcontractor_agreement_layout.execute again once they "
				"are signed or voided."
			),
		)
		return
	frappe.db.set_value("Contract Template", template_key, "body", new_body, update_modified=False)
	frappe.db.commit()
