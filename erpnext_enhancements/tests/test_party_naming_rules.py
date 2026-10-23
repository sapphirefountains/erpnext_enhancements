# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Party-linked naming, executed rather than inspected. Bench-free, unittest.

:mod:`crm_enhancements.party_naming_rules` imports only ``re`` and ``typing``, which is what
puts it in the bench-free tier — the same split :mod:`inventory_enhancements.item_naming_rules`
uses, and for the reason that module gives: there is no Frappe integration-test job in this
repo, so bench-free code is the only code that runs on every push.

**Two properties this file exists for**, both of which the obvious implementation gets wrong:

1. *Matching the party is not equality.* Of 164 Projects that already use the separator, 55
   lead with the customer name verbatim and 42 lead with a shortening of it. A rule that
   demanded equality would flag 42 records that are right, and the people maintaining them
   would stop reading the report.
2. *Out of scope is silence, not a pass.* An internal Project is not badly named; it is a
   different kind of record. 101 of 644 Projects are internal, and flagging them would be the
   fastest way to get the whole thing dismissed.
3. *A party has more than one name, and the one stored is not the one shown.* A Project
   stores the Customer's docname; the Desk renders the Customer's label. 125 of 1,662
   Customers have drifted apart, which flagged 14 records — 5 Projects, 3 Opportunities and
   6 Addresses — for a difference nothing on their form could explain. See
   ``test_a_stale_customer_docname_is_not_a_naming_defect``.
4. *Check the field the form shows.* Opportunity's form title is the Opportunity Name
   (``custom_opportunity_name``); ``title`` is ERPNext's auto-filled copy of the party. The
   check read ``title`` until v1.737.1 and told 346 Opportunities that a name reading
   ``Summit Vista - Circulation Pump`` had no separator. See ``OpportunityNameFieldTest``.

Every fixture is a verbatim record read from ERPNext Production on 19 August 2026, except
the Opportunities, which were re-read on 23 October 2026 with both name fields.
"""

import ast
import json
import pathlib
import unittest

from erpnext_enhancements.crm_enhancements import party_naming_rules as rules

ROOT = pathlib.Path(__file__).resolve().parents[1]


def codes_of(findings):
	return {f["code"] for f in findings}


# --- verbatim production fixtures ----------------------------------------------

PROJECTS = [
	# Compliant: prefix is a shortening of the customer.
	{"name": "PRJ-00754", "project_name": "Hess Construction - Colony 256 Cascading Pillar",
	 "project_type": "Build", "customer": "Hess Construction LLC"},
	{"name": "PRJ-00758", "project_name": "West Jordan - Splash Pad Controller Servive 8/17/26",
	 "project_type": "Service", "customer": "West Jordan Parks and Recreation"},
	# Named for the site rather than for who pays, and it matches NEITHER of the customer's
	# names. The genuine shape of party_prefix_mismatch; 49 customer-facing Projects reach it.
	{"name": "PRJ-00159", "project_name": "Myers Mortuary - Fountain",
	 "project_type": "Build", "customer": "Anderson Wahlen & Associates",
	 "customer_name": "Anderson Wahlen & Associates"},
	# Looks identical to the row above and is not a defect: the customer's docname is stale
	# (`CEM Aquatics`) and its label is `Landmark Aquatic - CEM`, so `Landmark` is a correct
	# shortening. This record was this module's flagship example of a site-named project
	# until the label was read; it is the reason party_names exists.
	{"name": "PRJ-00756", "project_name": "Landmark - Millcreek Commons Phase 2 Controller",
	 "project_type": "Build", "customer": "CEM Aquatics",
	 "customer_name": "Landmark Aquatic - CEM"},
	# The record the bug was reported on. The form shows a Customer of `Robert Carey
	# Residence`; the row stores `BJ Carey`.
	{"name": "PRJ-00706", "project_name": "Robert Carey Residence - Water Feature",
	 "project_type": "Build", "customer": "BJ Carey",
	 "customer_name": "Robert Carey Residence"},
	# No separator at all.
	{"name": "PRJ-00752", "project_name": "Sonnenburg Reflection Pool & Runnel Falls",
	 "project_type": "Build", "customer": "Sonnenburg"},
	# Customer-facing type with no customer linked — 130 live records are like this.
	{"name": "PRJ-00757", "project_name": "Summit Vista - New Pump 7/29/26",
	 "project_type": "Service", "customer": ""},
	# Internal: out of scope by project_type.
	{"name": "PRJ-00749", "project_name": "Overhead - IT & Software",
	 "project_type": "Overhead", "customer": ""},
	{"name": "PRJ-00755", "project_name": "Triton Enhancements", "project_type": "Internal", "customer": ""},
	# Leftover stage template and a software-backlog item: both carry no project_type.
	{"name": "PRJ-00629", "project_name": "Stage 1 - Predesign", "project_type": "", "customer": ""},
	{"name": "PRJ-00651", "project_name": "Better filtering - 1.5", "project_type": None, "customer": ""},
]

ADDRESSES = [
	{"name": "Hansen Design Firm-Billing", "address_title": "Hansen Design Firm",
	 "address_type": "Billing", "party": "Hansen Design Firm"},
	{"name": "Stenmark-Billing", "address_title": "Stenmark",
	 "address_type": "Billing", "party": "Hess Construction LLC"},
	{"name": "DELIVERY-Billing", "address_title": "DELIVERY",
	 "address_type": "Shipping", "party": "Ana Mendez-Law"},
	{"name": "Charles Cunniffe Architects - Office", "address_title": "Charles Cunniffe Architects - Office",
	 "address_type": "Billing", "party": "Charles Cunniffe Architects"},
	{"name": "HOME-Billing-1", "address_title": "HOME", "address_type": "Billing",
	 "party": "Chelsea Damon"},
	# Titled with the linked Customer's DOCNAME while the link's title has since been edited.
	# Six live Addresses; the title is a correct name for the party, not a defect.
	{"name": "CEM Aquatics - Billing", "address_title": "CEM Aquatics",
	 "address_type": "Billing", "party": "Landmark Aquatic - CEM", "party_alias": "CEM Aquatics"},
	# No link at all — 442 live records. Out of scope: nothing to name it after.
	{"name": "Orphan-Billing", "address_title": "Orphan", "address_type": "Billing", "party": ""},
]

#: Both name fields on every row, because the bug these guard was reading the wrong one.
#: ``title`` is what ERPNext writes (the party, from customer_name); ``custom_opportunity_name``
#: is the Opportunity Name the form shows and the check judges.
OPPORTUNITIES = [
	# The record the bug was reported on: the form reads "Summit Vista - Circulation Pump" and
	# the banner said separator_missing, because it was looking at "Summit Vista".
	{"name": "CRM-OPP-2026-00173", "title": "Summit Vista",
	 "custom_opportunity_name": "Summit Vista - Circulation Pump",
	 "party_name": "Summit Vista", "customer_name": "Summit Vista", "opportunity_from": "Customer"},
	# Compliant, and flagged separator_missing by the old read like 344 others.
	{"name": "CRM-OPP-2026-00153", "title": "Hess Construction LLC",
	 "custom_opportunity_name": "Hess Construction - Stenmark Residence",
	 "party_name": "Hess Construction LLC", "customer_name": "Hess Construction LLC",
	 "opportunity_from": "Customer"},
	# The trailing space is in title and customer_name, neither of which is the name.
	{"name": "CRM-OPP-2026-00156", "title": "West Jordan Parks and Recreation ",
	 "custom_opportunity_name": "West Jordan - Splash Pad Controller Servive 8/17/26",
	 "party_name": "West Jordan Parks and Recreation", "customer_name": "West Jordan Parks and Recreation ",
	 "opportunity_from": "Customer"},
	{"name": "CRM-OPP-2026-00155", "title": "Ore",
	 "custom_opportunity_name": "Ore Designs - DC Bronze Fountain on Belmont St.",
	 "party_name": "Ore Designs, Inc.", "customer_name": "Ore Designs, Inc.", "opportunity_from": "Customer"},
	# Real defects, which must still be flagged.
	# Named for the site rather than for who pays.
	{"name": "CRM-OPP-2025-00127", "title": "Anderson Wahlen & Associates",
	 "custom_opportunity_name": "Myers Mortuary - Fountain",
	 "party_name": "Anderson Wahlen & Associates", "customer_name": "Anderson Wahlen & Associates",
	 "opportunity_from": "Customer"},
	# The inverse of the bug: title has a separator (the customer's own name carries one) and
	# the Opportunity Name has none. The old read passed it.
	{"name": "CRM-OPP-2025-00076", "title": "Skyline Exhibits Utah - Salt Lake City",
	 "custom_opportunity_name": "Skyline Water TV",
	 "party_name": "Skyline Exhibits Utah - Salt Lake City",
	 "customer_name": "Skyline Exhibits Utah - Salt Lake City", "opportunity_from": "Customer"},
	# A separator with its leading space dropped. Five live names look like this.
	{"name": "CRM-OPP-2025-00620", "title": "AJC Architects",
	 "custom_opportunity_name": "Wadman Corporation- Saltair Affordable Housing Water Wall",
	 "party_name": "Wadman Corporation", "customer_name": "Wadman Corporation", "opportunity_from": "Customer"},
	# An em dash, and no party first. Written by fountain_move.conversion._opportunity_title,
	# not typed; all three live em-dash names came from there.
	{"name": "CRM-OPP-2026-00139", "title": "Sapphire Fountains",
	 "custom_opportunity_name": "Fountain Move — Bountiful (C&T Draper)",
	 "party_name": "Sapphire Fountains", "customer_name": "Sapphire Fountains", "opportunity_from": "Customer"},
	# A trailing space in the Opportunity Name itself. 55 live names have one.
	{"name": "CRM-OPP-2026-00045", "title": "C&C - Creativity & Community Architecture",
	 "custom_opportunity_name": "C&C - Indoor Bubbler ",
	 "party_name": "C&C - Creativity & Community Architecture",
	 "customer_name": "C&C - Creativity & Community Architecture", "opportunity_from": "Customer"},
]


class EvidenceTest(unittest.TestCase):
	def test_every_code_states_what_it_is_grounded_in(self):
		for code in rules.CODES:
			self.assertFalse(rules.missing_evidence(code), f"{code} has no EVIDENCE entry")

	def test_a_code_without_evidence_cannot_be_emitted(self):
		with self.assertRaises(ValueError):
			rules.finding("not_a_real_code", "hello")

	def test_every_severity_is_one_of_the_three(self):
		for code, severity in rules.SEVERITY.items():
			self.assertIn(severity, rules.SEVERITIES, code)

	def test_the_label_leads_every_doctypes_party_fields(self):
		"""party_of shows the first one and suggestions are built from it, so the recognisable
		name has to come first. Reversing any of these would put a docname on a form banner."""
		expected = {
			"Project": "customer_name",
			"Opportunity": "customer_name",
			"Address": "party",
		}
		for doctype, first in expected.items():
			self.assertEqual(rules.DOCTYPES[doctype]["party_fields"][0], first, doctype)
			self.assertGreater(len(rules.DOCTYPES[doctype]["party_fields"]), 1, doctype)

	def test_every_configured_doctype_declares_a_shape_and_a_field(self):
		for doctype, config in rules.DOCTYPES.items():
			self.assertIn(config["shape"], (rules.SHAPE_PARTY_QUALIFIER, rules.SHAPE_PARTY_ONLY), doctype)
			self.assertTrue(config["field"], doctype)
			self.assertTrue(config["party_fields"], doctype)


class PartyMatchTest(unittest.TestCase):
	"""The rule the whole module turns on."""

	def test_a_shortening_of_the_party_passes(self):
		self.assertTrue(rules.party_matches("West Jordan", "West Jordan Parks and Recreation"))
		self.assertTrue(rules.party_matches("Hess Construction", "Hess Construction LLC"))
		self.assertTrue(rules.party_matches("Ore", "Ore Designs, Inc."))

	def test_a_different_party_fails(self):
		self.assertFalse(rules.party_matches("Landmark", "CEM Aquatics"))

	def test_it_compares_words_not_characters(self):
		"""The guard that makes the rule worth anything. A character-prefix test would pass
		'O' against 'Ore Designs, Inc.' and wave through any name sharing a first letter."""
		self.assertFalse(rules.party_matches("O", "Ore Designs, Inc."))
		self.assertFalse(rules.party_matches("Ore D", "Ore Designs, Inc."))

	def test_punctuation_and_case_are_ignored(self):
		self.assertTrue(rules.party_matches("ore designs inc", "Ore Designs, Inc."))
		self.assertTrue(rules.party_matches("Ana Mendez-Law", "Ana Mendez-Law"))

	def test_it_is_symmetric(self):
		"""Over-specification is recognisable too — somebody writing the full legal name where
		the customer is recorded short has not made a mistake worth reporting."""
		self.assertTrue(rules.party_matches("Hess Construction LLC", "Hess Construction"))

	def test_a_leading_article_is_ignored(self):
		"""Three live Projects were flagged for nothing but a dropped 'The'."""
		self.assertTrue(rules.party_matches("Chateaux Deer Valley", "The Chateaux Deer Valley"))
		self.assertTrue(rules.party_matches("Little America", "The Little America Hotel - Salt Lake City"))
		self.assertTrue(rules.party_matches("Gateway Fountain", "The Gateway"))

	def test_only_a_LEADING_article_is_ignored(self):
		"""Dropping 'The' anywhere would stop distinguishing a party from a different one that
		merely contains the word."""
		self.assertTrue(rules.party_matches("The", "The"), "a party literally named 'The' stays matchable")
		self.assertFalse(rules.party_matches("The", "The Gateway"), "a bare article is not a party name")

	def test_acronyms_and_surnames_are_still_reported(self):
		"""A judgement, not an oversight. A rule loose enough to accept these would also accept
		a site name for the contractor who pays, and the site-named projects are the defect this
		exists to find. `Carey Residence` vs `BJ Carey` looks like a false positive and is
		not one — PRJ-00706 passes via party_names, because that customer is also recorded as
		`Robert Carey Residence`. Loosening this function would be the wrong repair."""
		self.assertFalse(rules.party_matches("LHM", "Larry H Miller Corp"))
		self.assertFalse(rules.party_matches("SLC Parks & Rec", "Salt Lake County Parks & Recreation"))
		self.assertFalse(rules.party_matches("Carey Residence", "BJ Carey"))

	def test_an_empty_side_never_matches(self):
		self.assertFalse(rules.party_matches("", "Hess Construction LLC"))
		self.assertFalse(rules.party_matches("Hess Construction LLC", ""))


class ProjectScopeTest(unittest.TestCase):
	"""Out of scope is silence, not a pass."""

	def test_only_customer_facing_types_are_checked(self):
		for row in PROJECTS:
			expected = (row["project_type"] or "") in rules.CUSTOMER_FACING_PROJECT_TYPES
			self.assertEqual(rules.in_scope("Project", row), expected, row["name"])

	def test_internal_projects_produce_no_findings_at_all(self):
		for name in ("PRJ-00749", "PRJ-00755", "PRJ-00629", "PRJ-00651"):
			row = next(r for r in PROJECTS if r["name"] == name)
			self.assertEqual(rules.check("Project", row), [], name)

	def test_leftover_templates_and_the_dev_backlog_fall_out_without_a_list(self):
		"""`Stage 1 - Predesign` and `Better filtering - 1.5` are excluded because they carry
		no project_type, not because anybody enumerated them. That is the property that keeps
		this maintainable — an exclusion list would need editing every time somebody adds
		another odd record."""
		for name in ("PRJ-00629", "PRJ-00651"):
			row = next(r for r in PROJECTS if r["name"] == name)
			self.assertFalse(rules.in_scope("Project", row), name)

	def test_rent_is_still_accepted_after_the_events_rename(self):
		"""WI-065 renamed Rent to Events and no live row uses Rent. Accepting a value that
		cannot appear costs nothing; a half-applied rename that started flagging real work
		would cost a morning."""
		self.assertTrue(rules.in_scope("Project", {"project_type": "Rent"}))
		self.assertTrue(rules.in_scope("Project", {"project_type": "Events"}))


class SeparatorTest(unittest.TestCase):
	"""Three answers, not two: absent, present-but-written-wrong, and not-a-separator.

	The middle one is why this class exists. Asking "is ' - ' in the value" reports
	`Candi Wadsworth- Fountain Installation` as having no separator, which its author can
	see is untrue — and a check that says something false about a name in front of somebody
	is a check they stop reading.

	Every value below is a verbatim production record (2026-09-14) except where marked.
	"""

	def test_the_house_separator(self):
		self.assertEqual(
			rules.separator_form("Hess Construction - Colony 256 Cascading Pillar"),
			rules.SEP_CANONICAL,
		)

	def test_the_declared_separator_is_the_one_that_passes(self):
		"""The house style is declared once, in SEPARATOR, and separator_form reads it off
		rather than writing it out again. This pins the two together."""
		self.assertEqual(rules.separator_form(f"Party{rules.SEPARATOR}Qualifier"), rules.SEP_CANONICAL)
		self.assertEqual(rules.split(f"Party{rules.SEPARATOR}Qualifier"), ("Party", "Qualifier"))

	def test_a_missing_space_is_a_separator_written_wrongly(self):
		"""Both live Projects in this state. The old rule called these separator_missing."""
		self.assertEqual(
			rules.separator_form("Candi Wadsworth- Fountain Installation 4-Tier"),
			rules.SEP_MALFORMED,
		)
		self.assertEqual(
			rules.separator_form("1 Rain Curtain 4 Pillars -July 4th Red, White, & Blue"),
			rules.SEP_MALFORMED,
		)

	def test_an_en_or_em_dash_is_a_separator_written_wrongly(self):
		"""No typed record uses one; three Opportunity Names written by the Fountain Move
		importer do. Word and Google Docs autocorrect a spaced hyphen into an en dash as you
		type, so the way in is a paste and it looks right at a glance."""
		for value in ("Hess Construction – Colony 256", "Hess Construction — Colony 256"):
			self.assertEqual(rules.separator_form(value), rules.SEP_MALFORMED, value)

	def test_a_bare_hyphen_inside_a_name_is_not_a_separator(self):
		"""The one failure this module cannot afford. Splitting these would invent a party
		called `Ana Mendez` and a qualifier called `Law`: a false finding on a correct name.
		18 Projects, 39 Opportunity Names and 16 Addresses contain a hyphen like this."""
		for value in (
			"Ana Mendez-Law Event",
			"Mayflower-Deer Valley Pool Controller",
			"Big-D Construction St. George",
			"Sarah Henley-Busse",
			"Flo-Tech",
			"Harris-Dudley Co.",
			"2023 MLS All-Star Week Lounge Fountain",
		):
			self.assertEqual(rules.separator_form(value), rules.SEP_NONE, value)
			self.assertEqual(rules.split(value), (value, ""), value)

	def test_no_dash_at_all(self):
		"""345 of 552 customer-facing Projects and 487 of 858 Opportunity Names. This is the
		bulk of what the guard reports, and for these separator_missing is simply correct."""
		self.assertEqual(rules.separator_form("CEM Daybreak Splash Pad"), rules.SEP_NONE)

	def test_extra_spaces_are_not_the_separators_problem(self):
		"""`Foo  -  Bar` is a double space, which DOUBLE_SPACE already reports. Calling the
		separator malformed as well would describe one defect as two."""
		self.assertEqual(rules.separator_form("Hess Construction  -  Colony 256"), rules.SEP_CANONICAL)

	def test_a_malformed_separator_still_splits(self):
		"""So the party prefix and the qualifier are judged on what the author meant."""
		self.assertEqual(
			rules.split("Candi Wadsworth- Fountain Installation"),
			("Candi Wadsworth", "Fountain Installation"),
		)

	def test_the_first_separator_wins_not_a_later_hyphen(self):
		self.assertEqual(
			rules.split("Candi Wadsworth - Fountain Installation 4-Tier"),
			("Candi Wadsworth", "Fountain Installation 4-Tier"),
		)


class ProjectCheckTest(unittest.TestCase):
	def _check(self, name):
		row = next(r for r in PROJECTS if r["name"] == name)
		return codes_of(rules.check("Project", row))

	def test_a_compliant_project_passes(self):
		self.assertEqual(self._check("PRJ-00754"), set())
		self.assertEqual(self._check("PRJ-00758"), set())

	def test_a_site_named_project_is_flagged(self):
		"""Matches neither of the customer's names, which is the genuine shape."""
		self.assertIn(rules.PARTY_PREFIX_MISMATCH, self._check("PRJ-00159"))

	def test_a_stale_customer_docname_is_not_a_naming_defect(self):
		"""The reported bug. PRJ-00706's form shows `Robert Carey Residence` and its row
		stores `BJ Carey`, so checking the column alone reported a mismatch that nothing on
		the form could explain. 14 records were flagged for this: 5 Projects, 3 Opportunities
		and 6 Addresses."""
		self.assertEqual(self._check("PRJ-00706"), set())

	def test_the_flagship_site_named_example_was_itself_a_false_positive(self):
		"""PRJ-00756 was cited in this module's EVIDENCE as *the* live case of a project named
		for the general contractor. Its customer's label is `Landmark Aquatic - CEM`, so
		`Landmark` is a correct shortening and only the stale docname disagreed."""
		self.assertEqual(self._check("PRJ-00756"), set())

	def test_a_project_whose_customer_label_was_never_supplied(self):
		"""attach_customer_labels leaves customer_name empty when no Customer row came back.
		The docname is still a name, so the check still works rather than going silent."""
		found = codes_of(rules.check("Project", {
			"project_name": "Hess Construction - Colony 256", "project_type": "Build",
			"customer": "Hess Construction LLC", "customer_name": "",
		}))
		self.assertEqual(found, set())

	def test_no_separator(self):
		found = self._check("PRJ-00752")
		self.assertIn(rules.SEPARATOR_MISSING, found)
		# ...and only once. Without a separator there is no prefix to judge, so reporting a
		# prefix mismatch as well would describe the same defect twice.
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_a_customer_facing_project_with_no_customer(self):
		"""130 live records. Usually the name is already right and it is the link that was
		never set, which no amount of renaming fixes."""
		found = self._check("PRJ-00757")
		self.assertIn(rules.PARTY_MISSING, found)
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_a_vague_qualifier(self):
		found = codes_of(rules.check("Project", {
			"project_name": "Hess Construction - TBD", "project_type": "Build",
			"customer": "Hess Construction LLC",
		}))
		self.assertIn(rules.QUALIFIER_VAGUE, found)

	def test_a_malformed_separator_is_not_reported_as_a_missing_one(self):
		found = codes_of(rules.check("Project", {
			"project_name": "Candi Wadsworth- Fountain Installation 4-Tier",
			"project_type": "Build", "customer": "Candi Wadsworth",
		}))
		self.assertIn(rules.SEPARATOR_MALFORMED, found)
		self.assertNotIn(rules.SEPARATOR_MISSING, found)
		# The two halves were read correctly, so the party is not also flagged.
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_the_malformed_suggestion_keeps_the_qualifier(self):
		"""The old path suggested `<party> - `, which throws away what the job was."""
		fix = next(
			f for f in rules.check("Project", {
				"project_name": "Candi Wadsworth- Fountain Installation 4-Tier",
				"project_type": "Build", "customer": "Candi Wadsworth",
			})
			if f["code"] == rules.SEPARATOR_MALFORMED
		)
		self.assertEqual(fix["suggestion"], "Candi Wadsworth - Fountain Installation 4-Tier")

	def test_nothing_after_the_separator_is_one_finding_not_two(self):
		"""`Foo -` has a separator missing its trailing space, but the useful thing to say is
		that nothing follows it. "Put a space after the hyphen" is advice about a space with
		nothing to go before it."""
		found = codes_of(rules.check("Project", {
			"project_name": "Hess Construction -", "project_type": "Build",
			"customer": "Hess Construction LLC",
		}))
		self.assertIn(rules.QUALIFIER_MISSING, found)
		self.assertNotIn(rules.SEPARATOR_MALFORMED, found)

	def test_a_hyphenated_customer_is_not_split_apart(self):
		"""`Ana Mendez-Law` is one party. The finding is that the name carries no qualifier,
		not that `Law Event` is the wrong party."""
		found = codes_of(rules.check("Project", {
			"project_name": "Ana Mendez-Law Event", "project_type": "Events",
			"customer": "Ana Mendez-Law",
		}))
		self.assertIn(rules.SEPARATOR_MISSING, found)
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_the_suggestion_is_usable_as_typed(self):
		row = next(r for r in PROJECTS if r["name"] == "PRJ-00159")
		fix = next(f for f in rules.check("Project", row) if f["code"] == rules.PARTY_PREFIX_MISMATCH)
		self.assertEqual(fix["suggestion"], "Anderson Wahlen & Associates - Fountain")

	def test_the_suggestion_uses_the_label_not_the_stale_docname(self):
		"""`BJ Carey - ...` would be advice to type a name nobody in the business uses."""
		fix = next(
			f for f in rules.check("Project", {
				"project_name": "Poolside Cabana Feature", "project_type": "Build",
				"customer": "BJ Carey", "customer_name": "Robert Carey Residence",
			})
			if f["code"] == rules.SEPARATOR_MISSING
		)
		self.assertEqual(fix["suggestion"], "Robert Carey Residence - ")


class AddressCheckTest(unittest.TestCase):
	def _check(self, name):
		row = next(r for r in ADDRESSES if r["name"] == name)
		return codes_of(rules.check("Address", row))

	def test_a_title_that_is_the_party_passes(self):
		self.assertEqual(self._check("Hansen Design Firm-Billing"), set())

	def test_a_site_named_title_is_flagged(self):
		self.assertIn(rules.PARTY_PREFIX_MISMATCH, self._check("Stenmark-Billing"))

	def test_a_generic_title_is_flagged(self):
		self.assertIn(rules.PARTY_PREFIX_MISMATCH, self._check("HOME-Billing-1"))

	def test_a_title_that_appends_the_type_itself(self):
		"""Frappe's autoname appends the address type, so a title carrying one produces
		'Site - Office-Billing'."""
		found = self._check("Charles Cunniffe Architects - Office")
		self.assertIn(rules.TITLE_CARRIES_QUALIFIER, found)
		# The party is judged against what remains once the doubled qualifier is stripped,
		# so a record whose party IS right does not also collect a mismatch.
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_a_name_whose_type_suffix_has_gone_stale(self):
		"""`autoname` runs once at insert; changing address_type afterwards leaves the name
		behind. NOTE, not FIX — the remedy is a rename."""
		found = self._check("DELIVERY-Billing")
		self.assertIn(rules.ADDRESS_TYPE_STALE, found)
		stale = next(
			f for f in rules.check("Address", next(r for r in ADDRESSES if r["name"] == "DELIVERY-Billing"))
			if f["code"] == rules.ADDRESS_TYPE_STALE
		)
		self.assertEqual(stale["severity"], rules.NOTE)

	def test_a_stale_note_never_moves_the_verdict(self):
		row = {"name": "Acme-Billing", "address_title": "Acme", "address_type": "Shipping", "party": "Acme"}
		findings = rules.check("Address", row)
		self.assertEqual(codes_of(findings), {rules.ADDRESS_TYPE_STALE})
		self.assertEqual(rules.verdict(findings), rules.VERDICT_PASS)

	def test_the_collision_suffix_does_not_read_as_stale(self):
		"""`HOME-Billing-1` is frappe's duplicate suffix, not a wrong type."""
		self.assertNotIn(rules.ADDRESS_TYPE_STALE, self._check("HOME-Billing-1"))

	def test_an_address_titled_with_the_links_docname_passes(self):
		"""Six live Addresses. `link_title` and `link_name` disagree on 99 Address links, and
		either is a correct thing to title the record with."""
		found = self._check("CEM Aquatics - Billing")
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_an_unlinked_address_is_out_of_scope(self):
		"""442 live records. There is no party to name them after, so there is nothing to
		judge — the missing link is the caller's finding to report, not this one's."""
		row = next(r for r in ADDRESSES if r["name"] == "Orphan-Billing")
		self.assertFalse(rules.in_scope("Address", row))
		self.assertEqual(rules.check("Address", row), [])


class OpportunityCheckTest(unittest.TestCase):
	def _check(self, name):
		row = next(r for r in OPPORTUNITIES if r["name"] == name)
		return codes_of(rules.check("Opportunity", row))

	def test_the_reported_record_passes(self):
		"""CRM-OPP-2026-00173. Its banner read `Naming: FIX — separator_missing` under a title
		of `Summit Vista - Circulation Pump`."""
		self.assertEqual(self._check("CRM-OPP-2026-00173"), set())

	def test_compliant_names_the_old_read_flagged_now_pass(self):
		"""346 Opportunities were told separator_missing about a name that has one; on 208 of
		them nothing else is wrong and the banner goes away."""
		for name in ("CRM-OPP-2026-00153", "CRM-OPP-2026-00156", "CRM-OPP-2026-00155"):
			self.assertEqual(self._check(name), set(), name)

	def test_a_bare_party_name_needs_a_qualifier(self):
		"""487 of 858 live Opportunity Names carry no separator at all. For them the finding is
		simply correct."""
		self.assertIn(rules.SEPARATOR_MISSING, codes_of(rules.check("Opportunity", {
			"custom_opportunity_name": "Hess Construction LLC", "party_name": "Hess Construction LLC",
		})))

	def test_a_site_named_opportunity_is_still_flagged(self):
		self.assertIn(rules.PARTY_PREFIX_MISMATCH, self._check("CRM-OPP-2025-00127"))

	def test_a_dropped_space_is_malformed_not_missing(self):
		found = self._check("CRM-OPP-2025-00620")
		self.assertIn(rules.SEPARATOR_MALFORMED, found)
		self.assertNotIn(rules.SEPARATOR_MISSING, found)
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, found)

	def test_the_generated_fountain_move_name_is_flagged_for_what_it_is(self):
		"""An em dash and no party first: two real findings, and neither is "no separator"."""
		found = self._check("CRM-OPP-2026-00139")
		self.assertIn(rules.SEPARATOR_MALFORMED, found)
		self.assertIn(rules.PARTY_PREFIX_MISMATCH, found)
		self.assertNotIn(rules.SEPARATOR_MISSING, found)

	def test_a_trailing_space(self):
		"""55 live Opportunity Names have one. MariaDB's PAD SPACE collation makes the SQL form
		of this test always false, so it is done here where a trailing space is still a
		character."""
		self.assertEqual(self._check("CRM-OPP-2026-00045"), {rules.EDGE_WHITESPACE})

	def test_every_opportunity_is_in_scope(self):
		"""All 858 carry a party; there is no internal-opportunity class to exclude."""
		for row in OPPORTUNITIES:
			self.assertTrue(rules.in_scope("Opportunity", row), row["name"])

	def test_a_compliant_name_passes(self):
		self.assertEqual(codes_of(rules.check("Opportunity", {
			"custom_opportunity_name": "Ore - Courtyard Fountain Refit", "party_name": "Ore Designs, Inc.",
		})), set())

	def test_both_names_are_accepted_when_the_link_and_the_label_disagree(self):
		"""They disagree on every record whose party was renamed after it was created, and a
		name built from either is correct. 24 live Opportunities were flagged for this."""
		row = {"custom_opportunity_name": "Acme - Thing", "party_name": "Acme Holdings",
		       "customer_name": "Something Else"}
		self.assertEqual(rules.party_names("Opportunity", row), ("Something Else", "Acme Holdings"))
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, codes_of(rules.check("Opportunity", row)))
		other = dict(row, custom_opportunity_name="Something - Thing")
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, codes_of(rules.check("Opportunity", other)))

	def test_the_label_is_what_gets_displayed(self):
		"""party_of feeds the form banner and the report's Party column. A row reading
		`CRM-LEAD-2025-00184` for a party the Desk calls `Cactus & Tropicals` describes a
		record nobody can find."""
		row = {"custom_opportunity_name": "Cactus & Tropicals", "party_name": "CRM-LEAD-2025-00184",
		       "customer_name": "Cactus & Tropicals"}
		self.assertEqual(rules.party_of("Opportunity", row), "Cactus & Tropicals")

	def test_an_opportunity_against_a_lead_is_not_compared_to_a_document_id(self):
		"""party_name can be a Lead docname. Comparing a name against `CRM-LEAD-2025-00184`
		is a mismatch by construction — it can never pass, however the record is named."""
		self.assertNotIn(rules.PARTY_PREFIX_MISMATCH, codes_of(rules.check("Opportunity", {
			"custom_opportunity_name": "Cactus & Tropicals - Grand Opening",
			"party_name": "CRM-LEAD-2025-00184", "customer_name": "Cactus & Tropicals",
		})))

	def test_a_party_with_one_name_is_named_once(self):
		"""The usual case. Deduplicated, so no message ever reads "X, also recorded as X"."""
		row = {"custom_opportunity_name": "Ore - Refit", "party_name": "Ore Designs, Inc.",
		       "customer_name": "Ore Designs, Inc."}
		self.assertEqual(rules.party_names("Opportunity", row), ("Ore Designs, Inc.",))


class OpportunityNameFieldTest(unittest.TestCase):
	"""The check judges the field the form shows, and only that one.

	The reported bug. Opportunity's form title is the Opportunity Name, set by a Property
	Setter in June; ``title`` is ERPNext's own field, filled from customer_name in
	``Opportunity.validate`` and shown nowhere. Reading ``title`` made the banner disagree with
	the name directly above it on 346 records, which is the one thing an advisory cannot do
	and keep being read.
	"""

	def test_it_reads_the_opportunity_name(self):
		self.assertEqual(rules.DOCTYPES["Opportunity"]["field"], "custom_opportunity_name")
		self.assertEqual(rules.DOCTYPES["Opportunity"]["label"], "Opportunity Name")

	def test_erpnexts_title_is_never_read(self):
		"""Whatever title holds, the verdict is the Opportunity Name's."""
		row = next(r for r in OPPORTUNITIES if r["name"] == "CRM-OPP-2026-00173")
		for title in ("Summit Vista", "", "Something Else Entirely", "A - B"):
			self.assertEqual(codes_of(rules.check("Opportunity", dict(row, title=title))), set(), title)

	def test_a_separator_in_title_does_not_excuse_the_name(self):
		"""The inverse of the bug: the old read passed this record because the customer's own
		name carries a separator."""
		found = codes_of(rules.check("Opportunity", next(
			r for r in OPPORTUNITIES if r["name"] == "CRM-OPP-2025-00076"
		)))
		self.assertIn(rules.SEPARATOR_MISSING, found)

	def test_an_empty_opportunity_name_is_reported_even_when_title_is_set(self):
		"""The field is mandatory, so this should not occur; if it does, the form title falls
		back to the record ID and "Opportunity Name is empty" is the true finding."""
		findings = rules.check("Opportunity", {
			"title": "Summit Vista - Circulation Pump", "custom_opportunity_name": "",
			"party_name": "Summit Vista",
		})
		self.assertEqual(codes_of(findings), {rules.VALUE_MISSING})
		self.assertIn("Opportunity Name", findings[0]["message"])

	def test_messages_name_the_field_people_see(self):
		findings = rules.check("Opportunity", {
			"custom_opportunity_name": "Summit Vista Circulation Pump", "party_name": "Summit Vista",
		})
		self.assertTrue(findings[0]["message"].startswith("Opportunity Name"), findings[0]["message"])

	def test_the_checked_field_is_the_forms_title_field(self):
		"""Pins the rule to the title_field Property Setter in the fixtures, so moving the form
		title again fails here instead of on the form. Party-qualifier shapes only: an
		Address's title field is its full address, and the check deliberately reads
		address_title, which is what frappe builds the record name from."""
		with open(ROOT / "fixtures" / "property_setter.json", encoding="utf-8") as fh:
			title_fields = {
				row["doc_type"]: row["value"]
				for row in json.load(fh)
				if row.get("property") == "title_field" and row.get("doctype_or_field") == "DocType"
			}
		self.assertIn("Opportunity", title_fields, "Opportunity's title_field Property Setter is gone")
		for doctype, config in rules.DOCTYPES.items():
			if config["shape"] != rules.SHAPE_PARTY_QUALIFIER or doctype not in title_fields:
				continue
			self.assertEqual(config["field"], title_fields[doctype], doctype)

	def test_the_opportunity_name_is_a_shipped_custom_field(self):
		"""The reader selects the column by name; it must exist wherever this app is installed."""
		with open(ROOT / "fixtures" / "custom_field.json", encoding="utf-8") as fh:
			shipped = {(row["dt"], row["fieldname"]) for row in json.load(fh)}
		self.assertIn(("Opportunity", "custom_opportunity_name"), shipped)

	def test_the_reader_selects_the_field_the_rules_check(self):
		"""party_naming.FIELDS is the column list the form endpoint, the report and the KPI read.
		It imports frappe, so it is read here as source rather than imported. A rule pointed at
		a column the reader never selects sees None on every record and reports value_missing
		on all of them."""
		tree = ast.parse((ROOT / "crm_enhancements" / "party_naming.py").read_text(encoding="utf-8"))
		fields = next(
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "FIELDS" for t in node.targets)
		)
		for doctype, config in rules.DOCTYPES.items():
			self.assertIn(config["field"], fields[doctype], doctype)
		self.assertNotIn("title", fields["Opportunity"])


class AuditTest(unittest.TestCase):
	def test_audit_drops_out_of_scope_rows_rather_than_passing_them(self):
		rows = rules.audit("Project", PROJECTS)
		names = {r["name"] for r in rows}
		self.assertNotIn("PRJ-00749", names, "an internal project must not appear at all")
		self.assertNotIn("PRJ-00629", names)
		self.assertIn("PRJ-00754", names)
		self.assertEqual(len(rows), 7, "seven of eleven fixtures are customer-facing")

	def test_collisions_are_found_in_one_pass(self):
		rows = [
			{"name": "PRJ-1", "project_name": "Acme - Fountain", "project_type": "Build", "customer": "Acme"},
			{"name": "PRJ-2", "project_name": "ACME  -  FOUNTAIN", "project_type": "Build", "customer": "Acme"},
			{"name": "PRJ-3", "project_name": "Acme - Pump", "project_type": "Build", "customer": "Acme"},
		]
		audited = {r["name"]: r for r in rules.audit("Project", rows)}
		self.assertIn(rules.DUPLICATE_NORMALISED, codes_of(audited["PRJ-1"]["findings"]))
		self.assertEqual(audited["PRJ-1"]["verdict"], rules.VERDICT_STOP)
		self.assertNotIn(rules.DUPLICATE_NORMALISED, codes_of(audited["PRJ-3"]["findings"]))

	def test_sort_is_worst_first_and_deterministic(self):
		rows = sorted(rules.audit("Project", PROJECTS), key=rules.audit_sort_key)
		seen = [rules.SEVERITY_ORDER.get(r["verdict"], 9) for r in rows]
		self.assertEqual(seen, sorted(seen))
		self.assertEqual(rows, sorted(rules.audit("Project", PROJECTS), key=rules.audit_sort_key))

	def test_summarise_reports_no_compliance_figure_for_nothing(self):
		self.assertIsNone(rules.summarise([])["compliance_pct"])

	def test_summarise_counts_only_in_scope_rows(self):
		summary = rules.summarise(rules.audit("Project", PROJECTS))
		self.assertEqual(summary["in_scope_rows"], 7)
		self.assertEqual(summary["normalisation"], rules.NORMALISATION)

	def test_no_check_raises_on_any_live_record(self):
		"""These fixtures are full of malformed strings. A validator that throws on the records
		it exists to find is worse than none."""
		for doctype, rows in (("Project", PROJECTS), ("Address", ADDRESSES), ("Opportunity", OPPORTUNITIES)):
			rules.audit(doctype, rows)
			for row in rows:
				rules.check(doctype, row)

	def test_an_unknown_doctype_is_silent_rather_than_an_error(self):
		self.assertEqual(rules.check("Sales Invoice", {"foo": "bar"}), [])
		self.assertEqual(rules.audit("Sales Invoice", [{"name": "X"}]), [])


class PartyRecordTest(unittest.TestCase):
	"""The record a party *is*, for the report's link (the report face, form kit P3.3)."""

	def test_each_doctype_points_at_its_party(self):
		self.assertEqual(
			rules.party_record("Project", {"customer": "BJ Carey", "customer_name": "Robert Carey Residence"}),
			("Customer", "BJ Carey"),
		)
		self.assertEqual(
			rules.party_record("Opportunity", {"opportunity_from": "Lead", "party_name": "CRM-LEAD-0001"}),
			("Lead", "CRM-LEAD-0001"),
		)
		self.assertEqual(
			rules.party_record("Address", {"party_doctype": "Supplier", "party_alias": "Pumps Inc", "party": "Pumps"}),
			("Supplier", "Pumps Inc"),
		)

	def test_both_parts_or_neither(self):
		self.assertEqual(rules.party_record("Project", {"customer": ""}), ("", ""))
		self.assertEqual(rules.party_record("Opportunity", {"party_name": "X"}), ("", ""))
		self.assertEqual(rules.party_record("Address", {"party_doctype": "Customer"}), ("", ""))
		self.assertEqual(rules.party_record("Sales Invoice", {"customer": "X"}), ("", ""))

	def test_the_audit_carries_it(self):
		rows = rules.audit("Project", PROJECTS)
		self.assertTrue(rows)
		for row in rows:
			with self.subTest(name=row["name"]):
				doctype, docname = row["party_record"]
				self.assertIn(doctype, ("Customer", ""))
				self.assertEqual(bool(doctype), bool(docname))


if __name__ == "__main__":
	unittest.main()
