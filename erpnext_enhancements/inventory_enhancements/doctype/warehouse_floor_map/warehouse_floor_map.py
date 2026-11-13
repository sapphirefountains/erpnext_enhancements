# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Warehouse Floor Map (v1.815.0): one floor or area of the building, drawn.

A map is a floor plan picture (a **private** file attached to this record) and the boxes placed
over it (``Warehouse Floor Map Shape`` rows), each tied to a Warehouse. The Warehouse Map page
(``inventory_enhancements/page/warehouse_map``) draws it and edits it through
``api/warehouse_map.py``; this form in the Desk is the plain way in and is held to the same rules,
because every one of them runs here in :meth:`validate` and not in the endpoint:

* the name can be part of an address (``warehouse_map_rules.map_name_problem``), on a rename too
  (:meth:`before_rename`: a rename never runs ``validate``);
* the picture is a private file on this site, never a public file or a web address
  (``background_problem``), and it is a File that exists, is private and is attached to **this**
  map (:meth:`_background_file_problem`): the floor plan is a real building's layout, and an
  address alone proves nothing about the file behind it. The other half of that promise is
  ``inventory_enhancements/warehouse_map_files.refuse_public_file`` (a ``File`` doc_event), which
  refuses a public File attached to a map however it was uploaded;
* each box is cleaned to the stored shape, held inside the drawing, and refused when it cannot
  mean anything; one warehouse is on one box (``clean_shapes``), and on one map
  (``elsewhere_problem``), so "show this bin on the map" has one answer.

A map **reads** warehouses and never writes one. Its ``warehouse`` Link follows a rename by
itself. ``Warehouse Floor Map`` is in ``ignore_links_on_delete`` (``hooks.py``), so a map never stops
somebody deleting an empty bin; the box left behind is drawn as "deleted" in the editor and
refused on the next save until it is removed or re-tied. A box whose warehouse was only
**disabled** (or made a Transit warehouse) is kept: it is drawn dashed, is not opened, and comes
back by itself when the warehouse is enabled again.

**Why it is not called "Warehouse Map".** The page is ``/desk/warehouse-map``, and Frappe's
router turns every DocType a user can read into a route of its own by slugging its name
(``router.js`` ``setup``: ``routes[slug(doctype)]``), then asks "is the first part of the address a
DocType?" *before* "is it a page?" (``segment_kind``, read at v16.56.0). A DocType named
``Warehouse Map`` slugs to ``warehouse-map``, so for everyone who can read it, which is everyone
the page is for, the page's address would open this DocType's list view and the page would never
be reached. It was named that until the day it was built; ``tests/test_warehouse_map.py`` now
fails the build if any Page in this app shares its address with any DocType in this app.

**Every door to the map reads one boot flag** (``frappe.boot.ee_warehouse_map``: a map that is
not hidden exists), and Frappe caches each person's boot payload until they sign in again. So
when a map's visibility changes (:meth:`on_update` for a new map or a changed *Disabled* box,
:meth:`on_trash` for a map that was on show) the cached payloads are dropped
(``warehouse_map_files.clear_boot_cache``, which cannot raise). Saving boxes clears nothing.

The class name is what Frappe derives from the DocType's name (spaces stripped, no re-casing);
``tests/test_doctype_controller_names.py`` holds it to that.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from erpnext_enhancements.inventory_enhancements import warehouse_map_files as files
from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as rules


class WarehouseFloorMap(Document):
	def before_rename(self, old, new, merge=False):
		"""A rename runs no ``validate`` (``rename_doc`` calls only this and ``after_rename``), and
		Frappe's own check refuses nothing but ``<`` and ``>``. Without this the Desk form's rename
		could give a map a name the page's address cannot carry."""
		problem = rules.map_name_problem(new)
		if problem:
			frappe.throw(_(problem))
		if merge:
			frappe.throw(_("Two maps cannot be merged. Move the boxes in the editor instead."))

	def validate(self):
		problem = (
			rules.map_name_problem(self.map_name)
			or rules.background_problem(self.background)
			or self._background_file_problem()
		)
		if problem:
			frappe.throw(_(problem))
		self.map_name = rules.text(self.map_name)
		self.width, self.height = rules.canvas_size(self.width, self.height)

		known = set(frappe.get_all("Warehouse", pluck="name"))
		try:
			shapes = rules.clean_shapes(self.get("shapes") or [], self.width, self.height, known)
		except rules.MapError as error:
			frappe.throw(_(str(error)))
		for row, shape in zip(self.get("shapes") or [], shapes):
			row.update(shape)

		problem = rules.elsewhere_problem(shapes, self._placed_elsewhere())
		if problem:
			frappe.throw(_(problem))

	def on_update(self):
		"""A new map, or one whose *Disabled* box changed, changes what the doors should do."""
		before = self.get_doc_before_save()
		if before is None or cint(before.get("disabled")) != cint(self.disabled):
			files.clear_boot_cache()

	def on_trash(self):
		"""Deleting a map that was on show may leave none: the doors have to be asked again."""
		if not cint(self.disabled):
			files.clear_boot_cache()

	def _background_file_problem(self):
		"""Why the picture's address does not name this map's own private file, or ``None``.

		``background_problem`` reads the address only. Here the File behind it is looked up: it
		must exist, be private, and be attached to this map, which is what makes Frappe serve it to
		exactly the people who can read the map (``File.has_permission``). Another record's private
		file, or an address nothing stands behind, is refused. A map that is not saved yet has no
		File of its own to point at, so its picture is uploaded after its first save.
		"""
		url = rules.text(self.background)
		if not url:
			return None
		name = self.name if not self.is_new() else None
		found = name and frappe.get_all(
			"File",
			filters={
				"file_url": url,
				"is_private": 1,
				"attached_to_doctype": self.doctype,
				"attached_to_name": name,
			},
			limit=1,
		)
		if found:
			return None
		return (
			"The floor plan picture has to be a private file uploaded to this map. "
			"Upload it again from the Warehouse Map page (Edit map)."
		)

	def _placed_elsewhere(self):
		"""``{warehouse: map}`` for every box on every other map."""
		rows = frappe.get_all(
			"Warehouse Floor Map Shape",
			filters={"parenttype": "Warehouse Floor Map", "parent": ["!=", self.name or ""]},
			fields=["parent", "warehouse"],
		)
		return {row.warehouse: row.parent for row in rows if row.warehouse}
