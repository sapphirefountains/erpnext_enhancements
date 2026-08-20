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

Every fixture is a verbatim record read from ERPNext Production on 19 August 2026.
"""

import unittest

from erpnext_enhancements.crm_enhancements import party_naming_rules as rules


def codes_of(findings):
	return {f["code"] for f in findings}


# --- verbatim production fixtures ----------------------------------------------

PROJECTS = [
	# Compliant: prefix is a shortening of the customer.
	{"name": "PRJ-00754", "project_name": "Hess Construction - Colony 256 Cascading Pillar",
	 "project_type": "Build", "customer": "Hess Construction LLC"},
	{"name": "PRJ-00758", "project_name": "West Jordan - Splash Pad Controller Servive 8/17/26",
	 "project_type": "Service", "customer": "West Jordan Parks and Recreation"},
	# Named for the site / general contractor rather than for who pays.
	{"name": "PRJ-00756", "project_name": "Landmark - Millcreek Commons Phase 2 Controller",
	 "project_type": "Build", "customer": "CEM Aquatics"},
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
	# No link at all — 442 live records. Out of scope: nothing to name it after.
	{"name": "Orphan-Billing", "address_title": "Orphan", "address_type": "Billing", "party": ""},
]

OPPORTUNITIES = [
	{"name": "CRM-OPP-2026-00153", "title": "Hess Construction LLC", "party_name": "Hess Construction LLC"},
	{"name": "CRM-OPP-2026-00156", "title": "West Jordan Parks and Recreation ",
	 "party_name": "West Jordan Parks and Recreation"},
	{"name": "CRM-OPP-2026-00155", "title": "Ore", "party_name": "Ore Designs, Inc."},
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
		'Landmark' for 'CEM Aquatics', and the site-named projects are the defect this exists
		to find."""
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


class ProjectCheckTest(unittest.TestCase):
	def _check(self, name):
		row = next(r for r in PROJECTS if r["name"] == name)
		return codes_of(rules.check("Project", row))

	def test_a_compliant_project_passes(self):
		self.assertEqual(self._check("PRJ-00754"), set())
		self.assertEqual(self._check("PRJ-00758"), set())

	def test_a_site_named_project_is_flagged(self):
		self.assertIn(rules.PARTY_PREFIX_MISMATCH, self._check("PRJ-00756"))

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

	def test_the_suggestion_is_usable_as_typed(self):
		row = next(r for r in PROJECTS if r["name"] == "PRJ-00756")
		fix = next(f for f in rules.check("Project", row) if f["code"] == rules.PARTY_PREFIX_MISMATCH)
		self.assertEqual(fix["suggestion"], "CEM Aquatics - Millcreek Commons Phase 2 Controller")


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

	def test_a_bare_party_name_needs_a_qualifier(self):
		"""769 of 823 live titles are exactly the party name, because ERPNext fills a blank
		title with customer_name and nobody types one."""
		self.assertIn(rules.SEPARATOR_MISSING, self._check("CRM-OPP-2026-00153"))

	def test_a_trailing_space(self):
		"""25 live titles have one. MariaDB's PAD SPACE collation makes the SQL form of this
		test always false, so it is done here where a trailing space is still a character."""
		self.assertIn(rules.EDGE_WHITESPACE, self._check("CRM-OPP-2026-00156"))

	def test_every_opportunity_is_in_scope(self):
		"""All 823 carry a party; there is no internal-opportunity class to exclude."""
		for row in OPPORTUNITIES:
			self.assertTrue(rules.in_scope("Opportunity", row), row["name"])

	def test_a_compliant_title_passes(self):
		self.assertEqual(codes_of(rules.check("Opportunity", {
			"title": "Ore - Courtyard Fountain Refit", "party_name": "Ore Designs, Inc.",
		})), set())

	def test_the_link_is_preferred_over_the_label(self):
		"""party_name and customer_name disagree on records where the party was renamed."""
		row = {"title": "Acme - Thing", "party_name": "Acme Holdings", "customer_name": "Something Else"}
		self.assertEqual(rules.party_of("Opportunity", row), "Acme Holdings")


class AuditTest(unittest.TestCase):
	def test_audit_drops_out_of_scope_rows_rather_than_passing_them(self):
		rows = rules.audit("Project", PROJECTS)
		names = {r["name"] for r in rows}
		self.assertNotIn("PRJ-00749", names, "an internal project must not appear at all")
		self.assertNotIn("PRJ-00629", names)
		self.assertIn("PRJ-00754", names)
		self.assertEqual(len(rows), 5, "five of nine fixtures are customer-facing")

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
		self.assertEqual(summary["in_scope_rows"], 5)
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


if __name__ == "__main__":
	unittest.main()
