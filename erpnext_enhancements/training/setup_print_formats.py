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
Order format and all previously learned the hard way: the letterhead is drawn by
the template itself (frappe injects one only for *standard* formats — and since
v1.494.0 `print_style.letterhead()` draws the wordmark, so the site's Letter Head is
no longer rendered beside it), CSS is print-safe — no flexbox, no grid,
`page-break-inside: avoid` — and styles are inline.

The chrome is `print_style`'s (docs/print-design-system.md): the neutral stripe,
the wordmark with our address, an eyebrow, the display face for the title, the
holder's name and the course. The holder's name is the largest thing on the page,
because a certificate is about the person; the verification code is the working
part and sits on an off-white ground under a sapphire rule.
"""

import frappe

from erpnext_enhancements import print_style as ps

MODULE = "Training"
CERTIFICATE_DOCTYPE = "Training Certificate"
CERTIFICATE_FORMAT = "Training Certificate - Sapphire"

_BADGE = """
    {#- The badge this course awards, printed above the holder's name.

        Keyed on the COURSE, not on the learner's award, and that is forced rather than chosen:
        `certificates.after_completion` issues and renders the certificate FIRST and calls
        `_award_badges` afterwards -- deliberately, because the certificate is the only one of the
        three anybody outside the company will ask to see. So at the moment this template runs, a
        `Training Badge Award` for this completion does not exist yet. A query for one would find
        nothing, render an empty space, and raise nothing: the silent-pass shape this repo keeps
        being bitten by. `Training Badge` with `criteria_type = "Course Completed"` is the same
        fact, available now, and one per course (checked: no duplicates on production).

        `width`/`height` are load-bearing. The badge SVGs carry width="256" height="256", so
        unsized they print at 256px and take over the page.

        The `if` is what keeps the certificates that already exist rendering exactly as they do:
        none of the three courses currently issuing one has a Course Completed badge. -#}
    {%- set badge_image = frappe.db.get_value("Training Badge", {"criteria_type": "Course Completed", "criteria_course": doc.course, "enabled": 1}, "image") %}
    {%- if badge_image %}
    <div style="text-align:center; margin:18px 0 6px; page-break-inside:avoid;">
      <img src="{{ badge_image }}" alt="" width="96" height="96" style="width:96px; height:96px;">
    </div>
    {%- endif %}
"""

_CERTIFICATE_HTML = (
	ps.page_open(None)
	+ """
  {#- A custom Jinja format renders its own letterhead: frappe builds the `#header-html`
      block only for *standard* formats, so a template that does not draw one simply goes
      out unbranded. The Purchase Order format spent its first month that way. A certificate
      leaves the company more often than anything else here, so it matters more, not less.
      Since v1.494.0 print_style.letterhead() inlines the wordmark itself, so `letter_head`
      is deliberately NOT rendered: two logos on one page is worse than one. -#}
"""
	+ ps.letterhead(
		None,
		"TRAINING &middot; CERTIFICATE OF COMPLETION",
		"Certificate of Completion",
		'<span style="' + ps.STRONG + '">{{ doc.name | e }}</span><br>'
		'{{ frappe.format(doc.issued_on, {"fieldtype": "Date"}) }}',
	)
	+ '<div style="page-break-inside:avoid;">\n'
	+ _BADGE
	+ """
    <div style="text-align:center; margin:22px 0 6px;">
      <div style=\""""
	+ ps.LABEL
	+ """\">THIS CERTIFIES THAT</div>
      <div style="margin:8px 0 4px; font-family:"""
	+ ps.DISPLAY_FONT
	+ """; font-weight:700; font-size:34px; line-height:34px; color:"""
	+ ps.DEEP_SEA_BLUE
	+ """;">{{ (doc.holder_name or "") | e }}</div>
      <div style="font-size:13px; color:"""
	+ ps.INK_700
	+ """;">has completed</div>
      <div style="margin:6px 0 4px; font-family:"""
	+ ps.DISPLAY_FONT
	+ """; font-weight:700; font-size:24px; line-height:24px; color:"""
	+ ps.BAHAMA_BLUE
	+ """;">{{ (doc.course_title or doc.course or "") | e }}</div>
      <div style="font-size:12.5px; color:"""
	+ ps.INK_700
	+ """;">
        {%- if doc.get("version_number") %}version {{ doc.version_number }}{% endif -%}
        {%- if doc.get("version_number") and doc.get("score_percent") %} &middot; {% endif -%}
        {%- if doc.get("score_percent") %}scored {{ doc.score_percent }}%{% endif -%}
      </div>
    </div>
"""
	+ ps.facts_open()
	+ ps.fact("ISSUED", '{{ frappe.format(doc.issued_on, {"fieldtype": "Date"}) }}')
	+ ps.fact(
		"VALID UNTIL",
		'{%- if doc.expires_on -%}{{ frappe.format(doc.expires_on, {"fieldtype": "Date"}) }}'
		"{%- else -%}Does not expire{%- endif -%}",
	)
	+ ps.fact("CERTIFICATE", "{{ doc.name | e }}")
	+ ps.facts_close()
	+ """
    {#- The verification block is the working part of the document. Everything above it
        can be forged in a word processor in five minutes; this is what makes the thing
        checkable, so it is printed in full rather than reduced to a QR code that a
        printed-and-scanned copy would lose. -#}
    <div style="margin-top:14px; padding:12px 14px; background:"""
	+ ps.OFF_WHITE
	+ """; border-top:3px solid """
	+ ps.FRESH_BLUE
	+ """; page-break-inside:avoid;">
      <div style=\""""
	+ ps.LABEL
	+ """\">VERIFY THIS CERTIFICATE</div>
      <div style="font-size:18px; letter-spacing:2px; margin-top:4px; """
	+ ps.STRONG
	+ """;">{{ (doc.verification_code or "") | e }}</div>
      <div style="margin-top:4px; font-size:12px;">Enter the code at {{ frappe.utils.get_url("/training_certificate") }}</div>
    </div>

    {#- Supervisor sign-off, looked up rather than stored on the certificate: the
        sign-off is its own submitted document and the certificate has no field for
        it. Two scalar lookups rather than one `as_dict` call, because the print
        sandbox is the wrong place to discover that a keyword argument is not
        allowed — the only symptom would be a blank certificate. -#}
    {%- set signoff = frappe.db.get_value("Training Signoff", {"course": doc.course, "user": doc.user, "outcome": "Competent", "docstatus": 1}, "supervisor") %}
    {%- if signoff %}
    <table style="width:100%; border-collapse:collapse; margin-top:26px; page-break-inside:avoid;">
      <tr>
        <td style="width:50%; padding-top:6px; border-top:1px solid """
	+ ps.DEEP_SEA_BLUE
	+ """; vertical-align:top;">
          <div style=\""""
	+ ps.STRONG
	+ """\">{{ (frappe.db.get_value("Employee", signoff, "employee_name") or signoff) | e }}</div>
          <div style=\""""
	+ ps.LABEL
	+ """\">VERIFIED COMPETENT BY</div>
        </td>
        <td style="width:8%;"></td>
        <td style="width:42%;"></td>
      </tr>
    </table>
    {%- endif %}

    <div style="margin-top:14px; font-size:11px; text-align:center;">
      This certificate records training completed on the date shown. It is not a licence or a
      statement of continuing competence.
    </div>
  </div>
"""
	+ ps.page_close(None)
)


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
