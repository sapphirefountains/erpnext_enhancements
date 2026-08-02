# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Granting the Training Learner role, correctly, on this site.

This exists as its own module because the correct way to grant a role here is
counter-intuitive, and it is needed from two places — the one-off patch for people
who already exist, and the Employee hook for new hires. Duplicating it is exactly
where the two copies would drift and one of them would quietly stop working.

**The trap.** ``User.validate`` calls ``populate_role_profile_roles``, which — for
any user holding at least one Role Profile — rebuilds ``roles`` from the *union of
those profiles* on **every** save. Direct roles are dropped, not merged. So
``user.add_roles("Training Learner")`` appears to succeed, survives until that
user is next saved for any reason, and then silently vanishes. The Desk enforces
the same rule visually by disabling the Roles grid once a profile is set.

So there are two paths and they must not be swapped:

* **Profiled user** — add a dedicated single-role ``Training Learner`` Role
  Profile as an *additional* profile. That is the only durable way to give a
  profiled user an extra role.
* **Profile-less user** — grant directly. Giving them a profile instead would
  regenerate ``roles`` from that one profile and **wipe** ``System Manager``,
  ``PO Approver`` and everything else they hold. Never assign a Role Profile to a
  user who has none.

(Verified on this site 2026-08-01: 11 of 15 active employees are profiled, 4 are
not — and the four include the System Managers, which is precisely why the second
rule matters.)
"""

import frappe

ROLE = "Training Learner"
PROFILE = "Training Learner"


def ensure_profile():
	"""The single-role Role Profile, created once.

	Deliberately minimal: it is added *alongside* somebody's real profile, so
	anything extra in it would silently widen that person's access.
	"""
	if frappe.db.exists("Role Profile", PROFILE):
		return
	doc = frappe.new_doc("Role Profile")
	doc.role_profile = PROFILE
	doc.append("roles", {"role": ROLE})
	doc.insert(ignore_permissions=True)


def grant_learner_role(user):
	"""Give ``user`` the Training Learner role by whichever route survives a save.

	Returns True if something was changed. Never raises — a role grant failing
	must not abort the Employee save or the migrate that triggered it.
	"""
	if not user or not frappe.db.exists("Role", ROLE):
		return False
	if not frappe.db.exists("User", user):
		return False
	if frappe.db.get_value("User", user, "user_type") != "System User":
		# A Website User (a customer contact) gets this when portal access is
		# granted, deliberately, rather than as a side effect of anything here.
		return False

	try:
		doc = frappe.get_doc("User", user)
		has_profile = bool(doc.get("role_profiles")) or bool(doc.get("role_profile_name"))

		if not has_profile:
			if ROLE in {row.role for row in doc.get("roles") or []}:
				return False
			doc.add_roles(ROLE)
			return True

		if PROFILE in {row.role_profile for row in doc.get("role_profiles") or []}:
			return False
		ensure_profile()
		doc.append("role_profiles", {"role_profile": PROFILE})
		doc.save(ignore_permissions=True)
		return True
	except Exception:
		frappe.log_error(
			f"Could not grant {ROLE} to {user}\n{frappe.get_traceback()}", "Training role grant"
		)
		return False
