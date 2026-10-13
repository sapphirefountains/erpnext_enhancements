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

Three things are done first, because the review of v1.611.0 found that "the
template changes, so every unsigned render changes" reaches further than it looks:

* **Contracts signed on paper are frozen** (``freeze_contract_copies``). An e-signed
  contract prints its stored executed copy, but one recorded with "Mark as Signed"
  had none and re-rendered from the live template. On prod that is SF-MAINT-2026-0001
  (Candi Wadsworth, signed 2026-08-03), which would otherwise have reprinted with
  terms she never saw.
* **Agreements already issued keep their old terms** (``keep_issued_terms``). The new
  fields' defaults ($250, $350, a 1-hour minimum, 12% at renewal) reach every existing
  row through the ``ALTER``. On an agreement issued under the old wording they would be
  copied into its operational contract and raise its rates at renewal. They are set
  back to 0 on every submitted maintenance agreement; drafts keep the new terms, since
  they will be issued on the new template.
* **No swap while a signing link is live** (``live_requests``). The executed copy is
  re-rendered from the live template at signing, so a client signing an old link after
  the swap would receive terms they were never shown. The body is then left alone and
  the reason logged; run this patch again once those requests are signed or voided.
  There were none on prod on 2026-10-13.
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


#: The new terms' fields and the value an agreement issued under the old wording carries:
#: nothing. The old §8.2 printed a blank rate, and the old §9.2 renewed "at the then-current
#: service rates" with no set increase.
OLD_TERMS = {
	"renewal_increase_percent": 0,
	"emergency_rate": 0,
	"after_hours_emergency_rate": 0,
	"emergency_minimum_hours": 0,
}


def freeze_contract_copies(template_key):
	"""Store the executed copy of every contract of this type signed on paper, as it reads now."""
	for name in frappe.get_all(
		"Project Contract",
		filters={"template_key": template_key, "docstatus": 1, "status": "Signed"},
		pluck="name",
	):
		try:
			frappe.get_doc("Project Contract", name).freeze_executed_copy()
		except Exception:
			frappe.log_error(title=f"Contract terms patch: could not freeze {name}", message=frappe.get_traceback())


def keep_issued_terms(template_key):
	"""Put the old terms back on agreements issued before the new ones existed."""
	for name in frappe.get_all(
		"Project Contract", filters={"template_key": template_key, "docstatus": 1}, pluck="name"
	):
		frappe.db.set_value("Project Contract", name, OLD_TERMS, update_modified=False)


def live_requests(template_key):
	"""Signing links still out for contracts of this type."""
	contracts = frappe.get_all("Project Contract", filters={"template_key": template_key}, pluck="name")
	if not contracts:
		return []
	return frappe.get_all(
		"Contract Signature Request",
		filters={"project_contract": ["in", contracts], "status": ["in", ["Sent", "Viewed"]]},
		pluck="name",
	)


def execute():
	try:
		_execute()
	except Exception:
		# A patch that raises aborts `bench migrate`, which is the deploy.
		frappe.log_error(title="Maintenance agreement terms patch failed", message=frappe.get_traceback())


def _execute():
	live = frappe.db.get_value("Contract Template", TEMPLATE_KEY, "body")
	if live is None:
		return  # no maintenance template on this site; the seed will insert the new one

	# Before anything reads the new body: freeze what paper-signed contracts say, and keep
	# the agreements already issued on the terms they were issued with.
	freeze_contract_copies(TEMPLATE_KEY)
	keep_issued_terms(TEMPLATE_KEY)
	frappe.db.commit()

	try:
		new_body = shipped_body()
	except OSError:
		frappe.log_error(title="Maintenance agreement terms: template file missing", message=frappe.get_traceback())
		return

	verdict = decide(live, new_body)
	if verdict == "current":
		return
	pending = live_requests(TEMPLATE_KEY)
	if pending:
		frappe.log_error(
			title="Maintenance agreement terms: template not updated yet",
			message=(
				"These signing links are still out, and a client who signs one after the template changes "
				"would receive terms they were never shown: " + ", ".join(pending) + ". Run "
				"erpnext_enhancements.patches.update_maintenance_agreement_terms.execute again once they "
				"are signed or voided."
			),
		)
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
