# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Customers sign in with an emailed link; staff never can (v1.565.0).

Nik's design (2026-09-29): customers reach the portal (``/rentals``, ``/pay``) through Frappe's
**Login with Email Link**. Sign-up stays disabled: an account is created only when a customer
signs a Rental Agreement (:func:`ensure_portal_user`), or when staff invite a contact to the portal.
A random visitor typing a real customer's email only sends a link to that customer's inbox, and an
email with no account gets nothing (frappe's own ``_generate_temporary_login_link`` refuses it).

**Why staff must be refused.** Frappe v16's ``login_via_key`` calls ``LoginManager.login_as()``
for *any* user: no password, no 2FA, no Google. The setting is site-wide, so switched on as it
ships, whoever can read a staff member's inbox could sign in as them — on a site whose
Administrator login was abused in August. Staff stay Google-only. Two layers:

1. :func:`send_login_link` overrides frappe's (``override_whitelisted_methods``) and mints no link
   for any account that is not a Website User. It answers exactly as frappe does for an unknown
   email — nothing — so it does not reveal which addresses are staff.
2. :func:`refuse_staff_email_link_login` (``on_login``) refuses a sign-in through
   ``login_via_key`` for any account that is not a Website User, whatever minted the key.

**The override is sealed** (:func:`seal_original`, ``before_request``). An override matches the
dotted NAME; frappe's whitelist matches the function OBJECT. ``frappe.www.login`` holds a
reference to the ``frappe`` package, so ``frappe.www.login.frappe.www.login.send_login_link`` is a
second name for frappe's original that the override does not catch. Taking the original off the
whitelist closes every alias at once; the canonical name keeps working because it resolves to the
wrapper here. The same mechanism as ``fieldlevel_read.seal_wrapped_originals``.
"""

import frappe
from frappe import _
from frappe.utils import validate_email_address

ORIGINAL = "frappe.www.login.send_login_link"
KEY_LOGIN = "login_via_key"
WEBSITE_USER = "Website User"
CUSTOMER_ROLE = "Customer"

_SEALED = set()
_seal_failure_logged = False


def is_portal_account(user):
	"""True only for an enabled Website User — the one kind of account email links may sign in."""
	if not user or user in ("Administrator", "Guest"):
		return False
	row = frappe.db.get_value("User", user, ["user_type", "enabled"], as_dict=True)
	return bool(row and row.enabled and row.user_type == WEBSITE_USER)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def send_login_link(email: str):
	"""frappe's Login with Email Link, for customer accounts only.

	Same signature and HTTP method as the original, which it calls unchanged for a Website User
	(that call keeps frappe's own rate limit and "does this user exist" check). Anything else —
	staff, Administrator, an unknown address — gets the same silence frappe gives an unknown one.
	"""
	email = (email or "").strip()
	if not is_portal_account(email):
		return
	from frappe.www.login import send_login_link as original

	return original(email)


def refuse_staff_email_link_login(login_manager=None):
	"""``on_login``: an email-link sign-in (``login_via_key``) is for Website Users only."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return
	path = getattr(request, "path", "") or ""
	cmd = str((frappe.local.form_dict or {}).get("cmd") or "")
	if KEY_LOGIN not in path and not cmd.endswith(KEY_LOGIN):
		return
	user = getattr(login_manager, "user", None) or frappe.session.user
	if not is_portal_account(user):
		frappe.throw(
			_("Staff accounts sign in with Google, not with an email link."),
			frappe.AuthenticationError,
		)


def seal_original():
	"""``before_request``: take frappe's own ``send_login_link`` off the whitelist while it is overridden.

	Never raises: a before_request hook that raises fails every request on the site.
	"""
	global _seal_failure_logged
	try:
		fn = frappe.get_attr(ORIGINAL)
		if frappe.override_whitelisted_method(ORIGINAL) != ORIGINAL:
			if fn in frappe.whitelisted:
				frappe.whitelisted.discard(fn)
				_SEALED.add(fn)
		elif fn in _SEALED:
			frappe.whitelisted.add(fn)
			_SEALED.discard(fn)
	except Exception:
		if not _seal_failure_logged:
			_seal_failure_logged = True
			try:
				frappe.log_error(title="Portal login: sealing send_login_link failed", message=frappe.get_traceback(), defer_insert=True)
			except Exception:
				pass


# ---------------------------------------------------------------- portal accounts


def ensure_portal_user(customer, email, full_name=None):
	"""A customer portal account for ``email``, linked to ``customer``; returns the User name or None.

	* An existing **staff** account is never touched and never linked: returns None.
	* An existing Website User gets the Customer role if it lacks it.
	* A new account is a Website User holding only Customer, with **no welcome email** — the
	  customer signs in with an emailed link, and the email that led here says how.
	* The Contact with that email is linked to the Customer, and its ``user`` set, which is how
	  ``get_portal_customers`` finds the customer for this account.
	"""
	email = (email or "").strip().lower()
	if not customer or not email or not validate_email_address(email):
		return None
	existing = frappe.db.get_value("User", email, ["name", "user_type"], as_dict=True)
	if existing and existing.user_type != WEBSITE_USER:
		return None
	first, _sep, last = (full_name or "").strip().partition(" ")
	if existing:
		user = frappe.get_doc("User", existing.name)
		if CUSTOMER_ROLE not in {r.role for r in user.roles}:
			user.append("roles", {"role": CUSTOMER_ROLE})
			user.flags.ignore_permissions = True
			user.save()
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first or email.split("@")[0],
				"last_name": last or None,
				"user_type": WEBSITE_USER,
				"send_welcome_email": 0,
				"roles": [{"role": CUSTOMER_ROLE}],
			}
		)
		user.flags.ignore_permissions = True
		user.flags.no_welcome_mail = True
		user.insert()
	_link_contact(customer, email, user.name, first or email.split("@")[0], last)
	return user.name


def _link_contact(customer, email, user, first, last):
	name = frappe.db.get_value("Contact Email", {"email_id": email, "parenttype": "Contact"}, "parent")
	if name:
		contact = frappe.get_doc("Contact", name)
	else:
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": first,
				"last_name": last or None,
				"email_ids": [{"email_id": email, "is_primary": 1}],
			}
		)
	if not any(l.link_doctype == "Customer" and l.link_name == customer for l in contact.links):
		contact.append("links", {"link_doctype": "Customer", "link_name": customer})
	if not contact.user:
		contact.user = user
	contact.flags.ignore_permissions = True
	if name:
		contact.save()
	else:
		contact.insert()
