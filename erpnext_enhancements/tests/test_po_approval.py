"""Bench-free unit tests for the Purchase Order approval-threshold logic (WI-013).

Stubs a minimal ``frappe`` (no site/bench) so the pure decision logic in
``erpnext_enhancements.po_approval`` runs under plain unittest. The stub is
installed in ``setUpModule`` (execution time), not at import, so it never fools
the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_po_approval
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Mutable state the frappe stub reads at call time.
STATE = {"roles": [], "user": "pm@example.com", "threshold": 500.0}
po_approval = None


class StubThrow(Exception):
    pass


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    def throw(msg, title=None, exc=None):
        raise StubThrow(msg)

    frappe.throw = throw
    frappe.ValidationError = StubThrow
    frappe._ = lambda s: s
    frappe.session = types.SimpleNamespace(user=STATE["user"])
    frappe.get_roles = lambda user=None: list(STATE["roles"])

    class _Settings:
        def get(self, key):
            return STATE["threshold"] if key == "po_approval_threshold" else None

    frappe.get_cached_doc = lambda dt: _Settings()

    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, precision=None: float(v or 0)
    utils.fmt_money = lambda v, currency=None: f"{(currency or '').strip()} {float(v or 0):,.2f}".strip()
    # `stamp_approval` records when an order cleared the submit gates. Fixed rather than
    # `datetime.now()` so an assertion on it can be exact.
    utils.now_datetime = lambda: datetime.datetime(2026, 1, 1, 12, 0, 0)
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global po_approval
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.po_approval", None)
    from erpnext_enhancements import po_approval as mod

    po_approval = mod


def _set(user="pm@example.com", roles=None, threshold=500.0):
    STATE.update(roles=list(roles or []), user=user, threshold=threshold)
    sys.modules["frappe"].session.user = user


class _Doc(dict):
    """Minimal PO stand-in: attribute access (doc.grand_total) + .get('currency')."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        # A real Document round-trips: `doc.field = x` is readable as `doc.get("field")`.
        # Without this, a hook that WRITES to the doc appears to succeed while the test
        # sees nothing — which is how stamp_approval nearly shipped unfenced.
        self[key] = value


def _po(total, currency="USD", project=None):
    return _Doc(grand_total=total, currency=currency, project=project)


class TestThresholdResolution(unittest.TestCase):
    def test_global_threshold(self):
        _set(threshold=500)
        self.assertEqual(po_approval.get_effective_threshold(_po(0)), 500.0)

    def test_zero_disables(self):
        _set(threshold=0)
        self.assertEqual(po_approval.get_effective_threshold(_po(0)), 0.0)


class TestApprovalAuthority(unittest.TestCase):
    def test_po_approver_has_authority(self):
        _set(user="ceo@example.com", roles=["Purchase User", "PO Approver"])
        self.assertTrue(po_approval.has_approval_authority())

    def test_non_approver_lacks_authority(self):
        _set(user="pm@example.com", roles=["Purchase User"])
        self.assertFalse(po_approval.has_approval_authority())

    def test_administrator_bypasses(self):
        _set(user="Administrator", roles=[])
        self.assertTrue(po_approval.has_approval_authority())


class _Meta:
    """Stands in for doc.meta. `has_field` is what stamp_approval guards on."""

    def __init__(self, fields):
        self._fields = set(fields)

    def has_field(self, fieldname):
        return fieldname in self._fields


STAMP_FIELDS = ("custom_approved_by", "custom_approved_on")


class TestStampApproval(unittest.TestCase):
    """The supplier-facing print format reads these, so they have to be true.

    `modified_by` is whoever touched the document last, which after any post-submit edit
    is not the approver — printing that on a document that goes to a vendor would be
    confidently wrong. stamp_approval records the fact at the moment it is established.
    """

    def test_it_records_the_submitting_user_and_time(self):
        _set(user="ceo@example.com", roles=["PO Approver"])
        doc = _po(1000)
        doc["meta"] = _Meta(STAMP_FIELDS)

        po_approval.stamp_approval(doc)

        self.assertEqual(doc["custom_approved_by"], "ceo@example.com")
        self.assertEqual(doc["custom_approved_on"], datetime.datetime(2026, 1, 1, 12, 0, 0))

    def test_it_no_ops_when_the_fields_do_not_exist(self):
        """The fields are created by a patch, and this hook fires during ERPNext's own
        test bootstrap — before that patch has run on a fresh database."""
        _set(user="ceo@example.com", roles=["PO Approver"])
        doc = _po(1000)
        doc["meta"] = _Meta([])

        po_approval.stamp_approval(doc)  # must not raise

        self.assertNotIn("custom_approved_by", doc)
        self.assertNotIn("custom_approved_on", doc)


class TestEnforceThreshold(unittest.TestCase):
    def test_below_threshold_passes(self):
        _set(roles=["Purchase User"], threshold=500)
        po_approval.enforce_threshold(_po(400))  # no raise

    def test_at_threshold_passes(self):
        # boundary: only totals strictly above the threshold escalate
        _set(roles=["Purchase User"], threshold=500)
        po_approval.enforce_threshold(_po(500))  # no raise

    def test_above_threshold_blocks_non_approver(self):
        _set(roles=["Purchase User"], threshold=500)
        with self.assertRaises(StubThrow):
            po_approval.enforce_threshold(_po(600))

    def test_above_threshold_allows_approver(self):
        _set(user="ceo@example.com", roles=["Purchase User", "PO Approver"], threshold=500)
        po_approval.enforce_threshold(_po(600))  # no raise

    def test_disabled_threshold_allows_large_po(self):
        _set(roles=["Purchase User"], threshold=0)
        po_approval.enforce_threshold(_po(999999))  # no raise


if __name__ == "__main__":
    unittest.main()
