# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Warehouse Map's two doc_events on other people's DocTypes, and its boot flag (v1.815.0).

Kept apart from ``api/warehouse_map.py`` on purpose: both handlers run on records this feature
does not own (every ``File`` saved on the site, every ``Warehouse`` renamed), so this module
imports nothing but Frappe and each handler leaves in one comparison for anything that is not
its business.

* :func:`refuse_public_file` (``File`` ``validate``) -- **a floor plan picture is never a public
  file.** The picture is a real building's layout. A public File is served to anyone who has its
  address, with no sign-in, and a name like ``floor-plan.png`` is easy to guess. The map's own
  ``validate`` only stops a public address being *stored* on the map; it cannot stop the public
  File being made, by the Desk form's attach panel, by the File form's *Is Private* box, or by
  any upload that names this DocType. So the File itself is refused, whoever saves it and however.
  Production does not restrict public uploads (System Settings, read on 2026-11-12), so nothing
  else would.

  Nothing is left behind by the refusal. ``File.before_insert`` has already written the upload
  to disk by the time a ``validate`` hook runs (a doc_event runs after the controller's own
  method), but it also registers ``on_rollback``, and the throw rolls the request back:
  ``on_rollback`` then deletes a blob this insert wrote (``_delete_file_on_disk``, which keeps a
  blob another File shares), and moves a file back that an *Is Private* change had moved. Read at
  frappe v16.56.0, ``core/doctype/file/file.py``.

* :func:`follow_warehouse_rename` (``Warehouse`` ``after_rename``) -- a box's own order
  (``Warehouse Floor Map Shape.bin_order``) is a list of warehouse names kept as text, so
  Frappe's rename, which rewrites Link fields, does not reach it. This rewrites the lines that
  name the old warehouse. **It cannot raise**: a failure here must never be the reason a
  warehouse could not be renamed; the cost of missing one is a bin drawn at the end of its shelf
  until the box's order is set again.

* :func:`has_visible_map` -- whether any map is on show, for ``frappe.boot.ee_warehouse_map``
  (``boot.py``), which every "Show on map" door in the Desk reads so that none of them leads to
  an empty page.
"""

import frappe
from frappe import _
from frappe.utils import cint

MAP = "Warehouse Floor Map"
SHAPE = "Warehouse Floor Map Shape"


def refuse_public_file(doc, method=None):
	"""``File`` ``validate``: refuse a File attached to a Warehouse Floor Map that is not private.
	A web link counts: its address is somebody else's server, and it is not private."""
	if (getattr(doc, "attached_to_doctype", None) or "") != MAP:
		return
	if cint(getattr(doc, "is_folder", 0)) or cint(getattr(doc, "is_private", 0)):
		return
	frappe.throw(
		_(
			"A floor plan picture is kept private, so only people signed in here can see it. "
			"Upload it from the Warehouse Map page (Edit map), or switch Private on."
		)
	)


def follow_warehouse_rename(doc, method=None, old=None, new=None, merge=False):
	"""``Warehouse`` ``after_rename``: carry the rename into every box's own order."""
	try:
		old, new = (old or "").strip(), (new or "").strip()
		if not old or not new or old == new:
			return
		rows = frappe.get_all(
			SHAPE, filters={"bin_order": ["like", f"%{old}%"]}, fields=["name", "bin_order"]
		)
		for row in rows:
			lines, seen, changed = [], set(), False
			for line in (row.bin_order or "").splitlines():
				name = line.strip()
				if name == old:
					name, changed = new, True
				# A merge can bring a name in twice; a box lists each place once.
				if name and name not in seen:
					seen.add(name)
					lines.append(name)
			if changed:
				frappe.db.set_value(SHAPE, row.name, "bin_order", "\n".join(lines), update_modified=False)
	except Exception:
		try:
			frappe.log_error(title="Warehouse Map: a renamed warehouse was not carried into a box's order")
		except Exception:
			pass


def has_visible_map():
	"""Whether there is a map that is not hidden. **Never raises**: it runs inside the Desk's own
	boot and Stock Scan's, and between a deploy's code and its migrate the table is not there."""
	try:
		return bool(frappe.db.exists(MAP, {"disabled": 0}))
	except Exception:
		return False
