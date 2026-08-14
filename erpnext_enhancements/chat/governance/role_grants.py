# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Granting oversight is itself an oversight event. Phase 6 §4.A.4.

Decision #12 lets a configured role read every conversation in the company. The role is
therefore the most privileged thing in this feature, and **a System Manager can grant it to
themselves** — which is not a flaw to be closed (somebody has to be able to grant it) but a fact
that makes the grant worth recording. §4.A.4: *"Adding or removing the oversight role from a
user writes a ``Chat Audit Log`` row… and raises an operational alert."*

WHY THE HOOK IS ON ``User``
===========================

``Has Role`` is a **child table of ``User``**, and Frappe fires no document event for a child
row on its own — the parent's ``on_update`` is the only place a role change is observable. The
Phase 6 prompt flags this as a ``VERIFY:`` and it is confirmed here against the installed
source: this app already carries a second ``User.on_update`` handler
(``training.assignment.on_user_roles_changed``) doing the same diff for course assignment, which
is the working precedent rather than a guess.

WHAT IT COMPARES, AND WHY NOT THE OBVIOUS THING
===============================================

``get_doc_before_save()``, not a stored copy and not the database. The obvious implementation
re-reads ``tabHas Role`` and compares to ``doc``, which reports **every** save as a no-op change
because the child rows have already been written by the time ``on_update`` runs.

**The profile trap this app has already been bitten by.** A profiled user's roles are rebuilt
wholesale from their Role Profiles on *every* save, so a save that changes nothing about
oversight still rewrites the whole child table. Diffing sets rather than rows is what makes that
a no-op here; diffing rows would log a grant and a revoke on every single save of every profiled
user, which is noise in the one table whose value is that it is quiet.

WHAT IT DELIBERATELY DOES NOT DO
================================

**It does not refuse the grant.** This is a record, not a gate. An audit hook that can veto a
save is an audit hook that takes down user administration when it has a bug, and the thing it
would be protecting against — somebody who already holds System Manager — is not stopped by a
veto they can remove.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.chat import audit


def on_user_roles_changed(doc: Any, method: str | None = None) -> None:
	"""Record a grant or revocation of the chat oversight role. **Never raises.**

	Wired as a ``User.on_update`` doc_event. Returns immediately — before any query — when the
	oversight role is not configured or the user's roles did not change, because this runs on
	every save of every ``User`` on the site and the common case must cost nothing.
	"""
	try:
		role = _oversight_role()
		if not role:
			# `admin_oversight_role` ships blank and blank means nobody, so there is no grant
			# to record. The hatch being shut is exactly when this has nothing to say.
			return

		before = doc.get_doc_before_save()
		if before is None:
			# A new User. Frappe fires `after_insert` rather than `on_update` for the insert
			# itself, so reaching here with no prior doc means a save we cannot diff; treat the
			# roles as unchanged rather than reporting the whole set as newly granted.
			return

		had = _roles(before)
		has = _roles(doc)
		if (role in had) == (role in has):
			return

		granted = role in has
		audit.record_governance_event(
			event_type="oversight_role_granted" if granted else "oversight_role_revoked",
			actor=frappe.session.user or "Administrator",
			subject_user=doc.name,
			reference_doctype="Role",
			reference_name=role,
			detail=(
				f"{'Granted' if granted else 'Revoked'} the chat oversight role {role!r} "
				f"{'to' if granted else 'from'} {doc.name}. This role can read every "
				f"conversation in the company."
			),
		)
	except Exception:
		# A record that cannot be written must not take down user administration with it. The
		# writer already logs its own failures; this catch covers the diff above it.
		try:
			frappe.log_error(
				title="chat governance: role-change audit failed",
				message=f"user={getattr(doc, 'name', '?')}",
			)
		except Exception:
			pass


def _oversight_role() -> str:
	"""The configured oversight role, or ``""``. Never raises — this runs inside a save."""
	try:
		return (frappe.db.get_single_value("Chat Settings", "admin_oversight_role") or "").strip()
	except Exception:
		# A site without Chat Settings yet (a fresh install mid-migrate) has no oversight role
		# and therefore nothing to audit. Failing closed here means "no event", not "no save".
		return ""


def _roles(user_doc: Any) -> set[str]:
	"""The role names on a ``User`` document, as a set.

	A **set**, not a list of rows: a profiled user's roles are rebuilt wholesale on every save,
	so row identity changes constantly while membership does not. Comparing rows would log a
	grant and a revoke every time anybody with a Role Profile saved their own profile.
	"""
	return {
		(row.get("role") if isinstance(row, dict) else getattr(row, "role", "")) or ""
		for row in (user_doc.get("roles") or [])
	}
