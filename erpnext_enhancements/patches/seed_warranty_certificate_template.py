"""Add the one-year Certificate of Warranty to the Contract Templates (v1.808.0).

Nikolas, 2026-11-12: "I need this added to the Contract Builder". The certificate is a ninth
entry in ``seed_contract_templates.TEMPLATES``, but that patch has already run on every
existing site and a patch never runs twice, so this one inserts the single new record
(``Contract Template`` ``warranty``) from ``templates/contracts/certificate_of_warranty.html``.

* **Insert-only.** A ``warranty`` template already on the site (this patch run before, a
  fresh install where the seed made it, or one someone wrote by hand) is left exactly as
  it is: wording edited on the site is the site's.
* **Only that one key.** It goes through ``seed(only={"warranty"})``, so it cannot bring
  back a template someone deleted on purpose.
* **Cannot raise.** A patch that raises aborts ``bench migrate``, which on this repo is the
  deploy. A failure is logged and printed, and the Builder simply does not offer the
  certificate until the record exists (paste the file into a new Contract Template, key
  ``warranty``, to finish by hand).

Prints what it did, so the deploy log says whether the record was made.
"""

import frappe

TEMPLATE_KEY = "warranty"


def execute():
	try:
		from erpnext_enhancements.patches.seed_contract_templates import seed

		if frappe.db.exists("Contract Template", TEMPLATE_KEY):
			print(
				f"seed_warranty_certificate_template: Contract Template '{TEMPLATE_KEY}' is already there; left as it is."
			)
			return
		inserted = seed(only={TEMPLATE_KEY})
		if TEMPLATE_KEY in inserted:
			print(
				f"seed_warranty_certificate_template: inserted Contract Template '{TEMPLATE_KEY}' (Certificate of Warranty)."
			)
		else:
			print(
				f"seed_warranty_certificate_template: Contract Template '{TEMPLATE_KEY}' was NOT inserted "
				"(its source file could not be read; see the Error Log)."
			)
	except Exception:
		print(
			f"seed_warranty_certificate_template: failed, Contract Template '{TEMPLATE_KEY}' not inserted (see the Error Log)."
		)
		try:
			frappe.log_error(
				title="seed_warranty_certificate_template: template not inserted",
				message=frappe.get_traceback(),
			)
		except Exception:
			pass
