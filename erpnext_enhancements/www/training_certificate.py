# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Frappe web-page controller for ``/training_certificate`` — two audiences, one route.

* **Anyone, signed in or not**, can check a certificate at
  ``/training_certificate?code=…``. That is the link printed on the document and
  handed to insurers and clients. It answers with validity, course title, holder
  *initials* and the two dates — nothing else, because the link travels further
  than anyone anticipates. The PII boundary is enforced in
  :func:`erpnext_enhancements.training.certificates.verify_certificate`, not
  here, so the API and the page can never drift apart.
* **A signed-in holder** sees their own certificates, and one in full at
  ``?certificate=TRN-CERT-…``. What is rendered is the HTML that was *stored on
  the certificate at issue*, never a fresh render — editing a print format next
  year must not change a document somebody already holds.

**The filename must stay ``training_certificate.py``, underscored.** Frappe
imports a web page's controller by hyphen-to-underscore-ing the *template*
basename, so a hyphenated controller is never imported and ``get_context``
silently never runs — no exception, no log line. ``www/stripe-return.py`` was
broken that way for months and told customers who had cancelled that their
payment was going through. ``scripts/check_www_controllers.py`` guards it in CI.

A certificate the visitor may not see is reported as *not found* rather than as a
permission error: a 403 confirms the document exists, which is itself a small
disclosure on a route anybody can hit.
"""

import urllib.parse

import frappe

from erpnext_enhancements.training.certificates import verify_certificate

# Two audiences on one route, one of them Guest — never serve a cached copy.
no_cache = 1

ROUTE = "/training_certificate"
CERTIFICATE = "Training Certificate"

# Roles that may open somebody else's certificate. Deliberately short: Training
# Manager runs the programme, System Manager already sees everything.
MANAGER_ROLES = {"Training Manager", "System Manager"}


def get_context(context):
	"""Route: ``/training_certificate`` (rendered by ``training_certificate.html``)."""
	context.no_cache = 1
	context.route = ROUTE

	code = (frappe.form_dict.get("code") or "").strip()
	if code:
		_verification_context(context, code)
		return context

	if frappe.session.user == "Guest":
		# No code and no session: there is nothing a guest can be shown here.
		frappe.local.flags.redirect_location = "/login?redirect-to=" + urllib.parse.quote(
			_requested_path(), safe=""
		)
		raise frappe.Redirect

	name = (frappe.form_dict.get("certificate") or frappe.form_dict.get("name") or "").strip()
	if name:
		_single_certificate_context(context, name)
	else:
		_my_certificates_context(context)
	return context


# ------------------------------------------------------------------ helpers


def _verification_context(context, code):
	context.mode = "verify"
	context.submitted_code = code
	try:
		context.result = verify_certificate(code)
	except Exception:
		# Almost always the rate limiter. Whatever it was, a verifier gets a page
		# telling them to try again rather than a traceback — this is the one screen
		# in the module that strangers see.
		frappe.clear_messages()
		context.result = None
		context.throttled = True


def _single_certificate_context(context, name):
	context.mode = "certificate"
	context.certificate = _readable_certificate(name)


def _my_certificates_context(context):
	context.mode = "list"
	context.certificates = []
	if not frappe.db.exists("DocType", CERTIFICATE):
		# Site mid-upgrade. An empty list reads as "you have none", which is true.
		return
	context.certificates = frappe.get_all(
		CERTIFICATE,
		filters={"user": frappe.session.user, "docstatus": ["<", 2]},
		fields=["name", "course_title", "issued_on", "expires_on", "status"],
		order_by="issued_on desc",
		limit=50,
	)


def _readable_certificate(name):
	"""The stored certificate, or None when it does not exist or is not theirs."""
	if not frappe.db.exists("DocType", CERTIFICATE):
		return None
	doc = frappe.db.get_value(
		CERTIFICATE,
		name,
		[
			"name",
			"user",
			"course_title",
			"holder_name",
			"issued_on",
			"expires_on",
			"status",
			"verification_code",
			"certificate_html",
			"docstatus",
		],
		as_dict=True,
	)
	if not doc:
		return None
	if doc.user != frappe.session.user and not (MANAGER_ROLES & set(frappe.get_roles())):
		# Reported as missing, not forbidden — see the module docstring.
		return None
	return doc


def _requested_path():
	"""The path plus query, for the login round trip. Rebuilt from ``form_dict``
	rather than taken from the request, so a crafted redirect target cannot ride
	in on the query string."""
	code = frappe.form_dict.get("certificate") or frappe.form_dict.get("name") or ""
	if code:
		return f"{ROUTE}?certificate={urllib.parse.quote(str(code), safe='')}"
	return ROUTE
