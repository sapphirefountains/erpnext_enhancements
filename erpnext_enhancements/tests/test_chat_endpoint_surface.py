"""The chat HTTP surface is enumerated, classified, and POST-only where it mutates.

**Bench-free, `unittest`, and it must stay that way** — this repo has no Frappe integration job
in CI, so a bench-required version of this test would run nowhere and protect nothing.
`chat/endpoints.py` is deliberately import-clean (`ast` and `pathlib` only) so this suite can
read it without `frappe` on the path.

WHAT EACH TEST IS FOR
=====================

* **The set equality** is the one that keeps the rest honest. A new `@frappe.whitelist()` in the
  chat package fails this suite until somebody classifies it, which means the classification
  cannot silently fall behind the code. A list nothing compares against the source is the same
  bug as no list, arriving later and with more confidence.
* **The POST rule** is Phase 6 §4.G.5. A GET that mutates is CSRF-able whatever the token
  handling does. The SPA's transport already POSTs everything
  (`public/js/chat/transport.js:92`), so declaring it costs nothing and closes the hole for
  every *other* caller — a link, a prefetch, an `<img src>`, a crawler.
* **The guest census** is §4.G.5's "there should be exactly the two, and if there are more,
  justify each". Today there is exactly one.

Verified non-vacuous the way this repo verifies things: each assertion was run against a
deliberately broken tree first — a new whitelisted function added and not classified, a
`methods=` argument deleted, a name moved between the two dicts — and each went red.
"""

from __future__ import annotations

import unittest

from erpnext_enhancements.chat import endpoints


class ChatEndpointSurfaceTest(unittest.TestCase):
	"""`chat/endpoints.py` describes the surface that actually exists."""

	@classmethod
	def setUpClass(cls) -> None:
		cls.discovered = endpoints.discover()
		cls.classified = endpoints.classified()

	def test_scanner_finds_a_plausible_surface(self) -> None:
		"""A scanner that silently found nothing would make every other test here pass.

		The floor is deliberately loose — this is a smoke check against a broken scanner (a
		renamed package, a changed decorator form), not a pin on the endpoint count, which is
		what the set equality is for.
		"""
		self.assertGreater(
			len(self.discovered),
			20,
			"the AST scan found almost nothing — chat/endpoints.py's scanner is broken, not the surface",
		)

	def test_every_endpoint_is_classified(self) -> None:
		"""Set equality both ways. **A new endpoint fails this test by default.**"""
		found = set(self.discovered)
		named = set(self.classified)

		unclassified = sorted(found - named)
		self.assertFalse(
			unclassified,
			"these @frappe.whitelist() functions are not in chat/endpoints.py.\n"
			"Add each to MUTATING (and give it methods=[\"POST\"]) or to NON_MUTATING, with a\n"
			"reason. Deciding which is the point of the exercise:\n  " + "\n  ".join(unclassified),
		)

		stale = sorted(named - found)
		self.assertFalse(
			stale,
			"these are classified in chat/endpoints.py but no longer exist. A stale entry is\n"
			"how the inventory starts describing a system nobody has:\n  " + "\n  ".join(stale),
		)

	def test_no_endpoint_is_in_both_dicts(self) -> None:
		"""`classified()` merges them, so an overlap would hide one reason behind the other."""
		overlap = sorted(set(endpoints.MUTATING) & set(endpoints.NON_MUTATING))
		self.assertFalse(overlap, f"classified in both MUTATING and NON_MUTATING: {overlap}")

	def test_every_reason_is_a_reason(self) -> None:
		"""An empty or one-word reason is an unclassified endpoint wearing a classification."""
		for dotted, reason in sorted(self.classified.items()):
			with self.subTest(endpoint=dotted):
				self.assertGreaterEqual(
					len(reason.split()),
					4,
					f"{dotted} needs a reason somebody can act on, not {reason!r}",
				)

	def test_every_mutating_endpoint_is_post_only(self) -> None:
		"""Phase 6 §4.G.5. The rule the whole file exists to enforce."""
		offenders = []
		for dotted in sorted(endpoints.MUTATING):
			endpoint = self.discovered.get(dotted)
			if endpoint is None:
				continue  # covered by test_every_endpoint_is_classified
			if tuple(m.upper() for m in endpoint.methods) != ("POST",):
				offenders.append(f"{dotted} ({endpoint.relpath}:{endpoint.lineno}) declares {endpoint.methods or 'no methods='}")

		self.assertFalse(
			offenders,
			"these endpoints change state and accept GET. A GET that mutates is CSRF-able\n"
			"regardless of token handling, and cacheable by intermediaries besides.\n"
			'Add methods=["POST"] to the @frappe.whitelist() call:\n  ' + "\n  ".join(offenders),
		)

	def test_download_stays_reachable_by_a_browser(self) -> None:
		"""The one endpoint that must NOT become POST-only, pinned so nobody tidies it.

		`sync.attachments.download` serves bytes to a URL a browser navigates to. Constraining
		it to POST would break every attachment link, image tag and right-click Save As, and it
		would do so in a way that reads as "attachments are broken" rather than as "somebody
		added a method restriction".
		"""
		download = self.discovered.get(f"{endpoints.DOTTED_ROOT}.sync.attachments.download")
		self.assertIsNotNone(download, "the attachment download endpoint has moved or been renamed")
		self.assertEqual(
			download.methods,
			(),
			"attachments.download must stay method-unconstrained — it is a URL, not a call",
		)

	def test_guest_endpoints_are_exactly_the_declared_ones(self) -> None:
		"""§4.G.5: enumerate them, and justify any beyond the expected set.

		One today: the Google Chat interaction webhook, authenticated by JWT alone. If Pub/Sub
		push is ever adopted its endpoint joins this list and gets its own verification, since
		the push token is a *different* token with a different issuer and audience.
		"""
		expected = {f"{endpoints.DOTTED_ROOT}.gchat.webhook.handle"}
		actual = {dotted for dotted, ep in self.discovered.items() if ep.allow_guest}
		self.assertEqual(
			actual,
			expected,
			"the set of allow_guest chat endpoints changed. Every one of these answers an\n"
			"unauthenticated request, so each needs its own authenticity check and its own\n"
			"place in the §4.G.2 curl matrix.",
		)

	def test_room_scoped_writers_ask_for_write_intent(self) -> None:
		"""The oversight role reads rooms it is not in; it does not write to them.

		`require_room` used to pass `"read"` for every caller, writers included, and it calls
		the permission hook **directly** rather than through `frappe.has_permission` — so
		`Chat Room`'s read-only DocPerm, which the hook's own docstring cited as the thing
		refusing writes "above us", was never consulted on that path. Filling
		`Chat Settings.admin_oversight_role` would have handed every holder the ability to
		post into any conversation in the company, as themselves.

		So the rule: a **mutating** endpoint that gates on a room asks for `intent="write"`,
		which is membership and nothing else. A reading one asks for `read`, which the hatch
		may answer. This test is the only thing standing between that distinction and a
		future endpoint that copies the wrong line from its neighbour.
		"""
		intents = endpoints.require_room_intents()
		must_write = set(endpoints.MUTATING) | set(endpoints.WRITE_GATED_READS)
		offenders = []
		for dotted in sorted(must_write):
			asked = intents.get(dotted)
			if asked is None:
				continue  # not room-scoped — create_group, mark_all_read, the webhook
			if asked != {"write"}:
				offenders.append(f"{dotted} asks require_room for {sorted(asked)}")

		self.assertFalse(
			offenders,
			'these endpoints change state but gate on require_room(intent="read"), which the\n'
			"oversight role and Administrator both satisfy without being in the room:\n  "
			+ "\n  ".join(offenders),
		)

	def test_readers_do_not_ask_for_write_intent(self) -> None:
		"""The other direction, which is a bug in the shape of extra caution.

		`intent="write"` on a read endpoint locks the oversight role out of the very thing
		decision #12 grants it — and it would present as "the auditor cannot open a room",
		which reads as a permission bug rather than as an over-tightened gate.
		"""
		intents = endpoints.require_room_intents()
		offenders = [
			f"{dotted} asks require_room for {sorted(intents[dotted])}"
			for dotted in sorted(set(endpoints.NON_MUTATING) - set(endpoints.WRITE_GATED_READS))
			if dotted in intents and "write" in intents[dotted]
		]
		self.assertFalse(
			offenders,
			"these only read, but demand room membership — which shuts the oversight role out\n"
			"of the read decision #12 exists to grant:\n  " + "\n  ".join(offenders),
		)

	def test_admin_endpoints_carry_their_own_role_gate(self) -> None:
		"""Set equality again, and it is what closes the finding this suite was written for.

		`enroll_org_units` and `start_org_mirror` shipped with **no role gate at all** — no
		`only_for`, no `require_session`, nothing anywhere in `sync/provisioning.py`. Any
		authenticated System User could create `Chat Room` rows and open a provisioning run,
		and `dry_run` is a caller-supplied parameter, so the default that exists to make the
		irreversible half deliberate was one query-string value away from being skipped.

		Equality both ways rather than a subset check. A new admin endpoint that forgets its
		gate fails here; and a gate quietly *removed* from a listed one fails here too, which
		is the direction a subset check would miss.
		"""
		gated = endpoints.gated_by_only_for()
		declared = set(endpoints.ADMIN_ONLY)

		ungated = sorted(declared - gated)
		self.assertFalse(
			ungated,
			"these are declared admin-only in chat/endpoints.py and do not call frappe.only_for\n"
			"in their own body. A role check in a caller is a check the next caller forgets:\n  "
			+ "\n  ".join(ungated),
		)

		undeclared = sorted(gated - declared)
		self.assertFalse(
			undeclared,
			"these call frappe.only_for but are not in ADMIN_ONLY. Add them with the role, so\n"
			"the checkpoint's endpoint table matches the code:\n  " + "\n  ".join(undeclared),
		)

	def test_admin_endpoints_are_not_reachable_by_guests(self) -> None:
		"""Two independent gates on the same door, asserted together because either alone lies."""
		for dotted in sorted(endpoints.ADMIN_ONLY):
			endpoint = self.discovered.get(dotted)
			with self.subTest(endpoint=dotted):
				self.assertIsNotNone(endpoint, f"{dotted} has moved or been renamed")
				self.assertFalse(
					endpoint.allow_guest,
					f"{dotted} is admin-only and allow_guest — one of those two is wrong",
				)

	def test_guest_endpoints_are_post_only(self) -> None:
		"""A guest endpoint reachable by GET is reachable by a link somebody clicks."""
		for dotted, endpoint in sorted(self.discovered.items()):
			if not endpoint.allow_guest:
				continue
			with self.subTest(endpoint=dotted):
				self.assertEqual(
					tuple(m.upper() for m in endpoint.methods),
					("POST",),
					f"{dotted} answers unauthenticated requests and must be POST-only",
				)


if __name__ == "__main__":
	unittest.main()
