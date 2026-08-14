"""Private files are never served inline when the browser would execute them.

**Bench-free, `unittest`.** `monkeypatches.py` imports `frappe`, so this suite installs a stub
set in `setUpModule` — the house pattern, and the reason each bench-free suite gets its own
`ci.yml` step: two suites' stubs in one process cross-talk, and this repo has lost a suite to
exactly that.

WHAT IS BEING GUARDED
=====================

Frappe v16 force-downloads four extensions from `/private/files/…` and serves everything else
`inline`. Frappe's `develop` widened that list to fourteen. The seven in the gap — `.xhtml`,
`.svgz`, `.shtml`, `.mhtml`, `.xsl`, `.xslt`, `.swf` — are served from **our own origin** with a
scriptable `Content-Type` and `Content-Disposition: inline`. Anyone who can attach a file to
anything can upload one, and the viewer's session cookies go along.

The patch widens the tuple and adds `nosniff`, which Frappe sets nowhere in v16.

**These tests assert the patch, not Frappe.** A future `bench update` that fixes this upstream
makes them pass trivially rather than fail — which is correct: the patch is idempotent and
`setdefault` means an upstream header wins. What they *do* catch is somebody deleting the patch,
or trimming an extension out of the list because it "looks like an image format".
"""

from __future__ import annotations

import sys
import types
import unittest

_STUBBED: list[str] = []


def setUpModule() -> None:
	"""Install the smallest `frappe` that `monkeypatches.py` can import."""
	for name in ("frappe", "frappe.utils", "frappe.utils.modules", "frappe.utils.response"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
			_STUBBED.append(name)

	frappe = sys.modules["frappe"]
	if not hasattr(frappe, "logger"):
		frappe.logger = lambda *_a, **_k: types.SimpleNamespace(warning=lambda *a, **k: None)

	utils = sys.modules["frappe.utils"]
	utils.modules = sys.modules["frappe.utils.modules"]
	utils.response = sys.modules["frappe.utils.response"]
	frappe.utils = utils

	modules = sys.modules["frappe.utils.modules"]
	if not hasattr(modules, "get_modules_from_app"):
		modules.get_modules_from_app = lambda app: []


def tearDownModule() -> None:
	for name in _STUBBED:
		sys.modules.pop(name, None)


class _FakeHeaders(dict):
	"""Just enough of werkzeug's Headers for `setdefault` to mean what it means there."""


class _FakeResponse:
	def __init__(self) -> None:
		self.headers = _FakeHeaders()


def _fresh_response_module():
	"""A `frappe.utils.response` in its unpatched v16 shape, for each test to patch alone."""
	response = sys.modules["frappe.utils.response"]
	response.FORCE_DOWNLOAD_EXTENSIONS = (".svg", ".html", ".htm", ".xml")

	def send_private_file(path, **_kwargs):
		send_private_file.calls.append(path)
		return _FakeResponse()

	send_private_file.calls = []
	response.send_private_file = send_private_file
	return response


class PrivateFileServingTest(unittest.TestCase):
	def setUp(self) -> None:
		self.response = _fresh_response_module()
		from erpnext_enhancements import monkeypatches

		self.monkeypatches = monkeypatches

	def _apply(self) -> None:
		self.monkeypatches._patch_private_files_are_never_served_inline()

	def test_the_v16_gap_is_real_before_the_patch(self) -> None:
		"""The premise. Without this, every other test here could pass against a no-op."""
		for extension in (".xhtml", ".svgz", ".shtml", ".mhtml", ".xsl", ".xslt", ".swf"):
			with self.subTest(extension=extension):
				self.assertNotIn(
					extension,
					self.response.FORCE_DOWNLOAD_EXTENSIONS,
					"stock v16 already covers this — the patch may no longer be needed",
				)

	def test_every_executable_extension_is_force_downloaded_after_the_patch(self) -> None:
		self._apply()
		for extension in self.monkeypatches._EXECUTABLE_ON_VIEW_EXTENSIONS:
			with self.subTest(extension=extension):
				self.assertIn(extension, self.response.FORCE_DOWNLOAD_EXTENSIONS)

	def test_svg_is_on_the_list_and_stays_there(self) -> None:
		"""Pinned by name because it is the entry somebody tidies away.

		An SVG is an image that runs script. In a list otherwise full of `.html` and `.xslt` it
		reads like a mistake, and removing it reopens the exact hole the list exists to close.
		"""
		self.assertIn(".svg", self.monkeypatches._EXECUTABLE_ON_VIEW_EXTENSIONS)
		self.assertIn(".svgz", self.monkeypatches._EXECUTABLE_ON_VIEW_EXTENSIONS)

	def test_the_patch_keeps_frappes_own_entries(self) -> None:
		"""Widening, not replacing. A tuple assignment would silently drop a future addition."""
		self._apply()
		for extension in (".svg", ".html", ".htm", ".xml"):
			with self.subTest(extension=extension):
				self.assertIn(extension, self.response.FORCE_DOWNLOAD_EXTENSIONS)

	def test_nosniff_is_set_on_the_response(self) -> None:
		self._apply()
		result = self.response.send_private_file("/private/files/x.png")
		self.assertEqual(result.headers.get("X-Content-Type-Options"), "nosniff")

	def test_the_wrapper_still_serves_the_file(self) -> None:
		"""A patch that adds a header and eats the response is worse than no patch."""
		self._apply()
		inner = self.response.send_private_file
		self.response.send_private_file("/private/files/report.pdf")
		self.assertEqual(inner.__wrapped__.calls, ["/private/files/report.pdf"])

	def test_an_upstream_nosniff_wins(self) -> None:
		"""`setdefault`, not assignment.

		If a future Frappe sets this header itself, ours must not be the value that survives —
		a patch that silently overrides an upstream fix is how the upstream fix gets blamed for
		behaviour it does not have.
		"""

		def send_private_file_with_header(path, **_kwargs):
			response = _FakeResponse()
			response.headers["X-Content-Type-Options"] = "nosniff; upstream"
			return response

		self.response.send_private_file = send_private_file_with_header
		self._apply()
		result = self.response.send_private_file("/private/files/x.png")
		self.assertEqual(result.headers.get("X-Content-Type-Options"), "nosniff; upstream")

	def test_applying_twice_does_not_double_wrap_or_duplicate(self) -> None:
		"""`apply()` runs once per process, but "once" is a claim about hook loading.

		Every patch in this module is documented as idempotent, and this is the one that would
		fail loudly if it were not: a doubled tuple is harmless, but a doubly-wrapped function
		grows a frame on every call for as long as the process lives.
		"""
		self._apply()
		first = self.response.send_private_file
		before = len(self.response.FORCE_DOWNLOAD_EXTENSIONS)

		self._apply()

		self.assertIs(self.response.send_private_file, first, "the wrapper was applied twice")
		self.assertEqual(len(self.response.FORCE_DOWNLOAD_EXTENSIONS), before)

	def test_apply_runs_this_patch(self) -> None:
		"""It is registered in `_PATCHES`, not merely defined."""
		self.assertIn(
			self.monkeypatches._patch_private_files_are_never_served_inline,
			self.monkeypatches._PATCHES,
		)
		self.monkeypatches.apply()
		self.assertIn(".xhtml", self.response.FORCE_DOWNLOAD_EXTENSIONS)


if __name__ == "__main__":
	unittest.main()
