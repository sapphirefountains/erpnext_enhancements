"""Port parity with the Digital Rain Curtain's electrical planner (phase L).

Where ``engine/electrical`` implements the same function as the rain curtain's
``src/electrical.js`` -- motor FLC, standard breakers, wire for a breaker, voltage
drop, the valve-coil plan (``coilPlan`` -> ``low_voltage_run``), the generator
rule and battery runtime -- these cases must agree. The expected values were
produced by running the rain curtain's own code under Node (DRC commit
737fa80f49067d2adcf25c5094c0f9efe6f78fbd, Node 24.22.1) on its demo project and the
lobby / atrium kits used by ``tests/hydro/electrical.test.mjs``, and are pinned
here as literals so this suite never needs the DRC checkout.

Tolerances: integers, sizes and flags match exactly; voltage drop matches to
2e-3 relative, because the DRC carries Table 8 resistance pre-converted to ohm/km
at three significant figures while this port reads ohm/kft from the table.

Run: python -m pytest erpnext_enhancements/tests/test_electrical_drc_parity.py -q
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.electrical import (
    ampacity,
    battery_runtime,
    generator_size,
    low_voltage_run,
    motor_flc,
    voltage_drop,
)
from erpnext_enhancements.water_engineering.engine.electrical._util import next_standard
from erpnext_enhancements.water_engineering.engine.electrical.loads import standard_motor_hp

M_PER_FT = 0.3048
DROP_REL = 2e-3

# coilPlan(P) for: demo; demo feedM 20; demo center; lobby; lobby center; lobby branchA 10; atrium.
COIL_CASES = [
    # (N, coilW, ledMa, V, pitchMm, feedM, supplyAt, branchA, busDropPct) -> (valveW, fuseA, per, B, wire, dropPct, wireM, class2)
    ((128, 1.5, 5, 24, 25.4, 1.8288, "end", 0, 5), (1.62, 4, 40, 4, "18", 2.5655778, 16.6624, True)),
    ((128, 1.5, 5, 24, 25.4, 20, "end", 0, 5), (1.62, 4, 40, 4, "12", 3.296475, 89.3472, True)),
    ((128, 1.5, 5, 24, 25.4, 1.8288, "center", 0, 5), (1.62, 4, 40, 4, "18", 1.7302734, 12.5984, True)),
    ((96, 4, 5, 24, 20, 1.8288, "end", 0, 5), (4.12, 4, 16, 6, "18", 2.14394912, 17.6928, True)),
    ((96, 4, 5, 24, 20, 1.8288, "center", 0, 5), (4.12, 4, 16, 6, "18", 1.57044512, 14.8128, True)),
    ((96, 4, 5, 24, 20, 1.8288, "end", 10, 5), (4.12, 10, 40, 3, "14", 1.78514107, 9.8064, False)),
    ((320, 4, 5, 24, 25.4, 1.8288, "end", 0, 5), (4.12, 4, 16, 20, "16", 3.66128469, 121.92, True)),
]


@pytest.mark.parametrize("case,expected", COIL_CASES)
def test_coil_plan_parity(case, expected):
    n, coil_w, led_ma, v, pitch_mm, feed_m, supply_at, branch_a, limit = case
    valve_w, fuse_a, per, b, wire, drop, wire_m, class2 = expected
    each = coil_w + v * led_ma / 1000.0  # coil + indicator LED, as coilPlan does
    assert each == pytest.approx(valve_w, rel=1e-12)
    r = low_voltage_run(
        n, each, pitch_mm / 1000.0 / M_PER_FT, voltage=v, feed_ft=feed_m / M_PER_FT, supply_at=supply_at,
        branch_fuse_a=branch_a, limit_pct=limit, group=8, kind="valve",
    ).value
    assert r["fuse_a"] == fuse_a
    assert r["per_branch"] == per
    assert r["branch_count"] == b
    assert r["wire"] == wire
    assert r["class2"] is class2
    assert r["drop_pct"] == pytest.approx(drop, rel=DROP_REL)
    assert r["wire_ft"] == pytest.approx(wire_m / M_PER_FT, abs=0.005)  # reported to 0.01 ft


# motorFLC(hp, V): rounds a pump rating up to the next table HP; 120 V -> 115 column, 208 V column.
@pytest.mark.parametrize(
    "hp,volts,amps",
    [(1, 120, 16), (0.5, 240, 4.9), (0.5, 208, 5.4), (2, 120, 24), (250 / 746, 120, 7.2), (5, 240, 28), (3, 208, 18.7)],
)
def test_motor_flc_parity(hp, volts, amps):
    assert motor_flc(standard_motor_hp(hp, 1), 1, volts) == amps


def test_quarter_and_third_hp_rounding():
    assert standard_motor_hp(250 / 746, 1) == pytest.approx(1 / 3)  # a 250 W pump is a 1/3 HP motor
    assert standard_motor_hp(500 / 746, 1) == 0.75  # 2/3 HP is not a table size: the next one up


@pytest.mark.parametrize("amps,breaker", [(12.5, 15), (20, 20), (20.1, 25), (35, 35), (99, 100)])
def test_breaker_for_parity(amps, breaker):
    assert next_standard(amps) == breaker


@pytest.mark.parametrize("breaker,awg", [(15, "14"), (20, "12"), (30, "10"), (40, "8")])
def test_wire_for_breaker_parity(breaker, awg):
    # wireForBreaker: smallest building wire whose 60 C ampacity carries the breaker
    sizes = ["14", "12", "10", "8", "6", "4", "2"]
    assert next(s for s in sizes if ampacity(s, "Cu", 60) >= breaker) == awg


@pytest.mark.parametrize(
    "awg,amps,len_m,volts,pct",
    [("12", 10, 15, 120, 1.625), ("10", 20, 45.72, 240, 3.10134), ("14", 8, 30, 120, 4.12)],
)
def test_drop_pct_parity(awg, amps, len_m, volts, pct):
    assert voltage_drop(amps, len_m / M_PER_FT, awg, volts, 1).value == pytest.approx(pct, rel=DROP_REL)


def test_backup_plan_parity():
    # backupPlan on the demo project with a 200 W pump: run 568.78 W, start 968.78 W -> 1000 W generator;
    # 2000 Wh battery at 453.58 W average -> 3.748 h.
    assert generator_size(568.7765333333333, 968.7765333333333).value == 1000
    assert battery_runtime(2000, 453.57653333333326).value == pytest.approx(3.7479893139680365, rel=1e-3)


def test_generator_rule_is_smallest_passing():
    # 80% continuous and 1.25 x surge: 5000 W running, 9000 W starting -> 8000 W
    assert generator_size(5000, 9000).value == 8000
    assert generator_size(5000, 5000).value == 6500
