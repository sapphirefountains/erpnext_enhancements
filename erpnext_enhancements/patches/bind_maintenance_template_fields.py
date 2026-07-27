"""Bind three fields the live Maintenance Services Agreement prints blank.

``seed_contract_templates`` is deliberately **insert-only**, so the
version-controlled HTML in ``templates/contracts/`` never reaches a site that
already has the Contract Template record. The repo template binds all three of
these; the live one predates the binding and still carries the paper
placeholders, so a maintenance agreement prints its billing cadence, its term
and its annual fee blank — even though all three are filled in on the form, and
the annual fee is one the controller computes for you
(``_compute_maintenance_annual_fee``).

A data-binding fix only: every one of these replacements leaves the visible
wording byte-identical and changes nothing but the value that lands in the gap.
§3.1 Visit Frequency already worked this way; these three now match it.

**The patch never guesses.** Each block is replaced only when the body contains
it *exactly once*; anything else (someone's legal team rewrote the section on
the site) is logged and skipped, leaving bespoke wording alone. Blocks are
independent — one divergence does not block the other two — and each is a no-op
once applied, so re-running is safe.
"""

import frappe

TEMPLATE_KEY = "maintenance"

# (label, old, new) — `label` names the section in the log when a block is
# refused, so "the patch did not apply here" says *which* part is still blank.
REPLACEMENTS = [
	(
		"Service Plan Selection — Annual Service Fee Total",
		"<p><b>Annual Service Fee Total (if applicable): {{ money(None) }}</b></p>",
		"<p><b>Annual Service Fee Total (if applicable): {{ money(doc.annual_maintenance_fee) }}</b></p>",
	),
	(
		"§4.2 — invoicing frequency",
		"<p>4.2  Invoices shall be issued: [ ] Per visit  [ ] Monthly  [ ] Quarterly  [ ] Annually (prepaid).</p>",
		'<p>4.2  Invoices shall be issued: {{ cb(doc.invoicing_frequency=="Per Visit") }} Per visit  '
		'{{ cb(doc.invoicing_frequency=="Monthly") }} Monthly  '
		'{{ cb(doc.invoicing_frequency=="Quarterly") }} Quarterly  '
		'{{ cb(doc.invoicing_frequency=="Annually") }} Annually (prepaid).</p>',
	),
	(
		"§9.1 — initial term",
		"<p>[ ] One (1) Year    [ ] Two (2) Years    [ ] Month-to-Month    [ ] Other: {{ blank(30) }}</p>",
		'<p>{{ cb(doc.initial_term=="One (1) Year") }} One (1) Year    '
		'{{ cb(doc.initial_term=="Two (2) Years") }} Two (2) Years    '
		'{{ cb(doc.initial_term=="Month-to-Month") }} Month-to-Month    '
		'{{ cb(doc.initial_term=="Other") }} Other: {{ blank(30) }}</p>',
	),
]


def rewrite_bindings(body):
	"""Return ``(new_body, applied, refused)``.

	``applied`` and ``refused`` are lists of labels, so the caller can report
	exactly which sections were bound and which were left alone. A block whose
	replacement is already present counts as neither — it is simply done.
	"""
	applied, refused = [], []
	if not body:
		return body, applied, refused

	for label, old, new in REPLACEMENTS:
		if new in body:
			continue  # already bound — idempotent
		if body.count(old) != 1:
			refused.append(label)
			continue
		body = body.replace(old, new)
		applied.append(label)
	return body, applied, refused


def execute():
	body = frappe.db.get_value("Contract Template", TEMPLATE_KEY, "body")
	if not body:
		return

	new_body, applied, refused = rewrite_bindings(body)

	if refused:
		frappe.log_error(
			"Contract Template '{0}': these sections do not match the expected wording, so their "
			"fields are still printing blank — {1}. Copy the matching lines from "
			"templates/contracts/maintenance_services_agreement.html into the template by hand "
			"(the change is only the {{{{ cb(...) }}}} / {{{{ money(...) }}}} binding; the visible "
			"wording is identical).".format(TEMPLATE_KEY, ", ".join(refused)),
			"Maintenance template: fields left unbound",
		)

	if applied:
		frappe.db.set_value(
			"Contract Template", TEMPLATE_KEY, "body", new_body, update_modified=False
		)
		frappe.db.commit()
