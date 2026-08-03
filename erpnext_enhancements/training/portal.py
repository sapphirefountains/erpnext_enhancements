# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Portal access for customer contacts, and the customer-side view of who is trained.

Two jobs, both of which have a cheap wrong version that costs money or leaks data.

**Granting access must not create System Users.** ``Training Learner`` is held by
customer contacts as well as staff, and it is seeded with ``desk_access = 0`` by
``patches/seed_training_roles.py`` precisely so that holding it does not make
somebody a billable System User. This module therefore *refuses to grant the role
at all* if it ever comes back with ``desk_access = 1`` — a check rather than a
comment, because the failure is silent (everything keeps working; the licence
count moves and nobody notices until the invoice). It also never creates the role:
creating it here would produce a second, subtly different definition on any site
where the patch has not run, and the wrong one would win.

**A profiled System User cannot be given a role directly.** ``User.validate``
rebuilds ``roles`` from the union of that user's Role Profiles on *every* save, so
a direct grant survives until the next save and then vanishes. That trap and its
two-path fix live in :mod:`training.roles`; this module delegates to it rather
than re-deriving it. Website Users are the common case here and have no profiles,
so they take the direct path.

**``Contact.user`` is the join, and it is load-bearing.** The whole module resolves
a learner to their Customer through ``Contact.user`` → ``Dynamic Link`` →
``Customer`` (see ``training_assignment._customer_for_user``). A Website User
created without that link back is a learner with no customer: they still see the
all-customer catalogue, but they are invisible to every customer-scoped course and
to the report a client is shown of their own trained people. Granting access
therefore always stamps ``Contact.user``.

The read side is deliberately narrow. A customer contact asking who on their side
is trained is answered about **their own** Customer only, resolved from the
session — the ``customer`` argument is ignored for them, not validated. Validating
it would mean writing a check that can be got wrong; ignoring it cannot be.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.training import roles
from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
	_customer_for_user,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

ROLE = roles.ROLE

MANAGER_ROLES = {"Training Manager", "System Manager"}

# Completion statuses that still prove something. "Superseded" is deliberately in:
# the person did pass, the material has since moved on, and a customer looking at
# their own list should see that distinction rather than a blank.
COUNTS_AS_TRAINED = ("Valid", "Superseded")


def _in_maintenance_context():
	"""True while migrating/installing/patching/importing — never grant roles then."""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _require_manager():
	"""Portal access is a Training Manager action.

	Checked here as well as in DocPerms because a whitelisted method is callable
	directly whatever the desk chooses to show.
	"""
	if not MANAGER_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only a Training Manager can grant portal access."), frappe.PermissionError)


def _assert_role_is_portal_safe():
	"""Refuse to grant ``Training Learner`` unless it is still desk-free.

	The whole reason this role exists separately is that a customer contact may
	hold it without becoming a System User. If somebody ticks ``desk_access`` on it
	in the Desk, every subsequent grant quietly starts costing a licence, and
	nothing else in the system complains. So the grant path checks, every time.
	"""
	if not frappe.db.exists("Role", ROLE):
		frappe.throw(
			_("The {0} role has not been created on this site yet. It is seeded by "
			  "patches/seed_training_roles.py — run bench migrate before granting portal access.").format(ROLE)
		)
	if cint(frappe.db.get_value("Role", ROLE, "desk_access")):
		frappe.throw(
			_("The {0} role has desk access turned on. Granting it to a customer contact would turn them "
			  "into a billable System User. Turn desk access back off on the role before continuing.").format(ROLE)
		)


# --------------------------------------------------------------------- contacts


def _contact_email(contact):
	"""The address a login would be made from: ``email_id``, else the primary row.

	``Contact.email_id`` is itself derived from the child table, but only on save —
	a contact imported or written by an integration can have the rows and an empty
	``email_id``, and refusing those would be refusing exactly the contacts a
	client sends over in bulk.
	"""
	if (contact.get("email_id") or "").strip():
		return contact.email_id.strip()
	rows = contact.get("email_ids") or []
	primary = next((row for row in rows if cint(row.get("is_primary"))), None)
	row = primary or (rows[0] if rows else None)
	return ((row.get("email_id") if row else "") or "").strip()


def _customer_of_contact(contact_name):
	return frappe.db.get_value(
		"Dynamic Link",
		{"parent": contact_name, "parenttype": "Contact", "link_doctype": "Customer"},
		"link_name",
	)


def _ensure_website_user(contact, email, send_welcome_email=False):
	"""``(user_name, created)`` for this contact's login.

	An existing account is reused whatever its type — a customer contact who is
	also, say, a subcontractor with a System User account must not end up with two
	logins for one mailbox.
	"""
	if frappe.db.exists("User", email):
		return email, False

	user = frappe.new_doc("User")
	user.email = email
	user.first_name = (contact.get("first_name") or "").strip() or email.split("@")[0]
	user.last_name = (contact.get("last_name") or "").strip()
	user.user_type = "Website User"
	user.send_welcome_email = 1 if cint(send_welcome_email) else 0
	user.insert(ignore_permissions=True)
	return user.name, True


def _grant_role(user_name):
	"""Add ``Training Learner``. Returns True when something changed.

	System Users go through :mod:`training.roles`, which knows that a profiled user
	needs an extra Role Profile rather than a direct role. Website Users have no
	profiles, so the direct path is both correct and durable for them.
	"""
	if frappe.db.get_value("User", user_name, "user_type") == "System User":
		return roles.grant_learner_role(user_name)

	doc = frappe.get_doc("User", user_name)
	if ROLE in {row.role for row in doc.get("roles") or []}:
		return False
	doc.flags.ignore_permissions = True
	doc.add_roles(ROLE)
	return True


# ---------------------------------------------------------------------- grant


@frappe.whitelist()
def grant_portal_access(contact, send_welcome_email=0):
	"""Give a customer Contact a login that can reach ``/training``.

	Deliberately **not** gated on ``training_enabled`` / ``portal_enabled``:
	preparing access for a client before the switches are turned on is the normal
	order of events, and refusing it would push somebody into doing it by hand.
	"""
	_require_manager()
	if _in_maintenance_context():
		return None

	_assert_role_is_portal_safe()

	doc = frappe.get_doc("Contact", contact)
	email = _contact_email(doc)
	if not email:
		frappe.throw(
			_("{0} has no email address, so there is nothing to make a login from.").format(doc.name)
		)

	customer = _customer_of_contact(doc.name)
	if not customer:
		# Warn rather than refuse. They will still reach the all-customer catalogue;
		# what they will not do is show up on their own company's training report,
		# and the person granting access is the one who can fix that.
		frappe.msgprint(
			_("{0} is not linked to a Customer. Portal access still works, but this person will not "
			  "appear on their company's training report until the link is added.").format(doc.name),
			indicator="orange",
			alert=True,
		)

	user_name, created = _ensure_website_user(doc, email, send_welcome_email)
	role_granted = _grant_role(user_name)

	if not doc.user:
		# The join the rest of the module resolves customers through. Written with
		# db_set rather than a full save so granting access to a contact that fails
		# some unrelated validation still works.
		doc.db_set("user", user_name, update_modified=False)

	return {
		"user": user_name,
		"created": created,
		"role_granted": role_granted,
		"customer": customer,
	}


@frappe.whitelist()
def revoke_portal_access(contact=None, user=None):
	"""Take the Training Learner role away again.

	Removes the role and nothing else. Disabling or deleting the login is not this
	module's decision to make — the same account is often the client's portal
	login for quotes and invoices.
	"""
	_require_manager()
	if _in_maintenance_context():
		return None

	user_name = user or (frappe.db.get_value("Contact", contact, "user") if contact else None)
	if not user_name or not frappe.db.exists("User", user_name):
		return {"user": None, "role_removed": False}

	doc = frappe.get_doc("User", user_name)
	if ROLE not in {row.role for row in doc.get("roles") or []}:
		return {"user": user_name, "role_removed": False}

	doc.flags.ignore_permissions = True
	doc.remove_roles(ROLE)
	return {"user": user_name, "role_removed": True}


# ----------------------------------------------------------- customer visibility


def customer_for_user(user):
	"""The Customer a portal learner belongs to, or None.

	One implementation, imported from the Assignment controller — Contact →
	Dynamic Link → Customer is written once in this module on purpose.
	"""
	return _customer_for_user(user)


def portal_users_for_customer(customer):
	"""Logins of every Contact linked to ``customer``, deduplicated.

	Contacts with no login are skipped rather than reported: a client's contact
	list is full of people who will never take a course, and listing them as
	untrained learners would make the report unreadable.
	"""
	if not customer:
		return []
	contacts = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
	)
	if not contacts:
		return []
	return [
		u
		for u in frappe.get_all("Contact", filters={"name": ["in", contacts]}, pluck="user")
		if u
	]


def _resolve_customer_scope(customer):
	"""Which Customer the caller is allowed to ask about.

	A manager may name one. Anybody else is answered about their own, resolved from
	the session — the argument is *ignored* for them rather than checked, because a
	check is a thing that can be written wrongly and an ignore cannot.
	"""
	if MANAGER_ROLES & set(frappe.get_roles()):
		return customer or customer_for_user(frappe.session.user)
	return customer_for_user(frappe.session.user)


@frappe.whitelist()
def get_customer_training(customer=None):
	"""Who on a client's side has been trained, and in what.

	This is the answer a client actually wants — "are the people who operate our
	fountain trained?" — so it reports completions, not attempts, and says plainly
	when one has expired.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	if not (is_enabled("training_enabled") and is_enabled("portal_enabled")):
		return {"enabled": False, "message": _("Training is not available yet."), "learners": []}

	customer = _resolve_customer_scope(customer)
	if not customer:
		return {"enabled": True, "customer": None, "learners": []}

	users = portal_users_for_customer(customer)
	if not users:
		return {"enabled": True, "customer": customer, "learners": []}

	names = {
		row.name: row.full_name or row.name
		for row in frappe.get_all(
			"User", filters={"name": ["in", users]}, fields=["name", "full_name"]
		)
	}

	learners = {u: {"user": u, "full_name": names.get(u, u), "courses": []} for u in users}
	today = getdate(nowdate())

	for row in frappe.get_all(
		"Training Completion",
		filters={"user": ["in", users], "docstatus": 1},
		fields=["user", "course", "course_title_snapshot", "status", "completed_on", "expires_on"],
		order_by="completed_on desc",
	):
		expired = bool(row.expires_on) and getdate(row.expires_on) < today
		learners[row.user]["courses"].append(
			{
				"course": row.course,
				"course_title": row.course_title_snapshot or row.course,
				# The stored status is the record; expiry is a function of today, so
				# it is computed here rather than trusted from a nightly sweep that
				# may not have run yet.
				"status": "Expired" if expired else row.status,
				"completed_on": row.completed_on,
				"expires_on": row.expires_on,
				"current": (not expired) and row.status in COUNTS_AS_TRAINED,
			}
		)

	return {
		"enabled": True,
		"customer": customer,
		"learners": sorted(learners.values(), key=lambda r: r["full_name"].lower()),
	}
