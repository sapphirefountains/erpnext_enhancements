# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The chat clients survive a 429. Bench-free, and a source scan because there is no JS runner.

This repo has no JavaScript test framework — `package.json` declares `lint` and nothing else —
so the established idiom for a JS invariant is a Python scan, the same shape
`scripts/check_www_controllers.py` and the realtime guards use.

--------------------------------------------------------------------------------------
Why this exists, and why it is not waiting on a rate limit being added
--------------------------------------------------------------------------------------

**A 429 can already reach these clients.** `frappe.rate_limiter` is wired into the request
lifecycle in `frappe/app.py` (`:91`, `:262`, `:405`), so `frappe.conf.rate_limit` applies a
**global** limiter to every request whether or not any endpoint carries a decorator — and
whatever sits in front of the app can produce one too. `TooManyRequestsError` and
`RateLimitExceededError` are both `http_status_code = 429`.

Before v1.294.1 no chat client mentioned 429 anywhere, and four paths failed **silently**:

* **presence** swallowed it and kept beating at full cadence, so the bucket never recovered
  and the tab spent the window absent — which reads to the person as notification spam;
* **the read mark** was *lost*: `take()` advances `emitted` before the POST and `shouldFlush`
  requires `candidate > emitted`, so the next flush carried nothing until strictly newer
  traffic arrived. The `catch` even asserted the opposite in a comment;
* **push** memoised the failed *promise*, disabling notifications for the tab's whole session;
* **a refused room open** left the tab joined to no socket room.

The first three are fixed and asserted below. The fourth is named in the module docstring of
`chat/endpoints.py` and is not fixed here — see :class:`WhatIsNotFixedTest`.
"""

import pathlib
import re
import unittest

_JS = pathlib.Path(__file__).resolve().parents[1] / "public" / "js" / "chat"
TRANSPORT = _JS / "transport.js"
SIGNALS = _JS / "signals.js"
PUSH = _JS / "push.js"
APP = _JS / "app.js"

#: Block comments and line comments, so an assertion cannot pass on prose. Six text-matching
#: assertions in this series have flagged a comment rather than code, every one of them the
#: sentence explaining why a thing is absent — and every file here discusses 429 at length.
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE = re.compile(r"^\s*//.*$", re.MULTILINE)


def _code(path: pathlib.Path) -> str:
	text = path.read_text(encoding="utf-8")
	return _LINE.sub("", _BLOCK.sub("", text))


class TheScanActuallyScansTest(unittest.TestCase):
	def test_the_files_exist_and_have_content(self):
		for path in (TRANSPORT, SIGNALS, PUSH, APP):
			with self.subTest(file=path.name):
				self.assertGreater(len(_code(path)), 500)

	def test_comment_stripping_removes_prose_but_keeps_code(self):
		"""Both directions. If stripping removed everything, every assertion below passes."""
		stripped = _code(TRANSPORT)
		self.assertIn("ChatCallError", stripped)
		self.assertNotIn("notification spam", stripped)


class TransportTest(unittest.TestCase):
	def test_a_429_is_recognisable_to_callers(self):
		"""Without a flag every caller has to compare a magic number, and three of them
		did not compare anything at all."""
		self.assertIn("this.throttled = status === 429", _code(TRANSPORT))

	def test_the_flag_sits_beside_the_ones_that_already_existed(self):
		code = _code(TRANSPORT)
		for existing in ("this.forbidden = status === 403", "this.missing = status === 404"):
			self.assertIn(existing, code)

	def test_a_throttle_has_its_own_message(self):
		"""`Request failed (429)` is what the person saw before, which reads as a bug."""
		self.assertIn("429", _code(TRANSPORT).split("forbidden")[-1])


class ReadMarkTest(unittest.TestCase):
	"""The one that lost data."""

	def test_the_batcher_can_roll_its_cursor_back(self):
		self.assertIn("rollback()", _code(SIGNALS))

	def test_take_records_what_it_advanced_from(self):
		"""A rollback with nothing to roll back to is a no-op that reads as a fix."""
		self.assertIn("this.previousEmitted = this.emitted", _code(SIGNALS))

	def test_rollback_refuses_to_clobber_a_newer_acknowledgement(self):
		"""`acknowledge` may have raised `emitted` past our value from another tab of the same
		user. Overwriting that starts the two tabs sawing the mark back and forth, which is
		exactly what acknowledge's max-in-both-directions rule exists to prevent."""
		code = _code(SIGNALS)
		body = code[code.index("rollback()") :]
		self.assertIn("this.emitted === this.candidate", body)

	def test_the_failed_send_calls_it(self):
		"""The fix is worthless if the catch still does nothing."""
		code = _code(APP)
		self.assertIn("this.readBatcher.rollback()", code)

	def test_the_false_comment_is_gone(self):
		"""It said 'the next flush carries the same or a higher value; nothing is lost'. That
		was false, and believing it is what hid the bug for four phases. Asserted against the
		RAW text, deliberately — this is the one case where the comment is the thing."""
		raw = APP.read_text(encoding="utf-8")
		self.assertNotIn("nothing is lost", raw)


class PushTest(unittest.TestCase):
	def test_a_transient_failure_is_not_memoised(self):
		"""The promise is what is cached, so one refusal disabled push for the tab's whole
		session — silently, with nothing calling `attach()` again after boot."""
		code = _code(PUSH)
		self.assertIn("configPromise = null", code.split("function pushConfig")[-1])

	def test_the_reset_is_conditional_on_the_failure_being_transient(self):
		"""A genuine 'push is not configured' answer must still memoise, or every call retries
		a question whose answer will not change."""
		body = _code(PUSH).split("function pushConfig")[-1]
		self.assertIn("throttled", body)


class PresenceTest(unittest.TestCase):
	def test_a_throttled_beat_backs_off(self):
		code = _code(APP)
		self.assertIn("HEARTBEAT_BACKOFF_MAX_MS", code)

	def test_the_backoff_is_bounded(self):
		"""Unbounded doubling reaches hours, and presence never recovers on that tab."""
		code = _code(APP)
		self.assertIn("Math.min(", code[code.index("err.throttled") - 400 : code.index("err.throttled") + 400])

	def test_the_cap_is_declared_as_a_named_constant(self):
		self.assertIn("const HEARTBEAT_BACKOFF_MAX_MS", _code(APP))


class WhatIsNotFixedTest(unittest.TestCase):
	"""Named rather than implied, so a green run is not read as "429 is handled everywhere"."""

	def test_the_room_open_path_is_still_unhandled_and_recorded_as_such(self):
		"""A refused `get_room` leaves the tab joined to no socket room and swallows the error,
		and the refusal removes the `focus()` call that had been suppressing the key-repeat
		burst that caused it. Fixing it means restructuring `openRoom`'s failure path, which is
		its own change; what this asserts is that the fact is written down where somebody
		touching rate limits will read it."""
		endpoints = pathlib.Path(__file__).resolve().parents[1] / "chat" / "endpoints.py"
		text = endpoints.read_text(encoding="utf-8")
		self.assertIn("rooms.get_room", text)
		self.assertIn("focus()", text)


if __name__ == "__main__":
	unittest.main()
