"""Bench-free test: the publishing scaffold's promises (TASK-2026-01479, Marketing P2).

Publishing is the first part of the marketing module that writes to the outside world, and
it arrives in a module whose whole guarantee so far was "read-only". These pin the three
things that keep that guarantee true once writes exist:

1. **The gate.** An approved post may go out only when the master switch *and* the network's
   own switch are on. The master switch alone publishes nothing, and the ad-reporting
   switches play no part.
2. **The switches.** Every network has one, on Marketing Settings, shipping ``0``; only
   System Manager can change it, and the doctype keeps a Version history of who did.
   Networks are named apart from the ad platforms, so no switch can mean both.
3. **No spend.** No spend-capable OAuth scope may be requested anywhere in ``marketing/``
   (decision 3). Meta's ``pages_manage_ads`` is the one a publishing connection would reach
   for, because it lets a post be boosted into paid spend. Google Ads' only scope is full
   access, and it stays confined to the read-only connector's constants.

Filesystem, ``json``, ``ast`` and ``re`` only, plus two modules that import no frappe. No
stub, so it shares a CI step with ``test_marketing_settings``.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_publishing
"""

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import gate

APP_DIR = Path(__file__).resolve().parents[1]
MARKETING = APP_DIR / "marketing"
CORE_CONSTANTS = MARKETING / "core" / "constants.py"
SETTINGS_JSON = MARKETING / "doctype" / "marketing_settings" / "marketing_settings.json"

GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


def settings(**values):
	"""A Marketing Settings stand-in: every switch off unless given."""
	base = {"enabled": 0, **{flag: 0 for flag in P.PUBLISH_FLAG.values()}}
	base.update(values)
	return base


def all_publishing_on(**extra):
	return settings(enabled=1, **{flag: 1 for flag in P.PUBLISH_FLAG.values()}, **extra)


def string_literals(path, skip_assignments=()):
	"""Every str constant in a Python file, except docstrings and the values assigned to ``skip_assignments``."""
	tree = ast.parse(path.read_text(encoding="utf-8"))
	skipped = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
			first = node.body[0]
			if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
				skipped.add(id(first.value))
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id in skip_assignments for t in node.targets
		):
			skipped.update(id(inner) for inner in ast.walk(node.value))
	return [
		node.value
		for node in ast.walk(tree)
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped
	]


def tokens(text):
	return set(re.split(r"[\s,]+", text.strip())) - {""}


class GateTests(unittest.TestCase):
	def test_ships_closed(self):
		self.assertEqual(gate.enabled_networks(settings()), [])

	def test_master_switch_alone_publishes_nothing(self):
		self.assertEqual(gate.enabled_networks(settings(enabled=1)), [])

	def test_network_switch_alone_publishes_nothing(self):
		for network, flag in P.PUBLISH_FLAG.items():
			self.assertFalse(gate.network_enabled(network, settings(**{flag: 1})), network)

	def test_both_switches_open_that_network_only(self):
		values = settings(enabled=1, instagram_publishing_enabled=1)
		self.assertEqual(gate.enabled_networks(values), [P.NETWORK_INSTAGRAM])
		self.assertTrue(gate.network_enabled(P.NETWORK_INSTAGRAM, values))
		self.assertFalse(gate.network_enabled(P.NETWORK_FACEBOOK, values))

	def test_all_on_keeps_network_order(self):
		self.assertEqual(gate.enabled_networks(all_publishing_on()), list(P.PUBLISH_NETWORKS))

	def test_ad_reporting_switches_publish_nothing(self):
		ads_on = settings(enabled=1, **{flag: 1 for flag in C.ENABLED_FLAG.values()})
		self.assertEqual(gate.enabled_networks(ads_on), [])

	def test_stored_values_as_they_arrive(self):
		# tabSingles stores text; a form submits ints; a stand-in might pass bools.
		for on in (1, "1", True):
			self.assertTrue(
				gate.network_enabled(P.NETWORK_YOUTUBE, settings(enabled=on, youtube_publishing_enabled=on))
			)
		for off in (0, "0", None, "", False, "yes"):
			self.assertFalse(
				gate.network_enabled(P.NETWORK_YOUTUBE, settings(enabled=1, youtube_publishing_enabled=off)),
				off,
			)
			self.assertFalse(
				gate.network_enabled(P.NETWORK_YOUTUBE, settings(enabled=off, youtube_publishing_enabled=1)),
				off,
			)

	def test_unknown_network_is_never_enabled(self):
		self.assertFalse(gate.network_enabled("X", all_publishing_on()))
		self.assertFalse(
			gate.network_enabled(C.PLATFORM_META, all_publishing_on()), "an ad platform is not a network"
		)


class SwitchSchemaTests(unittest.TestCase):
	def setUp(self):
		self.schema = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		self.fields = {f["fieldname"]: f for f in self.schema["fields"] if f.get("fieldname")}

	def test_every_network_has_a_check_that_ships_off(self):
		self.assertEqual(set(P.PUBLISH_FLAG), set(P.PUBLISH_NETWORKS))
		for network, flag in P.PUBLISH_FLAG.items():
			self.assertIn(flag, self.fields, f"{network}: {flag} is not on Marketing Settings")
			self.assertEqual(self.fields[flag]["fieldtype"], "Check", flag)
			self.assertEqual(self.fields[flag].get("default"), "0", f"{flag} must ship off")
			self.assertIn(flag, self.schema["field_order"], flag)

	def test_networks_are_named_apart_from_the_ad_platforms(self):
		self.assertFalse(set(P.PUBLISH_NETWORKS) & set(C.PLATFORMS))
		self.assertFalse(set(P.PUBLISH_FLAG.values()) & set(C.ENABLED_FLAG.values()))

	def test_only_system_manager_can_flip_a_switch(self):
		writers = {p["role"] for p in self.schema["permissions"] if p.get("write")}
		self.assertEqual(writers, {"System Manager"})
		for flag in P.PUBLISH_FLAG.values():
			self.assertFalse(
				self.fields[flag].get("permlevel"), f"{flag}: a permlevel would need its own grant"
			)

	def test_who_switched_publishing_on_is_recorded(self):
		self.assertEqual(self.schema.get("track_changes"), 1)


class NoSpendScopeTests(unittest.TestCase):
	def test_no_spend_capable_scope_anywhere_in_the_module(self):
		for path in MARKETING.rglob("*.py"):
			if "__pycache__" in path.parts:
				continue
			skip = ("SPEND_CAPABLE_SCOPES",) if path == CORE_CONSTANTS else ()
			for value in string_literals(path, skip):
				hit = tokens(value) & C.SPEND_CAPABLE_SCOPES
				self.assertFalse(hit, f"{path.relative_to(APP_DIR)}: requests {sorted(hit)} in {value!r}")

	def test_no_spend_capable_scope_in_the_module_js_or_json(self):
		pattern = re.compile(r"\b(" + "|".join(sorted(C.SPEND_CAPABLE_SCOPES)) + r")\b")
		for path in list(MARKETING.rglob("*.js")) + list(MARKETING.rglob("*.json")):
			match = pattern.search(path.read_text(encoding="utf-8"))
			self.assertIsNone(match, f"{path.relative_to(APP_DIR)}: {match and match.group(0)}")

	def test_declared_scopes_are_clean(self):
		for platform, config in C.OAUTH.items():
			self.assertFalse(set(config["scopes"]) & C.SPEND_CAPABLE_SCOPES, platform)

	def test_google_ads_full_access_scope_stays_in_the_read_only_connector(self):
		self.assertEqual(C.OAUTH[C.PLATFORM_GOOGLE]["scopes"], (GOOGLE_ADS_SCOPE,))
		for path in MARKETING.rglob("*.py"):
			if "__pycache__" in path.parts or path == CORE_CONSTANTS:
				continue
			for value in string_literals(path):
				self.assertNotIn("auth/adwords", value, f"{path.relative_to(APP_DIR)}: {value!r}")

	def test_the_guard_would_fire(self):
		# The scans above pass vacuously if the scanner finds nothing, so prove it finds a
		# scope written the way a publisher would write one -- in a tuple, or joined.
		source = (
			'"""pages_manage_ads in a docstring is prose, not a request."""\n'
			'SCOPES = ("pages_manage_posts", "pages_manage_ads")\n'
			'JOINED = "r_organization_social,rw_ads"\n'
			'SPEND_CAPABLE_SCOPES = frozenset({"ads_management"})\n'
		)
		with tempfile.TemporaryDirectory() as tmp:
			probe = Path(tmp) / "probe.py"
			probe.write_text(source, encoding="utf-8")
			found = set().union(*(tokens(v) & C.SPEND_CAPABLE_SCOPES for v in string_literals(probe)))
			exempted = set().union(
				*(
					tokens(v) & C.SPEND_CAPABLE_SCOPES
					for v in string_literals(probe, ("SPEND_CAPABLE_SCOPES",))
				)
			)
		self.assertEqual(found, {"pages_manage_ads", "rw_ads", "ads_management"})
		self.assertEqual(exempted, {"pages_manage_ads", "rw_ads"}, "only the named assignment is exempt")


if __name__ == "__main__":
	unittest.main()
