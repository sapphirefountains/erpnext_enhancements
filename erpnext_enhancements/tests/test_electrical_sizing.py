"""Bench-free pytest suite for the electrical package's sizing math (phase L).

Hand goldens from the NEC tables (2023 edition, ``engine/data/nec_tables.py``)
and worked examples. Plain pytest functions, so CI runs this file with
``python -m pytest`` (``python -m unittest`` would collect nothing here).

Run: python -m pytest erpnext_enhancements/tests/test_electrical_sizing.py -q
"""

import itertools
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.data import nec_tables as T
from erpnext_enhancements.water_engineering.engine.electrical import (
    ampacity,
    conduit_fill,
    continuous_ocpd,
    egc_size,
    motor_branch_ocpd,
    motor_flc,
    size_conductor,
    total_drop,
    voltage_drop,
    wire_for_load,
)
from erpnext_enhancements.water_engineering.engine.electrical.conductors import (
    count_adjustment,
    temperature_correction,
)
from erpnext_enhancements.water_engineering.engine.electrical.voltage_drop import (
    size_for_drop,
)
from erpnext_enhancements.water_engineering.engine.pump import electrical_load

# --- the worked example: a 5 HP, 230 V, single-phase pump ---------------------


def test_5hp_230v_flc_is_28a():
    assert motor_flc(5, 1, 230) == 28.0  # Table 430.248
    assert motor_flc(5, 1, 240) == 28.0  # 240 V system uses the 230 V column


def test_5hp_inverse_time_breaker_max_is_70a():
    r = motor_branch_ocpd(28, device="inverse_time_breaker", phase=1)
    assert r.value == 70  # 250% x 28 = 70.0, a standard size
    assert r.inputs["percent_of_flc"]["value"] == 250
    assert r.inputs["conductor_min_a"]["value"] == 35.0  # 430.22
    assert r.inputs["exception_2_max_a"]["value"] == 110  # 400% = 112 -> rounds DOWN to 110
    assert any("430.52" in c for c in r.citations)


@pytest.mark.parametrize(
    "device,expected",
    [
        ("dual_element_fuse", 50),  # 175% = 49.0 -> next standard 50
        ("nontime_delay_fuse", 90),  # 300% = 84.0 -> 90
        ("instantaneous_trip", 225),  # 800% = 224.0 -> 225
    ],
)
def test_5hp_other_devices(device, expected):
    assert motor_branch_ocpd(28, device=device, phase=1).value == expected


def test_next_size_up_rule():
    # 10 A x 250% = 25 (standard) ; 11 A x 250% = 27.5 -> next standard 30 (Exception 1)
    assert motor_branch_ocpd(10).value == 25
    assert motor_branch_ocpd(11).value == 30
    # never below a 15 A breaker; fuses go as small as their own ratings
    assert motor_branch_ocpd(1.7).value == 15
    assert motor_branch_ocpd(1.7, device="dual_element_fuse").value == 3  # 175% = 2.975 -> 3 A fuse


def test_design_b_energy_efficient_instantaneous_is_1100pct():
    r = motor_branch_ocpd(10, device="instantaneous_trip", phase=3, motor_type="design_b_energy_efficient")
    assert r.value == 110  # 1100% x 10
    assert r.inputs["exception_2_max_a"]["value"] == 150  # 1700% = 170 -> rounds down to 150


def test_5hp_conductor_is_8_awg_at_60c_terminations():
    # 125% x 28 = 35 A. 110.14(C)(1)(a): a 100 A-or-less circuit uses the 60 C column,
    # where #10 carries 30 A and #8 40 A -> #8.
    r = size_conductor(28, is_motor=True, ocpd_a=70)
    assert r.value == "8"
    assert r.inputs["required_table_a"]["value"] == 35.0


def test_5hp_conductor_is_10_awg_with_75c_terminations():
    # Equipment listed for 75 C terminations: #10 = 35 A at 75 C (and 40 A x 1.0 at 90 C).
    assert size_conductor(28, is_motor=True, ocpd_a=70, termination_c=75).value == "10"


def test_5hp_egc_for_70a_is_8_awg():
    assert egc_size(70).value == "8"  # Table 250.122: 61-100 A -> 8 Cu


def test_pump_electrical_load_uses_430_52():
    r = electrical_load(28, hp=5, phase=1, voltage=230)
    assert r.value == 70
    assert r.inputs["conductor_min_a"]["value"] == 35.0
    assert r.calc == "electrical_load" and r.unit == "A breaker"


def test_pump_electrical_load_non_motor_is_125pct():
    # Fika 11 kW heater at 240 V: 45.83 A x 1.25 = 57.3 -> 60 A
    assert electrical_load(11000 / 240, is_motor=False).value == 60


# --- conductors ----------------------------------------------------------------


def test_ampacity_spot_values():
    assert ampacity("12", "Cu", 60) == 20
    assert ampacity("10", "Cu", 75) == 35
    assert ampacity("4/0", "Cu", 75) == 230
    assert ampacity("1/0", "Al", 75) == 120
    assert ampacity("14", "Al", 75) is None  # no 14 AWG aluminum in the table


def test_table_columns_increase_with_size_and_temperature():
    for mat in ("Cu", "Al"):
        rows = [T.AMPACITY_310_16[mat][s] for s in T.SIZES if s in T.AMPACITY_310_16[mat]]
        for prev, cur in itertools.pairwise(rows):
            assert all(c > p for p, c in zip(prev, cur, strict=True))
        for row in rows:
            assert row[0] < row[1] < row[2]


def test_temperature_and_count_corrections():
    assert temperature_correction(86, 90) == 1.0  # 30 C
    assert temperature_correction(95, 90) == 0.96  # 35 C
    assert temperature_correction(104, 75) == 0.88  # 40 C
    assert temperature_correction(140, 60) is None  # 60 C: TW cannot be used
    assert count_adjustment(3) == 1.0
    assert count_adjustment(4) == 0.8
    assert count_adjustment(9) == 0.7
    assert count_adjustment(12) == 0.5


def test_continuous_load_uses_125pct_before_correction_only():
    # 30 A continuous: 37.5 A before correction -> OCPD 40 ; #8 (40 A at 60 C) carries it
    oc = continuous_ocpd(30)
    assert oc.value == 40
    assert size_conductor(30, continuous=True, ocpd_a=oc.value).value == "8"
    # Hot attic, 9 conductors: the corrected check uses the plain 30 A, not 37.5 A, and
    # #8 passes it (55 A x 0.82 (122 F = 50 C) x 0.7 = 31.6 A >= 30). But 31.6 A may be
    # protected at 35 A at most (240.4(B)), not 40 A -> #6 (75 x 0.82 x 0.7 = 43.1 A).
    hot = size_conductor(30, continuous=True, ocpd_a=40, ambient_f=122, current_carrying=9)
    assert hot.value == "6"
    assert hot.inputs["required_corrected_a"]["value"] == 30.0
    assert size_conductor(30, continuous=True, ocpd_a=35, ambient_f=122, current_carrying=9).value == "8"


def test_small_conductor_rule_240_4_d():
    # 21 A noncontinuous -> 25 A OCPD ; #12 is capped at 20 A by 240.4(D) -> #10
    assert continuous_ocpd(21, continuous=False).value == 25
    assert size_conductor(21, continuous=False, ocpd_a=25).value == "10"
    # Motor circuits are exempt (240.4(G)): 1 HP 230 V, FLC 8 A -> #14 on a 20 A breaker
    assert size_conductor(8, is_motor=True, ocpd_a=20).value == "14"


def test_egc_table_and_proportional_upsizing():
    for ocpd, size in [(15, "14"), (20, "12"), (30, "10"), (60, "10"), (100, "8"), (200, "6"), (400, "3")]:
        assert egc_size(ocpd).value == size
    assert egc_size(100, "Al").value == "6"
    # 250.122(B): ungrounded upsized #12 -> #10 for drop; EGC 12 x (10380/6530) -> #10
    assert egc_size(20, ungrounded_min="12", ungrounded_actual="10").value == "10"
    # pool circuits: insulated copper EGC no smaller than 12 AWG
    assert egc_size(15, pool=True).value == "12"


# --- voltage drop ---------------------------------------------------------------


def test_voltage_drop_single_phase_150ft():
    # 2 x 16 A x 1.24 ohm/kft x 150 ft / 1000 = 5.952 V -> 2.48% of 240 V
    r = voltage_drop(16, 150, "10", 240, 1)
    assert r.value == pytest.approx(2.48, abs=1e-9)
    assert r.status == "good"
    # #12: 2 x 16 x 1.98 x 150 / 1000 = 9.504 V -> 3.96%, over the 3% recommendation
    r12 = voltage_drop(16, 150, "12", 240, 1)
    assert r12.value == pytest.approx(3.96, abs=1e-9)
    assert r12.status == "warn"


def test_voltage_drop_three_phase_150ft():
    # sqrt(3) x 30 A x 0.778 x 150 / 1000 = 6.0638 V -> 2.915% of 208 V
    r = voltage_drop(30, 150, "8", 208, 3)
    assert r.value == pytest.approx(100 * math.sqrt(3) * 30 * 0.778 * 150 / 1000 / 208, abs=1e-3)
    assert r.value < 3.0
    assert size_for_drop(30, 150, 208, 3) == "8"


def test_wire_for_load_upsizes_for_a_3pct_drop_on_150ft():
    # 16 A continuous at 240 V: #12 by ampacity (20 A at 60 C), 3.96% over 150 ft -> #10 at 2.48%
    r = wire_for_load(16, voltage=240, phase=1, length_ft=150, continuous=True, ocpd_a=20)
    assert r.inputs["ampacity_size"]["value"] == "12"
    assert r.value == "10"
    assert r.inputs["drop_pct"]["value"] == pytest.approx(2.48, abs=1e-9)
    assert r.inputs["egc"]["value"] == "10"  # grown with the ungrounded conductors (250.122(B))


def test_total_drop_5pct():
    assert total_drop(2.0, 2.9).status == "good"
    r = total_drop(2.5, 2.9)
    assert r.status == "warn" and r.value == pytest.approx(5.4)


# --- conduit ------------------------------------------------------------------


def test_conduit_fill_sizes():
    # 4 x #10 THHN = 0.0844 in^2 <= 40% of 1/2" PVC-40 (0.114)
    assert conduit_fill([{"size": "10", "qty": 4}], "PVC-40").value == '1/2"'
    # 3 x #4 + #8 = 0.2838 in^2 : 3/4" PVC-40 holds 0.203, 1" holds 0.333
    rows = [{"size": "4", "qty": 3}, {"size": "8", "qty": 1}]
    assert conduit_fill(rows, "PVC-40").value == '1"'
    assert conduit_fill(rows, "EMT").value == '1"'
    # Schedule 80 has less room: 1" holds 0.275 -> 1-1/4"
    assert conduit_fill(rows, "PVC-80").value == '1-1/4"'


def test_conduit_fill_percent_by_count():
    r = conduit_fill([{"size": "4/0", "qty": 1}], "PVC-40", trade_size='3/4"')
    assert r.inputs["fill_allowed_pct"]["value"] == 53
    assert conduit_fill([{"size": "12", "qty": 2}], "EMT").inputs["fill_allowed_pct"]["value"] == 31


def test_conduit_tables_grow_with_trade_size():
    for table in T.CONDUIT_AREA_IN2.values():
        vals = list(table.values())
        assert vals == sorted(vals)
