"""Bench-free tests for the proposed fixes (form kit P2-C, part two: utils/normalize_fixes.py).

A proposal is shown to a System Manager next to a value the normalization rules cannot read, and
applied only where they tick it. So the tests hold two things equally: each rule proposes the fix
it should, and — more important — it proposes *nothing* where only a person can tell (a typo, a
truncated number, a foreign number whose country is unknown, an address missing its .com).

Every value here is made up: 555 numbers, example.com addresses, the Ofcom drama ranges. The
repository is public, and no real record's value belongs in it.

The international rule needs ``phonenumbers`` (Frappe v16 depends on it, ``~=9.0.23``); CI
installs that version for this step. Without it those tests skip, and the rule proposes nothing.

Run: python -m unittest erpnext_enhancements.tests.test_normalize_fixes -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import normalize
from erpnext_enhancements.utils import normalize_fixes as fixes

try:
    import phonenumbers

    HAVE_PHONENUMBERS = True
except ImportError:
    HAVE_PHONENUMBERS = False


def phone(raw, record=None, doctype="Contact", fieldname="custom_phone_number"):
    return fixes.propose("phone", raw, record, doctype, fieldname)


class TestEveryProposalIsForAStuckValue(unittest.TestCase):
    def test_the_values_these_tests_use_are_ones_the_rules_cannot_read(self):
        """A proposal is only ever asked for when normalize() failed; keep the cases honest."""
        for raw in (
            "123-456-7890",
            "X801-555-0142",
            "(801) 555-0142 (front desk)",
            "801-555-0142 PIN 4321",
            "1-801-555-TEST",
            "44 20 7946 0958",
            "+44 (0)20 7946 0958",
            "Email only",
            "someone@example.com",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(normalize.phone(raw).ok)


class TestPhones(unittest.TestCase):
    def test_only_spaces_becomes_blank(self):
        self.assertEqual(phone("   "), fixes.Fix("", {}, "only spaces"))
        self.assertEqual(fixes.propose("email", " ", {}, "Customer"), fixes.Fix("", {}, "only spaces"))

    def test_only_spaces_is_never_blanked_where_a_field_may_be_mandatory(self):
        for kind in ("text", "state", "zip"):
            with self.subTest(kind=kind):
                self.assertIsNone(fixes.propose(kind, "   ", {"country": "United States"}))

    def test_placeholders_are_cleared(self):
        for raw in ("123-456-7890", "098-765-4321", "(120) 120-1200", "1-123-456-7890", "000-000-0000"):
            with self.subTest(raw=raw):
                fix = phone(raw)
                self.assertEqual((fix.value, fix.also), ("", {}))
                self.assertIn("placeholder", fix.why)

    def test_one_number_inside_text_is_kept(self):
        self.assertEqual(phone("X801-555-0142").value, "+18015550142")
        self.assertEqual(phone("(801) 555-0142 (front desk)").value, "+18015550142")
        self.assertEqual(phone("Name - Pat ExamplePhone - (801) 555-0142").value, "+18015550142")

    def test_a_pin_or_extension_after_it_becomes_the_extension(self):
        self.assertEqual(phone("801-555-0142 PIN 4321").value, "+18015550142,4321")
        self.assertEqual(phone("main 801-555-0142 ext. 12").value, "+18015550142,12")

    def test_two_numbers_in_text_are_left_for_a_person(self):
        self.assertIsNone(phone("office 801-555-0142 or cell 801-555-0143"))

    def test_vanity_numbers(self):
        self.assertEqual(phone("1-801-555-TEST").value, "+18015558378")
        self.assertEqual(phone("801-555 -TEST (8378)").value, "+18015558378", "a bracketed restatement")
        self.assertEqual(phone("801.555.TEST Ext.2").value, "+18015558378,2")
        self.assertIn("vanity", phone("1-801-555-TEST").why)

    def test_text_that_is_not_a_vanity_number(self):
        # No digits at all: cleared as a note, never read as keypad letters.
        self.assertEqual(phone("old contact").why, "no digits: a name or a note, not a phone number")
        self.assertEqual(phone("Email only").value, "")
        # Too many letters to be a vanity number, and no area code first.
        self.assertIsNone(phone("801-CALLUSNOW"))

    def test_an_email_in_a_phone_field_moves_to_an_empty_email_field(self):
        fix = phone(
            "Someone@Example.com",
            {"custom_accounts_email_address": ""},
            "Customer",
            "custom_accounts_phone_number",
        )
        self.assertEqual(fix.value, "")
        self.assertEqual(fix.also, {"custom_accounts_email_address": "someone@example.com"})

    def test_an_email_already_in_the_email_field_is_just_cleared(self):
        fix = phone(
            "someone@example.com",
            {"custom_accounts_email_address": "SOMEONE@example.com"},
            "Customer",
            "custom_accounts_phone_number",
        )
        self.assertEqual((fix.value, fix.also), ("", {}))

    def test_an_email_that_would_overwrite_another_is_left_for_a_person(self):
        record = {"custom_accounts_email_address": "billing@example.com"}
        self.assertIsNone(phone("someone@example.com", record, "Customer", "custom_accounts_phone_number"))
        self.assertIsNone(phone("someone@example.com", {}, "Contact", "custom_phone_number"), "no known target")

    def test_what_only_a_person_can_tell(self):
        for raw in (
            "801",  # a fragment
            "801-555-014",  # a digit short
            "8015550142 0",  # a digit too many, run together
            "80155501420",  # eleven digits not starting with 1
            "590",
            "(055) 555-0142",  # a trunk 0: a foreign number, country unknown
            "001 555 0142 7",
            " ext. 3550",  # an extension with no number
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(phone(raw))


@unittest.skipUnless(HAVE_PHONENUMBERS, "phonenumbers is not installed")
class TestInternational(unittest.TestCase):
    def test_a_country_code_set_apart_gets_its_plus(self):
        self.assertEqual(phone("44 20 7946 0958").value, "+442079460958")
        self.assertEqual(phone("(44) 20 7946 0958").value, "+442079460958")
        self.assertEqual(phone("44-20-7946-0958").value, "+442079460958")
        self.assertIn("without its +", phone("44 20 7946 0958").why)

    def test_a_trunk_zero_is_dropped(self):
        fix = phone("+44 (0)20 7946 0958")
        self.assertEqual(fix.value, "+442079460958")
        self.assertEqual(fix.why, "the (0) trunk prefix dropped")
        fix = phone("44(0)20 7946 0958")
        self.assertEqual(fix.value, "+442079460958")
        self.assertIn("without its +", fix.why)
        self.assertIn("(0) trunk prefix", fix.why)

    def test_never_guessed(self):
        for raw in (
            "442079460958",  # no separator: maybe a country code, maybe a typo
            "(755) 5550 1420",  # 755 is no country code
            "44 20 7946",  # a valid country code, but not a valid number
            "1 801 555 014",  # +1 is North American, which the main rules already handle
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(phone(raw))


class TestWithoutPhonenumbers(unittest.TestCase):
    def test_the_international_rule_stays_quiet(self):
        original = fixes._phonenumbers
        fixes._phonenumbers = lambda: None
        try:
            self.assertIsNone(phone("44 20 7946 0958"))
            self.assertEqual(phone("X801-555-0142").value, "+18015550142", "the other rules still work")
        finally:
            fixes._phonenumbers = original


class TestEmails(unittest.TestCase):
    def test_a_website_moves_to_an_empty_website_field(self):
        fix = fixes.propose("email", "https://www.example.com/", {"website": ""}, "Customer")
        self.assertEqual(fix.also, {"website": "https://www.example.com/"})
        fix = fixes.propose("email", "example.com/contact", {"website": None}, "Customer")
        self.assertEqual(fix.also, {"website": "https://example.com/contact"})

    def test_a_website_already_there_is_cleared_and_another_is_left(self):
        same = fixes.propose("email", "https://www.example.com", {"website": "https://www.example.com/"}, "Customer")
        self.assertEqual((same.value, same.also), ("", {}))
        self.assertIsNone(fixes.propose("email", "https://example.com", {"website": "https://other.example"}, "Customer"))

    def test_a_job_title_on_a_contact_moves_to_designation(self):
        fix = fixes.propose("email", "Founder and Principal", {"designation": ""}, "Contact")
        self.assertEqual(fix.also, {"designation": "Founder and Principal"})
        self.assertIsNone(fixes.propose("email", "Founder", {"designation": "Owner"}, "Contact"))
        dup = fixes.propose("email", "Owner", {"designation": "owner"}, "Contact")
        self.assertEqual((dup.value, dup.also), ("", {}))

    def test_other_text_is_cleared(self):
        fix = fixes.propose("email", "123 Main St Suite 4, Anytown", {}, "Customer")
        self.assertEqual((fix.value, fix.also), ("", {}))
        self.assertIn("cleared", fix.why)

    def test_an_address_missing_its_ending_is_left_for_a_person(self):
        self.assertIsNone(fixes.propose("email", "pat@example", {}, "Contact"))
        self.assertIsNone(fixes.propose("email", "pat@example.com, sam@example.com", {}, "Contact"))


class TestZips(unittest.TestCase):
    US = "United States"

    def test_a_lost_leading_zero_is_put_back_in_a_zero_state(self):
        self.assertEqual(fixes.propose("zip", "7030", {"state": "NJ", "country": self.US}).value, "07030")
        self.assertEqual(fixes.propose("zip", "2903", {"state": "Rhode Island", "country": self.US}).value, "02903")

    def test_otherwise_it_is_left(self):
        self.assertIsNone(fixes.propose("zip", "8520", {"state": "UT", "country": self.US}), "Utah ZIPs start 84")
        self.assertIsNone(fixes.propose("zip", "1234", {"state": "NJ", "country": self.US}), "out of NJ's range")
        self.assertIsNone(fixes.propose("zip", "7030", {"state": "NJ", "country": "Canada"}))
        self.assertIsNone(fixes.propose("zip", "703", {"state": "NJ", "country": self.US}))


class TestEverythingElse(unittest.TestCase):
    def test_kinds_without_rules_propose_nothing(self):
        self.assertIsNone(fixes.propose("state", "Springfield", {"country": "United States"}))
        self.assertIsNone(fixes.propose("website", "not a site"))
        self.assertIsNone(fixes.propose("phone", None))

    def test_every_proposed_phone_is_one_the_rules_accept(self):
        for raw in ("X801-555-0142", "1-801-555-TEST", "801-555-0142 PIN 4321"):
            with self.subTest(raw=raw):
                result = normalize.phone(phone(raw).value)
                self.assertTrue(result.ok)
                self.assertFalse(result.changed, "stored in its one form already")

    def test_every_move_target_is_a_field_the_report_reads(self):
        from erpnext_enhancements.utils.normalize_fields import FIELDS

        for (doctype, source), target in fixes.EMAIL_FOR_PHONE.items():
            with self.subTest(doctype=doctype, source=source):
                self.assertEqual(FIELDS[doctype].get(source), "phone")
                self.assertEqual(FIELDS[doctype].get(target), "email")
        for doctype, target in fixes.WEBSITE_FIELD.items():
            with self.subTest(doctype=doctype):
                self.assertEqual(FIELDS[doctype].get(target), "website")
        for doctype, target in fixes.TITLE_FIELD.items():
            with self.subTest(doctype=doctype):
                self.assertIn(target, fixes.EXTRA_COLUMNS[doctype])


if __name__ == "__main__":
    unittest.main()
