"""Bench-free unit tests for the Purchase Order separation-of-duties gate (WI-066).

Stubs a minimal ``frappe`` (no site/bench) so the pure decision logic in
``erpnext_enhancements.po_segregation`` runs under plain unittest. The stub is
installed in ``setUpModule`` (execution time), not at import, so it never fools
the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_po_segregation
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Mutable state the frappe stub reads at call time.
# ``mr_owners`` stands in for the Material Request table: name -> owner.
STATE = {"user": "parker@example.com", "mr_owners": {}, "enabled": 1}
po_segregation = None


class StubThrow(Exception):
    pass


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def throw(msg, title=None, exc=None):
        raise StubThrow(msg)

    def get_all(doctype, filters=None, pluck=None, **kwargs):
        # Mirrors the real call: filters={"name": ("in", [...]), "owner": user}
        names = list((filters or {})["name"][1])
        owner = (filters or {})["owner"]
        return [n for n in names if STATE["mr_owners"].get(n) == owner]

    frappe.throw = throw
    frappe.ValidationError = StubThrow
    frappe._ = lambda s: s
    frappe.session = types.SimpleNamespace(user=STATE["user"])
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(
        get_single_value=lambda dt, field: STATE["enabled"],
    )

    sys.modules["frappe"] = frappe


def setUpModule():
    global po_segregation
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.po_segregation", None)
    from erpnext_enhancements import po_segregation as mod

    po_segregation = mod


def _set(user="parker@example.com", mr_owners=None, enabled=1):
    STATE.update(user=user, mr_owners=dict(mr_owners or {}), enabled=enabled)
    sys.modules["frappe"].session.user = user


class _Doc(dict):
    """Minimal PO stand-in: attribute access (doc.owner) + .get('items')."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _po(*material_requests, owner="someone-else@example.com"):
    """A PO whose lines carry the given material_request values (None/'' allowed)."""
    return _Doc(
        owner=owner,
        items=[{"material_request": mr} for mr in material_requests],
    )


class TestLinkedMaterialRequests(unittest.TestCase):
    def test_no_items_returns_empty(self):
        self.assertEqual(po_segregation.get_linked_material_requests(_Doc(items=[])), [])

    def test_missing_items_key_returns_empty(self):
        # ERPNext's own test bootstrap can hand us a doc with no items at all
        self.assertEqual(po_segregation.get_linked_material_requests(_Doc()), [])

    def test_blank_material_requests_ignored(self):
        doc = _po(None, "", "   ")
        self.assertEqual(po_segregation.get_linked_material_requests(doc), [])

    def test_duplicates_collapse(self):
        doc = _po("MR-001", "MR-001", "MR-001")
        self.assertEqual(po_segregation.get_linked_material_requests(doc), ["MR-001"])

    def test_names_are_sorted(self):
        doc = _po("MR-009", "MR-002", "MR-005")
        self.assertEqual(
            po_segregation.get_linked_material_requests(doc), ["MR-002", "MR-005", "MR-009"]
        )

    def test_whitespace_is_stripped(self):
        doc = _po("  MR-001  ")
        self.assertEqual(po_segregation.get_linked_material_requests(doc), ["MR-001"])


class TestSeparationExempt(unittest.TestCase):
    def test_administrator_is_exempt(self):
        self.assertTrue(po_segregation.is_separation_exempt("Administrator"))

    def test_po_approver_is_not_exempt(self):
        # decision 3: no role clears this gate, not even the CEO's
        self.assertFalse(po_segregation.is_separation_exempt("ceo@example.com"))

    def test_defaults_to_session_user(self):
        _set(user="Administrator")
        self.assertTrue(po_segregation.is_separation_exempt())


class TestEnforceRequesterSeparation(unittest.TestCase):
    def test_no_material_request_link_passes(self):
        _set(user="parker@example.com", mr_owners={"MR-001": "parker@example.com"})
        po_segregation.enforce_requester_separation(_po())  # no raise

    def test_own_material_request_blocks(self):
        _set(user="parker@example.com", mr_owners={"MR-001": "parker@example.com"})
        with self.assertRaises(StubThrow):
            po_segregation.enforce_requester_separation(_po("MR-001"))

    def test_someone_elses_material_request_passes(self):
        _set(user="clegg@example.com", mr_owners={"MR-001": "parker@example.com"})
        po_segregation.enforce_requester_separation(_po("MR-001"))  # no raise

    def test_owning_one_of_several_blocks(self):
        _set(
            user="parker@example.com",
            mr_owners={"MR-001": "james@example.com", "MR-002": "parker@example.com"},
        )
        with self.assertRaises(StubThrow) as ctx:
            po_segregation.enforce_requester_separation(_po("MR-001", "MR-002"))
        # the message names only the MR they actually own
        self.assertIn("MR-002", str(ctx.exception))
        self.assertNotIn("MR-001", str(ctx.exception))

    def test_deleted_material_request_fails_open(self):
        # dangling link: the MR no longer resolves, so it can never brick the PO
        _set(user="parker@example.com", mr_owners={})
        po_segregation.enforce_requester_separation(_po("MR-GONE"))  # no raise

    def test_administrator_bypasses(self):
        _set(user="Administrator", mr_owners={"MR-001": "Administrator"})
        po_segregation.enforce_requester_separation(_po("MR-001"))  # no raise

    def test_po_approver_does_not_bypass(self):
        # the decision-3 proof: holding the approving role changes nothing here
        _set(user="ceo@example.com", mr_owners={"MR-001": "ceo@example.com"})
        with self.assertRaises(StubThrow):
            po_segregation.enforce_requester_separation(_po("MR-001"))

    def test_uses_session_user_not_doc_owner(self):
        # drafted by someone else, submitted by the requester -> still blocked
        _set(user="parker@example.com", mr_owners={"MR-001": "parker@example.com"})
        with self.assertRaises(StubThrow):
            po_segregation.enforce_requester_separation(
                _po("MR-001", owner="clegg@example.com")
            )

    def test_doc_owner_conflict_alone_does_not_block(self):
        # the PO's own creator is irrelevant; only the submitter's MRs matter
        _set(user="clegg@example.com", mr_owners={"MR-001": "parker@example.com"})
        po_segregation.enforce_requester_separation(
            _po("MR-001", owner="parker@example.com")
        )  # no raise

    def test_message_names_both_remedies(self):
        _set(user="parker@example.com", mr_owners={"MR-001": "parker@example.com"})
        with self.assertRaises(StubThrow) as ctx:
            po_segregation.enforce_requester_separation(_po("MR-001"))
        message = str(ctx.exception)
        self.assertIn("PO Creator", message)
        self.assertIn("PO Approver", message)
        self.assertIn("MR-001", message)

    def test_kill_switch_off_skips_the_check(self):
        _set(user="parker@example.com", mr_owners={"MR-001": "parker@example.com"}, enabled=0)
        po_segregation.enforce_requester_separation(_po("MR-001"))  # no raise


if __name__ == "__main__":
    unittest.main()
