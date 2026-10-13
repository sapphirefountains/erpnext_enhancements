# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Visit Sign-Off Request: one emailed link for a client to sign off a maintenance visit (v1.614.0).

Nik, 2026-10-13: "will this signature requirement allow the tech to send it via email to the
client to sign off? the clients aren't always available to sign in person". Everything here is
made and moved on by ``sapphire_maintenance/sign_off.py``: the technician's Finish or the visit
form's button sends one, the public page ``/visit-sign-off`` signs it, and a resend supersedes
the one before. Only the SHA-256 of the link's token is kept (``token_hash``).

The class name is what frappe derives from the DocType name, spaces and hyphens stripped and
nothing title-cased: ``VisitSignOffRequest`` (``tests/test_doctype_controller_names.py``).
"""

from frappe.model.document import Document


class VisitSignOffRequest(Document):
	pass
