"""A failed ``@triton`` turn must record WHY, not just that it failed.

**Bench-free, `unittest`, own stub set in `setUpModule`, own `ci.yml` step** — the house
pattern. `chat/invoke/handler.py` reaches the invocation-log controller at import, so this
needs `frappe.model.document` as well as `frappe.utils`; that is exactly why each suite owns
its stubs rather than borrowing another's.

THE BUG
=======

A real turn failed on production and the invocation log said, in full:

    model turn failed: TritonUnavailable

That names the symptom and hides the cause. Every raise site of `TritonUnavailable` writes a
deliberately content-free sentence, and each points at a different file:

* "The Triton assistant is switched off in ERPNext."          → a setting
* "Triton Settings has no Gateway URL, so there is nothing to ask." → configuration
* "could not mint a Triton token for this identity: PermissionError" → the OAuth link
* "Could not reach Triton: ConnectionError"                    → the network or the service

The handler recorded `exc.__class__.__name__` and collapsed all four into one word.
`triton_client`'s own comment says why that matters: the class is *"named, because 'the model
was unavailable' for a permission problem sent us looking at the model for an hour."* The
author made the message safe to log, and the logger dropped it.

AND THE HALF THAT KEEPS IT SAFE
===============================

**Only `TritonUnavailable` gets its message recorded.** Its docstring guarantees it never
carries a token, and its messages are written to be content-free. Nothing else makes that
promise — an arbitrary exception's `str()` can carry a prompt, a database row or a credential,
and this table is read by operators through the health report. Everything else still records
the class name alone, and there is a test for that specifically.
"""

from __future__ import annotations

import sys
import types
import unittest

_STUBBED: list[str] = []


def setUpModule() -> None:
	for name in ("frappe", "frappe.utils", "frappe.model", "frappe.model.document"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
			_STUBBED.append(name)

	frappe = sys.modules["frappe"]
	utils = sys.modules["frappe.utils"]

	def _cint(value: object) -> int:
		try:
			return int(float(value))  # type: ignore[arg-type]
		except (TypeError, ValueError):
			return 0

	utils.cint = _cint
	utils.now_datetime = lambda: "2026-08-14 10:23:25"
	utils.now = lambda: "2026-08-14 10:23:25"
	utils.get_datetime = lambda value=None: value
	utils.get_datetime_str = lambda value=None: str(value)
	utils.cstr = lambda value=None: "" if value is None else str(value)
	utils.escape_html = lambda value="": value
	frappe.utils = utils
	frappe.cint = _cint

	frappe.session = types.SimpleNamespace(user="Administrator")
	frappe.flags = types.SimpleNamespace()
	frappe.log_error = lambda *_a, **_k: None
	frappe._ = lambda text: text
	frappe.throw = lambda *_a, **_k: None
	frappe.get_all = lambda *_a, **_k: []
	frappe.new_doc = lambda *_a, **_k: types.SimpleNamespace()
	frappe.db = types.SimpleNamespace(
		get_value=lambda *_a, **_k: None,
		set_value=lambda *_a, **_k: None,
		exists=lambda *_a, **_k: None,
		get_single_value=lambda *_a, **_k: None,
	)

	document = sys.modules["frappe.model.document"]
	if not hasattr(document, "Document"):
		document.Document = object


def tearDownModule() -> None:
	for name in _STUBBED:
		sys.modules.pop(name, None)


class TheModelFailureDetailIsRecorded(unittest.TestCase):
	def setUp(self) -> None:
		from erpnext_enhancements.chat.invoke import handler
		from erpnext_enhancements.chat.invoke.triton_client import TritonUnavailable

		self.detail = handler._model_failure_detail
		self.TritonUnavailable = TritonUnavailable

	def test_THE_REASON_SURVIVES_instead_of_just_the_class_name(self) -> None:
		"""The four messages, each pointing somewhere different."""
		for message in (
			"The Triton assistant is switched off in ERPNext.",
			"Triton Settings has no Gateway URL, so there is nothing to ask.",
			"could not mint a Triton token for this identity: PermissionError",
			"Could not reach Triton: ConnectionError",
		):
			with self.subTest(message=message):
				got = self.detail(self.TritonUnavailable(message))
				self.assertIn(message, got)
				self.assertNotEqual(
					got,
					"TritonUnavailable",
					"the class name alone is what made a production failure undiagnosable",
				)

	def test_status_and_code_are_appended_when_present(self) -> None:
		"""Both are machine-readable by design, and together they are what separates
		"Triton is down" from "this person needs to link ERPNext"."""
		got = self.detail(self.TritonUnavailable("refused", status=503, code="upstream_down"))
		self.assertIn("status=503", got)
		self.assertIn("code=upstream_down", got)

	def test_a_zero_status_is_not_recorded_as_noise(self) -> None:
		"""`status` is 0 whenever the failure was before or after the response, which is most
		of them. Printing `status=0` on every row trains people to stop reading the field."""
		self.assertNotIn("status=", self.detail(self.TritonUnavailable("switched off")))

	def test_ANY_OTHER_EXCEPTION_RECORDS_ONLY_ITS_CLASS(self) -> None:
		"""The guard, and the reason this is not simply `str(exc)`.

		An arbitrary exception's message can carry the prompt, a database row or a credential,
		and this table is surfaced to operators by the health report.
		"""
		for exc in (
			ValueError("a raw prompt the model was sent"),
			RuntimeError("Bearer ya29.a0AfH6SMBx-not-a-real-token"),
			KeyError("customer_ssn"),
		):
			with self.subTest(exc=exc.__class__.__name__):
				got = self.detail(exc)
				self.assertEqual(got, exc.__class__.__name__)
				self.assertNotIn("Bearer", got)
				self.assertNotIn("prompt", got)

	def test_an_empty_message_falls_back_to_the_class_name(self) -> None:
		"""Better a class name than an empty `error`, which reads as "no reason given" and is
		indistinguishable from a row nothing wrote."""
		self.assertEqual(self.detail(self.TritonUnavailable("")), "TritonUnavailable")

	def test_the_handler_actually_calls_it(self) -> None:
		"""A helper nothing calls is a helper that changes nothing.

		Source-level, because exercising the real `except` branch needs a bench.
		"""
		import pathlib

		from erpnext_enhancements.chat.invoke import handler

		source = pathlib.Path(handler.__file__).read_text(encoding="utf-8")
		self.assertIn("_model_failure_detail(exc)", source)
		self.assertNotIn(
			'error=f"model turn failed: {exc.__class__.__name__}"',
			source,
			"the handler is back to recording only the class name",
		)


if __name__ == "__main__":
	unittest.main()
