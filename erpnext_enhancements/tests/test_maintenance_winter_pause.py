# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The winter pause on Sapphire Maintenance Contracts (``pause_over_winter``).

Before it, the regular cadence had no off-season. Every submitted visit rolled the
feature forward one interval. So a drained fountain kept drafting November-March
visits, and Highlands, which is weekly, kept drafting a visit every week after its
last September/October one. The rule is in ``api.maintenance_scheduling``:

* A rolled-forward next visit that lands between the 1st of the winterization month
  and the startup month (the window wraps the year end) moves to the 1st of the
  startup month.
* Finishing the Winterization visit moves every feature on the contract to that date.

Bench-free: it installs its own ``frappe`` stub and runs the real module.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_winter_pause
"""

import calendar
import datetime
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
CONTRACT_DIR = APP / "sapphire_maintenance/doctype/sapphire_maintenance_contract"

D = datetime.date
_saved_modules = {}
sched = None


class _Doc(dict):
    """Enough of a frappe Document: dict access plus attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _getdate(value=None):
    if value is None:
        return D(2026, 10, 5)
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, D):
        return value
    return D.fromisoformat(str(value)[:10])


def _add_days(value, days):
    return _getdate(value) + datetime.timedelta(days=days)


def _add_months(value, months):
    d = _getdate(value)
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return D(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def setUpModule():
    global sched
    for name in ("frappe", "frappe.utils", "erpnext_enhancements.api.maintenance_scheduling"):
        _saved_modules[name] = sys.modules.pop(name, None)

    frappe = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.getdate = _getdate
    utils.add_days = _add_days
    utils.add_months = _add_months
    utils.nowdate = lambda: "2026-10-05"
    frappe.utils = utils
    frappe.writes = []
    frappe.docs = {}
    frappe.db = types.SimpleNamespace(
        set_value=lambda dt, name, field, value=None: frappe.writes.append((dt, name, field, value)),
        get_value=lambda *a, **k: None,
        sql=lambda *a, **k: [],
    )
    frappe.get_doc = lambda doctype, name: frappe.docs[(doctype, name)]
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils

    sched = importlib.import_module("erpnext_enhancements.api.maintenance_scheduling")


def tearDownModule():
    for name, module in _saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _contract(pause=1, stop="October", resume="April", frequency="Weekly", features=1):
    rows = [
        _Doc(name=f"row{i}", serial_no=f"SN-{i}", frequency=frequency, next_visit_date="2026-10-06")
        for i in range(features)
    ]
    contract = _Doc(
        name="MNT-CON-1",
        pause_over_winter=pause,
        winterization_month=stop,
        startup_month=resume,
        covered_features=rows,
    )
    sys.modules["frappe"].docs[("Sapphire Maintenance Contract", "MNT-CON-1")] = contract
    return contract


def _record(visit_date, label=None, serial="SN-0"):
    return _Doc(
        name="MNT-REC-1",
        visit_label=label,
        visit_date=visit_date,
        maintenance_contract="MNT-CON-1",
        project=None,
        serial_no=serial,
        maintenance_results=[],
        chemistry_readings=[],
        cleaning_tasks=[],
        consumables=[],
    )


class TestDeferForWinter(unittest.TestCase):
    def test_dates_in_the_window_move_to_the_first_of_the_startup_month(self):
        contract = _contract()
        for day in ("2026-10-01", "2026-10-13", "2026-11-19", "2026-12-31", "2027-01-15", "2027-03-31"):
            self.assertEqual(sched.defer_for_winter(contract, day), D(2027, 4, 1), day)

    def test_dates_outside_the_window_are_left_alone(self):
        contract = _contract()
        for day in ("2026-09-30", "2027-04-01", "2027-06-15"):
            self.assertEqual(sched.defer_for_winter(contract, day), day, day)

    def test_no_pause_without_the_checkbox(self):
        contract = _contract(pause=0)
        self.assertEqual(sched.defer_for_winter(contract, "2026-11-19"), "2026-11-19")

    def test_a_window_that_does_not_wrap_the_year(self):
        # Unusual, but the months are free text on the form: Jan-Mar off, back in March.
        contract = _contract(stop="January", resume="March")
        self.assertEqual(sched.defer_for_winter(contract, "2027-02-10"), D(2027, 3, 1))
        self.assertEqual(sched.defer_for_winter(contract, "2026-12-10"), "2026-12-10")

    def test_blank_or_equal_months_never_pause(self):
        self.assertEqual(sched.defer_for_winter(_contract(stop=""), "2026-11-19"), "2026-11-19")
        self.assertEqual(sched.defer_for_winter(_contract(stop="April"), "2026-11-19"), "2026-11-19")


class TestRollForward(unittest.TestCase):
    def setUp(self):
        sys.modules["frappe"].writes.clear()

    def _next_dates(self):
        """next_visit_date per feature row, from either set_value call shape."""
        dates = {}
        for _, name, field, value in sys.modules["frappe"].writes:
            if isinstance(field, dict):
                if "next_visit_date" in field:
                    dates[name] = field["next_visit_date"]
            elif field == "next_visit_date":
                dates[name] = value
        return dates

    def test_weekly_highlands_stops_after_its_last_visit(self):
        _contract(frequency="Weekly")
        sched.update_next_visit_dates(_record("2026-10-06"), None)
        self.assertEqual(self._next_dates(), {"row0": D(2027, 4, 1)})

    def test_monthly_visit_before_winter_rolls_normally(self):
        _contract(frequency="Monthly")
        sched.update_next_visit_dates(_record("2026-08-19"), None)
        self.assertEqual(self._next_dates(), {"row0": D(2026, 9, 19)})

    def test_a_contract_that_runs_all_winter_is_unchanged(self):
        _contract(pause=0, frequency="Monthly")
        sched.update_next_visit_dates(_record("2026-10-19"), None)
        self.assertEqual(self._next_dates(), {"row0": D(2026, 11, 19)})

    def test_finishing_winterization_parks_every_feature(self):
        _contract(frequency="Monthly", features=3)
        sched.update_next_visit_dates(_record("2026-10-21", label="Winterization", serial=None), None)
        self.assertEqual(self._next_dates(), {f"row{i}": D(2027, 4, 1) for i in range(3)})

    def test_winterization_without_the_pause_changes_nothing(self):
        _contract(pause=0)
        sched.update_next_visit_dates(_record("2026-10-21", label="Winterization"), None)
        self.assertEqual(sys.modules["frappe"].writes, [])

    def test_other_labelled_visits_still_do_not_touch_the_cadence(self):
        _contract()
        for label in ("Seasonal Startup", "Extra Visit", "Chemistry Follow-Up"):
            sched.update_next_visit_dates(_record("2026-10-21", label=label), None)
        self.assertEqual(sys.modules["frappe"].writes, [])


class TestKeptInStep(unittest.TestCase):
    """The module copies two strings from the contract controller. They must agree."""

    def test_label_and_months_match_the_contract_controller(self):
        source = (CONTRACT_DIR / "sapphire_maintenance_contract.py").read_text(encoding="utf-8")
        label = re.search(r'^WINTERIZATION_LABEL = "([^"]+)"', source, re.M).group(1)
        self.assertEqual(sched.WINTERIZATION_LABEL, label)
        months_block = re.search(r"^MONTHS = \[(.*?)\]", source, re.M | re.S).group(1)
        self.assertEqual(sched.MONTHS, re.findall(r'"([A-Za-z]+)"', months_block))

    def test_the_field_exists_on_the_contract(self):
        import json

        meta = json.loads((CONTRACT_DIR / "sapphire_maintenance_contract.json").read_text(encoding="utf-8"))
        fields = {f["fieldname"]: f for f in meta["fields"]}
        self.assertEqual(fields["pause_over_winter"]["fieldtype"], "Check")
        self.assertIn("pause_over_winter", meta["field_order"])


if __name__ == "__main__":
    unittest.main()
