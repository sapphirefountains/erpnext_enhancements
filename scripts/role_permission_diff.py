#!/usr/bin/env python3
"""What does this account actually LOSE if we take these roles away?

**Not a CI guard.** Every other script in this directory runs bench-free in CI; this one
needs a real bench and a real site, because the only correct answer comes from Frappe's own
metadata resolution. Run it before any role revoke on a service account.

``scripts/`` is not an importable package, so there is no ``bench execute`` dotted path for
it. Load it into a console instead::

    bench --site <site> console
    >>> exec(open("apps/erpnext_enhancements/scripts/role_permission_diff.py").read())
    >>> print(json.dumps(diff("svc@example.com", ["Script Manager"]), indent=2))

Everything here is read-only and imports nothing but ``frappe`` and ``json``, so the body of
a function also pastes straight into the FAC ``run_python_code`` sandbox. Two quirks of that
sandbox if you go that route: it rejects any line beginning with ``from`` (its import
detector is a prefix match, and a SQL ``from`` on a continuation line trips it), and it
returns every local variable alongside your output — set the big ones to ``None`` before the
final ``print`` or the result is truncated to a file.

--------------------------------------------------------------------------------------
Why this exists rather than a hand-written query
--------------------------------------------------------------------------------------

The obvious version of this check — ``select role from tabDocPerm where parent = %s`` — is
wrong on this site, in three distinct ways, and each one produces a **false clear**: a role
that looks safe to remove and is not.

1. **A Custom DocPerm row does not add to the standard rows, it REPLACES them wholesale.**
   If a doctype has any ``Custom DocPerm`` row, every ``DocPerm`` row for that doctype is
   ignored entirely. Query the wrong table and you get a grant set that does not exist.
   ``frappe.get_meta`` applies this rule (``set_custom_permissions``); a hand-built query
   has to remember to, and the memorable thing about that rule is that people forget it.

2. **The ``All`` role is implicit and belongs to everybody.** It never appears in
   ``tabHas Role``, so a naive before/after diff drops it from both sides and manufactures
   losses that cannot happen. ``ToDo`` is the one that bit us: it reads as "System Manager
   only" from ``tabHas Role``-joined queries and is in fact granted to ``All``.

3. **Only the losses matter, and only at the (doctype, ptype) grain.** "This role grants
   read on 58 doctypes" says nothing. The question is whether any OTHER retained role also
   grants it — a role can carry hundreds of permissions and still be free to remove.

--------------------------------------------------------------------------------------
What it cannot see, and why you must not treat a clean run as permission to proceed
--------------------------------------------------------------------------------------

This computes the DocPerm surface only. Three gates live outside it, and all three have
teeth here:

* **Role literals in Python.** ``if "Assistant User" in frappe.get_roles(...)`` is invisible
  to any DocPerm analysis. Frappe Assistant Core gates assistant access on
  ``{System Manager, Assistant Admin, Assistant User}`` this way; this app does it in a
  dozen ``api/`` modules. Grep the installed apps for the literal before trusting a clear.
* **``permission_query_conditions`` / ``has_permission`` hooks.** These NARROW a grant the
  DocPerm table says exists. Worse, they narrow it by returning a shorter list rather than
  raising — the failure is a smaller answer, not an error.
* **``User.user_type``.** ``set_system_user()`` recomputes it from whether ANY held role has
  ``desk_access``. Strip the last desk role and the account silently becomes a Website User,
  and ``on_update`` then calls ``clear_sessions(force=True)``. Keep ``System User``.

So: a doctype:ptype appearing in the output is a definite loss. A doctype:ptype NOT appearing
is the absence of one *kind* of loss, which is not the same as safety.

--------------------------------------------------------------------------------------
Reading the output
--------------------------------------------------------------------------------------

``lost`` is the complete list of ``"DocType:ptype"`` the account can do BEFORE and cannot do
AFTER. An empty list means the roles are redundant with what remains — every permission they
carried is carried by something else too.

Cross-reference each entry against two things before accepting it as a real problem:
  (a) Is the operation performed with ``ignore_permissions=True``? Then it is not a loss at
      all. Most writes on this app's webhook paths are.
  (b) Is the doctype in the account's actual call surface? A lost permission on a doctype
      nothing ever touches costs nothing, and most of what a bloated service account holds
      is exactly that.
"""

import json

import frappe

#: Frappe grants these to every user without a ``Has Role`` row. Omitting them from BOTH
#: sides of the diff is the single most common way this check gets written wrongly — it
#: invents losses on every doctype whose only grant is one of these.
IMPLICIT_ROLES = frozenset({"All"})

PTYPES = ("read", "write", "create", "delete", "submit", "cancel", "report", "export")


def held_roles(user: str) -> set[str]:
	rows = frappe.get_all("Has Role", filters={"parent": user, "parenttype": "User"}, pluck="role")
	return set(rows)


def grant_map(relevant: set[str]) -> dict[str, set[str]]:
	"""``{"DocType:ptype": {roles that grant it}}``, restricted to grants the account has.

	``frappe.get_meta`` is doing the load-bearing work: it is what applies the Custom DocPerm
	replacement rule. Single and child doctypes are excluded — a Single has no rows to filter
	and a child table's permissions are its parent's.
	"""
	grants: dict[str, set[str]] = {}
	for doctype in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, pluck="name"):
		try:
			perms = frappe.get_meta(doctype).permissions
		except Exception:
			# A doctype whose meta will not load must not abort the audit — an incomplete
			# answer here is a false clear, so the skip is deliberate and bounded.
			continue
		for ptype in PTYPES:
			roles = {p.role for p in perms if p.get(ptype)}
			if roles & relevant:
				grants[f"{doctype}:{ptype}"] = roles
	return grants


def diff(user: str, drop: list[str] | set[str]) -> dict[str, object]:
	"""Everything ``user`` could do before removing ``drop`` and could not do after."""
	held = held_roles(user)
	drop = set(drop)

	before = held | IMPLICIT_ROLES
	after = (held - drop) | IMPLICIT_ROLES

	grants = grant_map(before)
	lost = sorted(k for k, roles in grants.items() if (roles & before) and not (roles & after))

	return {
		"user": user,
		"held": len(held),
		"dropping": sorted(drop),
		"not_held": sorted(drop - held),
		"remaining": len(held - drop),
		"grants_examined": len(grants),
		"lost": lost,
		"lost_doctypes": sorted({k.split(":")[0] for k in lost}),
	}


def sole_grants(user: str) -> dict[str, list[str]]:
	"""``{role: [doctypes it is the ONLY held grant for]}`` — the structure of the role set.

	Roles absent from the result are individually free: every permission they carry is also
	carried by another held role. Note the word *individually* — free-one-at-a-time does not
	compose, so confirm the whole batch with :func:`diff` before removing it.
	"""
	held = held_roles(user)
	grants = grant_map(held | IMPLICIT_ROLES)

	sole: dict[str, list[str]] = {}
	for role in held:
		others = (held - {role}) | IMPLICIT_ROLES
		only = {k.split(":")[0] for k, roles in grants.items() if not (roles & others)}
		if only:
			sole[role] = sorted(only)
	return sole


def main(user: str, drop: list[str] | None = None) -> None:
	if drop:
		print(json.dumps(diff(user, drop), indent=2))
	else:
		print(json.dumps(sole_grants(user), indent=2))


if __name__ == "__main__":
	raise SystemExit(
		"This needs a bench, and scripts/ has no dotted import path. Run:\n"
		"  bench --site <site> console\n"
		'  >>> exec(open("apps/erpnext_enhancements/scripts/role_permission_diff.py").read())\n'
		'  >>> print(json.dumps(diff("svc@example.com", ["Script Manager"]), indent=2))'
	)
