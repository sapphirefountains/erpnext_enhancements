# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every guest-callable endpoint is listed in a register, with who calls it and what checks it.

Bench-free, AST only. ``@frappe.whitelist(allow_guest=True)`` makes a function callable with no
sign-in at all, so each one needs a stated caller and a stated check.
``data/guest_endpoints.json`` holds one entry per guest endpoint (dotted path to
``{"caller": ..., "check": ...}``).

The test fails when

* a guest endpoint exists in the code and is missing from the register,
* the register names an endpoint that no longer exists or is no longer guest-callable, or
* an entry lacks a non-empty ``caller`` or ``check``.

Adding a guest endpoint therefore means adding a line to the register in the same change, which
is where its authentication gets written down and reviewed. Removing one means removing its line.
"""

import ast
import json
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
REGISTER = Path(__file__).resolve().parent / "data" / "guest_endpoints.json"


def _is_guest(decorator):
	"""True when the decorator is ``frappe.whitelist(...)`` with ``allow_guest`` that is not plainly false."""
	if not isinstance(decorator, ast.Call):
		return False
	if ast.unparse(decorator.func) not in ("frappe.whitelist", "whitelist"):
		return False
	for keyword in decorator.keywords:
		if keyword.arg != "allow_guest":
			continue
		value = keyword.value
		if isinstance(value, ast.Constant):
			return bool(value.value)
		# Not a literal: count it as guest-callable so it has to be registered and explained.
		return True
	return False


def guest_endpoints():
	"""Dotted paths of every guest-callable whitelisted function in the app, outside ``tests/``."""
	found = set()
	for path in sorted(PKG.rglob("*.py")):
		relative = path.relative_to(PKG.parent)
		if "tests" in relative.parts:
			continue
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except SyntaxError:
			continue
		module = ".".join(relative.with_suffix("").parts)
		if module.endswith(".__init__"):
			module = module[: -len(".__init__")]
		for node in ast.walk(tree):
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
				_is_guest(d) for d in node.decorator_list
			):
				found.add(f"{module}.{node.name}")
	return found


def load_register():
	with open(REGISTER, encoding="utf-8") as handle:
		return json.load(handle)


class GuestEndpointRegisterTest(unittest.TestCase):
	def test_every_guest_endpoint_is_registered(self):
		missing = sorted(guest_endpoints() - set(load_register()))
		self.assertEqual(
			missing,
			[],
			"Guest endpoints missing from tests/data/guest_endpoints.json "
			"(add caller and check for each): " + ", ".join(missing),
		)

	def test_register_names_only_existing_guest_endpoints(self):
		stale = sorted(set(load_register()) - guest_endpoints())
		self.assertEqual(
			stale,
			[],
			"Register entries with no matching guest endpoint (remove them): " + ", ".join(stale),
		)

	def test_every_entry_states_caller_and_check(self):
		problems = []
		for path, entry in sorted(load_register().items()):
			if not isinstance(entry, dict):
				problems.append(f"{path}: entry is not an object")
				continue
			for field in ("caller", "check"):
				value = entry.get(field)
				if not isinstance(value, str) or not value.strip():
					problems.append(f"{path}: missing {field}")
		self.assertEqual(problems, [], "; ".join(problems))

	def test_the_scan_finds_endpoints(self):
		# A scan that silently matches nothing would pass the first two tests against an empty register.
		self.assertGreater(len(guest_endpoints()), 20)


if __name__ == "__main__":
	unittest.main()
