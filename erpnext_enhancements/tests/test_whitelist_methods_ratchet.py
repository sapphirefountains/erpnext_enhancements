# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every whitelisted endpoint declares the HTTP methods it accepts. Bench-free, AST only.

``@frappe.whitelist()`` with no ``methods`` argument answers GET, POST, PUT and DELETE.
A state-changing endpoint should say ``methods=["POST"]`` so a plain link or an image tag
cannot invoke it, and a read endpoint that is meant to be fetched with GET should say
``methods=["GET"]`` (or ``["GET", "POST"]``) so the choice is visible in review.

This suite is a ratchet. ``data/whitelist_methods_baseline.txt`` lists, one per line and
sorted, every endpoint that does NOT declare ``methods=`` today. The suite fails when:

* an endpoint without ``methods=`` is missing from the baseline: declare
  ``methods=[...]`` explicitly on new endpoints rather than extending the baseline; or
* the baseline names an endpoint that now declares ``methods=`` or no longer exists:
  remove that line from the baseline.

So the baseline can only shrink. Names are ``dotted.module.function`` (or
``dotted.module.Class.method`` for a controller method).

Regenerate the baseline after declaring methods on some endpoints (or after a rename)::

    python -m erpnext_enhancements.tests.test_whitelist_methods_ratchet --write

Run: python -m unittest erpnext_enhancements.tests.test_whitelist_methods_ratchet
"""

import ast
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BASELINE = APP / "tests" / "data" / "whitelist_methods_baseline.txt"

#: The tests tree defines stubs and fixtures, not endpoints.
SKIP_PARTS = frozenset({"tests", "node_modules", "__pycache__", ".git"})

HEADER = (
	"# Whitelisted endpoints that do not declare `methods=`, one per line, sorted.\n"
	"# This list can only shrink: declare `methods=[...]` on a new endpoint instead of adding it here.\n"
	"# Regenerate with: python -m erpnext_enhancements.tests.test_whitelist_methods_ratchet --write\n"
	"# See tests/test_whitelist_methods_ratchet.py.\n"
)


def _sources():
	for path in sorted(APP.rglob("*.py")):
		if SKIP_PARTS.intersection(path.relative_to(APP).parts):
			continue
		yield path


def _module_name(path):
	parts = list(path.relative_to(APP.parent).with_suffix("").parts)
	if parts[-1] == "__init__":
		parts.pop()
	return ".".join(parts)


def _whitelist_call(decorator):
	"""Return ``(is_whitelist, declares_methods)`` for one decorator node."""
	target = decorator.func if isinstance(decorator, ast.Call) else decorator
	if ast.unparse(target) not in ("frappe.whitelist", "whitelist"):
		return False, False
	declares = isinstance(decorator, ast.Call) and any(kw.arg == "methods" for kw in decorator.keywords)
	return True, declares


def _walk(node, prefix):
	for child in ast.iter_child_nodes(node):
		if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
			for decorator in child.decorator_list:
				is_whitelist, declares = _whitelist_call(decorator)
				if is_whitelist:
					yield f"{prefix}.{child.name}", declares
			# A function nested in a function is not an endpoint.
		elif isinstance(child, ast.ClassDef):
			yield from _walk(child, f"{prefix}.{child.name}")
		elif isinstance(child, (ast.If, ast.Try)):
			yield from _walk(child, prefix)


def endpoints():
	"""``{dotted name: declares_methods}`` for every whitelisted function in the app."""
	found = {}
	for path in _sources():
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue
		for name, declares in _walk(tree, _module_name(path)):
			# A name defined twice counts as declared only if every definition declares.
			found[name] = found.get(name, True) and declares
	return found


def undeclared():
	return sorted(name for name, declares in endpoints().items() if not declares)


def read_baseline():
	text = BASELINE.read_text(encoding="utf-8")
	return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


class TestWhitelistMethodsRatchet(unittest.TestCase):
	def test_no_new_endpoint_without_declared_methods(self):
		baseline = set(read_baseline())
		new = [name for name in undeclared() if name not in baseline]
		self.assertEqual(
			new,
			[],
			"These whitelisted endpoints do not declare `methods=`. Declare "
			'`@frappe.whitelist(methods=["POST"])` (or `["GET"]` for a read) on new '
			"endpoints instead of adding them to the baseline:\n  " + "\n  ".join(new),
		)

	def test_baseline_has_no_stale_entries(self):
		current = set(undeclared())
		stale = [name for name in read_baseline() if name not in current]
		self.assertEqual(
			stale,
			[],
			"The baseline names endpoints that now declare `methods=` or no longer exist. "
			"Remove them from tests/data/whitelist_methods_baseline.txt (or regenerate it "
			"with `python -m erpnext_enhancements.tests.test_whitelist_methods_ratchet "
			"--write`):\n  " + "\n  ".join(stale),
		)

	def test_baseline_is_sorted_and_unique(self):
		lines = read_baseline()
		self.assertEqual(lines, sorted(set(lines)), "the baseline must be sorted with no duplicates")

	def test_the_scan_reaches_the_endpoint_modules(self):
		"""An absence rule over an empty corpus passes forever; pin that the walk is real."""
		found = endpoints()
		self.assertGreater(len(found), 500, "endpoint walk collapsed -- the ratchet proves nothing")
		self.assertTrue(
			found.get("erpnext_enhancements.stripe_payments.core.api.refund_payment"),
			"refund_payment is expected to declare methods",
		)
		self.assertTrue(
			found.get("erpnext_enhancements.api.time_kiosk.log_time"),
			"log_time is expected to declare methods",
		)


if __name__ == "__main__":
	if "--write" in sys.argv:
		names = undeclared()
		BASELINE.parent.mkdir(parents=True, exist_ok=True)
		with open(BASELINE, "w", encoding="utf-8", newline="\n") as handle:
			handle.write(HEADER + "\n".join(names) + "\n")
		print(f"wrote {len(names)} endpoints to {BASELINE}")
	else:
		unittest.main()
