# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Training Certificate print format, created idempotently on every migrate.

Same `after_migrate` upsert as the other nine formats this app ships (see
`enhancements_core/setup_print_formats.py` for the full rationale): the template
lives in the repo, edits deploy on the next migrate with no export step, and
`after_migrate` runs *after* fixture sync so nothing can silently override it.

Two things here are specific to certificates and neither is obvious:

- **Re-upserting this format does not rewrite a single issued certificate.** Every
  certificate stores its own rendered HTML at issue (`training/certificates.py`),
  so what a holder has in their hands is a snapshot, and this template only
  affects certificates issued *after* the deploy. That is the intended direction
  and it is why editing the template freely is safe — but it also means fixing a
  typo here does **not** fix it on documents already out there. Those need
  reissuing, deliberately, one at a time.
- **Order in `after_migrate` is load-bearing.** This hook must sit *above*
  `enhancements_core.setup_print_formats.ensure_chrome_pdf_generator`, which is
  deliberately last and points every format at the chrome PDF backend. Registered
  below it, the certificate would be the one format left on wkhtmltopdf, and the
  only symptom would be a subtly different PDF.

House rules for a `custom_format = 1` template, all inherited from the Purchase
Order format and all previously learned the hard way: the letter head is rendered
by the template itself (frappe injects one only for *standard* formats), CSS is
print-safe — no flexbox, no grid, `page-break-inside: avoid` — styles are inline,
and the type is Helvetica/Arial.
"""

import frappe

MODULE = "Training"
CERTIFICATE_DOCTYPE = "Training Certificate"
CERTIFICATE_FORMAT = "Training Certificate - Sapphire"

_CERTIFICATE_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:13px; page-break-inside:avoid;">

  {#- A custom Jinja format renders its own letterhead: frappe builds the `#header-html`
      block only for *standard* formats, so a template that does not ask for `letter_head`
      simply goes out unbranded. The Purchase Order format spent its first month that way.
      A certificate leaves the company more often than anything else here, so it matters
      more, not less. -#}
  {%- if letter_head %}
  <div style="margin-bottom:14px;">{{ letter_head }}</div>
  {%- endif %}

  <div style="border:3px double #333; padding:28px 30px;">

    <div style="text-align:center; border-bottom:1px solid #ccc; padding-bottom:14px; margin-bottom:22px;">
      <div style="font-size:11px; letter-spacing:3px; color:#777; text-transform:uppercase;">Certificate of Completion</div>
      <h1 style="margin:10px 0 0 0; font-size:26px; font-weight:normal;">{{ (doc.course_title or doc.course or "") | e }}</h1>
    </div>

    <div style="text-align:center; margin-bottom:24px;">
      <div style="color:#777; font-size:11px; text-transform:uppercase; letter-spacing:1px;">This certifies that</div>
      <div style="font-size:24px; margin:8px 0 10px 0;"><b>{{ (doc.holder_name or "") | e }}</b></div>
      <div style="color:#555;">
        has completed the training named above
        {%- if doc.get("version_number") %} (version {{ doc.version_number }}){% endif -%}
        {%- if doc.get("score_percent") %}, scoring {{ doc.score_percent }}%{% endif -%}.
      </div>
    </div>

    {#- A table rather than a flex row: the PDF engine on this host is print-CSS only,
        and a flex layout collapses into a single column with no warning. -#}
    <table style="width:100%; border-collapse:collapse; margin-bottom:22px; page-break-inside:avoid;">
      <tr>
        <td style="width:33%; padding:6px 4px; border-top:1px solid #eee; vertical-align:top;">
          <div style="color:#777; font-size:10px; text-transform:uppercase; letter-spacing:1px;">Issued</div>
          <div>{{ frappe.format(doc.issued_on, {"fieldtype": "Date"}) }}</div>
        </td>
        <td style="width:33%; padding:6px 4px; border-top:1px solid #eee; vertical-align:top;">
          <div style="color:#777; font-size:10px; text-transform:uppercase; letter-spacing:1px;">Valid Until</div>
          <div>
            {%- if doc.expires_on -%}
              {{ frappe.format(doc.expires_on, {"fieldtype": "Date"}) }}
            {%- else -%}
              <span style="color:#555;">Does not expire</span>
            {%- endif -%}
          </div>
        </td>
        <td style="width:34%; padding:6px 4px; border-top:1px solid #eee; vertical-align:top;">
          <div style="color:#777; font-size:10px; text-transform:uppercase; letter-spacing:1px;">Certificate</div>
          <div>{{ doc.name | e }}</div>
        </td>
      </tr>
    </table>

    {#- The verification block is the working part of the document. Everything above it
        can be forged in a word processor in five minutes; this is what makes the thing
        checkable, so it is printed in full rather than reduced to a QR code that a
        printed-and-scanned copy would lose. -#}
    <div style="background:#f4f5f7; padding:12px 14px; page-break-inside:avoid;">
      <div style="color:#777; font-size:10px; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Verify This Certificate</div>
      <div style="font-size:16px; letter-spacing:2px;"><b>{{ (doc.verification_code or "") | e }}</b></div>
      <div style="color:#555; margin-top:4px;">
        Enter the code at {{ frappe.utils.get_url("/training_certificate") }}
      </div>
    </div>

    {#- Supervisor sign-off, looked up rather than stored on the certificate: the
        sign-off is its own submitted document and the certificate has no field for
        it. Two scalar lookups rather than one `as_dict` call, because the print
        sandbox is the wrong place to discover that a keyword argument is not
        allowed — the only symptom would be a blank certificate. -#}
    {%- set signoff = frappe.db.get_value("Training Signoff", {"course": doc.course, "user": doc.user, "outcome": "Competent", "docstatus": 1}, "supervisor") %}
    {%- if signoff %}
    <table style="width:100%; border-collapse:collapse; margin-top:22px; page-break-inside:avoid;">
      <tr>
        <td style="width:50%; padding-top:22px; border-top:1px solid #333; vertical-align:top;">
          <div>{{ (frappe.db.get_value("Employee", signoff, "employee_name") or signoff) | e }}</div>
          <div style="color:#777; font-size:11px;">Verified competent by</div>
        </td>
        <td style="width:8%;"></td>
        <td style="width:42%;"></td>
      </tr>
    </table>
    {%- endif %}

  </div>

  <div style="margin-top:10px; color:#777; font-size:10px; text-align:center;">
    This certificate records training completed on the date shown. It is not a licence or a
    statement of continuing competence.
  </div>

</div>
"""


def ensure_training_print_formats():
	"""`after_migrate` entry point. Failures log rather than abort the migrate."""
	try:
		if not frappe.db.exists("DocType", CERTIFICATE_DOCTYPE):
			# A site that has not migrated the Phase 4 doctypes yet. Guarded rather
			# than assumed: this hook runs on every bench in the fleet.
			return
		_upsert_print_format(CERTIFICATE_FORMAT, CERTIFICATE_DOCTYPE, _CERTIFICATE_HTML)
		frappe.db.commit()
		frappe.logger().info(f"Training print formats: ensured {CERTIFICATE_FORMAT}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Training print formats")


def _upsert_print_format(name, doc_type, html):
	if frappe.db.exists("Print Format", name):
		pf = frappe.get_doc("Print Format", name)
	else:
		pf = frappe.new_doc("Print Format")
		pf.name = name

	pf.doc_type = doc_type
	pf.module = MODULE
	pf.print_format_type = "Jinja"
	pf.custom_format = 1
	pf.standard = "No"
	pf.disabled = 0
	pf.html = html
	pf.save(ignore_permissions=True)
