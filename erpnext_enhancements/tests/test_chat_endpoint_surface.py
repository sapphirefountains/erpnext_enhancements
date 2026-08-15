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
			'Add each to MUTATING (and give it methods=["POST"]) or to NON_MUTATING, with a\n'
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
				offenders.append(
					f"{dotted} ({endpoint.relpath}:{endpoint.lineno}) declares {endpoint.methods or 'no methods='}"
				)

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


class ChatRateLimitSurfaceTest(unittest.TestCase):
	"""G6-5 / §4.G.4 — *every endpoint is rate limited **or** explicitly exempt with a reason*.

	The "or" is not a loophole here, it is the answer. ``chat/endpoints.py``'s rate-limit block
	states why at length: ``frappe.rate_limiter`` has no per-user mode, keys on
	``frappe.local.request_ip``, and that value is the first ``X-Forwarded-For`` element taken
	unconditionally — a string the caller writes. On this host it has additionally been the load
	balancer's own address for most traffic since ~2026-07-18, measured rather than supposed. The
	repo's own standing rule, written before this change, is *"do not add one that depends on the
	IP"*.

	So these assert that the **classification** is complete and honest, which is what G6-5 asks
	for, rather than asserting that decorators exist.
	"""

	def setUp(self) -> None:
		self.discovered = endpoints.discover()
		self.decided = endpoints.rate_limit_classified()

	def _decorators(self, dotted: str) -> set[str]:
		import ast

		endpoint = self.discovered[dotted]
		source = (endpoints.CHAT_PACKAGE / endpoint.relpath).resolve()
		tree = ast.parse(source.read_text(encoding="utf-8"))
		function = dotted.rsplit(".", 1)[1]
		names: set[str] = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef) and node.name == function:
				for dec in node.decorator_list:
					target = dec.func if isinstance(dec, ast.Call) else dec
					names.add(getattr(target, "id", "") or getattr(target, "attr", ""))
		return names

	def test_the_decorator_scan_actually_finds_decorators(self) -> None:
		"""An empty scan passes both honesty checks below it."""
		any_dotted = next(iter(sorted(endpoints.RATE_LIMIT)))
		self.assertTrue(self._decorators(any_dotted))

	def test_every_endpoint_has_a_rate_limit_decision(self) -> None:
		missing = sorted(set(self.discovered) - set(self.decided))
		self.assertFalse(
			missing,
			f"these chat endpoints have no rate-limit decision: {missing}. "
			"Add each to endpoints.RATE_LIMIT or endpoints.RATE_LIMIT_EXEMPT, with the reason. "
			"Set equality rather than containment, so a new endpoint fails the build by default — "
			"a checklist item nobody was asked about is one that gets answered silently.",
		)

	def test_no_decision_names_an_endpoint_that_no_longer_exists(self) -> None:
		stale = sorted(set(self.decided) - set(self.discovered))
		self.assertFalse(stale, f"rate-limit decisions for endpoints that are gone: {stale}")

	def test_no_endpoint_is_both_limited_and_exempt(self) -> None:
		both = sorted(set(endpoints.RATE_LIMIT) & set(endpoints.RATE_LIMIT_EXEMPT))
		self.assertFalse(both, f"{both} are recorded as both limited and exempt")

	def test_every_decision_carries_a_reason(self) -> None:
		for dotted, reason in self.decided.items():
			with self.subTest(endpoint=dotted):
				self.assertGreater(
					len(reason.strip()),
					60,
					f"{dotted}'s rate-limit reason is too short to be one. G6-5 asks for a reason "
					"because the exemption that gets forgotten is the one written as 'not needed'.",
				)

	def test_the_limited_endpoints_actually_carry_a_decorator(self) -> None:
		"""A dict claiming a limit the source does not apply is worse than no dict: it reports
		the surface as covered."""
		for dotted in sorted(endpoints.RATE_LIMIT):
			with self.subTest(endpoint=dotted):
				self.assertIn("rate_limit", self._decorators(dotted))

	def test_the_exempt_endpoints_carry_no_decorator(self) -> None:
		"""The other direction: a decorator nobody recorded is a limit nobody reasoned about,
		and on this host that means one firing on the wrong person."""
		for dotted in sorted(endpoints.RATE_LIMIT_EXEMPT):
			with self.subTest(endpoint=dotted):
				self.assertNotIn("rate_limit", self._decorators(dotted))

	def test_the_prerequisites_are_recorded(self) -> None:
		"""The exemptions are a position, not a permanent state. What would change the answer is
		written down, so the next person does not re-derive it from the framework."""
		self.assertGreaterEqual(len(endpoints.RATE_LIMIT_PREREQUISITES), 3)
		for step in endpoints.RATE_LIMIT_PREREQUISITES:
			self.assertGreater(len(step), 80)

	def test_no_spa_endpoint_carries_a_limit(self) -> None:
		"""What keeps the short list short. A limit on an SPA endpoint fires on whoever shares
		the load balancer address, on a client that cannot see the refusal."""
		spa = (f"{endpoints.DOTTED_ROOT}.api.", f"{endpoints.DOTTED_ROOT}.notifications.api.")
		for dotted in endpoints.RATE_LIMIT:
			with self.subTest(endpoint=dotted):
				self.assertFalse(
					dotted.startswith(spa),
					f"{dotted} is an SPA endpoint carrying a rate limit. Read the rate-limit block "
					"in chat/endpoints.py first: no chat client handles a 429, and four of the "
					"refusal paths are silent.",
				)


class ChatPilotGateSurfaceTest(unittest.TestCase):
	"""§4.J — *"pilot gating is enforced server-side on every chat endpoint"*.

	The requirement is worded about deep links: hiding a button is not a rollout control, so a
	non-pilot user holding a URL has to be refused by the server. `api/_common.require_session`
	is that refusal — it rejects Guest, rejects when `Chat Settings.enabled` is off, and rejects
	anyone outside the whitelist — and `require_room`/`require_message` call it, so most
	endpoints inherit it.

	**This suite exists because a transitive scan found fourteen that did not.** Thirteen were
	correct and are now classified with a reason; the fourteenth, `sync.attachments.download`,
	was not: it enforced membership but neither the pilot whitelist nor the master switch, so
	`Chat Settings.enabled = 0` — the top layer of the rollback table — left attachment bytes
	flowing. Fixed in v1.294.0, and now the scan is a test rather than an audit somebody has to
	remember to repeat.
	"""

	@classmethod
	def setUpClass(cls) -> None:
		cls.discovered = set(endpoints.discover())
		cls.gated = endpoints.pilot_gated()

	def test_the_scan_finds_a_plausible_number_of_gated_endpoints(self) -> None:
		"""A walker that silently found nothing would make the exemption test pass for every
		endpoint on the surface."""
		self.assertGreater(len(self.gated), 20, "the pilot-gate walker is broken, not the surface")

	def test_the_scan_finds_the_gate_on_a_known_gated_endpoint(self) -> None:
		"""Both directions of the walker, pinned. `send_message` reaches it through
		`require_room`, so this also proves the transitive hop works."""
		self.assertIn(f"{endpoints.DOTTED_ROOT}.api.compose.send_message", self.gated)

	def test_every_ungated_endpoint_is_declared_exempt(self) -> None:
		ungated = sorted(self.discovered - self.gated - set(endpoints.PILOT_GATE_EXEMPT))
		self.assertFalse(
			ungated,
			"these chat endpoints never reach require_session, and nobody has said why: "
			f"{ungated}. §4.J requires the pilot gate to be enforced server-side on every "
			"endpoint, because a non-pilot user holding a deep link must be refused by the "
			"server. Either call require_session (usually via require_room), or add the endpoint "
			"to endpoints.PILOT_GATE_EXEMPT with the reason.",
		)

	def test_no_exemption_names_an_endpoint_that_is_actually_gated(self) -> None:
		"""A stale exemption is worse than none: it reads as a considered decision about an
		endpoint whose behaviour has since changed."""
		stale = sorted(set(endpoints.PILOT_GATE_EXEMPT) & self.gated)
		self.assertFalse(stale, f"declared exempt from the pilot gate but reaches it: {stale}")

	def test_no_exemption_names_an_endpoint_that_no_longer_exists(self) -> None:
		gone = sorted(set(endpoints.PILOT_GATE_EXEMPT) - self.discovered)
		self.assertFalse(gone, f"pilot-gate exemptions for endpoints that are gone: {gone}")

	def test_every_exemption_carries_a_reason(self) -> None:
		for dotted, reason in endpoints.PILOT_GATE_EXEMPT.items():
			with self.subTest(endpoint=dotted):
				self.assertGreater(len(reason.strip()), 80, f"{dotted} needs a real reason")

	def test_every_content_endpoint_is_gated(self) -> None:
		"""The property the fix restored, pinned as a rule rather than as one endpoint.

		Anything under `api/` serves or writes conversation, and `attachments.download` serves
		the bytes. None of them may be exempt: those are precisely the deep links §4.J is about.
		"""
		content = {f"{endpoints.DOTTED_ROOT}.sync.attachments.download"} | {
			dotted for dotted in self.discovered if dotted.startswith(f"{endpoints.DOTTED_ROOT}.api.")
		}
		ungated = sorted(content - self.gated)
		self.assertFalse(
			ungated,
			f"these endpoints serve or write conversation and do not enforce the pilot gate: "
			f"{ungated}. A rollback switch that leaves one of these open has less blast radius "
			"than the rollback table claims for it.",
		)


if __name__ == "__main__":
	unittest.main()
