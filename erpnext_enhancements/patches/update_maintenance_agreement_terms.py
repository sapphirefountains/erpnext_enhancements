"""Bring the live Maintenance Services Agreement up to the 2026-10-13 terms.

Nik, comparing the Hardware / Salt Development agreement of 2026-09-30 with what
ERPNext generates (2026-10-13), set the terms the template now carries:

* **Base + chemicals.** Standard Maintenance is the base labor rate per visit,
  and chemicals are *always* billed in addition. The old text said chemicals
  were included (§2.1 and §5.1), which the Word copy had already contradicted.
* **Emergency rates** of $250/hr in business hours and $350/hr after hours,
  printed from fields instead of a blank line (§8.2).
* **Automatic renewal with a 12% annual increase**, adjustable per contract
  (§9.2, §4.5).
* **Any payment method on file.** A card is optional; a bank account (ACH), pay
  by invoice or another agreed method also satisfy §4.3, but one must be on file.
* **Client initials** beside the clauses a client most needs to know about,
  each drawn separately on the signing page (``initial()``).

``seed_contract_templates`` is insert-only, so the repo template never reaches
a site that already has the record. This patch replaces the body **only when it
is exactly a body this repo shipped before** — compared by SHA-256 against the
known earlier versions. A body someone edited on the site is left alone and
logged, the same never-guess rule as ``add_esign_signature_block`` and
``bind_maintenance_template_fields``. Prod's body matched the v1.609.0 file
byte for byte when this was written (MD5 23469235dc…, checked 2026-10-13).

Signed agreements are untouched either way: they print their stored executed
copy, never a re-render.
"""

import hashlib

import frappe

TEMPLATE_KEY = "maintenance"
TEMPLATE_FILE = "maintenance_services_agreement.html"

#: SHA-256 of maintenance template bodies this repo has shipped and that this patch may
#: replace. Only the latest pre-2026-10-13 body is listed: the earlier ones were
#: superseded by patches that could only have run on a body like it.
REPLACEABLE_BODY_SHA256 = {
	# v1.609.0 — signature-aware, with the §4.2 / §9.1 / annual-fee bindings.
	"4b77c564f827ccff3ab915a8cab6b582f72bb4493ff67ec730c4b0e07fba2967",
}


def body_sha256(body):
	return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def shipped_body():
	path = frappe.get_app_path("erpnext_enhancements", "templates", "contracts", TEMPLATE_FILE)
	with open(path, encoding="utf-8") as handle:
		return handle.read()


def decide(live_body, new_body):
	"""``"replace"``, ``"current"`` or ``"diverged"`` — what to do with the live body."""
	if live_body == new_body:
		return "current"
	if body_sha256(live_body) in REPLACEABLE_BODY_SHA256:
		return "replace"
	return "diverged"


def execute():
	live = frappe.db.get_value("Contract Template", TEMPLATE_KEY, "body")
	if live is None:
		return  # no maintenance template on this site; the seed will insert the new one

	try:
		new_body = shipped_body()
	except OSError:
		frappe.log_error(title="Maintenance agreement terms: template file missing", message=frappe.get_traceback())
		return

	verdict = decide(live, new_body)
	if verdict == "current":
		return
	if verdict == "diverged":
		frappe.log_error(
			title="Maintenance agreement terms: template not updated",
			message=(
				"Contract Template 'maintenance' has been edited on this site, so the 2026-10-13 terms "
				"(base rate plus chemicals, $250/$350 emergency rates, 12% increase at renewal, any "
				"payment method on file, client initials) were NOT applied. Merge "
				f"templates/contracts/{TEMPLATE_FILE} into the template by hand."
			),
		)
		return

	frappe.db.set_value("Contract Template", TEMPLATE_KEY, "body", new_body, update_modified=False)
	frappe.db.commit()
