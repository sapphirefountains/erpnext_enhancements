"""Bench-free guards for the Opportunity won-date backfill.

The patch's whole reason for existing is a distinction that is easy to lose on a
later edit: an audit-trail status transition is evidence of a win *only* when it
looks like one deal closing. On this instance 197 of the 220 candidate
transitions happened on a single day (2025-08-20) during the post-migration
cleanup, so a backfill that trusts the audit trail uncritically rebuilds exactly
the fake revenue spike the sales dashboard was fixed to stop showing.

These tests pin that behaviour: bulk days are excluded, organic days are
trusted, and a deal with nothing but bulk transitions keeps a blank date rather
than being handed a plausible-looking wrong one.

Plain pytest against a frappe stub — no bench, no site. Runs in CI.
"""

import sys
import types


def _install_frappe_stub():
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_ee_wondate_stub", False):
		return

	frappe = types.ModuleType("frappe")
	frappe._ee_wondate_stub = True

	# Populated per-test: {opportunity_name: [(creation, data_json), ...]} and the
	# list of undated Closed-Won opportunities.
	frappe._versions = {}
	frappe._blanks = []
	frappe._written = {}
	frappe._logs = []

	def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
		if doctype == "Opportunity":
			return list(frappe._blanks)
		if doctype == "Version":
			rows = frappe._versions.get((filters or {}).get("docname"), [])
			out = [{"creation": c, "data": d} for c, d in rows]
			# The patch asks for newest-first and relies on that ordering.
			out.sort(key=lambda r: r["creation"], reverse=True)
			return [_Dict(r) for r in out]
		return []

	class _Dict(dict):
		def __getattr__(self, key):
			return self.get(key)

		def __setattr__(self, key, value):
			self[key] = value

	frappe._dict = _Dict
	frappe.get_all = get_all
	frappe.log_error = lambda *a, **k: frappe._logs.append(a)
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		set_value=lambda dt, name, field, value, **k: frappe._written.__setitem__(name, value),
		commit=lambda: None,
	)
	frappe.logger = lambda: types.SimpleNamespace(info=lambda msg: frappe._logs.append(msg))

	sys.modules["frappe"] = frappe


_install_frappe_stub()

import frappe  # noqa: E402  (the stub above must be installed first)

from erpnext_enhancements.patches import backfill_opportunity_won_date as patch  # noqa: E402


def _status_change(old, new="Closed Won"):
	return '{"changed": [["status", "%s", "%s"]], "added": [], "removed": []}' % (old, new)


def _reset(blanks, versions):
	frappe._blanks = blanks
	frappe._versions = versions
	frappe._written = {}
	frappe._logs = []


def test_an_organic_transition_is_stamped():
	_reset(
		["OPP-1"],
		{"OPP-1": [("2026-04-24 09:04:42", _status_change("Negotiation/Review"))]},
	)
	patch.execute()
	assert frappe._written == {"OPP-1": "2026-04-24"}


def test_a_bulk_edit_day_is_not_treated_as_a_win_date():
	"""The failure this patch exists to avoid: 197 deals flipped on one day are a
	cleanup, and stamping them rebuilds the spike the dashboard removed."""
	bulk = {
		f"OPP-{i}": [("2025-08-20 11:00:00", _status_change("Qualification"))]
		for i in range(patch.BULK_EDIT_THRESHOLD + 5)
	}
	_reset(list(bulk), bulk)
	patch.execute()
	assert frappe._written == {}


def test_organic_days_survive_alongside_a_bulk_day():
	"""The threshold must not be so blunt that it discards the real dates too."""
	rows = {
		f"BULK-{i}": [("2025-08-20 11:00:00", _status_change("Qualification"))]
		for i in range(patch.BULK_EDIT_THRESHOLD + 5)
	}
	rows["REAL-1"] = [("2026-03-10 13:54:10", _status_change("Proposal/Price Quote"))]
	rows["REAL-2"] = [("2026-05-14 13:55:11", _status_change("Negotiation/Review"))]
	_reset(list(rows), rows)
	patch.execute()
	assert frappe._written == {"REAL-1": "2026-03-10", "REAL-2": "2026-05-14"}


def test_a_deal_rewon_after_a_bulk_edit_uses_the_organic_transition():
	"""Newest-first, skipping bulk days — a deal caught in the cleanup and then
	genuinely re-won later should carry the later, real date."""
	rows = {
		f"BULK-{i}": [("2025-08-20 11:00:00", _status_change("Qualification"))]
		for i in range(patch.BULK_EDIT_THRESHOLD + 5)
	}
	rows["REWON"] = [
		("2025-08-20 11:00:00", _status_change("Qualification")),
		("2026-06-02 08:00:00", _status_change("Negotiation/Review")),
	]
	_reset(list(rows), rows)
	patch.execute()
	assert frappe._written["REWON"] == "2026-06-02"


def test_a_deal_with_no_recorded_transition_stays_blank():
	_reset(["OPP-NONE"], {"OPP-NONE": []})
	patch.execute()
	assert frappe._written == {}


def test_non_status_changes_are_ignored():
	"""Version rows record every field change; only status matters here."""
	_reset(
		["OPP-1"],
		{"OPP-1": [("2026-04-24 09:04:42", '{"changed": [["opportunity_amount", 0, 5000]]}')]},
	)
	patch.execute()
	assert frappe._written == {}


def test_a_transition_to_some_other_status_is_ignored():
	_reset(
		["OPP-1"],
		{"OPP-1": [("2026-04-24 09:04:42", '{"changed": [["status", "Open", "Lost"]]}')]},
	)
	patch.execute()
	assert frappe._written == {}


def test_malformed_audit_data_does_not_abort_the_document():
	"""A bad row must not cost the document its real date on a later version."""
	_reset(
		["OPP-1"],
		{
			"OPP-1": [
				("2026-04-24 09:04:42", "{not json"),
				("2026-04-20 09:00:00", _status_change("Qualification")),
			]
		},
	)
	patch.execute()
	assert frappe._written == {"OPP-1": "2026-04-20"}


def test_missing_column_logs_loudly_instead_of_returning_silently():
	_reset(["OPP-1"], {"OPP-1": [("2026-04-24 09:04:42", _status_change("Open"))]})
	original = frappe.db.has_column
	frappe.db.has_column = lambda *a, **k: False
	try:
		patch.execute()
	finally:
		frappe.db.has_column = original
	assert frappe._written == {}
	assert frappe._logs, "a missing column must be reported, not silently skipped"
