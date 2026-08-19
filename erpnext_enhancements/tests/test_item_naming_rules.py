# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Item naming schema, executed rather than inspected. Bench-free, unittest.

:mod:`inventory_enhancements.item_naming_rules` imports nothing but ``re`` and ``typing``,
which is what lets this run in the bench-free tier — the same split
:mod:`chat.governance.drift_rules` already uses, and for the reason that module gives:
there is no Frappe integration-test job in CI, so bench-free code is the only code that
runs on every push.

**The property this file exists for** is the one that shipped broken in the hand-written
validator prompt this module replaces:

    A block-allocated number is occupied by any code that begins with the prefix and
    exactly the declared number of digits, however badly the rest of that record is
    named — and by nothing whose digits run wider than the declared width.

Both halves were live bugs on production and both are asserted here against the literal
strings that produced them, not against a paraphrase. :class:`BlockSlotTrapTest` is that
rule; :class:`ProductionCorpusTest` is the corpus itself.

Every fixture in this file is a verbatim `item_code` / `item_name` pair read from ERPNext
Production on 19 August 2026. They are quoted rather than invented because the defects
that matter here are all defects of *character* — a trailing space, a five-digit code
where four were meant, a schedule in the wrong segment — and an invented fixture rounds
exactly those off.
"""

import unittest

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

# --- verbatim production fixtures (prod_items, verified 2026-08-19) -------------

#: The three records that make the four-digit boundary rule non-obvious.
PDT_0008_COPY = "PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy"
PDT_00051_PLACEHOLDER = "PDT-00051"
PDT_00XX_PLACEHOLDER = "PDT-00XX"

#: A representative slice of the live corpus: the PDT family with both placeholder
#: styles, the SOP Appendix D defects, and the breaker families that collide.
CORPUS = [
	{"item_code": "PDT-0000", "item_name": "PDT-0000 SPLASH WIZARD BASIC", "item_group": "All Item Groups"},
	{"item_code": "PDT-00000 (deleted)", "item_name": "PDT-00000 (deleted)", "item_group": "All Item Groups"},
	{"item_code": "PDT-00013 (deleted)", "item_name": "PDT-00013 (deleted)", "item_group": "All Item Groups"},
	{"item_code": "PDT-00040 (deleted)", "item_name": "PDT-00040 (deleted)", "item_group": "All Item Groups"},
	{"item_code": PDT_00051_PLACEHOLDER, "item_name": "PDT-00051", "item_group": "All Item Groups"},
	{"item_code": PDT_00XX_PLACEHOLDER, "item_name": "PDT-00XX", "item_group": "All Item Groups"},
	{"item_code": PDT_0008_COPY, "item_name": PDT_0008_COPY, "item_group": "All Item Groups"},
	{"item_code": "PDT-0009", "item_name": "PDT-0009 VFD BYPASS W/MOTOR PROTECTION, 7.5HP", "item_group": "All Item Groups"},
	{"item_code": "PDT-0040", "item_name": "PDT-0040 STILLWATER E-STOP SYSTEM", "item_group": "All Item Groups"},
	{"item_code": "PDT-0051", "item_name": "LEVEL SENSOR, FLOAT, VERTICAL MOUNT, 316 SS", "item_group": "All Item Groups"},
	{"item_code": "PDT-0261", "item_name": 'WATERSTOP FITTING, 316 SS, 1"', "item_group": "Products"},
	{"item_code": "SRV-015 (deleted)", "item_name": "SRV-015 (deleted)", "item_group": "All Item Groups"},
	{"item_code": "SRV-202", "item_name": "SERVICE VISIT", "item_group": "Service"},
	# SOP D-1: an HP 952 ink cartridge wearing a water level sensor's name.
	{
		"item_code": "CON-OFFC-INK-HP-952-COLOR",
		"item_name": "WATER LEVEL SENSOR, REED SWITCH, M10 THREAD",
		"item_group": "Office",
	},
	{
		"item_code": "PLS-081B-6VAI",
		"item_name": "WATER LEVEL SENSOR, REED SWITCH, M10 THREAD",
		"item_group": "Products",
	},
	# SOP D-2: four breakers, one name, no way to tell them apart.
	{"item_code": "GMCB-1B-1", "item_name": "PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR", "item_group": "Electrical"},
	{"item_code": "GMCB-1B-6", "item_name": "PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR", "item_group": "Electrical"},
	{"item_code": "GMCB-1B-10", "item_name": "PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR", "item_group": "Electrical"},
	{"item_code": "GMCB-1C-10", "item_name": "PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR", "item_group": "Electrical"},
	{"item_code": "CON-ELEC-BREAK-1", "item_name": "BREAKER, 1 AMP", "item_group": "Electrical"},
	# SOP D-3: the same physical pump, two codes, two word orders. Normalises differently.
	{"item_code": "4010052576503", "item_name": "PUMP, VARIONAUT, 150, 24 V, /DMX/02", "item_group": "Products"},
	{"item_code": "57650", "item_name": "PUMP, VARIONAUT 150, DMX/02, 24 V", "item_group": "Service"},
	# SOP D-3 again: this pair DOES collide, but only because punctuation is stripped.
	{"item_code": "100GM DC24V DMX512 3W", "item_name": "LIGHT, SUBMERSIBLE, RGB-DMX, SS, DC24V 3W", "item_group": "Products"},
	{"item_code": "xyh100gm-3x1w", "item_name": "Light, Submersible, RGB_DMX,SS, DC24V 3W", "item_group": "Products"},
	# SOP D-5: category errors.
	{"item_code": "802-247S", "item_name": 'HOSE, TEE, PVC, 2"X1/2", GREY, RDCR SXFPT', "item_group": "Products"},
	{"item_code": "837-249", "item_name": 'RED BUSH, SPIGOT X SOC, PVC, SCH80, 2" X 1"', "item_group": "Products"},
	{"item_code": "140342", "item_name": "SAND FILTER, PENTAIR TR140C-3", "item_group": "Products"},
	# SOP D-6: character-level defects. Note the trailing comma AND space.
	{"item_code": "B01M3Y86MY", "item_name": "PLATE, WALL, COROSIVE RESISTANT, STAINLESS STEEL, ", "item_group": "Products"},
	{"item_code": "P1119 SS", "item_name": 'PIPE CLAMP, STRUT, SS304, 3", 1-5/8" SERIES ', "item_group": "Products"},
	# Compliant records, for the PASS case and as similarity neighbours.
	{"item_code": "2622-010", "item_name": 'VALVE, BALL, UTILITY, SOC, PVC, 1", EPDM', "item_group": "Products"},
	{"item_code": "2622-025", "item_name": 'VALVE, BALL, UTILITY, SOC, PVC, EPDM, 2 - 1/2"', "item_group": "Products"},
	{"item_code": "806-020", "item_name": 'ELBOW, 90, SOC, PVC, 2" SCH80', "item_group": "Products"},
]

BRANDS = ("ABB", "PENTAIR", "SPEARS")


def codes_of(findings):
	return {f["code"] for f in findings}


class EvidenceTest(unittest.TestCase):
	"""The rule the module is shaped around, asserted rather than commented."""

	def test_every_finding_code_states_what_it_is_grounded_in(self):
		for code in rules.CODES:
			self.assertFalse(
				rules.missing_evidence(code),
				f"{code} has no EVIDENCE entry, so nothing says what it is grounded in",
			)

	def test_a_code_without_evidence_cannot_be_emitted(self):
		"""The enforcement point: a check added without writing down its grounding fails here
		rather than shipping as folklore."""
		with self.assertRaises(ValueError):
			rules.finding("not_a_real_code", "hello")

	def test_every_severity_is_one_of_the_three(self):
		for code, severity in rules.SEVERITY.items():
			self.assertIn(severity, rules.SEVERITIES, code)


class BlockSlotTrapTest(unittest.TestCase):
	"""The two traps, against the literal strings that produced them.

	Trap 1: an anchored regex hides a taken number, because the record's code carries
	trailing text. Trap 2: unanchoring it invents taken numbers, because the five-digit
	QuickBooks family collapses onto four-digit slots.
	"""

	def test_trailing_text_does_not_free_a_number(self):
		"""`^PDT-[0-9]{4}$` reports 0008 free. It is not: this is the only record of the
		5HP VFD bypass."""
		self.assertEqual(rules.block_slot(PDT_0008_COPY, "PDT"), 8)

	def test_a_fifth_digit_is_a_different_code_family(self):
		self.assertIsNone(rules.block_slot(PDT_00051_PLACEHOLDER, "PDT"))
		self.assertIsNone(rules.block_slot("PDT-00000 (deleted)", "PDT"))
		self.assertIsNone(rules.block_slot("PDT-00013 (deleted)", "PDT"))

	def test_the_four_digit_and_five_digit_codes_are_different_items(self):
		self.assertEqual(rules.block_slot("PDT-0051", "PDT"), 51)
		self.assertIsNone(rules.block_slot("PDT-00051", "PDT"))

	def test_a_literal_placeholder_occupies_nothing(self):
		self.assertIsNone(rules.block_slot(PDT_00XX_PLACEHOLDER, "PDT"))

	def test_a_tombstone_still_holds_its_number(self):
		"""`SRV-015 (deleted)` is retired, and retired is not free — reissuing 015 would put
		two meanings on one number."""
		self.assertEqual(rules.block_slot("SRV-015 (deleted)", "SRV"), 15)

	def test_occupancy_names_the_holder_rather_than_returning_a_bool(self):
		occupied = rules.occupancy([row["item_code"] for row in CORPUS], "PDT")
		self.assertEqual(occupied[8], [PDT_0008_COPY])
		self.assertNotIn(13, occupied, "PDT-00013 (deleted) must not occupy the four-digit 0013")
		self.assertEqual(occupied[51], ["PDT-0051"])

	def test_the_block_report_offers_gaps_and_never_allocates(self):
		occupied = rules.occupancy([row["item_code"] for row in CORPUS], "PDT")
		report = rules.block_report(occupied, 8)
		self.assertEqual((report["block_start"], report["block_end"]), (0, 99))
		self.assertIn(8, report["occupied"])
		self.assertNotIn(8, report["free"])
		for free in (4, 5, 6, 7, 13):
			self.assertIn(free, report["free"], f"{free} is genuinely unused in the 00xx block")


class VocabularyTest(unittest.TestCase):
	def test_slashed_tier2_entries_admit_each_alternative(self):
		"""`FUSE / FUSEHOLDER` is one Appendix A row and two admissible category words.
		Without the split, Tier 3's own prescribed replacement `FUSES -> FUSE` would be
		rejected as an unapproved category."""
		self.assertIn("FUSE", rules.APPROVED_CATEGORIES)
		self.assertIn("FUSEHOLDER", rules.APPROVED_CATEGORIES)
		self.assertIn("CABLE", rules.APPROVED_CATEGORIES)
		self.assertIn("WIRE", rules.APPROVED_CATEGORIES)

	def test_multiword_categories_survive(self):
		for category in ("PIPE CLAMP", "TERMINAL BLOCK", "LINK SEAL", "WATERSTOP FITTING"):
			self.assertIn(category, rules.APPROVED_CATEGORIES, category)

	def test_the_sop_replacement_that_points_at_an_undeclared_category_is_flagged(self):
		"""SOP Appendix A Tier 3 sends `SUBPANELT` to `PANEL, SUB`, and `PANEL` is on
		neither Tier 1 nor Tier 2. Computed, not listed, so it stays true if Appendix A
		changes — the point being that a validator must never hand somebody a correction
		that fails its own check."""
		self.assertIn("SUBPANELT", rules.TIER3_REPLACEMENT_UNAPPROVED)
		self.assertNotIn("BREAKER", rules.TIER3_REPLACEMENT_UNAPPROVED)


class NameCheckTest(unittest.TestCase):
	def test_trailing_comma_and_space_together(self):
		"""The live offender has both, which is why `LIKE '%,'` finds nothing on a corpus
		that has one."""
		found = codes_of(rules.check_name("PLATE, WALL, COROSIVE RESISTANT, STAINLESS STEEL, "))
		self.assertIn(rules.NAME_TRAILING_COMMA, found)
		self.assertIn(rules.NAME_EDGE_WHITESPACE, found)

	def test_trailing_space_alone(self):
		"""MariaDB's PAD SPACE collation makes `item_name <> TRIM(item_name)` always false.
		Compared by length here, where a trailing space is still a character."""
		found = codes_of(rules.check_name('PIPE CLAMP, STRUT, SS304, 3", 1-5/8" SERIES '))
		self.assertIn(rules.NAME_EDGE_WHITESPACE, found)

	def test_mixed_case_and_missing_separator_space(self):
		found = codes_of(rules.check_name("Light, Submersible, RGB_DMX,SS, DC24V 3W"))
		self.assertIn(rules.NAME_NOT_UPPERCASE, found)
		self.assertIn(rules.NAME_SEPARATOR_SPACING, found)

	def test_tier3_category_names_its_replacement(self):
		findings = rules.check_name("RED BUSH, SPIGOT X SOC, PVC, SCH80, 2\" X 1\"")
		tier3 = [f for f in findings if f["code"] == rules.NAME_CATEGORY_TIER3]
		self.assertEqual(len(tier3), 1)
		self.assertEqual(tier3[0]["suggestion"], "BUSHING, REDUCER")

	def test_a_schedule_alone_in_a_segment(self):
		found = codes_of(rules.check_name('RED BUSH, SPIGOT X SOC, PVC, SCH80, 2" X 1"'))
		self.assertIn(rules.NAME_SCHEDULE_OWN_SEGMENT, found)

	def test_sub_type_led_name(self):
		found = codes_of(rules.check_name("SAND FILTER, PENTAIR TR140C-3"))
		self.assertIn(rules.NAME_CATEGORY_TIER3, found)

	def test_grey_and_the_word_inch(self):
		found = codes_of(rules.check_name("COUPLING, REPAIR, PVC, 1 INCH, GREY"))
		self.assertIn(rules.NAME_SIZE_WORD_INCH, found)
		self.assertIn(rules.NAME_COLOUR_GREY, found)

	def test_only_one_category_finding_ever(self):
		"""`SAND FILTER` is at once unapproved, sub-type led and on Tier 3. Saying so three
		times buries the sentence that names the fix."""
		findings = rules.check_name("SAND FILTER, PENTAIR TR140C-3")
		category_codes = {
			rules.NAME_CATEGORY_TIER3,
			rules.NAME_CATEGORY_PLURAL,
			rules.NAME_CATEGORY_UNAPPROVED,
			rules.NAME_CATEGORY_IS_SIZE,
			rules.NAME_CATEGORY_IS_BRAND,
		}
		self.assertEqual(len(codes_of(findings) & category_codes), 1)

	def test_plural_only_fires_when_the_singular_is_approved(self):
		self.assertIn(rules.NAME_CATEGORY_PLURAL, codes_of(rules.check_name("BAGS, TRASH, 55 GAL")))
		# ...and never on a word that merely ends in S.
		self.assertNotIn(rules.NAME_CATEGORY_PLURAL, codes_of(rules.check_name("XYZZYS, SOMETHING")))
		self.assertNotIn(rules.NAME_CATEGORY_PLURAL, codes_of(rules.check_name("GLASSES, SAFETY")))

	def test_irregular_plurals(self):
		"""`BATTERIES` is live on five records and `BATTERIE` is not a word, so stripping the
		trailing S alone would send it to STOP-unapproved and bury a one-character fix."""
		for plural, singular in (("BATTERIES", "BATTERY"), ("FERRULES", "FERRULE"), ("PENS", "PEN")):
			findings = [f for f in rules.check_name(f"{plural}, SOMETHING") if f["code"] == rules.NAME_CATEGORY_PLURAL]
			self.assertEqual(len(findings), 1, plural)
			self.assertEqual(findings[0]["suggestion"], singular, plural)

	def test_size_led_and_brand_led_names(self):
		self.assertIn(rules.NAME_CATEGORY_IS_SIZE, codes_of(rules.check_name('2-1/2 PVC, SOC')))
		self.assertIn(
			rules.NAME_CATEGORY_IS_BRAND,
			codes_of(rules.check_name("ABB, N/F, SW, 600V, 40A", brands=BRANDS)),
		)

	def test_an_unknown_brand_is_not_silently_excused(self):
		"""A brand nobody has recorded falls through to `unapproved`, which is a STOP. The
		alternative — passing it — is the failure this whole check exists to prevent."""
		found = codes_of(rules.check_name("NEVERHEARDOFIT, THING", brands=BRANDS))
		self.assertIn(rules.NAME_CATEGORY_UNAPPROVED, found)

	def test_a_compliant_name_produces_nothing(self):
		self.assertEqual(rules.check_name('ELBOW, 90, SOC, PVC, 2" SCH80'), [])

	def test_name_repeating_the_code(self):
		found = codes_of(rules.check_name(PDT_00XX_PLACEHOLDER, code=PDT_00XX_PLACEHOLDER))
		self.assertIn(rules.NAME_EQUALS_CODE, found)


class CodeCheckTest(unittest.TestCase):
	def test_families(self):
		self.assertEqual(rules.classify_code_family("806-020"), rules.FAMILY_VENDOR)
		self.assertEqual(rules.classify_code_family("CON-ELEC-FUSE-2"), rules.FAMILY_CONSUMABLE)
		self.assertEqual(rules.classify_code_family("PDT-0016"), rules.FAMILY_PRODUCT)
		self.assertEqual(rules.classify_code_family("SRV-202"), rules.FAMILY_SERVICE)

	def test_a_malformed_reserved_code_stays_in_its_family(self):
		"""`PDT-00XX` is a broken product code, not a vendor part number that happens to
		start with PDT. Classifying it as vendor would excuse it."""
		self.assertEqual(rules.classify_code_family(PDT_00XX_PLACEHOLDER), rules.FAMILY_PRODUCT)
		self.assertIn(
			rules.CODE_RESERVED_PREFIX_MALFORMED, codes_of(rules.check_code(PDT_00XX_PLACEHOLDER))
		)

	def test_comma_and_working_suffix(self):
		found = codes_of(rules.check_code(PDT_0008_COPY))
		self.assertIn(rules.CODE_HAS_COMMA, found)
		self.assertIn(rules.CODE_WORKING_SUFFIX, found)

	def test_unknown_consumable_group(self):
		found = codes_of(rules.check_code("CON-XXXX-THING"))
		self.assertIn(rules.CODE_CONSUMABLE_GROUP_UNKNOWN, found)

	def test_a_vendor_part_number_is_left_alone(self):
		"""SOP §5 Step 2.1: character for character, no prefix, no stripped zeros, no case
		change. A check that 'tidied' 806-020 would destroy the schedule and size it encodes."""
		self.assertEqual(rules.check_code("806-020"), [])


class CodeNameAgreementTest(unittest.TestCase):
	def test_the_hp_952_cartridge(self):
		"""SOP D-1, the worst record in the catalogue."""
		found = codes_of(
			rules.check_code_name_agreement(
				"CON-OFFC-INK-HP-952-COLOR", "WATER LEVEL SENSOR, REED SWITCH, M10 THREAD"
			)
		)
		self.assertIn(rules.CODE_NAME_DISAGREES, found)

	def test_an_agreeing_consumable_is_quiet(self):
		self.assertEqual(rules.check_code_name_agreement("CON-ELEC-FUSE-2", "FUSE, 2 AMP"), [])

	def test_vendor_codes_are_never_checked(self):
		"""A vendor number is arbitrary by design. Firing here would bury the one real case
		in 410 false ones."""
		self.assertEqual(
			rules.check_code_name_agreement("806-020", 'ELBOW, 90, SOC, PVC, 2" SCH80'), []
		)


class DuplicateTest(unittest.TestCase):
	def test_the_four_way_breaker_collision(self):
		"""SOP D-2. Four breakers, one name, and no amperage anywhere in it."""
		duplicates = rules.find_duplicates(
			"GMCB-1B-2", "PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR", CORPUS
		)
		self.assertEqual(len(duplicates["normalised_name"]), 4)

	def test_punctuation_only_difference_collides(self):
		"""`RGB_DMX` against `RGB-DMX`, plus case and one missing space. This pair is the
		reason the normalisation strips punctuation rather than collapsing it."""
		duplicates = rules.find_duplicates(
			"NEW-CODE", "LIGHT, SUBMERSIBLE, RGB-DMX, SS, DC24V 3W", CORPUS
		)
		names = {row["item_code"] for row in duplicates["normalised_name"]}
		self.assertEqual(names, {"100GM DC24V DMX512 3W", "xyh100gm-3x1w"})

	def test_word_order_defeats_every_normalisation(self):
		"""SOP D-3's VARIONAUT pair is the same physical pump under two codes and two word
		orders. It collides on nothing, and a clean duplicate result is therefore NOT
		evidence the part is new — which is exactly what the EVIDENCE entry says."""
		duplicates = rules.find_duplicates(
			"4010052576503", "PUMP, VARIONAUT, 150, 24 V, /DMX/02", CORPUS
		)
		self.assertEqual(duplicates["normalised_name"], [])

	def test_but_similarity_does_surface_it(self):
		"""The compensating surface. The judgement stays with the reader; the pairing does
		not have to be noticed unaided."""
		neighbours = rules.similar_records(
			"PUMP, VARIONAUT, 150, 24 V, /DMX/02", CORPUS, exclude_code="4010052576503"
		)
		self.assertIn("57650", [row["item_code"] for row in neighbours])

	def test_similarity_is_deterministic(self):
		first = rules.similar_records('VALVE, BALL, UTILITY, SOC, PVC, 1-1/2", EPDM', CORPUS)
		second = rules.similar_records('VALVE, BALL, UTILITY, SOC, PVC, 1-1/2", EPDM', CORPUS)
		self.assertEqual(first, second)

	def test_a_record_does_not_duplicate_itself(self):
		neighbours = rules.similar_records(
			'ELBOW, 90, SOC, PVC, 2" SCH80', CORPUS, exclude_code="806-020"
		)
		self.assertNotIn("806-020", [row["item_code"] for row in neighbours])


class VerdictTest(unittest.TestCase):
	def test_a_note_never_moves_the_verdict(self):
		"""What makes the heuristics safe to emit: a known false-positive class must not be
		able to block a correct record."""
		self.assertEqual(rules.verdict([{"severity": rules.NOTE}]), rules.VERDICT_PASS)

	def test_stop_beats_fix(self):
		self.assertEqual(
			rules.verdict([{"severity": rules.FIX}, {"severity": rules.STOP}]), rules.VERDICT_STOP
		)

	def test_an_existing_code_is_a_stop(self):
		result = rules.evaluate(
			{
				"item_code": "PDT-0009",
				"item_name": "PDT-0009 VFD BYPASS W/MOTOR PROTECTION, 7.5HP",
				"item_group": "Products",
				"stock_uom": "Nos",
			},
			CORPUS,
		)
		self.assertEqual(result["verdict"], rules.VERDICT_STOP)
		self.assertIn(rules.DUPLICATE_CODE_EXACT, codes_of(result["findings"]))

	def test_reissuing_pdt_0008_is_a_stop(self):
		"""The headline case. A query anchored with `$` says 0008 is free; it is not."""
		result = rules.evaluate(
			{
				"item_code": "PDT-0008",
				"item_name": "CONTROLLER, VFD BYPASS, 5HP",
				"item_group": "Products",
				"stock_uom": "Nos",
			},
			CORPUS,
		)
		self.assertEqual(result["verdict"], rules.VERDICT_STOP)
		self.assertIn(rules.CODE_SLOT_OCCUPIED, codes_of(result["findings"]))

	def test_a_genuinely_free_slot_in_the_same_block_is_fine(self):
		result = rules.evaluate(
			{
				"item_code": "PDT-0013",
				"item_name": "CONTROLLER, VFD BYPASS, 25HP",
				"item_group": "Products",
				"stock_uom": "Nos",
			},
			CORPUS,
		)
		self.assertNotIn(rules.CODE_SLOT_OCCUPIED, codes_of(result["findings"]))
		self.assertEqual(result["verdict"], rules.VERDICT_PASS)

	def test_the_sop_worked_example_passes(self):
		"""SOP §5 Phase 3 worked examples are the standard's own definition of compliant.
		If one of them fails here, this module is wrong and not the record."""
		for name in (
			'ELBOW, 90, SOC, PVC, 2" SCH80',
			'VALVE, BALL, UTILITY, SOC, PVC, 1", EPDM',
			'BUSHING, REDUCER, SPIGOTXSOC, PVC, 2-1/2"X2" SCH80',
			"CEMENT, SOLVENT, 711, PVC, QUART, GRAY",
			"LIGHT, SUBMERSIBLE, RGB-DMX, SS, DC24V 3W",
		):
			self.assertEqual(rules.check_name(name), [], name)

	def test_a_clean_new_item_passes(self):
		result = rules.evaluate(
			{
				"item_code": "2622-015",
				"item_name": 'VALVE, BALL, UTILITY, SOC, PVC, 1-1/2", EPDM',
				"item_group": "Products",
				"stock_uom": "Unit",
			},
			CORPUS,
		)
		self.assertEqual(result["verdict"], rules.VERDICT_PASS, result["findings"])

	def test_the_payload_says_the_segments_are_positional(self):
		"""Four of the seven segments have no decidable shape and no vocabulary. The payload
		must never imply this module classified them."""
		result = rules.evaluate({"item_code": "806-020", "item_name": 'ELBOW, 90, SOC, PVC, 2" SCH80',
			"item_group": "Products", "stock_uom": "Unit"}, CORPUS)
		self.assertTrue(result["segments"]["slots_are_positional"])

	def test_the_payload_states_the_normalisation_it_used(self):
		"""A duplicate count is a property of the normalisation, not of the data. Quoting one
		without the other is unfalsifiable."""
		result = rules.evaluate({"item_code": "X", "item_name": "VALVE, BALL"}, CORPUS)
		self.assertTrue(result["normalisation"])
		self.assertEqual(result["duplicates"]["normalisation"], rules.NORMALISATION)


class ProductionCorpusTest(unittest.TestCase):
	"""Findings this module must produce for records the SOP already ruled on."""

	def _findings_for(self, code):
		row = next(r for r in CORPUS if r["item_code"] == code)
		return codes_of(
			rules.check_code(row["item_code"])
			+ rules.check_name(row["item_name"], row["item_code"], BRANDS)
			+ rules.check_supporting(row["item_group"], "Unit")
			+ rules.check_code_name_agreement(row["item_code"], row["item_name"])
		)

	def test_d1_hp_952(self):
		self.assertIn(rules.CODE_NAME_DISAGREES, self._findings_for("CON-OFFC-INK-HP-952-COLOR"))

	def test_d2_breaker_category(self):
		self.assertIn(rules.NAME_CATEGORY_TIER3, self._findings_for("GMCB-1B-1"))

	def test_d5_hose_on_a_tee(self):
		"""SOP C-5: the Naming Convention Sheet's PIPE row was given the HOSE abbreviation by
		a copy-paste error, and it propagated into the data. HOSE is a real Tier 1 category,
		so the *category* check cannot catch this one — the GREY does."""
		found = self._findings_for("802-247S")
		self.assertIn(rules.NAME_COLOUR_GREY, found)

	def test_d6_corosive_plate(self):
		found = self._findings_for("B01M3Y86MY")
		self.assertIn(rules.NAME_TRAILING_COMMA, found)
		self.assertIn(rules.NAME_EDGE_WHITESPACE, found)

	def test_every_all_item_groups_record_is_flagged(self):
		for row in CORPUS:
			if row["item_group"] == rules.ROOT_ITEM_GROUP:
				self.assertIn(
					rules.GROUP_IS_ROOT, codes_of(rules.check_supporting(row["item_group"], "Nos"))
				)

	def test_no_check_raises_on_any_live_record(self):
		"""The corpus is full of malformed strings. Nothing here may throw on one — a
		validator that crashes on the records it exists to find is worse than none."""
		for row in CORPUS:
			rules.evaluate(
				{
					"item_code": row["item_code"],
					"item_name": row["item_name"],
					"item_group": row["item_group"],
					"stock_uom": "Nos",
				},
				CORPUS,
			)


class CorpusAuditTest(unittest.TestCase):
	"""`audit()` — the entry point every non-AI surface goes through."""

	def test_collision_groups_finds_the_four_way_and_orders_worst_first(self):
		groups = rules.collision_groups(CORPUS)
		self.assertEqual(groups[0]["count"], 4, "the GMCB four-way is the worst group and must lead")
		self.assertEqual(
			groups[0]["codes"], ["GMCB-1B-1", "GMCB-1B-10", "GMCB-1B-6", "GMCB-1C-10"]
		)
		self.assertTrue(all(g["count"] > 1 for g in groups), "a group of one is not a collision")

	def test_a_record_is_told_who_it_collides_with_not_merely_that_it_does(self):
		rows = {row["item_code"]: row for row in rules.audit(CORPUS, BRANDS)}
		findings = [
			f for f in rows["GMCB-1B-1"]["findings"] if f["code"] == rules.DUPLICATE_NAME_NORMALISED
		]
		self.assertEqual(len(findings), 1)
		self.assertEqual(sorted(findings[0]["matches"]), ["GMCB-1B-10", "GMCB-1B-6", "GMCB-1C-10"])

	def test_audit_covers_every_record_and_never_throws(self):
		rows = rules.audit(CORPUS, BRANDS)
		self.assertEqual(len(rows), len(CORPUS))
		self.assertEqual({r["item_code"] for r in rows}, {r["item_code"] for r in CORPUS})

	def test_audit_does_no_per_row_similarity_scoring(self):
		"""The O(n²) regression guard, and the reason it is worth a test of its own.

		`evaluate()` scores near-neighbours by document frequency over the WHOLE corpus, so it
		costs a pass per candidate. Calling it once per record — which is the obvious
		simplification, and does not look wrong — turns a linear audit into a quadratic one.
		Poisoning `similar_records` is the only way to assert the absence of a call.
		"""
		original = rules.similar_records
		calls = []

		def poisoned(*args, **kwargs):
			calls.append(args)
			raise AssertionError(
				"audit() called similar_records — that is O(n) per row and makes the audit "
				"quadratic. Near-neighbour scoring belongs to evaluate(), one candidate at a time."
			)

		rules.similar_records = poisoned
		try:
			rules.audit(CORPUS, BRANDS)
		finally:
			rules.similar_records = original
		self.assertEqual(calls, [])

	def test_evaluate_still_does_score_neighbours(self):
		"""The other half of the same rule — the guard above must not be satisfiable by
		deleting the feature."""
		result = rules.evaluate(
			{"item_code": "2622-015", "item_name": 'VALVE, BALL, UTILITY, SOC, PVC, 1-1/2", EPDM'},
			CORPUS,
		)
		self.assertTrue(result["similar"], "evaluate() must still return near neighbours")

	def test_tombstones_are_flagged_by_the_suffix_not_by_disabled(self):
		"""Every live row carries `disabled = 0`, so the suffix is the only marker there is."""
		rows = {row["item_code"]: row for row in rules.audit(CORPUS, BRANDS)}
		self.assertTrue(rows["PDT-00000 (deleted)"]["is_tombstone"])
		self.assertFalse(rows["PDT-0009"]["is_tombstone"])
		self.assertFalse(
			rows[PDT_00051_PLACEHOLDER]["is_tombstone"],
			"PDT-00051 is a live placeholder, not a tombstone — it carries no (deleted) suffix",
		)

	def test_sort_puts_stop_above_fix_above_pass(self):
		rows = sorted(rules.audit(CORPUS, BRANDS), key=rules.audit_sort_key)
		seen = [rules.SEVERITY_ORDER.get(r["verdict"], 9) for r in rows]
		self.assertEqual(seen, sorted(seen), "rows must be ordered worst-first")

	def test_summarise_counts_live_rows_separately(self):
		summary = rules.summarise(rules.audit(CORPUS, BRANDS))
		self.assertEqual(summary["rows"], len(CORPUS))
		self.assertEqual(summary["live_rows"] + summary["tombstone_rows"], summary["rows"])
		self.assertEqual(summary["normalisation"], rules.NORMALISATION)

	def test_summarise_reports_no_compliance_figure_for_an_empty_corpus(self):
		"""None rather than 0.0 or 100.0 — both of those are claims, and neither is true of
		nothing."""
		self.assertIsNone(rules.summarise([])["compliance_pct"])


class SelfDuplicateTest(unittest.TestCase):
	"""A record is not its own duplicate — and a proposal that collides still is.

	Both halves matter, and the second is why this class exists rather than a single
	assertion. The bug (v1.337.0) was that `evaluate` never excluded the candidate's own
	code, so every saved Item matched itself on `item_code` and came back STOP. The obvious
	fix — always exclude — would silently destroy the check the whole feature is *for*:
	somebody about to re-create a record that already exists. The two cases are opposite
	readings of the same corpus and only the caller knows which one it is asking.
	"""

	SAVED = {
		"item_code": "806-020",
		"item_name": 'ELBOW, 90, SOC, PVC, 2" SCH80',
		"item_group": "Products",
		"stock_uom": "Unit",
	}

	def test_a_saved_record_is_not_its_own_duplicate(self):
		result = rules.evaluate(self.SAVED, CORPUS, BRANDS, existing=True)
		self.assertEqual(result["duplicates"]["exact"], [])
		self.assertNotIn(rules.DUPLICATE_CODE_EXACT, codes_of(result["findings"]))
		self.assertEqual(result["verdict"], rules.VERDICT_PASS, result["findings"])

	def test_a_proposal_that_collides_is_still_a_stop(self):
		"""The half a careless fix would delete. `item_code` is the primary key, so this is
		somebody about to re-create a record that is already there — the failure SOP §5
		Step 1.3 opens with."""
		result = rules.evaluate(self.SAVED, CORPUS, BRANDS, existing=False)
		self.assertEqual([d["item_code"] for d in result["duplicates"]["exact"]], ["806-020"])
		self.assertIn(rules.DUPLICATE_CODE_EXACT, codes_of(result["findings"]))
		self.assertEqual(result["verdict"], rules.VERDICT_STOP)

	def test_a_saved_record_does_not_collide_with_its_own_name_either(self):
		"""The name check has the same shape and would have had the same bug."""
		result = rules.evaluate(self.SAVED, CORPUS, BRANDS, existing=True)
		self.assertEqual(result["duplicates"]["normalised_name"], [])
		self.assertNotIn(rules.DUPLICATE_NAME_NORMALISED, codes_of(result["findings"]))

	def test_a_saved_record_still_sees_a_real_name_collision(self):
		"""Excluding itself must not excuse it. Four breakers share one name; re-checking one
		of them must still report the other three."""
		row = next(r for r in CORPUS if r["item_code"] == "GMCB-1B-1")
		result = rules.evaluate(
			{"item_code": row["item_code"], "item_name": row["item_name"],
			 "item_group": row["item_group"], "stock_uom": "Nos"},
			CORPUS, BRANDS, existing=True,
		)
		matched = {r["item_code"] for r in result["duplicates"]["normalised_name"]}
		self.assertEqual(matched, {"GMCB-1B-6", "GMCB-1B-10", "GMCB-1C-10"})
		self.assertEqual(result["verdict"], rules.VERDICT_STOP)

	def test_self_exclusion_is_exact_not_normalised(self):
		"""The subtle half. Excluding by the normalised code would also drop a genuine
		punctuation-variant sibling — which is precisely the collision
		`duplicate_code_normalised` exists to report, so the exclusion would have quietly
		disabled another check while fixing this one."""
		corpus = list(CORPUS) + [
			{"item_code": "806020", "item_name": "ELBOW, 90, SOC, PVC, 2 INCH", "item_group": "Products"}
		]
		result = rules.evaluate(self.SAVED, corpus, BRANDS, existing=True)
		self.assertEqual(
			[r["item_code"] for r in result["duplicates"]["normalised_code"]],
			["806020"],
			"a sibling differing only in punctuation must survive self-exclusion",
		)

	def test_a_record_is_never_its_own_nearest_neighbour(self):
		"""True in both modes — a proposal whose code is taken is reported by `duplicates`,
		and listing it again at a score of 1.0 tells the reader nothing."""
		for existing in (True, False):
			result = rules.evaluate(self.SAVED, CORPUS, BRANDS, existing=existing)
			self.assertNotIn(
				"806-020", [r["item_code"] for r in result["similar"]], f"existing={existing}"
			)

	def test_block_occupancy_already_drew_this_distinction(self):
		"""`check_block` has always excluded the record from its own slot. This pins that the
		two agree, so a future edit cannot fix one and regress the other."""
		occupied = rules.occupancy([r["item_code"] for r in CORPUS], "PDT")
		findings, _report = rules.check_block("PDT-0009", occupied)
		self.assertEqual(findings, [], "a record must not occupy its own slot against itself")

	def test_the_payload_states_which_question_was_asked(self):
		"""A reader cannot interpret a duplicate result without knowing which mode produced
		it, so the answer carries the flag."""
		self.assertTrue(rules.evaluate(self.SAVED, CORPUS, BRANDS, existing=True)["existing"])
		self.assertFalse(rules.evaluate(self.SAVED, CORPUS, BRANDS)["existing"])


if __name__ == "__main__":
	unittest.main()
