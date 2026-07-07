# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Product Configuration — one priced configuration of a product.

The frappe<->engine bridge is ``recompute()``: it reads the product definition,
derives the selections from the options child table, runs the pure engine
(pricing + part number + parts explosion + build-step resolution) and persists
everything as read-only fields and child rows. Print formats and the ERPNext
generation layer only ever read those persisted rows — no engine calls at
print/generate time.

``sync_option_rows()`` makes the options table self-healing: rows are rebuilt
from the product's current definition on every save, carrying the user's
selections over by (option_key, choice_code) — so a product edit (new option,
renamed label) propagates without stranding old configurations, and a bare
insert with just ``product`` set yields the product's default configuration.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from erpnext_enhancements.product_configurator.engine import (
	explode_parts,
	price_configuration,
	render_build_steps,
)


class ProductConfiguration(Document):
	def validate(self):
		if not self.company:
			self.company = frappe.defaults.get_global_default("default_company")
		self.sync_option_rows()
		self.recompute()
		if not (self.config_title or "").strip():
			self.config_title = self.part_number

	# ------------------------------------------------------------- options
	def sync_option_rows(self):
		"""Rebuild the options table from the product definition, keeping user input."""
		product = self._product_doc()
		previous = {
			(row.option_key, row.choice_code or ""): row for row in (self.options or [])
		}

		# Choice selection is reconciled PER GROUP, not per row: a selection
		# carried from the saved rows always beats the definition's default,
		# so moving the default to a new choice on the product can neither
		# flip nor double-select an existing configuration. A carried
		# selection whose choice was removed falls back to the default.
		carried_choice = {}
		default_choice = {}
		for opt in product.options:
			if opt.option_type != "Choice":
				continue
			code = str(opt.choice_code or "")
			old = previous.get((opt.option_key, code))
			if old is not None and old.selected and opt.option_key not in carried_choice:
				carried_choice[opt.option_key] = code
			if opt.is_default or opt.option_key not in default_choice:
				default_choice[opt.option_key] = code

		rows = []
		for opt in product.options:
			if opt.option_type == "Base":
				continue  # the base module prices implicitly; nothing to choose
			if opt.option_type == "Choice":
				code = str(opt.choice_code or "")
				group_selection = carried_choice.get(
					opt.option_key, default_choice.get(opt.option_key)
				)
				selected = 1 if code == group_selection else 0
				qty = 0
			else:
				old = previous.get((opt.option_key, ""))
				qty = int(opt.default_qty or 0) if old is None else int(old.qty or 0)
				selected = 0
				code = ""
			rows.append(
				{
					"option_key": opt.option_key,
					"option_type": opt.option_type,
					"option_label": opt.option_label,
					"choice_code": code,
					"choice_label": opt.choice_label or "",
					"module_key": opt.module_key,
					"selected": selected,
					"qty": qty,
				}
			)
		self.set("options", rows)

	def selections(self):
		"""{option_key: choice_code str | qty int} from the options child rows."""
		selections = {}
		for row in self.options:
			if row.option_type == "Choice":
				if row.selected:
					if row.option_key in selections:
						frappe.throw(
							_("Select only one {0}.").format(row.option_label),
							title=_("Multiple Choices Selected"),
						)
					selections[row.option_key] = row.choice_code
			elif row.option_type == "Quantity":
				selections[row.option_key] = int(row.qty or 0)
		return selections

	# ------------------------------------------------------------- bridge
	def recompute(self):
		"""Run the pure engine and persist pricing, parts and build steps."""
		product = self._product_doc()
		product_dict = product_as_engine_dict(product)

		try:
			result = price_configuration(
				product_dict,
				self.selections(),
				additional={
					"description": self.additional_description,
					"cost": flt(self.additional_cost),
				},
			)
		except ValueError as e:
			frappe.throw(str(e), title=_("Invalid Configuration"))

		steps, step_warnings = render_build_steps(product_dict["build_steps"], result["context"])
		parts = explode_parts(product_dict["components"], result["module_qtys"])

		line_price_by_module = {ln["module_key"]: ln["line_price"] for ln in result["lines"]}
		for row in self.options:
			row.line_price = line_price_by_module.get(row.module_key, 0)

		self.part_number = result["part_number"]
		self.total_parts_cost = flt(result["total_parts_cost"], 2)
		self.total_labor_hours = result["total_labor_hours"]
		self.total_labor_cost = flt(result["total_labor_cost"], 2)
		self.total_cost = flt(result["total_cost"], 2)
		self.sell_price = flt(result["total_price"], 2)
		self.sell_price_exact = result["total_price"]
		self.warnings_text = "\n".join(result["warnings"] + step_warnings)

		self.set(
			"price_lines",
			[
				{
					"module_key": ln["module_key"],
					"module_label": ln["module_label"],
					"qty": ln["qty"],
					"unit_parts_cost": ln["unit_parts_cost"],
					"unit_labor_cost": ln["unit_labor_cost"],
					"unit_cost": ln["unit_cost"],
					"unit_price": ln["unit_price"],
					"line_cost": ln["line_cost"],
					"line_price": ln["line_price"],
				}
				for ln in result["lines"]
			],
		)
		self.set(
			"parts",
			[
				{
					"module_key": p["module_key"],
					"component_name": p["component_name"],
					"item_code": p["item_code"],
					"qty": p["qty"],
					"uom": p["uom"],
					"unit_cost": p["unit_cost"],
					"amount": p["amount"],
					"supplier_name": p["supplier_name"],
					"manufacturer": p["manufacturer"],
					"manufacturer_part_no": p["manufacturer_part_no"],
				}
				for p in parts
			],
		)
		self.set("build_steps", steps)

	def mark_generated(self, item_code, bom_name, item_price_name):
		"""Stamp the generated-record links (inside the caller's transaction)."""
		self.db_set("item", item_code, update_modified=False)
		self.db_set("bom", bom_name, update_modified=False)
		self.db_set("item_price", item_price_name, update_modified=False)
		self.db_set("generated_on", now_datetime(), update_modified=False)

	def _product_doc(self):
		if not self.product:
			frappe.throw(_("Select a Configurable Product first."))
		product = frappe.get_cached_doc("Configurable Product", self.product)
		if product.disabled:
			frappe.throw(_("{0} is disabled.").format(product.product_name))
		return product


def product_as_engine_dict(product):
	"""Convert a Configurable Product doc to the plain dict the engine expects."""
	return {
		"product_code": product.product_code,
		"product_name": product.product_name,
		"labor_rate": flt(product.labor_rate),
		"markup_percent": flt(product.markup_percent),
		"part_number_template": product.part_number_template,
		"options": [row.as_dict() for row in product.options],
		"components": [row.as_dict() for row in (product.components or [])],
		"build_steps": [row.as_dict() for row in (product.build_steps or [])],
	}
