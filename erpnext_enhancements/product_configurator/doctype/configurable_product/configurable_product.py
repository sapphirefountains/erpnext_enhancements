# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Configurable Product — the generic product definition.

Validation keeps the definition internally consistent so a Product
Configuration can never hit a half-broken template at pricing time: the
part-number template must resolve from the options, every Choice group needs
exactly one default, and module keys referenced by components/steps must
exist. The pricing math itself lives in the pure engine.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.product_configurator.engine import (
	ConditionError,
	build_context,
	build_part_number,
)


class ConfigurableProduct(Document):
	def validate(self):
		self._validate_option_rows()
		self._validate_template()
		self._warn_on_unknown_module_keys()

	def default_selections(self):
		"""{option_key: default choice_code / default qty} for this definition."""
		selections = {}
		for row in self.options:
			if row.option_type == "Choice":
				if row.is_default or row.option_key not in selections:
					selections[row.option_key] = str(row.choice_code)
			elif row.option_type == "Quantity":
				selections[row.option_key] = int(row.default_qty or 0)
		return selections

	def _validate_option_rows(self):
		# One costed module per key: only Choice rows may share an option_key
		# (they form the pick-one group). A duplicated Quantity/Base key would
		# silently price both rows and make the config grid last-row-wins.
		types_by_key = {}
		for row in self.options:
			types_by_key.setdefault(row.option_key, []).append(row.option_type)
		for key, types in types_by_key.items():
			if len(types) > 1 and any(t != "Choice" for t in types):
				frappe.throw(
					_(
						"Option key {0} is used by {1} rows — only Choice rows may "
						"share a key (one row per choice)."
					).format(frappe.bold(key), len(types))
				)

		defaults_per_choice = {}
		for row in self.options:
			if row.option_type == "Choice":
				if not (row.choice_code or "").strip():
					frappe.throw(
						_("Row {0}: Choice options need a Choice Code.").format(row.idx)
					)
				group = defaults_per_choice.setdefault(row.option_key, {"defaults": 0, "codes": set()})
				code = str(row.choice_code).strip()
				if code in group["codes"]:
					frappe.throw(
						_("Option {0}: duplicate choice code {1}.").format(row.option_key, code)
					)
				group["codes"].add(code)
				group["defaults"] += 1 if row.is_default else 0
			elif row.option_type == "Quantity":
				if row.max_qty and int(row.min_qty or 0) > int(row.max_qty):
					frappe.throw(
						_("Option {0}: Min Qty exceeds Max Qty.").format(row.option_key)
					)

		for key, group in defaults_per_choice.items():
			if group["defaults"] != 1:
				frappe.throw(
					_("Choice option {0} needs exactly one Default Choice (has {1}).").format(
						key, group["defaults"]
					)
				)

	def _validate_template(self):
		options = [row.as_dict() for row in self.options]
		try:
			ctx = build_context(options, self.default_selections())
			build_part_number(self.part_number_template, ctx)
		except (ConditionError, ValueError) as e:
			frappe.throw(
				_("Part-number template does not resolve from the options: {0}").format(e),
				title=_("Invalid Template"),
			)

	def _warn_on_unknown_module_keys(self):
		known = {row.module_key for row in self.options if row.module_key}
		orphans = sorted(
			{
				row.module_key
				for row in (self.components or [])
				if row.module_key and row.module_key not in known
			}
		)
		if orphans:
			frappe.msgprint(
				_("Component module keys with no matching option module: {0}. "
				"Their parts will never appear in a configuration.").format(", ".join(orphans)),
				indicator="orange",
			)
