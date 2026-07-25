"""Seed the signing consent text and the E-SIGN consumer disclosure.

A field ``default`` only applies when the Settings Single is first created, and
it already exists on every live site — so without this the two text blocks come
up blank, and the signing page would ask a customer to agree to nothing.

**Fill-blanks-only.** Anything already written on the site is left alone: this is
legal wording, and a re-migrate must never overwrite a lawyer's edit.

The disclosure is the one piece the app did not previously have anywhere. §16.6
of the maintenance agreement says the agreement *may* be executed electronically,
which is the contract asserting its own enforceability — it is not the separate
consumer disclosure (right to a paper copy, how to withdraw consent, hardware and
software needed) that E-SIGN requires for that signature to hold up against a
consumer. Both blocks are recorded verbatim, with a hash, on every signature.
"""

import frappe

CONSENT_TEXT = (
	"By typing or drawing my name below and clicking Sign, I intend to sign this "
	"agreement electronically, I adopt the signature shown as my own, and I agree "
	"to be bound by the terms of the agreement above. I confirm I am authorised to "
	"sign it."
)

DISCLOSURE_TEXT = (
	"Electronic Records and Signatures Disclosure\n\n"
	"You are being asked to sign this agreement electronically. Please read this "
	"before you do.\n\n"
	"Consent. By signing electronically you consent to receive this agreement and "
	"related records in electronic form rather than on paper.\n\n"
	"Paper copies. You may request a paper copy of this agreement at any time, at "
	"no charge, by contacting us. A PDF copy is also emailed to you as soon as you "
	"sign, and you may download and keep it.\n\n"
	"Withdrawing consent. You may withdraw your consent to do business "
	"electronically at any time by contacting us before you sign. Withdrawing "
	"consent does not affect the validity of records you have already signed. If "
	"you would prefer to sign on paper, contact us and we will send you a printed "
	"copy instead.\n\n"
	"What you need. To read and keep this agreement you need a device with a "
	"current web browser and internet access, an email account able to receive "
	"messages from us, and software able to display PDF files. To keep a copy you "
	"need the ability to save or print a PDF.\n\n"
	"Keeping your email current. Tell us if your email address changes, so that we "
	"can continue to send you records you are entitled to receive."
)

FIELDS = {
	"contract_esign_consent_text": CONSENT_TEXT,
	"contract_esign_disclosure_text": DISCLOSURE_TEXT,
}


def execute():
	meta = frappe.get_meta("ERPNext Enhancements Settings")
	for fieldname, value in FIELDS.items():
		if not meta.has_field(fieldname):
			continue
		if (frappe.db.get_single_value("ERPNext Enhancements Settings", fieldname) or "").strip():
			continue  # already written on this site — never clobber legal wording
		frappe.db.set_single_value("ERPNext Enhancements Settings", fieldname, value)
	frappe.db.commit()
