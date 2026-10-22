# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Supplier Finder's matching and ranking, executed. Bench-free, unittest.

:mod:`inventory_enhancements.supplier_search_rules` imports no Frappe, so everything that decides
an answer runs on every push. That covers which items match, which groups match, how duplicate
supplier records fold, and the order suppliers come back in. The question it answers is Nik's
(2026-10-22): "I know we need a pump, but I don't remember all our pump suppliers". The case this
file exists for is :meth:`RankTest.test_history_outranks_the_pump_group`. A supplier we bought pumps
from, filed under a group with no "pump" in its name, must rank above a supplier that only sits in
the "Pumps" group. That was the shape of prod that day.

Item names are POL-0602 style names of the kind on prod. Supplier names are invented.
"""

import datetime as dt
import json
import unittest

from erpnext_enhancements.inventory_enhancements import supplier_search_rules as rules

TODAY = dt.date(2026, 10, 22)


def _item(code, name, group="Products", description="", parts=()):
	return {
		"item_code": code,
		"item_name": name,
		"item_group": group,
		"description": description,
		"part_nos": list(parts),
	}


CATALOG = [
	_item("340605", "PUMP, PENTAIR EQKT750, 7.5HP", parts=["PEN340605"]),
	_item("342002", "PUMP, PENTAIR SUPERFLO VST, 1.5HP"),
	_item("500035", "Pump, 66 GPH, Submersible", group="Pumps"),
	_item("501199", "COVER, PUMP, TOP SHIELD, PRJ-00701"),
	_item("CL-1", 'CLAMP, HOSE, STAINLESS STEEL, 2"'),
	_item("LT-1", "LAMP, UNDERWATER, LED, 24V"),
	_item("SQ-1", "PUMP, GRUNDFOS SQ 3-65, 1HP"),
	_item("UN-1", 'UNION, PVC, 1-1/2", BLACK/WHITE'),
]


def _search(query, catalog=CATALOG):
	return rules.match_items(catalog, rules.query_terms(query), rules.whole_query(query))


def _codes(found):
	return [r["item_code"] for r in found["items"]]


class WordsTest(unittest.TestCase):
	def test_stem_folds_plain_plurals_only(self):
		self.assertEqual(rules.stem("pumps"), "PUMP")
		self.assertEqual(rules.stem("boxes"), "BOX")
		self.assertEqual(rules.stem("batteries"), "BATTERY")
		self.assertEqual(rules.stem("hoses"), "HOSE")
		self.assertEqual(rules.stem("glass"), "GLASS")
		self.assertEqual(rules.stem("bus"), "BUS")
		self.assertEqual(rules.stem("24VDCS"), "24VDCS")

	def test_query_terms_drop_filler_and_duplicates(self):
		self.assertEqual(rules.query_terms("Who sells a submersible pump? pumps!"), ["SUBMERSIBLE", "PUMP"])

	def test_filler_only_query_keeps_its_words(self):
		self.assertEqual(rules.query_terms("the supplier"), ["THE", "SUPPLIER"])

	def test_sizes_and_units_become_one_term(self):
		self.assertEqual(rules.query_terms("1-1/2 pvc union"), ["112", "PVC", "UNION"])
		self.assertEqual(rules.query_terms("2 hp pump"), ["2HP", "PUMP"])
		self.assertEqual(rules.query_terms("pentair 7.5 hp"), ["PENTAIR", "75HP"])

	def test_whole_query_is_the_model_number_squashed(self):
		self.assertEqual(rules.whole_query("Grundfos SQ 3-65"), "GRUNDFOSSQ365")
		self.assertEqual(rules.whole_query("who sells EQKT-750"), "EQKT750")

	def test_markup_never_reaches_a_term(self):
		# Entities are decoded before tags are stripped, so an encoded tag goes too.
		self.assertEqual(rules.query_terms("<b>pump</b>&lt;script&gt;"), ["PUMP"])

	def test_terms_are_bounded(self):
		self.assertEqual(len(rules.query_terms(" ".join(f"word{i}" for i in range(30)))), rules.TERMS_MAX)


class ItemMatchTest(unittest.TestCase):
	def test_category_first_ranks_the_thing_above_its_parts(self):
		codes = _codes(_search("pump"))
		self.assertIn("501199", codes)
		self.assertLess(codes.index("340605"), codes.index("501199"))
		self.assertLess(codes.index("500035"), codes.index("501199"))

	def test_every_term_must_match_when_any_item_matches_them_all(self):
		found = _search("submersible pump")
		self.assertEqual(_codes(found), ["500035"])
		self.assertFalse(found["partial"])

	def test_partial_when_nothing_matches_every_term(self):
		found = _search("pentair heater")
		self.assertTrue(found["partial"])
		self.assertIn("340605", _codes(found))
		self.assertTrue(all(r["hits"] == 1 for r in found["items"]))

	def test_nothing_when_nothing_matches(self):
		self.assertEqual(_search("parachute"), {"items": [], "partial": False})

	def test_model_number_in_the_name(self):
		found = _search("EQKT750")
		self.assertEqual(_codes(found)[0], "340605")
		self.assertTrue(found["items"][0]["model"])

	def test_model_number_written_with_spaces_and_dashes(self):
		self.assertEqual(_codes(_search("grundfos sq 3-65"))[0], "SQ-1")
		self.assertEqual(_codes(_search("SQ365"))[0], "SQ-1")

	def test_supplier_part_number_is_a_model_match(self):
		found = _search("pen-340605")
		self.assertEqual(_codes(found)[0], "340605")
		self.assertTrue(found["items"][0]["model"])

	def test_a_plain_word_inside_a_code_is_not_a_model_match(self):
		# Prod, 2026-10-22: PUMP is inside CHEMPUMP-STENNER-45M1, and as a model match that one
		# chemical feed pump outranked every real pump and switched the AI step off.
		catalog = [
			*CATALOG,
			_item("CHEMPUMP-STENNER-45M1", "Chemical Feed Pump, Stenner 45M1"),
			_item("V-9", "VALVE, CHECK", parts=["PUMPHOUSE-1"]),
		]
		found = _search("pump", catalog)
		self.assertFalse(any(r["model"] for r in found["items"]))
		self.assertEqual(_codes(found)[-1], "V-9")  # a word start in a part number: matched, last
		self.assertEqual(_codes(found)[0], "340605")
		self.assertTrue(_search("45M1", catalog)["items"][0]["model"])

	def test_a_category_and_a_size_is_not_a_model(self):
		# Review of PR #1341: "pvc 1-1/2" was an exact match for 22 fittings, "sch 40" for 60 of 60.
		catalog = [
			*CATALOG,
			_item("PV-1", 'ELBOW, PVC, SCH 40, 1-1/2"'),
			_item("PV-2", 'TEE, PVC, SCH 40, 1-1/2"'),
			_item("HP-2", "PUMP, POOL, 2HP"),
		]
		for query in ("pvc 1-1/2", "sch 40", "2 hp pump"):
			found = rules.match_items(
				catalog,
				rules.query_terms(query),
				rules.whole_query(query),
				single=rules.is_single_chunk(query),
			)
			with self.subTest(query=query):
				self.assertTrue(found["items"])
				self.assertFalse(any(r["model"] for r in found["items"]))
		self.assertEqual(_codes(_search("2 hp pump", catalog))[0], "HP-2")

	def test_one_chunk_is_a_model_search(self):
		self.assertTrue(rules.is_single_chunk("EQKT-750"))
		self.assertTrue(rules.is_single_chunk("who sells SQ3-65"))
		self.assertFalse(rules.is_single_chunk("pvc 1-1/2"))
		self.assertFalse(rules.is_single_chunk("sch 40"))

	def test_exact_code_outranks_a_code_containing_it(self):
		catalog = [_item("A1234", "VALVE, BALL"), _item("1234", "VALVE, CHECK")]
		self.assertEqual(_codes(_search("1234", catalog))[0], "1234")

	def test_short_words_match_word_starts_only(self):
		self.assertEqual(_codes(_search("lamp")), ["LT-1"])

	def test_synonyms_match_both_ways(self):
		self.assertEqual(_codes(_search("underwater light")), ["LT-1"])

	def test_prefix_of_a_word_matches(self):
		self.assertIn("340605", _codes(_search("pent")))

	def test_long_word_matches_inside_a_squashed_name(self):
		catalog = [_item("X1", "PUMP, WHISPERFLOXF, 3HP")]
		self.assertEqual(_codes(_search("whisperflo", catalog)), ["X1"])

	def test_answer_rows_carry_no_description(self):
		catalog = [_item("X1", "PUMP, SUMP", description="secret notes")]
		self.assertNotIn("description", _search("pump", catalog)["items"][0])

	def test_limit(self):
		catalog = [_item(f"P{i}", f"PUMP, TYPE {i}") for i in range(100)]
		self.assertEqual(len(rules.match_items(catalog, ["PUMP"], "", limit=7)["items"]), 7)

	def test_category_terms(self):
		self.assertEqual(rules.category_terms(_search("EQKT750")["items"]), ["PUMP"])
		self.assertEqual(rules.category_terms([]), [])


class GroupMatchTest(unittest.TestCase):
	GROUPS = [
		"Pumps",
		"Fountain Supplies - Pumps, Filters",
		"Plumbing Supplies",
		"Pool Supplies",
		"Motors",
		"Lighting - Supplier",
	]

	def test_pump_finds_both_pump_groups_tightest_first(self):
		self.assertEqual(
			[g["name"] for g in rules.match_groups(self.GROUPS, ["PUMP"])],
			["Pumps", "Fountain Supplies - Pumps, Filters"],
		)

	def test_filler_words_match_nothing(self):
		self.assertEqual(rules.match_groups(self.GROUPS, ["SUPPLY"]), [])

	def test_synonym_reaches_a_group(self):
		self.assertEqual(
			[g["name"] for g in rules.match_groups(self.GROUPS, ["LAMP"])], ["Lighting - Supplier"]
		)

	def test_a_word_start_is_not_a_group_match(self):
		groups = [
			"Waterproofing",
			"Software Tools - File Transfer",
			"Handyman",
			"Fasteners",
			"Electrical Supplies",
		]
		self.assertEqual(rules.match_groups(groups, ["WATER", "HAND", "SOLID"]), [])
		self.assertEqual([g["name"] for g in rules.match_groups(groups, ["FASTEN"])], ["Fasteners"])
		self.assertEqual(
			[g["name"] for g in rules.match_groups(groups, ["ELECTRIC"])], ["Electrical Supplies"]
		)

	def test_group_word_matches(self):
		self.assertTrue(rules.group_word_matches("PUMP", "PUMP"))
		self.assertTrue(rules.group_word_matches("FASTENER", "FASTEN"))
		self.assertFalse(rules.group_word_matches("WATER", "WATERPROOFING"))
		self.assertFalse(rules.group_word_matches("SOLID", "SOLIDWORKS"))

	def test_more_terms_matched_ranks_first(self):
		names = [g["name"] for g in rules.match_groups(self.GROUPS, ["FOUNTAIN", "PUMP"])]
		self.assertEqual(names[0], "Fountain Supplies - Pumps, Filters")


class SupplierKeyTest(unittest.TestCase):
	def test_entered_twice_folds_to_one_key(self):
		for a, b in (
			("Acme Pump Co", "Acme Pump Company, Inc."),
			("Mc Widget-Works", "McWidget Works LLC"),
			("Pipe Depot", "PipeDepot.com"),
			("Delta Supply", "The Delta Supply Corp"),
		):
			self.assertEqual(rules.supplier_key(a), rules.supplier_key(b), (a, b))

	def test_different_companies_stay_apart(self):
		self.assertNotEqual(rules.supplier_key("Acme Pump"), rules.supplier_key("Acme Pool"))

	def test_a_name_of_only_suffixes_keeps_itself(self):
		self.assertEqual(rules.supplier_key("The Company"), "THECOMPANY")


def _ev(supplier, code, **kw):
	row = {
		"supplier": supplier,
		"item_code": code,
		"po_lines": 0,
		"pr_lines": 0,
		"sq_lines": 0,
		"listed": False,
		"part_no": "",
		"default": False,
		"price": False,
		"last_date": None,
		"last_rate": None,
		"uom": None,
	}
	row.update(kw)
	return row


SUPPLIERS = {
	"Valley Pool Co": {"supplier_name": "Valley Pool Co", "groups": ["Pool Supplies"], "disabled": False},
	"Acme Pump Co": {"supplier_name": "Acme Pump Co", "groups": ["Pumps"], "disabled": False},
	"Acme Pump Company, Inc.": {"supplier_name": "Acme Pump Company, Inc.", "groups": [], "disabled": False},
	"Quote Only LLC": {"supplier_name": "Quote Only LLC", "groups": ["Plumbing"], "disabled": False},
	"Old Pump Shop": {"supplier_name": "Old Pump Shop", "groups": ["Pumps"], "disabled": True},
}


class RankTest(unittest.TestCase):
	def setUp(self):
		self.items = _search("pump")["items"]

	def rank(self, evidence, group_hits=None, name_hits=None, **kw):
		return rules.rank_suppliers(
			evidence, self.items, SUPPLIERS, group_hits=group_hits, name_hits=name_hits, today=TODAY, **kw
		)

	def test_history_outranks_the_pump_group(self):
		ranked = self.rank(
			[_ev("Valley Pool Co", "342002", po_lines=1, last_date="2026-10-15")],
			group_hits={"Acme Pump Co": 1},
		)
		self.assertEqual([r["supplier"] for r in ranked], ["Valley Pool Co", "Acme Pump Co"])
		self.assertEqual(ranked[0]["tier"], rules.TIER_HISTORY)
		self.assertEqual(ranked[1]["tier"], rules.TIER_GROUP)
		self.assertIn("Bought 1 matching item", rules.reason(ranked[0]))
		self.assertIn("no purchase history", rules.reason(ranked[1]))

	def test_quoted_or_listed_sits_between(self):
		ranked = self.rank(
			[
				_ev("Quote Only LLC", "340605", sq_lines=2, listed=True, part_no="X-1"),
				_ev("Valley Pool Co", "500035", pr_lines=1, last_date="2020-01-01"),
			],
			group_hits={"Acme Pump Co": 1},
		)
		self.assertEqual(
			[r["tier"] for r in ranked], [rules.TIER_HISTORY, rules.TIER_CARRIES, rules.TIER_GROUP]
		)
		self.assertIn("never bought", rules.reason(ranked[1]))

	def test_duplicate_records_fold_into_one_row(self):
		ranked = self.rank(
			[
				_ev("Acme Pump Co", "340605", po_lines=1, last_date="2026-01-01"),
				_ev(
					"Acme Pump Company, Inc.",
					"340605",
					po_lines=2,
					last_date="2026-06-01",
					last_rate=900.0,
					uom="Nos",
				),
				_ev("Acme Pump Company, Inc.", "342002", pr_lines=1, last_date="2025-05-01"),
			]
		)
		self.assertEqual(len(ranked), 1)
		row = ranked[0]
		self.assertEqual(row["supplier"], "Acme Pump Company, Inc.")
		self.assertEqual(row["aliases"], ["Acme Pump Co"])
		self.assertEqual(row["groups"], ["Pumps"])
		self.assertEqual(row["item_count"], 2)
		self.assertEqual(row["bought_count"], 2)
		self.assertEqual(row["last_date"], "2026-06-01")
		first = row["items"][0]
		self.assertEqual((first["item_code"], first["po_lines"], first["last_rate"]), ("340605", 3, 900.0))

	def test_a_direct_purchase_invoice_is_history(self):
		ranked = self.rank([_ev("Valley Pool Co", "340605", pi_lines=1, last_date="2026-09-01")])
		self.assertEqual(ranked[0]["tier"], rules.TIER_HISTORY)
		self.assertEqual(ranked[0]["bought_count"], 1)

	def test_the_price_keeps_its_currency(self):
		ranked = self.rank(
			[
				_ev(
					"Valley Pool Co",
					"340605",
					po_lines=1,
					last_date="2025-01-01",
					last_rate=500.0,
					currency="USD",
				),
				_ev(
					"Valley Pool Co",
					"340605",
					pi_lines=1,
					last_date="2026-03-01",
					last_rate=700.0,
					currency="CAD",
				),
			]
		)
		first = ranked[0]["items"][0]
		self.assertEqual((first["last_rate"], first["currency"]), (700.0, "CAD"))

	def test_every_pair_has_the_same_keys(self):
		ranked = self.rank(
			[_ev("Valley Pool Co", "340605", sq_lines=1), _ev("Valley Pool Co", "342002", po_lines=1)]
		)
		keys = {frozenset(i) for i in ranked[0]["items"]}
		self.assertEqual(len(keys), 1)

	def test_newest_price_wins_within_a_record(self):
		ranked = self.rank(
			[
				_ev("Valley Pool Co", "340605", po_lines=1, last_date="2025-01-01", last_rate=500.0),
				_ev("Valley Pool Co", "340605", pr_lines=1, last_date="2026-03-01", last_rate=650.0),
			]
		)
		self.assertEqual(ranked[0]["items"][0]["last_rate"], 650.0)

	def test_recent_and_frequent_beats_old_and_once(self):
		ranked = self.rank(
			[
				_ev("Valley Pool Co", "340605", po_lines=1, last_date="2019-01-01"),
				_ev("Quote Only LLC", "340605", po_lines=4, last_date="2026-09-01"),
			]
		)
		self.assertEqual(ranked[0]["supplier"], "Quote Only LLC")

	def test_a_model_match_weighs_more_than_a_part_for_it(self):
		self.items = _search("pump")["items"]
		ranked = self.rank(
			[
				_ev("Valley Pool Co", "501199", po_lines=1, last_date="2026-09-01"),
				_ev("Quote Only LLC", "340605", po_lines=1, last_date="2026-09-01"),
			]
		)
		self.assertEqual(ranked[0]["supplier"], "Quote Only LLC")

	def test_disabled_suppliers_sink_to_the_bottom(self):
		ranked = self.rank(
			[_ev("Old Pump Shop", "340605", po_lines=5, last_date="2026-10-01")],
			group_hits={"Acme Pump Co": 1},
		)
		self.assertEqual([r["supplier"] for r in ranked], ["Acme Pump Co", "Old Pump Shop"])

	def test_unknown_suppliers_and_blank_rows_are_ignored(self):
		ranked = self.rank(
			[_ev("Nobody", "340605", po_lines=1), _ev("", "340605"), _ev("Valley Pool Co", "")]
		)
		self.assertEqual(ranked, [])

	def test_limit_and_items_per_supplier(self):
		catalog = [_item(f"P{i}", f"PUMP, TYPE {i}") for i in range(10)]
		self.items = rules.match_items(catalog, ["PUMP"], "")["items"]
		ranked = self.rank(
			[_ev("Valley Pool Co", f"P{i}", po_lines=1, last_date="2026-01-01") for i in range(10)]
			+ [_ev("Quote Only LLC", "P1", sq_lines=1)],
			limit=1,
		)
		self.assertEqual(len(ranked), 1)
		self.assertEqual(len(ranked[0]["items"]), rules.ITEMS_PER_SUPPLIER)
		self.assertEqual(ranked[0]["item_count"], 10)
		self.assertIn("Bought 10 matching items", rules.reason(ranked[0]))

	def test_order_is_deterministic(self):
		evidence = [
			_ev(s, "340605", po_lines=1, last_date="2026-01-01") for s in ("Valley Pool Co", "Quote Only LLC")
		]
		first = [r["supplier"] for r in self.rank(evidence)]
		second = [r["supplier"] for r in self.rank(list(reversed(evidence)))]
		self.assertEqual(first, second)
		self.assertEqual(first, ["Quote Only LLC", "Valley Pool Co"])

	def test_recency_factor(self):
		self.assertEqual(rules.recency_factor("2026-01-01", TODAY), 1.0)
		self.assertEqual(rules.recency_factor("2024-01-01", TODAY), 0.8)
		self.assertEqual(rules.recency_factor("2015-01-01", TODAY), 0.6)
		self.assertEqual(rules.recency_factor(None, TODAY), 0.6)
		self.assertEqual(rules.recency_factor("not a date", TODAY), 0.6)


class ExpansionTest(unittest.TestCase):
	GROUPS = ["Pumps", "Fountain Supplies - Pumps, Filters", "Motors"]

	def test_prompt_carries_the_search_and_the_groups_only(self):
		prompt = rules.build_expansion_prompt("something like a 2hp <i>fountain</i> pump", self.GROUPS)
		self.assertIn("something like a 2hp fountain pump", prompt)
		self.assertIn("- Pumps", prompt)
		self.assertNotIn("<i>", prompt)

	def test_reply_is_held_to_its_shape(self):
		reply = (
			"```json\n"
			+ json.dumps(
				{
					"terms": [
						"submersible pump",
						"PENTAIR",
						"pump",
						"<b>fountain pump</b>",
						7,
						"",
						"SUBMERSIBLE PUMPS",
					],
					"groups": ["pumps", "Made Up Group", "Motors", None],
					"note": "Read as a small <script>pump</script>.",
				}
			)
			+ "\n```"
		)
		parsed = rules.parse_expansion(reply, self.GROUPS, original_terms=["PUMP"])
		self.assertEqual(parsed["terms"], [["SUBMERSIBLE", "PUMP"], ["PENTAIR"], ["FOUNTAIN", "PUMP"]])
		self.assertEqual(parsed["groups"], ["Pumps", "Motors"])
		self.assertNotIn("<", parsed["note"])

	def test_not_json_is_none(self):
		self.assertIsNone(rules.parse_expansion("Sorry, I cannot help.", self.GROUPS))
		self.assertIsNone(rules.parse_expansion("[1, 2]", self.GROUPS))
		self.assertIsNone(rules.parse_expansion(None, self.GROUPS))

	def test_bounds(self):
		reply = json.dumps(
			{"terms": [f"thing{i}" for i in range(20)], "groups": self.GROUPS * 5, "note": "x" * 900}
		)
		parsed = rules.parse_expansion(reply, self.GROUPS)
		self.assertEqual(len(parsed["terms"]), rules.AI_TERMS_MAX)
		self.assertEqual(parsed["groups"], self.GROUPS)
		self.assertLessEqual(len(parsed["note"]), rules.NOTE_MAX)

	def test_wrong_types_are_ignored(self):
		parsed = rules.parse_expansion(
			json.dumps({"terms": "pump", "groups": "Pumps", "note": ["x"]}), self.GROUPS
		)
		self.assertEqual(parsed, {"terms": [], "groups": [], "note": ""})


if __name__ == "__main__":
	unittest.main()
