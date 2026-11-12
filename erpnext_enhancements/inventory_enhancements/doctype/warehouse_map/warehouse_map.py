# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Warehouse Map (v1.815.0): one floor or area of the building, drawn.

A map is a floor plan picture (a **private** file attached to this record) and the boxes placed
over it (``Warehouse Map Shape`` rows), each tied to a Warehouse. The Warehouse Map page
(``inventory_enhancements/page/warehouse_map``) draws it and edits it through
``api/warehouse_map.py``; this form in the Desk is the plain way in and is held to the same rules,
because every one of them runs here in :meth:`validate` and not in the endpoint:

* the name can be part of an address (``warehouse_map_rules.map_name_problem``);
* the picture is a private file on this site, never a public file or a web address
  (``background_problem``): the floor plan is a real building's layout;
* each box is cleaned to the stored shape, held inside the drawing, and refused when it cannot
  mean anything; one warehouse is on one box (``clean_shapes``), and on one map
  (``elsewhere_problem``), so "show this bin on the map" has one answer.

A map **reads** warehouses and never writes one. Its ``warehouse`` Link follows a rename by
itself. ``Warehouse Map`` is in ``ignore_links_on_delete`` (``hooks.py``), so a map never stops
somebody deleting an empty bin; the box left behind is drawn as "no longer a warehouse" in the
editor and refused on the next save until it is removed or re-tied.

The class name is what Frappe derives from the DocType's name (spaces stripped, no re-casing);
``tests/test_doctype_controller_names.py`` holds it to that.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as rules


class WarehouseMap(Document):
	def validate(self):
		problem = rules.map_name_problem(self.map_name) or rules.background_problem(self.background)
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

	def _placed_elsewhere(self):
		"""``{warehouse: map}`` for every box on every other map."""
		rows = frappe.get_all(
			"Warehouse Map Shape",
			filters={"parenttype": "Warehouse Map", "parent": ["!=", self.name or ""]},
			fields=["parent", "warehouse"],
		)
		return {row.warehouse: row.parent for row in rows if row.warehouse}
