"""Remove the copies of one number a Contact's phone table piled up (v1.745.1).

From v1.618.0 to v1.745.0 frappe's User -> Contact sync appended ``User.phone`` to the
Contact on every User save, because it compares the raw number against rows this app stores
as ``+1…`` (``utils/contact_phones.py`` has the chain). Every deploy saves the Users holding a
fixture Role Profile, so two staff Contacts held 394 and 254 identical rows on 2026-10-26.
``dedupe_contact_phones`` stops new copies; this removes the ones already stored.

**What it changes.** For each Contact whose phone rows hold one number more than once
(compared by ``contact_phones.phone_key``, the stored E.164 form), the first row in table
order of that number is kept and the others are deleted. The kept row takes every primary
flag the number's rows carried, so a number that was the primary phone or primary mobile
stays so. The surviving rows are renumbered ``idx`` 1..n. A Contact whose numbers all differ
is left untouched. No document is saved — the rows are written through ``frappe.db``, so no
hook fires, no Version is written and ``modified`` does not move — and the Contact's cached
document is cleared.

On production after the 2026-10-26 15:23 deploy: 822 Contact Phone rows on 172 Contacts; six
Contacts hold more than one row, and only two of those repeat a number (every copy already
``+1…``). Expected: 646 rows removed, 176 left. Each deploy before this one adds five more.

Each Contact runs in its own savepoint: a failure rolls that Contact back whole — never a
kept row carrying a flag beside a copy that still has it — is counted and logged, and the
rest go on. The counts before and after are printed. Never raises (a patch that raises
aborts ``bench migrate``, which here is the deploy). Safe twice: the second run finds no
repeated number and writes nothing.
"""

import frappe

from erpnext_enhancements.utils.contact_phones import TABLE, duplicate_groups, is_set, merged_flags

LOG = "dedupe_contact_phone_rows"
SAVEPOINT = "dedupe_contact_phone_rows"


def execute():
	try:
		dedupe_contact_phone_rows()
	except Exception:
		try:
			frappe.log_error(title="Contact phone dedupe patch failed", message=frappe.get_traceback())
		except Exception:
			pass
		print(f"{LOG}: aborted early; see the Error Log.")


def dedupe_contact_phone_rows():
	"""Returns ``{"before", "after", "contacts", "removed", "failed"}``."""
	before = _count_rows()
	contacts, removed, failed = 0, 0, []
	for parent in _parents_with_several_rows():
		try:
			frappe.db.savepoint(SAVEPOINT)
			dropped = _dedupe(parent)
		except Exception as exc:
			try:
				frappe.db.rollback(save_point=SAVEPOINT)
			except Exception:
				pass
			failed.append(f"{parent}: {type(exc).__name__}")
			continue
		if dropped:
			contacts += 1
			removed += dropped
	after = _count_rows()
	print(
		f"{LOG}: Contact Phone rows {before} -> {after}; removed {removed} repeated rows "
		f"from {contacts} Contacts; {len(failed)} Contacts failed."
	)
	if failed:
		try:
			frappe.log_error(
				title="Contact phone dedupe patch skipped some Contacts", message="\n".join(failed)
			)
		except Exception:
			pass
	return {"before": before, "after": after, "contacts": contacts, "removed": removed, "failed": failed}


def _count_rows():
	return frappe.db.sql(
		"select count(*) from `tabContact Phone` where parenttype = 'Contact' and parentfield = %s",
		(TABLE,),
	)[0][0]


def _parents_with_several_rows():
	rows = frappe.db.sql(
		"""select parent from `tabContact Phone`
		where parenttype = 'Contact' and parentfield = %s
		group by parent having count(*) > 1""",
		(TABLE,),
	)
	return [row[0] for row in rows]


def _dedupe(parent):
	"""Collapse one Contact's repeated numbers. Returns how many rows it deleted."""
	rows = frappe.db.sql(
		"""select name, idx, phone, is_primary_phone, is_primary_mobile_no from `tabContact Phone`
		where parenttype = 'Contact' and parentfield = %s and parent = %s
		order by idx, creation, name""",
		(TABLE, parent),
		as_dict=True,
	)
	groups = duplicate_groups(rows)
	if not groups:
		return 0
	drop = set()
	for kept, dropped in groups:
		flags = merged_flags(kept, dropped)
		changed = {flag: value for flag, value in flags.items() if int(is_set(kept.get(flag))) != value}
		if changed:
			frappe.db.set_value("Contact Phone", kept.name, changed, update_modified=False)
		drop.update(row.name for row in dropped)
	frappe.db.delete("Contact Phone", {"name": ("in", sorted(drop))})
	survivors = [row for row in rows if row.name not in drop]
	for idx, row in enumerate(survivors, start=1):
		if row.idx != idx:
			frappe.db.set_value("Contact Phone", row.name, "idx", idx, update_modified=False)
	frappe.clear_document_cache("Contact", parent)
	return len(drop)
