"""Bench-free test: the marketing data model's load-bearing schema promises.

Three things in this module are correctness rather than style, and all three are the kind
that break silently:

1. **`MarketingSettings.POSITIVE_DIALS` must agree with the JSON's declared defaults.**
   `validate` repairs a missing dial by reading the meta and falling back to that dict. If
   the two drift, a value "self-heals" to the wrong number — which is worse than not healing,
   because nothing looks broken afterwards.
2. **The identity fields the record names are built from must be `set_only_once`.**
   `Ad Daily Metric` is named `format:ADM-{campaign}-{metric_date}` precisely so a restated
   day upserts instead of duplicating. If either field became editable, changing one would
   orphan the row from its own name and the upsert guarantee would quietly stop holding.
3. **The doctypes Sales Manager can read must not grow a secret-shaped field.** Marketing
   Sync Log and Marketing Raw Payload archive whatever a connector hands them, and this app
   has published private key material to a log before.

Filesystem, `json` and `ast` only. No bench, no `frappe` import — so it collects under
`python -m unittest` with no stub set, and cannot cross-talk with any other suite.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_settings
"""

import ast
import json
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
MARKETING = APP_DIR / "marketing" / "doctype"

SETTINGS_JSON = MARKETING / "marketing_settings" / "marketing_settings.json"
SETTINGS_PY = MARKETING / "marketing_settings" / "marketing_settings.py"

#: DocType -> the fields its `autoname` format is built from. Every one must be
#: `set_only_once`, or the name and the row can disagree.
IDENTITY_FIELDS = {
	"ad_account": ("platform", "external_id"),
	"ad_campaign": ("ad_account", "external_id"),
	"ad_daily_metric": ("campaign", "metric_date"),
}

#: Substrings that have no business in a doctype a non-System-Manager can read.
SECRET_SHAPED = ("token", "secret", "password", "authorization", "api_key", "credential")

#: Readable by Sales Manager, so subject to the rule above.
WIDELY_READABLE = ("marketing_sync_log", "marketing_raw_payload", "ad_account", "ad_campaign")


def load(doctype_dir: str) -> dict:
	path = MARKETING / doctype_dir / f"{doctype_dir}.json"
	return json.loads(path.read_text(encoding="utf-8"))


def fields_by_name(schema: dict) -> dict:
	return {f["fieldname"]: f for f in schema.get("fields", []) if f.get("fieldname")}


def positive_dials_from_source() -> dict:
	"""Read POSITIVE_DIALS out of the controller without importing it.

	The controller imports frappe, which is not installed on the bench-free runner. Parsing
	is not a workaround for a lazy import — it is what lets this invariant be checked in CI
	at all, and the same trick guards `snapshots.py` elsewhere in this app.
	"""
	tree = ast.parse(SETTINGS_PY.read_text(encoding="utf-8"))
	for node in tree.body:
		if not isinstance(node, ast.Assign):
			continue
		targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
		if "POSITIVE_DIALS" in targets:
			return ast.literal_eval(node.value)
	raise AssertionError("POSITIVE_DIALS not found in marketing_settings.py")


class TestMarketingSettingsDials(unittest.TestCase):
	def setUp(self):
		self.schema = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		self.fields = fields_by_name(self.schema)
		self.dials = positive_dials_from_source()

	def test_every_dial_exists_in_the_schema(self):
		for fieldname in self.dials:
			self.assertIn(
				fieldname,
				self.fields,
				f"POSITIVE_DIALS names {fieldname!r}, which is not a field on Marketing Settings",
			)

	def test_every_dial_default_matches_the_schema(self):
		for fieldname, fallback in self.dials.items():
			declared = self.fields[fieldname].get("default")
			self.assertIsNotNone(
				declared,
				f"{fieldname} is a positive dial but declares no default in the JSON",
			)
			self.assertEqual(
				int(declared),
				int(fallback),
				f"{fieldname}: JSON default {declared!r} disagrees with POSITIVE_DIALS "
				f"{fallback!r}. A dial that self-heals to the wrong number looks fine "
				f"afterwards, which is why this is a test and not a comment.",
			)

	def test_every_dial_default_is_positive(self):
		for fieldname, fallback in self.dials.items():
			self.assertGreater(
				int(fallback), 0, f"{fieldname} would heal to a non-positive value"
			)

	def test_every_int_field_is_either_a_dial_or_deliberately_not(self):
		"""A new Int on this Single is a decision, not an accident.

		Anything numeric that sizes behaviour belongs in POSITIVE_DIALS so it self-heals.
		This fails loudly on a new Int so somebody has to make that call explicitly.
		"""
		ints = {
			name
			for name, f in self.fields.items()
			if f.get("fieldtype") == "Int"
		}
		unclassified = ints - set(self.dials)
		self.assertFalse(
			unclassified,
			f"New Int field(s) on Marketing Settings not in POSITIVE_DIALS: "
			f"{sorted(unclassified)}. Add them there so a missing tabSingles row heals, "
			f"or add them to this test's exemptions with a reason.",
		)

	def test_checks_are_never_coerced(self):
		"""Switches must not be in the dial set.

		A dial of 0 is meaningless. A checkbox of 0 is somebody deliberately switching a
		connector off, and restoring it would turn a platform back on behind their back.
		"""
		checks = {
			name for name, f in self.fields.items() if f.get("fieldtype") == "Check"
		}
		overlap = checks & set(self.dials)
		self.assertFalse(overlap, f"Check field(s) would be coerced: {sorted(overlap)}")

	def test_master_switch_ships_off(self):
		self.assertEqual(
			self.fields["enabled"].get("default"),
			"0",
			"the marketing module must ship dormant",
		)
		for flag in ("google_ads_enabled", "meta_ads_enabled", "linkedin_ads_enabled"):
			self.assertEqual(
				self.fields[flag].get("default"), "0", f"{flag} must ship off"
			)


class TestAdModelIdentity(unittest.TestCase):
	def test_identity_fields_are_set_only_once(self):
		for doctype_dir, identity in IDENTITY_FIELDS.items():
			schema = load(doctype_dir)
			fields = fields_by_name(schema)
			for fieldname in identity:
				self.assertIn(fieldname, fields, f"{doctype_dir}: missing {fieldname}")
				self.assertTrue(
					fields[fieldname].get("set_only_once"),
					f"{doctype_dir}.{fieldname} builds the record name and must be "
					f"set_only_once, or changing it orphans the row from its own name and "
					f"the restate-as-upsert guarantee stops holding.",
				)

	def test_autoname_references_only_real_fields(self):
		for doctype_dir, identity in IDENTITY_FIELDS.items():
			schema = load(doctype_dir)
			autoname = schema.get("autoname", "")
			self.assertTrue(
				autoname.startswith("format:"),
				f"{doctype_dir} must be deterministically named so a re-pull upserts",
			)
			for fieldname in identity:
				self.assertIn(
					"{" + fieldname + "}",
					autoname,
					f"{doctype_dir}: autoname {autoname!r} omits identity field {fieldname}",
				)


class TestNoSecretShapedFields(unittest.TestCase):
	def test_widely_readable_doctypes_hold_no_credential_fields(self):
		for doctype_dir in WIDELY_READABLE:
			schema = load(doctype_dir)
			for fieldname in fields_by_name(schema):
				lowered = fieldname.lower()
				for needle in SECRET_SHAPED:
					self.assertNotIn(
						needle,
						lowered,
						f"{doctype_dir}.{fieldname} looks like a credential and this "
						f"doctype is readable beyond System Manager.",
					)


if __name__ == "__main__":
	unittest.main()
