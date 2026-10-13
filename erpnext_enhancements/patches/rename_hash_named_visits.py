"""Give the visits named before v1.597.0 their PRJ-#####-YYYY-V### numbers (v1.610.0).

v1.597.0 named every new maintenance visit after its project, its year and its number that
year (``PRJ-00179-2026-V001``, ``SapphireMaintenanceRecord.autoname``) and left the visits
already created with their random names, because review emails and links named them. No visit
was created after it shipped, so on 2026-10-13 all 20 visits on prod (17 drafts, 3 submitted)
were still ``4lvpg04h6h`` and the like, and Nik: "I'm still seeing the old record numbers
(4lvpg04h6h) instead of PRJ-#####-V### or something similar". So they are renamed.

**The numbers.** Each visit goes into the series its own autoname would have used: the key is
``<project>-<year>-V`` with the year of the visit's day (``visit_compare.visit_day``: its date,
else its scheduled date, else the day it was created; the controller uses the day it is
inserted for the last, which is the same day). Within a key the visits are numbered in the
order they were done (that day, then creation), starting after whatever the key has used
already: its ``tabSeries`` counter, or the highest number a visit already named in the scheme
carries, whichever is higher. ``make_autoname("PRJ-00179-2026-V.###")`` keys ``tabSeries`` on
``PRJ-00179-2026-V`` (``parse_naming_series`` joins the parts before the ``#`` part and
``getseries`` pads with ``%03d``), so as each visit is renamed its key's counter is raised to at
least its number, in the same savepoint, and the next visit created takes the number after it
instead of colliding.

**Skipped:** a visit already in the scheme (so the patch is safe to run twice), one with no
project, and one whose project name could not lead a series (``PROJECT_IN_NAME``). Each keeps
its name, as autoname would have given it.

**What follows a rename.** ``frappe.rename_doc`` rewrites every Link to the visit (Maintenance
Follow-Up, the HR and planner doctypes), the Dynamic Links (Comment, ToDo, Workflow Action,
Communication, DocShare), ``File.attached_to_name``, Versions and the child rows' ``parent``,
and leaves a "renamed from" comment on the timeline. It does **not** touch:

* Calendar sync rows. ``Calendar Sync Link`` keeps the visit's name in two Data fields
  (``source_name`` and the unique ``source_ref``, ``doctype|name|key``), so the nightly repair
  would read the old row as a visit that vanished and delete its Google event, then create a
  new one. Each row is rewritten here: same ``event_id`` (so the same Google event), the new
  name, and ``content_hash`` blanked so the next sync writes the event again with the new
  name in its private properties and description.
* The old name written into text: review emails already sent, the kiosk's links, and the
  answers a technician's phone is still holding. The old name is kept in ``legacy_name``, and
  the Visit Wizard and Visit Review pages and the wizard's endpoints open a visit by it
  (``sapphire_maintenance.visit_names.current_visit_name``); the wizard moves what a phone
  kept onto the new name. A link to the **Desk form** by an old name still shows "not found":
  search for the old ID in a Link field, or filter the list by Previous Number.
* Files attached by the old name after the rename (a wizard page left open across the deploy
  uploads with the name it loaded). ``visit_names.attach_to_current_name`` (a File
  ``before_insert`` hook) re-points new ones, and :func:`repoint_files` here re-points any
  already there.

Synchronous, in the migrate: a deploy flushes the job queue straight after ``bench migrate``,
so anything enqueued here would be lost. ``rebuild_search=False`` for the same reason (it
would enqueue a global-search rebuild per visit). No ``ignore_permissions``: Frappe 16.30's
``frappe.rename_doc`` has no such keyword, and passing it wedged every migrate on 2026-08-07
(``tests/test_uom_cleanup.py`` checks every call site); a patch runs as Administrator, and
``force=True`` is what lets a doctype without ``allow_rename`` be renamed.

One savepoint per visit: a visit whose rename fails is rolled back whole, logged, and keeps its
old name; the rest carry on. Never raises: a patch that raises aborts ``bench migrate``, which
on this repo is the deploy (CLAUDE.md).
"""

import re

import frappe

DOCTYPE = "Sapphire Maintenance Record"
LINK = "Calendar Sync Link"
LOG_TITLE = "rename_hash_named_visits"
SAVEPOINT = "rename_hash_named_visit"
#: A name already in the scheme: ``<project>-<year>-V<number>``, the number three digits or more.
NEW_STYLE = re.compile(r"-\d{4}-V(\d{3,})$")


def execute():
	try:
		if not frappe.db.exists("DocType", DOCTYPE):
			return
		rename_all()
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())


def rename_all():
	"""Rename every visit not yet in the scheme. Returns ``[(old, new)]`` for those renamed."""
	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
		PROJECT_IN_NAME,
	)

	rows = frappe.get_all(
		DOCTYPE,
		fields=["name", "project", "visit_date", "scheduled_visit_date", "creation"],
		order_by="creation asc",
		limit_page_length=0,
	)
	wanted = [
		row
		for row in rows
		if not NEW_STYLE.search(row.name or "") and PROJECT_IN_NAME.fullmatch(row.project or "")
	]
	renamed = []
	if wanted:
		keys = {series_key(row) for row in wanted}
		counters = used_numbers([row.name for row in rows], keys)
		for key in keys:
			counters[key] = max(counters.get(key, 0), series_current(key))
		for old, new, key in plan(wanted, counters):
			if rename_one(old, new, key):
				renamed.append((old, new))
				print(f"rename_hash_named_visits: {old} -> {new}")
	repoint_files()
	frappe.db.commit()
	return renamed


def series_key(row):
	"""``PRJ-00179-2026-V``: the tabSeries key the visit's own autoname would have counted in."""
	from erpnext_enhancements.sapphire_maintenance.visit_compare import visit_day

	return f"{row.get('project')}-{visit_day(row).year}-V"


def number_of(name):
	match = NEW_STYLE.search(name or "")
	return int(match.group(1)) if match else 0


def used_numbers(names, keys):
	"""``{key: highest number}`` among names already in the scheme, for the keys wanted."""
	used = {}
	for name in names:
		match = NEW_STYLE.search(name or "")
		if not match:
			continue
		key = name[: match.start(1)]
		if key in keys:
			used[key] = max(used.get(key, 0), int(match.group(1)))
	return used


def plan(rows, counters):
	"""``[(old, new, key)]``: each visit's new name, in the order they were done, per key.

	``counters`` is each key's highest number already used; numbering starts after it. Pure.
	"""
	from erpnext_enhancements.sapphire_maintenance.visit_compare import visit_day

	by_key = {}
	for row in rows:
		by_key.setdefault(series_key(row), []).append(row)
	out = []
	for key in sorted(by_key):
		number = counters.get(key, 0)
		for row in sorted(
			by_key[key], key=lambda r: (visit_day(r), str(r.get("creation") or ""), r.get("name"))
		):
			number += 1
			out.append((row.get("name"), f"{key}{number:03d}", key))
	return out


def series_current(key):
	"""The key's ``tabSeries`` counter, 0 when it has none."""
	found = frappe.db.sql("SELECT `current` FROM `tabSeries` WHERE `name` = %s", (key,))
	return int((found[0][0] if found else 0) or 0)


def raise_series(key, number):
	"""Make the key's counter at least ``number``, so the next autoname comes after it."""
	frappe.db.sql(
		"""INSERT INTO `tabSeries` (`name`, `current`) VALUES (%s, %s)
		ON DUPLICATE KEY UPDATE `current` = GREATEST(COALESCE(`current`, 0), %s)""",
		(key, number, number),
	)


def rename_one(old, new, key):
	"""Rename one visit, keep its old name, move its calendar rows and raise its series' counter,
	all in one savepoint. True when it was renamed.

	The counter is raised here, with the rename, and not once at the end: a failure between the
	two would leave renamed visits behind a counter that never moved and the patch recorded as
	done, and the next visit drafted for that project and year would be given V001 again and
	stop the nightly scheduler on a duplicate name.
	"""
	if frappe.db.exists(DOCTYPE, new):
		frappe.log_error(
			title=LOG_TITLE,
			message=f"{old} was not renamed: a visit named {new} already exists.",
		)
		return False
	frappe.db.savepoint(SAVEPOINT)
	try:
		frappe.rename_doc(DOCTYPE, old, new, force=True, show_alert=False, rebuild_search=False)
		frappe.db.set_value(DOCTYPE, new, "legacy_name", old, update_modified=False)
		move_calendar_links(old, new)
		raise_series(key, number_of(new))
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(
			title=LOG_TITLE,
			message=f"{old} was not renamed to {new} (rolled back whole).\n\n" + frappe.get_traceback(),
		)
		return False
	return True


def rewrite_ref(ref, old, new):
	"""``Sapphire Maintenance Record|<old>|<key>`` with the name segment changed, else ``ref``."""
	parts = str(ref or "").split("|")
	if len(parts) >= 2 and parts[0] == DOCTYPE and parts[1] == old:
		parts[1] = new
	return "|".join(parts)


def move_calendar_links(old, new):
	"""Point the visit's Calendar Sync Link rows at its new name, keeping their Google event."""
	if not frappe.db.exists("DocType", LINK):
		return
	for link in frappe.get_all(
		LINK,
		filters={"source_doctype": DOCTYPE, "source_name": old},
		fields=["name", "source_ref"],
	):
		frappe.db.set_value(
			LINK,
			link.name,
			{"source_name": new, "source_ref": rewrite_ref(link.source_ref, old, new), "content_hash": ""},
			update_modified=False,
		)


def repoint_files():
	"""Files attached to a visit by its old name, onto the name it has now.

	``rename_doc`` moves the Files a visit has when it is renamed. This catches any attached by
	the old name since (a Visit Wizard page left open across the deploy uploads with the name it
	loaded; ``visit_names.attach_to_current_name`` handles them from now on), and it covers every
	visit with a ``legacy_name``, not only this run's. A failure is logged, not raised.
	"""
	try:
		frappe.db.sql(
			"""UPDATE `tabFile` f
			JOIN `tabSapphire Maintenance Record` v ON v.`legacy_name` = f.`attached_to_name`
			SET f.`attached_to_name` = v.`name`
			WHERE f.`attached_to_doctype` = %s AND COALESCE(v.`legacy_name`, '') != ''""",
			(DOCTYPE,),
		)
	except Exception:
		frappe.log_error(title=LOG_TITLE, message="Files were not re-pointed.\n\n" + frappe.get_traceback())
