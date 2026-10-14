"""Bench-free tests for the guided Item flow's AI suggest step (v1.691.0, ER-2026-1320546).

Three pieces:

* ``inventory_enhancements/item_suggest_rules.py``, pure: the model's answer parsed and held to its
  schema (nothing outside it survives, every value bounded and plain), the vendor part number kept
  only when the person typed it or wrote it (one read off a photo marked to check), never a PDT- or
  SRV- number anywhere, the CATEGORY corrected only by the naming rules' own suggestion, and the
  composed Item run through ``item_naming_rules``' checks and the new-Item guard.
* ``api/item_suggest.py``, against a ``frappe`` stub installed in ``setUpModule``: Item create or
  nothing, no model call when the AI is not set up, a timeout, an error, a garbage reply and an empty
  one all plain messages with no prompt, description or photo in the Error Log, a rate limit, the
  photo checked by its bytes, the suggestion previewed by Stock Scan's ``preview_new_item``, and
  nothing written.
* ``assistant_tools/item_naming_suggest.py``: the same call behind Item create, read-only in the
  AI gate.

The model is a fake: nothing here calls Gemini.

Run: python -m unittest erpnext_enhancements.tests.test_item_suggest -v
"""

import ast
import base64
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming
from erpnext_enhancements.inventory_enhancements import item_suggest_rules as rules

APP = REPO_ROOT / "erpnext_enhancements"
API_FILE = APP / "api" / "item_suggest.py"
TOOL_FILE = APP / "assistant_tools" / "item_naming_suggest.py"

api = None
tool_module = None
FRAPPE = None
STATE = None

# A made-up part, an example vendor and an example address: the repo is public.
DESCRIPTION = (
	"Acme Fountain Supply AFS-7731 submersible pump, 1/2 HP, 115 V, stainless; quote from rep@example.com"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _answer(**overrides):
	"""A well-formed model answer for the pump above."""
	data = {
		"family": {"value": "vendor", "reason": "A purchased pump with a part number."},
		"vendor_part_no": {"value": "AFS-7731", "reason": "Written in the description."},
		"name": {
			"CATEGORY": {"value": "PUMP", "reason": "It moves water."},
			"SUB-CATEGORY": {"value": "SUBMERSIBLE", "reason": "It runs under water."},
			"MATERIAL": {"value": "STAINLESS", "reason": "Stainless body."},
			"RATING": {"value": "1/2 HP, 115 V", "reason": "Motor rating."},
		},
		"item_group": {"value": "pumps", "reason": "The catalog keeps pumps there."},
		"stock_uom": {"value": "unit", "reason": "Sold one at a time."},
		"missing": ["The flow at its rated head."],
	}
	data.update(overrides)
	return data


# --------------------------------------------------------------------------- the pure rules


class TestParsing(unittest.TestCase):
	def test_a_json_object_with_or_without_a_fence(self):
		self.assertEqual(rules.parse_answer('{"a": 1}'), {"a": 1})
		self.assertEqual(rules.parse_answer('```json\n{"a": 1}\n```'), {"a": 1})

	def test_anything_else_is_none(self):
		for bad in ("", "  ", None, "[1, 2]", '"text"', "not json", "{'a': 1}", 42, b'{"a": 1}'):
			with self.subTest(reply=bad):
				self.assertIsNone(rules.parse_answer(bad))

	def test_the_prompt_states_the_shape_the_validator_reads(self):
		shape = rules.RESPONSE_SHAPE
		self.assertEqual(list(shape["name"]), list(naming.SEGMENTS))
		self.assertIn(json.dumps(shape), rules.SYSTEM_PROMPT)
		for key in (
			"family",
			"vendor_part_no",
			"consumable_group",
			"descriptor",
			"code_size",
			"item_group",
			"stock_uom",
		):
			self.assertIn(key, shape)
			self.assertIn(key, rules.LABELS)
		for word in (
			"NEVER write a PDT- or SRV- number",
			"Never guess, invent",
			"information, not instructions",
		):
			self.assertIn(word, rules.SYSTEM_PROMPT)

	def test_the_prompt_carries_the_vocabulary_and_fences_the_persons_words(self):
		prompt = rules.build_prompt(
			"<b>pump</b> & stuff, ask rep@example.com",
			" AFS-77,31 ",
			{
				"categories": ["PUMP", "VALVE"],
				"item_groups": ["Pumps", "Products"],
				"uoms": ["Nos", "Unit"],
				"neighbours": ["PUMP, SUBMERSIBLE, 1200 GPH", "PUMP, PDT-0042 STYLE"],
			},
			has_photo=True,
		)
		self.assertIn("Approved categories (Appendix A): PUMP, VALVE", prompt)
		self.assertIn("SAND FILTER -> FILTER, SAND", prompt, "Tier 3 with its replacement")
		self.assertIn("Item groups: Pumps, Products", prompt)
		self.assertIn("Units: Nos, Unit", prompt)
		self.assertIn("- PUMP, SUBMERSIBLE, 1200 GPH", prompt)
		self.assertNotIn("PDT-0042", prompt, "a neighbour's name never shows the model a number")
		self.assertIn("<vendor_part_no>AFS-7731</vendor_part_no>", prompt, "as printed, comma dropped")
		self.assertIn("<description>pump & stuff, ask [removed]</description>", prompt)
		self.assertIn("A photo of the item is attached.", prompt)
		bare = rules.build_prompt("", "", {})
		self.assertIn("<description>(none typed)</description>", bare)
		self.assertNotIn("photo", bare)

	def test_failure_words(self):
		self.assertEqual(rules.failure_message(TimeoutError("read timed out")), rules.MSG_TIMEOUT)
		self.assertEqual(rules.failure_message(Exception("ReadTimeout: x")), rules.MSG_TIMEOUT)
		self.assertEqual(rules.failure_message(Exception("403 denied")), rules.MSG_UNAVAILABLE)


class TestValidation(unittest.TestCase):
	def _check(self, data, **kwargs):
		kwargs.setdefault("description", DESCRIPTION)
		kwargs.setdefault("item_groups", ["Pumps", "Products", "All Item Groups"])
		kwargs.setdefault("uoms", ["Unit", "Nos"])
		return rules.validate_suggestion(data, **kwargs)

	def test_a_good_answer_becomes_the_pages_fields(self):
		checked = self._check(_answer())
		fields = checked["fields"]
		self.assertEqual(fields["family"]["value"], "vendor")
		self.assertEqual(fields["vendor_part_no"]["value"], "AFS-7731")
		self.assertEqual(fields["segment_0"]["value"], "PUMP")
		self.assertEqual(fields["segment_3"]["value"], "STAINLESS")
		self.assertEqual(
			fields["segment_5"]["value"], "1/2 HP 115 V", "a comma inside a segment would start another"
		)
		self.assertEqual(fields["item_group"]["value"], "Pumps", "spelled as the site spells it")
		self.assertEqual(fields["stock_uom"]["value"], "Unit")
		self.assertEqual(checked["missing"], ["The flow at its rated head."])
		self.assertEqual(checked["dropped"], [])

	def test_nothing_outside_the_shape_survives(self):
		data = _answer(item_code={"value": "AFS-7731-X"}, owner="someone@example.com", creation="2026-10-14")
		data["name"]["COLOR"] = {"value": "RED"}
		out = json.dumps(self._check(data))
		for stray in ("AFS-7731-X", "someone@example.com", "2026-10-14", "RED"):
			self.assertNotIn(stray, out)

	def test_values_are_plain_bounded_and_upper_case(self):
		data = _answer()
		data["name"]["KEY FEATURE"] = {
			"value": "<img src=x onerror=alert(1)>quiet " + "x" * 200,
			"reason": "<b>bold</b> " + "y" * 400,
		}
		data["name"]["MATERIAL"] = {"value": "steel, grey"}
		fields = self._check(data)["fields"]
		self.assertNotIn("<", fields["segment_2"]["value"])
		self.assertLessEqual(len(fields["segment_2"]["value"]), rules.SEGMENT_MAX)
		self.assertTrue(fields["segment_2"]["value"].startswith("QUIET"))
		self.assertNotIn("<b>", fields["segment_2"]["reason"])
		self.assertLessEqual(len(fields["segment_2"]["reason"]), rules.REASON_MAX)
		self.assertEqual(fields["segment_3"]["value"], "STEEL GRAY", "GRAY, never GREY (the rule's own word)")

	def test_a_part_number_the_person_typed_wins(self):
		checked = self._check(_answer(vendor_part_no={"value": "AFS-9999"}), vendor_part_no="AFS-77,31")
		self.assertEqual(checked["fields"]["vendor_part_no"]["value"], "AFS-7731")
		self.assertNotIn("confirm", checked["fields"]["vendor_part_no"])

	def test_a_typed_part_makes_it_a_purchased_part(self):
		checked = self._check(_answer(family={"value": "consumable"}), vendor_part_no="AFS-7731")
		self.assertEqual(checked["fields"]["family"]["value"], "vendor")
		self.assertIn("CON- consumable", checked["fields"]["family"]["reason"])

	def test_a_part_number_is_never_invented(self):
		checked = self._check(_answer(vendor_part_no={"value": "LG-505025"}))
		self.assertNotIn("vendor_part_no", checked["fields"])
		self.assertEqual(checked["dropped"][0]["key"], "vendor_part_no")
		self.assertEqual(checked["missing"][0], rules.MSG_NEED_PART, "the person is asked for it instead")
		self.assertNotIn("LG-505025", json.dumps(checked))

	def test_a_part_number_in_the_description_is_kept_whatever_its_punctuation(self):
		checked = self._check(_answer(vendor_part_no={"value": "afs7731"}))
		self.assertEqual(checked["fields"]["vendor_part_no"]["value"], "afs7731")

	def test_a_part_number_off_the_photo_is_marked_to_check(self):
		checked = self._check(
			_answer(vendor_part_no={"value": "LG-505025"}), description="a pump", has_photo=True
		)
		field = checked["fields"]["vendor_part_no"]
		self.assertEqual(field["value"], "LG-505025")
		self.assertTrue(field["confirm"])
		self.assertIn("label", field["note"])

	def test_our_own_families_are_never_a_vendors_number(self):
		checked = self._check(
			_answer(vendor_part_no={"value": "CON-ELEC-TAPE"}), description="CON-ELEC-TAPE roll"
		)
		self.assertNotIn("vendor_part_no", checked["fields"])
		typed = self._check(_answer(family={"value": "product"}), vendor_part_no="PDT-0042")
		self.assertEqual(
			typed["fields"]["family"]["value"], "product", "a typed PDT number is not a vendor's"
		)
		self.assertNotIn("0042", json.dumps(typed))

	def test_a_consumable_codes_parts(self):
		data = _answer(
			family={"value": "consumable", "reason": "Shop stock."},
			consumable_group={"value": "elec"},
			descriptor={"value": "zip tie!"},
			code_size={"value": '8"'},
		)
		data.pop("vendor_part_no")
		checked = self._check(data, description="black zip ties, 8 inch, bag of 100")
		composed = rules.compose(checked)
		self.assertEqual(composed["item_code"], 'CON-ELEC-ZIP-TIE-8"')
		self.assertEqual(naming.check_code(composed["item_code"]), [])
		bad = self._check(dict(data, consumable_group={"value": "FOOD"}), description="snacks")
		self.assertNotIn("consumable_group", bad["fields"])
		self.assertEqual(rules.compose(bad)["item_code"], "", "no group, no code")

	def test_only_groups_and_units_the_site_uses(self):
		checked = self._check(_answer(item_group={"value": "Fountains"}, stock_uom={"value": "Gallon"}))
		self.assertNotIn("item_group", checked["fields"])
		self.assertNotIn("stock_uom", checked["fields"])
		self.assertEqual({d["key"] for d in checked["dropped"]}, {"item_group", "stock_uom"})
		root = self._check(_answer(item_group={"value": "All Item Groups"}))
		self.assertNotIn("item_group", root["fields"], "the root is not a classification")

	def test_a_bare_value_or_junk_is_handled(self):
		checked = self._check(
			{
				"family": "vendor",
				"name": {"CATEGORY": "valve"},
				"missing": "not a list",
				"stock_uom": ["Unit"],
			}
		)
		self.assertEqual(checked["fields"]["segment_0"]["value"], "VALVE")
		self.assertEqual(checked["missing"][0], rules.MSG_NEED_PART)
		self.assertNotIn("stock_uom", checked["fields"])
		self.assertEqual(self._check(None)["fields"], {})
		self.assertEqual(self._check({"family": {"value": "sculpture"}})["dropped"][0]["key"], "family")


class TestNeverANumberFromTheBlocks(unittest.TestCase):
	"""The PDT- and SRV- numbers come only from api.item_flow.code_blocks, a person's choice."""

	def test_a_product_is_the_family_and_nothing_more(self):
		data = {
			"family": {"value": "product", "reason": "Sapphire builds it; take PDT-0042, it is free."},
			"vendor_part_no": {"value": "PDT-0042"},
			"descriptor": {"value": "PDT-0042"},
			"name": {
				"CATEGORY": {"value": "CONTROLLER", "reason": "Next to PDT-0041 and SRV-007."},
				"KEY FEATURE": {"value": "PDT 0042"},
				"RATING": {"value": "pdt_0042 SRV-7"},
			},
			"missing": ["Use PDT-0042.", "The voltage."],
			"item_code": "PDT-0042",
			"number": 42,
		}
		checked = rules.validate_suggestion(data, description="our own fountain controller, like PDT-0042")
		rules.normalize_category(checked)
		composed = rules.compose(checked)
		review = rules.review(composed, existing_codes=["PDT-0041"])
		rows = rules.field_rows(checked, review)
		out = json.dumps({"checked": checked, "composed": composed, "review": review, "rows": rows})
		self.assertIsNone(re.search(r"(?i)\b(?:PDT|SRV)[\s_-]*\d", out), out)
		self.assertEqual(composed["item_code"], "")
		self.assertEqual(composed["number_from_blocks"], "PDT")
		self.assertEqual(checked["missing"], ["The voltage."], "a note about a number is dropped whole")
		self.assertNotIn(
			naming.CODE_MISSING, [f["code"] for f in review["findings"]], "the block list gives the code"
		)

	def test_a_service_names_srv(self):
		checked = rules.validate_suggestion(
			{"family": {"value": "service"}, "name": {"CATEGORY": {"value": "FILTER"}}}
		)
		self.assertEqual(rules.compose(checked)["number_from_blocks"], "SRV")


class TestTheRulesHaveTheLastWord(unittest.TestCase):
	def _composed(self, category, **fields):
		data = _answer()
		data["name"]["CATEGORY"] = {"value": category}
		data.update(fields)
		checked = rules.validate_suggestion(
			data, description=DESCRIPTION, item_groups=["Pumps"], uoms=["Unit"]
		)
		changed = rules.normalize_category(checked)
		return checked, changed, rules.compose(checked)

	def test_a_plural_category_takes_the_rules_singular(self):
		checked, changed, composed = self._composed("PUMPS")
		self.assertEqual(changed[0]["to"], "PUMP")
		self.assertTrue(composed["item_name"].startswith("PUMP, SUBMERSIBLE"))
		self.assertIn("PUMPS", checked["fields"]["segment_0"]["note"])

	def test_a_tier3_word_takes_its_replacement_across_two_segments(self):
		data = _answer()
		data["name"] = {"CATEGORY": {"value": "CHECK VALVE"}, "MATERIAL": {"value": "PVC"}}
		checked = rules.validate_suggestion(data, description=DESCRIPTION)
		rules.normalize_category(checked)
		self.assertEqual(rules.compose(checked)["item_name"], "VALVE, CHECK, PVC")

	def test_the_sops_and_so_on_is_never_a_segment(self):
		data = {"family": {"value": "vendor"}, "name": {"CATEGORY": {"value": "PROTE"}}}
		checked = rules.validate_suggestion(data)
		rules.normalize_category(checked)
		self.assertEqual(rules.compose(checked)["item_name"], "CIRCUIT BREAKER, SUPPLEMENTARY")

	def test_an_unapproved_category_is_reported_never_repaired(self):
		_checked, changed, composed = self._composed("WIDGETRON")
		self.assertEqual(changed, [])
		review = rules.review(composed, existing_codes=[])
		self.assertEqual(review["verdict"], naming.VERDICT_STOP)
		self.assertEqual(review["by_field"]["segment_0"]["severity"], naming.STOP)

	def test_review_is_the_naming_rules_and_the_guard(self):
		_checked, _changed, composed = self._composed("PUMP")
		review = rules.review(composed, existing_codes=["afs 7731", "OTHER-1"])
		expected = (
			naming.check_code(composed["item_code"])
			+ naming.check_name(composed["item_name"], composed["item_code"])
			+ naming.check_supporting(composed["item_group"], composed["stock_uom"])
			+ naming.check_code_name_agreement(composed["item_code"], composed["item_name"])
		)
		self.assertEqual([f["code"] for f in review["findings"]], [f["code"] for f in expected])
		guard = naming.blocking_findings(
			composed["item_code"], composed["item_name"], ["afs 7731", "OTHER-1"]
		)
		self.assertEqual([f["code"] for f in review["blocking"]], [f["code"] for f in guard])
		self.assertEqual([f["code"] for f in review["blocking"]], [naming.DUPLICATE_CODE_NORMALISED])
		self.assertEqual(review["verdict"], naming.VERDICT_STOP)
		self.assertEqual(review["by_field"]["vendor_part_no"]["severity"], naming.STOP)

	def test_a_name_that_is_just_the_code_is_refused_by_the_guard(self):
		review = rules.review(
			{"family": "vendor", "item_code": "AFS-7731", "item_name": "AFS-7731", "segments": []},
			existing_codes=[],
		)
		self.assertIn(naming.NAME_EQUALS_CODE, [f["code"] for f in review["blocking"]])

	def test_the_rows_carry_label_target_reason_and_verdict(self):
		checked, _changed, composed = self._composed("PUMP")
		rows = rules.field_rows(checked, rules.review(composed, existing_codes=[]))
		keys = [row["key"] for row in rows]
		self.assertEqual(keys[:3], ["family", "vendor_part_no", "segment_0"])
		self.assertEqual(keys[-2:], ["item_group", "stock_uom"])
		first = rows[2]
		self.assertEqual(
			(first["label"], first["segment"], first["verdict"]["severity"]), ("CATEGORY", 0, "OK")
		)
		self.assertEqual(rows[0]["display"], "Vendor part number")


class TestThePhoto(unittest.TestCase):
	def test_the_bytes_say_what_it_is(self):
		self.assertEqual(rules.sniff_image(JPEG), "image/jpeg")
		self.assertEqual(rules.sniff_image(PNG), "image/png")
		self.assertEqual(rules.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8), "image/webp")
		self.assertEqual(rules.sniff_image(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8), "image/heic")
		self.assertEqual(rules.sniff_image(b"\x00\x00\x00\x18ftypmif1" + b"\x00" * 8), "image/heif")
		for bad in (b"%PDF-1.7 " + b"\x00" * 8, b"<svg xmlns='x'>", b"", None, "text"):
			with self.subTest(head=bad):
				self.assertIsNone(rules.sniff_image(bad))

	def test_base64_bare_or_as_a_data_url(self):
		encoded = base64.b64encode(PNG).decode()
		self.assertEqual(rules.decode_image(encoded), PNG)
		self.assertEqual(rules.decode_image(f"data:image/png;base64,{encoded}"), PNG)
		with self.assertRaises(ValueError) as ctx:
			rules.decode_image("not base64!!")
		self.assertEqual(str(ctx.exception), rules.MSG_PHOTO_UNREADABLE)

	def test_a_huge_photo_is_refused_before_it_is_decoded(self):
		with self.assertRaises(ValueError) as ctx:
			rules.decode_image("A" * (rules.IMAGE_MAX_BYTES * 2))
		self.assertEqual(str(ctx.exception), rules.MSG_PHOTO_SIZE)
		self.assertEqual(rules.image_problem(PNG + b"\x00" * rules.IMAGE_MAX_BYTES), rules.MSG_PHOTO_SIZE)
		self.assertEqual(rules.image_problem(b"%PDF-1.7 \x00\x00\x00\x00"), rules.MSG_PHOTO_TYPE)
		self.assertIsNone(rules.image_problem(JPEG))


class TestTheBuildersInPython(unittest.TestCase):
	"""naming.js and codes.js, line for line (test_guided_item runs both on the same inputs)."""

	def test_names_and_codes(self):
		self.assertEqual(
			rules.assemble_name(["elbow", "90", "", "pvc", '2"  sch80', "", "", "extra"]),
			'ELBOW, 90, PVC, 2" SCH80',
		)
		self.assertEqual(rules.clean_part("  a,b  c "), "A B C")
		self.assertEqual(rules.vendor_code(" AQU-98,125 "), "AQU-98125")
		self.assertEqual(rules.consumable_code("elec", "zip tie", '8"'), 'CON-ELEC-ZIP-TIE-8"')
		self.assertEqual(rules.consumable_code("OFFC", "Paper  Towel!", ""), "CON-OFFC-PAPER-TOWEL")
		self.assertEqual(rules.con_part("--a--b--"), "A-B")


# --------------------------------------------------------------------------- the stub


class _File:
	def __init__(self, row):
		self.row = row

	def get(self, key, default=None):
		return self.row.get(key, default)

	def check_permission(self, ptype="read"):
		STATE["perm_checks"].append(("File", ptype, self.row["name"]))
		if not STATE["file_readable"]:
			raise FRAPPE.PermissionError("No permission for File")

	def get_content(self, encodings=None):
		"""v16's File.get_content: with its default encodings a binary file that decodes as
		windows-1252 comes back a str, so the suggester must ask for none."""
		if encodings is None:
			return self.row["content"].decode("latin-1")
		return self.row["content"]


def _install_stubs():
	global FRAPPE, STATE
	STATE = {}
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	class PermissionError(Exception):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe._ = lambda s, *a, **k: s

	def throw(msg="", exc=ValidationError, title=None):
		raise exc(msg)

	frappe.throw = throw

	def whitelist(*dargs, **dkwargs):
		def wrap(fn):
			fn._whitelist = dkwargs
			return fn

		return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

	frappe.whitelist = whitelist
	frappe.session = types.SimpleNamespace(user="parker@example.com")

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		allowed = STATE["perm"].get((doctype, ptype), True)
		STATE["perm_checks"].append((doctype, ptype))
		if throw and not allowed:
			raise PermissionError(f"No permission for {doctype}")
		return allowed

	frappe.has_permission = has_permission

	def get_all(doctype, filters=None, pluck=None, limit=None, **kwargs):
		STATE["reads"].append(doctype)
		if doctype == "File":
			rows = [r for r in STATE["files"] if r["file_url"] == (filters or {}).get("file_url")]
			return [r["name"] for r in rows][: limit or None]
		if doctype == "Item Group":
			return list(STATE["item_groups"])
		if doctype == "Item":
			return [row["item_code"] for row in STATE["corpus"]]
		raise AssertionError(f"unexpected read of {doctype}")

	frappe.get_all = get_all

	def get_doc(doctype, name=None):
		if doctype != "File":
			STATE["writes"].append(("get_doc", doctype))
			raise AssertionError(f"the suggester opened a {doctype}")
		return _File(next(r for r in STATE["files"] if r["name"] == name))

	frappe.get_doc = get_doc

	def write(kind):
		def record(*args, **kwargs):
			STATE["writes"].append((kind, args))

		return record

	frappe.new_doc = write("new_doc")
	frappe.db = types.SimpleNamespace(
		set_value=write("db.set_value"),
		sql=write("db.sql"),
		commit=write("db.commit"),
		delete=write("db.delete"),
		insert=write("db.insert"),
	)

	def log_error(*args, **kwargs):
		STATE["logged"].append((args, kwargs))

	frappe.log_error = log_error
	frappe.get_traceback = lambda *a, **k: STATE["writes"].append(("get_traceback", a)) or "traceback"

	counts = {}

	def incrby(key, amount=1):
		if STATE["cache_broken"]:
			raise RuntimeError("redis gone")
		counts[key] = counts.get(key, 0) + amount
		return counts[key]

	frappe.cache = types.SimpleNamespace(
		make_key=lambda key: f"site:{key}", incrby=incrby, expire=lambda key, seconds: None
	)
	STATE["counts"] = counts

	utils = types.ModuleType("frappe.utils")
	utils.cstr = lambda value: "" if value is None else str(value)
	utils.cint = lambda value: int(float(value or 0))
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	FRAPPE = frappe

	# The Google service account the Gemini client signs in with.
	drive_pkg = types.ModuleType("erpnext_enhancements.google_drive")
	drive_utils = types.ModuleType("erpnext_enhancements.google_drive.drive_utils")
	drive_utils.get_service_account_info = lambda: STATE["service_account"]
	drive_pkg.drive_utils = drive_utils
	sys.modules["erpnext_enhancements.google_drive"] = drive_pkg
	sys.modules["erpnext_enhancements.google_drive.drive_utils"] = drive_utils

	# The model.
	gemini = types.ModuleType("erpnext_enhancements.api.gemini")

	def generate(prompt, system_instruction, settings, feature="unknown", model_id=None, **kwargs):
		STATE["model_calls"].append(
			{"prompt": prompt, "system": system_instruction, "feature": feature, **kwargs}
		)
		if STATE["model_raises"]:
			raise STATE["model_raises"]
		return STATE["reply"], "thoughts"

	gemini.generate_content_with_vertex_ai = generate
	sys.modules["erpnext_enhancements.api.gemini"] = gemini

	# Stock Scan's preview: the naming check's own answer.
	stock_scan = types.ModuleType("erpnext_enhancements.api.stock_scan")

	def preview_new_item(item_code, item_name=None, item_group=None, stock_uom=None):
		STATE["previews"].append((item_code, item_name, item_group, stock_uom))
		return dict(STATE["preview"])

	stock_scan.preview_new_item = preview_new_item
	sys.modules["erpnext_enhancements.api.stock_scan"] = stock_scan

	# The catalog read.
	item_naming = types.ModuleType("erpnext_enhancements.inventory_enhancements.item_naming")
	item_naming.read_corpus = lambda include_deleted=True: (
		[dict(row) for row in STATE["corpus"]],
		{"visible": len(STATE["corpus"]), "total": len(STATE["corpus"])},
	)
	sys.modules["erpnext_enhancements.inventory_enhancements.item_naming"] = item_naming

	# FAC's BaseTool, the attribute contract only.
	base_tool = types.ModuleType("frappe_assistant_core.core.base_tool")

	class BaseTool:
		def __init__(self):
			self.name = ""
			self.description = ""
			self.inputSchema = {}
			self.requires_permission = None
			self.default_config = {}

	base_tool.BaseTool = BaseTool
	fac = types.ModuleType("frappe_assistant_core")
	fac_core = types.ModuleType("frappe_assistant_core.core")
	fac.core = fac_core
	fac_core.base_tool = base_tool
	sys.modules["frappe_assistant_core"] = fac
	sys.modules["frappe_assistant_core.core"] = fac_core
	sys.modules["frappe_assistant_core.core.base_tool"] = base_tool


_STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.google_drive",
	"erpnext_enhancements.google_drive.drive_utils",
	"erpnext_enhancements.api.gemini",
	"erpnext_enhancements.api.stock_scan",
	"erpnext_enhancements.inventory_enhancements.item_naming",
	"frappe_assistant_core",
	"frappe_assistant_core.core",
	"frappe_assistant_core.core.base_tool",
	"erpnext_enhancements.api.item_suggest",
	"erpnext_enhancements.assistant_tools",
	"erpnext_enhancements.assistant_tools._gate",
	"erpnext_enhancements.assistant_tools.item_naming_suggest",
)


def setUpModule():
	global api, tool_module
	for mod in _STUBBED:
		sys.modules.pop(mod, None)
	_install_stubs()
	_reset()
	from erpnext_enhancements.api import item_suggest
	from erpnext_enhancements.assistant_tools import item_naming_suggest

	api = item_suggest
	tool_module = item_naming_suggest


def tearDownModule():
	for mod in _STUBBED:
		sys.modules.pop(mod, None)


def _reset(**overrides):
	counts = STATE.get("counts", {})
	counts.clear()
	STATE.update(
		{
			"perm": {},
			"perm_checks": [],
			"reads": [],
			"writes": [],
			"logged": [],
			"model_calls": [],
			"model_raises": None,
			"reply": json.dumps(_answer()),
			"previews": [],
			"preview": {
				"checked": True,
				"exists": None,
				"similar": [],
				"blocking": [],
				"advice": [],
				"will_refuse": False,
			},
			"service_account": {"client_email": "svc@example.iam.gserviceaccount.com"},
			"cache_broken": False,
			"file_readable": True,
			"files": [],
			"item_groups": ["Pumps", "Products", "All Item Groups"],
			"corpus": [
				{
					"item_code": "806-020",
					"item_name": 'ELBOW, 90, SOC, PVC, 2" SCH80',
					"item_group": "Products",
					"stock_uom": "Unit",
				},
				{
					"item_code": "EX-1200",
					"item_name": "PUMP, SUBMERSIBLE, 1200 GPH",
					"item_group": "Pumps",
					"stock_uom": "Nos",
				},
				{
					"item_code": "PDT-0017",
					"item_name": "CONTROLLER, SHOW",
					"item_group": "Products",
					"stock_uom": "Unit",
				},
			],
			"counts": counts,
		}
	)
	STATE.update(overrides)


# --------------------------------------------------------------------------- the endpoint


class TestTheEndpoint(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_post_only_and_only_for_whoever_may_create_an_item(self):
		self.assertEqual(api.suggest_item._whitelist, {"methods": ["POST"]})
		_reset(perm={("Item", "create"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			api.suggest_item(description=DESCRIPTION)
		self.assertEqual(STATE["perm_checks"][0], ("Item", "create"))
		self.assertEqual(STATE["model_calls"], [], "no model call for someone who cannot create an Item")

	def test_a_suggestion(self):
		out = api.suggest_item(description=DESCRIPTION)
		self.assertTrue(out["ok"], out)
		self.assertEqual(out["item_code"], "AFS-7731")
		self.assertEqual(out["item_name"], "PUMP, SUBMERSIBLE, STAINLESS, 1/2 HP 115 V")
		self.assertEqual(out["family"], "vendor")
		self.assertIsNone(out["number_from_blocks"])
		self.assertEqual(out["note"], rules.REVIEW_NOTE)
		self.assertEqual([row["key"] for row in out["fields"]][:3], ["family", "vendor_part_no", "segment_0"])
		self.assertEqual(out["review"]["verdict"], naming.VERDICT_PASS, out["review"])
		self.assertEqual(out["check"]["checked"], True)

	def test_the_model_is_asked_for_json_with_the_vocabulary(self):
		api.suggest_item(description=DESCRIPTION, vendor_part_no="AFS-7731")
		call = STATE["model_calls"][0]
		self.assertEqual(call["feature"], "item_naming_suggest")
		self.assertEqual(call["response_mime_type"], "application/json")
		self.assertEqual(call["thinking_level"], api.THINKING_LEVEL)
		self.assertEqual(call["timeout"], api.TIMEOUT_SECONDS)
		self.assertIsNone(call["images"])
		self.assertEqual(call["system"], rules.SYSTEM_PROMPT)
		self.assertIn("Item groups: Products, Pumps", call["prompt"], "leaves only, never the root")
		self.assertIn("Units: Nos, Unit", call["prompt"])
		self.assertIn("- PUMP, SUBMERSIBLE, 1200 GPH", call["prompt"], "the nearest catalog names")
		self.assertNotIn("EX-1200", call["prompt"], "names, never codes")
		self.assertIn("[removed]", call["prompt"])
		self.assertNotIn("rep@example.com", call["prompt"])

	def test_the_suggestion_is_previewed_as_the_naming_check_previews_it(self):
		_reset(
			preview={
				"checked": True,
				"exists": {"item_code": "AFS-7731", "item_name": "PUMP"},
				"similar": [],
				"blocking": [],
				"advice": [],
				"will_refuse": False,
			}
		)
		out = api.suggest_item(description=DESCRIPTION)
		self.assertEqual(
			STATE["previews"], [("AFS-7731", "PUMP, SUBMERSIBLE, STAINLESS, 1/2 HP 115 V", "Pumps", "Unit")]
		)
		self.assertEqual(out["check"]["exists"]["item_code"], "AFS-7731")

	def test_the_guard_reads_every_code(self):
		_reset(
			corpus=[
				{
					"item_code": "afs 7731",
					"item_name": "PUMP, OLD",
					"item_group": "Pumps",
					"stock_uom": "Unit",
				}
			]
		)
		out = api.suggest_item(description=DESCRIPTION)
		self.assertEqual([f["code"] for f in out["review"]["blocking"]], [naming.DUPLICATE_CODE_NORMALISED])

	def test_it_writes_nothing(self):
		api.suggest_item(description=DESCRIPTION, image=base64.b64encode(PNG).decode())
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(STATE["logged"], [])

	def test_never_a_number_from_the_blocks(self):
		data = _answer(
			family={"value": "product", "reason": "Take PDT-0042."}, descriptor={"value": "SRV-007"}
		)
		data["name"]["KEY FEATURE"] = {"value": "PDT-0042", "reason": "SRV-007 is close"}
		data["missing"] = ["PDT-0042 is free"]
		_reset(reply=json.dumps(data))
		out = api.suggest_item(description="our own controller")
		text = json.dumps({k: v for k, v in out.items() if k != "check"})
		self.assertNotIn("0042", text)
		self.assertNotIn("SRV-007", text)
		self.assertEqual(out["item_code"], "")
		self.assertEqual(out["number_from_blocks"], "PDT")

	def test_nothing_to_go_on(self):
		out = api.suggest_item(description="  ", vendor_part_no="")
		self.assertEqual(out, {"ok": False, "message": rules.MSG_EMPTY})
		self.assertEqual(STATE["model_calls"], [])


class TestGracefulFailure(unittest.TestCase):
	def setUp(self):
		_reset()

	def _secrets_absent(self):
		blob = json.dumps(STATE["logged"], default=str)
		for secret in (
			"Acme Fountain Supply",
			"AFS-7731",
			"Approved categories",
			base64.b64encode(PNG).decode()[:20],
		):
			self.assertNotIn(secret, blob)
		for args, kwargs in STATE["logged"]:
			self.assertEqual(
				args, (), "keyword arguments: v16 reads a positional first argument as the title"
			)
			self.assertEqual(set(kwargs), {"title", "message"})
		self.assertNotIn("get_traceback", [w[0] for w in STATE["writes"]])

	def test_ai_not_set_up(self):
		_reset(service_account=None)
		out = api.suggest_item(description=DESCRIPTION)
		self.assertEqual(out, {"ok": False, "message": rules.MSG_NOT_SET_UP})
		self.assertEqual(STATE["model_calls"], [])

	def test_a_timeout(self):
		_reset(
			model_raises=Exception(
				"Gemini Enterprise Agent Platform request failed: ReadTimeout: read timed out"
			)
		)
		out = api.suggest_item(description=DESCRIPTION, image=base64.b64encode(PNG).decode())
		self.assertEqual(out, {"ok": False, "message": rules.MSG_TIMEOUT})
		self.assertEqual(len(STATE["logged"]), 1)
		self.assertIn("Exception: Gemini", STATE["logged"][0][1]["message"])
		self._secrets_absent()

	def test_an_error(self):
		_reset(model_raises=Exception("403 Forbidden"))
		out = api.suggest_item(description=DESCRIPTION)
		self.assertEqual(out["message"], rules.MSG_UNAVAILABLE)
		self._secrets_absent()

	def test_an_answer_that_is_not_json(self):
		_reset(reply="Sure! Here is a name: PUMP, SUBMERSIBLE")
		out = api.suggest_item(description=DESCRIPTION)
		self.assertEqual(out, {"ok": False, "message": rules.MSG_BAD_ANSWER})
		self._secrets_absent()

	def test_an_empty_answer(self):
		_reset(reply="{}")
		out = api.suggest_item(description=DESCRIPTION)
		self.assertFalse(out["ok"])
		self.assertEqual(out["message"], rules.MSG_NOTHING)

	def test_the_preview_failing_leaves_the_suggestion(self):
		stock_scan = sys.modules["erpnext_enhancements.api.stock_scan"]
		original = stock_scan.preview_new_item

		def broken(*args):
			raise RuntimeError("corpus too large")

		stock_scan.preview_new_item = broken
		try:
			out = api.suggest_item(description=DESCRIPTION)
		finally:
			stock_scan.preview_new_item = original
		self.assertTrue(out["ok"])
		self.assertEqual(out["check"], {"checked": False})

	def test_a_rate_limit_per_person(self):
		for _ in range(api.RATE_LIMIT):
			self.assertTrue(api.suggest_item(description=DESCRIPTION)["ok"])
		out = api.suggest_item(description=DESCRIPTION)
		self.assertEqual(out["message"], rules.MSG_BUSY)
		self.assertEqual(len(STATE["model_calls"]), api.RATE_LIMIT)
		FRAPPE.session.user = "clegg@example.com"
		try:
			self.assertTrue(api.suggest_item(description=DESCRIPTION)["ok"], "another person's budget")
		finally:
			FRAPPE.session.user = "parker@example.com"

	def test_a_cache_that_cannot_count_lets_the_call_through(self):
		_reset(cache_broken=True)
		self.assertTrue(api.suggest_item(description=DESCRIPTION)["ok"])


class TestThePhotoAtTheEndpoint(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_photo_goes_to_the_model_as_its_own_type(self):
		api.suggest_item(description="", image="data:image/jpeg;base64," + base64.b64encode(PNG).decode())
		images = STATE["model_calls"][0]["images"]
		self.assertEqual(
			images,
			[{"mime_type": "image/png", "data": base64.b64encode(PNG).decode()}],
			"the bytes decide, not the label",
		)
		self.assertIn("A photo of the item is attached.", STATE["model_calls"][0]["prompt"])

	def test_a_bad_photo_is_refused_before_the_model(self):
		for image, message in (
			(base64.b64encode(b"%PDF-1.7 " + b"\x00" * 16).decode(), rules.MSG_PHOTO_TYPE),
			("not base64 at all!", rules.MSG_PHOTO_UNREADABLE),
			("A" * (rules.IMAGE_MAX_BYTES * 2), rules.MSG_PHOTO_SIZE),
		):
			with self.subTest(message=message):
				_reset()
				self.assertEqual(
					api.suggest_item(description=DESCRIPTION, image=image), {"ok": False, "message": message}
				)
				self.assertEqual(STATE["model_calls"], [])

	def test_a_file_the_person_can_read(self):
		_reset(
			files=[
				{
					"name": "F-1",
					"file_url": "/private/files/pump.jpg",
					"file_size": len(JPEG),
					"content": JPEG,
				}
			]
		)
		out = api.suggest_item(description=DESCRIPTION, file_url="/private/files/pump.jpg")
		self.assertTrue(out["ok"])
		self.assertIn(("File", "read", "F-1"), STATE["perm_checks"])
		self.assertEqual(STATE["model_calls"][0]["images"][0]["mime_type"], "image/jpeg")

	def test_a_file_the_person_cannot_read_is_refused(self):
		_reset(
			files=[{"name": "F-1", "file_url": "/private/files/pump.jpg", "file_size": 10, "content": JPEG}],
			file_readable=False,
		)
		with self.assertRaises(FRAPPE.PermissionError):
			api.suggest_item(description=DESCRIPTION, file_url="/private/files/pump.jpg")
		self.assertEqual(STATE["model_calls"], [])

	def test_a_missing_or_huge_file(self):
		self.assertEqual(
			api.suggest_item(description=DESCRIPTION, file_url="/files/none.png")["message"],
			rules.MSG_PHOTO_UNREADABLE,
		)
		_reset(
			files=[
				{
					"name": "F-2",
					"file_url": "/files/big.png",
					"file_size": rules.IMAGE_MAX_BYTES + 1,
					"content": PNG,
				}
			]
		)
		self.assertEqual(
			api.suggest_item(description=DESCRIPTION, file_url="/files/big.png")["message"],
			rules.MSG_PHOTO_SIZE,
		)


class TestTheAssistantTool(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_it_is_the_endpoints_call_behind_item_create(self):
		tool = tool_module.ItemNamingSuggest()
		self.assertEqual(tool.name, "item_naming_suggest")
		self.assertEqual(tool.requires_permission, "Item")
		self.assertEqual(tool.inputSchema["required"], ["description"])
		self.assertNotIn("file_url", tool.inputSchema["properties"], "no File through an assistant call")
		out = tool.execute({"description": DESCRIPTION, "vendor_part_no": "AFS-7731"})
		self.assertTrue(out["ok"])
		self.assertEqual(out["item_code"], "AFS-7731")
		_reset(perm={("Item", "create"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			tool.execute({"description": DESCRIPTION})
		self.assertEqual(STATE["model_calls"], [])
		with self.assertRaises(FRAPPE.ValidationError):
			tool.execute({"description": " "})

	def test_read_only_in_the_gate_and_annotated_so(self):
		from erpnext_enhancements.assistant_tools import _gate

		self.assertIn("item_naming_suggest", _gate.EXPLICIT_READONLY)
		self.assertNotIn("item_naming_suggest", _gate.APP_MUTATING)
		self.assertIs(tool_module.ItemNamingSuggest().annotations.get("readOnlyHint"), True)

	def test_registered_in_hooks(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.assistant_tools.item_naming_suggest.ItemNamingSuggest",', hooks)


class TestTheSource(unittest.TestCase):
	"""What the endpoint's code may never do, read from its syntax tree (docstrings and comments
	name some of these on purpose)."""

	def _calls(self, path):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		return [
			node
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		]

	def test_no_write_and_no_traceback(self):
		for path in (API_FILE, TOOL_FILE, APP / "inventory_enhancements" / "item_suggest_rules.py"):
			names = {call.func.attr for call in self._calls(path)}
			with self.subTest(file=path.name):
				for forbidden in (
					"insert",
					"save",
					"submit",
					"delete_doc",
					"set_value",
					"db_set",
					"new_doc",
					"get_traceback",
					"commit",
				):
					self.assertNotIn(forbidden, names)

	def test_every_log_error_uses_keywords(self):
		for call in self._calls(API_FILE):
			if call.func.attr == "log_error":
				self.assertEqual(call.args, [])
				self.assertEqual({kw.arg for kw in call.keywords}, {"title", "message"})

	def test_the_pure_module_imports_no_frappe(self):
		tree = ast.parse(
			(APP / "inventory_enhancements" / "item_suggest_rules.py").read_text(encoding="utf-8")
		)
		imported = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				imported.update(alias.name.split(".")[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom):
				imported.add((node.module or "").split(".")[0])
		self.assertNotIn("frappe", imported)

	def test_wired_into_ci(self):
		ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_item_suggest", ci)


if __name__ == "__main__":
	unittest.main()
