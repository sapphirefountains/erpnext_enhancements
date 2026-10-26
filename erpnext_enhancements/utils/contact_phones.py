# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One row per number in a Contact's phone table, whatever format each copy was typed in.

**The bug this closes (v1.745.1).** Two staff Contacts gained one ``Contact Phone`` row per
fixture Role Profile their User holds on every deploy from v1.618.0 on — 394 and 254 rows of
a single number by 2026-10-26. The chain, read in frappe at the v16.53.0 tag (production):

1. ``bench migrate`` imports ``fixtures/role_profile.json`` with ``force=True``, so each Role
   Profile is saved; ``RoleProfile.on_update`` queues ``update_all_users``, which saves every
   User holding that profile ("resaving syncs roles").
2. ``User.on_update`` enqueues ``frappe.core.doctype.user.user.create_contact``. On a User
   that already has a Contact it adds ``User.phone`` "if phone does not exists in contact" —
   ``any(new_contact.phone == user.phone for new_contact in contact.phone_nos)`` (user.py,
   line 1500), then ``Contact.add_phone``'s ``frappe.db.exists("Contact Phone", {"phone":
   phone, ...})`` (contact.py, line 162). Both compare the raw strings.
3. ``User`` is not in the normalization registry, so ``User.phone`` keeps the form it was
   typed in (``801-555-0100``), while ``normalize_hook.normalize_doc`` stores every Contact
   Phone row as ``+18015550100`` (form kit P2-B, v1.618.0). The raw number never equals a
   stored row, so a copy is appended, the hook normalizes it to ``+1…``, and the next User
   save appends another. Any User save did it; a deploy did it once per fixture profile.

**The fix is a guard here, not a change to frappe's comparison.** ``create_contact`` is
frappe's and reached through ``frappe.enqueue`` by dotted path, so patching it means a
monkey-patch on a module attribute in a worker — fragile, and blind to every other caller of
``Contact.add_phone``. Normalizing ``User.phone`` would fix this one caller but changes what
a User stores, which login by mobile number and Call via Triton both read. So
:func:`dedupe_contact_phones` runs as ``doc_events["Contact"]["before_validate"]`` and drops
any row whose number another row already holds, compared by :func:`phone_key` — the stored
form ``normalize.phone`` gives, so ``801-555-0100``, ``(801) 555-0100`` and
``+18015550100`` are one number. Frappe composes ``doc_events[doctype]`` before
``doc_events["*"]``, so this runs before ``normalize_doc``; comparing keys makes the order
irrelevant. The appended copy is dropped in memory, the Contact saves unchanged, and no
Version is written.

**Which row survives.** The first row in table order of each number; the rest are removed.
It takes every primary flag the number's rows carried (``is_primary_phone``,
``is_primary_mobile_no``), so a number that was the primary phone stays the primary phone.
Folding flags onto one row never adds a primary: a number's rows count once afterwards, so
``Contact.set_primary``'s "only one primary" check sees the same or fewer.

It is independent of the ``disable_value_normalization`` kill switch: it writes no value, it
only removes a second copy of a number the Contact already holds. A row with an empty phone is
never touched. It never raises; a failure is logged without the number.

``patches/dedupe_contact_phone_rows`` applies the same rule once to the rows already stored.
"""

import frappe

from erpnext_enhancements.utils import normalize

#: The child table on Contact, and the flags a phone row carries.
TABLE = "phone_nos"
PRIMARY_FLAGS = ("is_primary_phone", "is_primary_mobile_no")


def phone_key(value):
	"""What two phone rows are compared by: the stored form when the rule can read the value,
	else the value trimmed. ``""`` for an empty one, which is never compared."""
	text = "" if value is None else str(value)
	result = normalize.phone(text)
	return result.value if result.ok else text.strip()


def is_set(value):
	"""A Check value as Frappe stores it (``1``, ``"1"``, ``True``) read as a bool."""
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return False


def duplicate_groups(rows):
	"""``[(kept, [dropped, ...]), ...]`` for each number held by more than one row.

	``rows`` in table order, each anything with ``.get`` (a child Document, a ``frappe._dict``).
	The first row of a number is kept. Numbers held once are not listed.
	"""
	groups = {}
	for row in rows:
		key = phone_key(row.get("phone"))
		if key:
			groups.setdefault(key, []).append(row)
	return [(same[0], same[1:]) for same in groups.values() if len(same) > 1]


def merged_flags(kept, dropped):
	"""``{flag: 0 or 1}`` the kept row must carry: each flag set if any row of the number had it."""
	return {flag: int(any(is_set(row.get(flag)) for row in (kept, *dropped))) for flag in PRIMARY_FLAGS}


def dedupe_contact_phones(doc, method=None):
	"""``doc_events["Contact"]["before_validate"]``: one row per number. Never raises."""
	try:
		for kept, dropped in duplicate_groups(list(doc.get(TABLE) or [])):
			for flag, value in merged_flags(kept, dropped).items():
				kept.set(flag, value)
			for row in dropped:
				# Document.remove takes the row out of the table and renumbers idx; the save then
				# deletes a stored row that is no longer listed (Document.update_child_table).
				doc.remove(row)
	except Exception as exc:
		try:
			frappe.log_error(
				title="Contact phone dedupe skipped",
				message=(
					f"Contact: {type(exc).__name__}. The Contact saved with its phone rows as they were; "
					"utils/contact_phones.py never logs the number."
				),
			)
		except Exception:
			pass
