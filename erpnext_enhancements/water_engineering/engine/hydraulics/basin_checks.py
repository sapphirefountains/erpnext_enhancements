"""Basin checks: water in the pipes and in the air, drain-back, freeboard, intake submergence,
evaporation and spray loss, strainer mesh.

Port of Digital Rain Curtain ``src/hydro/checks.js`` ``basinChecks`` without the curtain
geometry; golden file ``tests/golden/drc/basin_checks.json`` (1e-12). DRC builds the basin plan
from the curtain (``(W + 0.5) x D``) and the in-flight water from the curtain's fall time; here
the caller supplies the plan area and, per feature, its flow and fall time (a weir's nappe, a
jet's time aloft), so the same checks serve every family (audit section 2). DRC's splash and
wind-drift catch width (``vh = 0.2 vImp 0.6``) is an unsourced heuristic (audit E11) and is
left out; phase C's families own setback and splash.

* **Pipe volume**: every supply segment (branches counted), the suction and the manifold.
* **Drain-back** when the pump stops: the supply and the manifold empty into the basin unless a
  check valve (swing or lift) on the supply or a foot valve on the suction holds them. Both
  figures are reported.
* **Level rise** = (drain-back + water in flight) / plan area, against the freeboard.
* **Intake submergence**, ANSI/HI 9.8: ``S = D (1 + 2.3 Fr)``, ``Fr = V / sqrt(g D)`` at the
  suction inlet; the water over the intake is the depth less one pipe diameter, as DRC.
* **Evaporation**, Carrier's form with wind: ``A (p_w - p_a) (0.089 + 0.0782 V) / h_fg`` kg/s
  (vapor pressures in kPa), plus **spray loss** at 0.5% of the flow.
* **Strainer mesh** from the smallest orifice: a quarter of the orifice, mesh number
  ``25400 / (2 x opening um)``.

The workbook figures stay beside them as cross-checks: ``drainage.surge_basin_volume``
(DOC-0049 B allowances) and ``treatment.evaporation_rate`` (ASHRAE).
"""

from __future__ import annotations

import math

from ..drainage import surge_basin_volume
from ..envelope import CalcResult, make_input
from ..treatment import evaporation_rate
from . import _js
from ._checks import BLOCKER, INFO, WARNING, make_check
from .fittings import resolve_fitting
from .segment import Segment
from .units import CIT_DRC, M_PER_FT, MM_PER_IN, G, f_to_c, gpm_to_m3s
from .water import Water, water_si

CIT_HI98 = "ANSI/HI 9.8-2018 Rotodynamic Pumps for Pump Intake Design: S = D (1 + 2.3 Fr)"
CIT_CARRIER = "Carrier (1918) evaporation from a free water surface, W = A (pw - pa)(0.089 + 0.0782 V) / Y (ASHRAE Applications ch. 51)"
CIT_SPRAY = "Spray and drift loss 0.5% of circulation (Sapphire standard 2026-11-01, DRC checks.js)"
CIT_MESH = "Strainer opening a quarter of the smallest orifice (DRC checks.js)"

SPRAY_FRACTION = 0.005
L_PER_GAL = 3.785411784
SQFT_PER_M2 = 1 / (M_PER_FT * M_PER_FT)
CHECK_KEYS = ("check", "lift_check")
FOOT_KEYS = ("foot_valve",)


def _volume_l(seg: Segment | None) -> float:
    if seg is None:
        return 0.0
    return seg.spec.area * (seg.length_m or 0) * seg.branches * 1000


def has_check_valve(supply, suction: Segment | None) -> bool:
    """A check valve on the supply, or a foot valve on the suction, holds the line full."""
    for seg in supply or ():
        for key, n in seg.fittings:
            if resolve_fitting(key) in CHECK_KEYS and (n or 0) > 0:
                return True
    if suction is not None:
        for key, n in suction.fittings:
            if resolve_fitting(key) in FOOT_KEYS and (n or 0) > 0:
                return True
    return False


def basin_checks_si(
    *,
    plan_area_m2: float,
    water_depth_m: float,
    freeboard_m: float,
    q_all: float,
    w: Water,
    supply=(),
    suction: Segment | None = None,
    manifold_vol_l: float = 0.0,
    in_flight=(),
    has_check: bool | None = None,
    orifice_mm: float | None = None,
    air_c: float = 22.0,
    rh_pct: float = 50.0,
    wind_ms: float = 0.0,
    hours_per_day: float = 8.0,
    spray_fraction: float = SPRAY_FRACTION,
) -> dict:
    """``basinChecks`` (SI). ``in_flight``: ``(flow m^3/s, fall time s)`` per feature. ``q_all``: the
    circulation (m^3/s) through the suction, for submergence, turnover and spray loss."""
    supply_vol = 0.0
    for seg in supply or ():
        supply_vol += _volume_l(seg)
    suction_vol = _volume_l(suction)
    in_flight_l = 0.0
    for q, t in in_flight or ():
        in_flight_l += (q or 0) * (t or 0) * 1000
    drainback_no_check = manifold_vol_l + supply_vol
    check = has_check_valve(supply, suction) if has_check is None else bool(has_check)
    drainback = 0.0 if check else drainback_no_check
    level_rise_mm = ((drainback + in_flight_l) / 1000 / plan_area_m2) * 1000 if plan_area_m2 > 0 else 0.0
    freeboard_ok = level_rise_mm / 1000 <= (freeboard_m or 0)

    d = suction.spec.id_m if suction is not None else 0.0
    area = suction.spec.area if suction is not None else 0.0
    vs = (q_all or 0) / area if d > 0 and area > 0 else 0.0
    fr = vs / math.sqrt(G * d) if d > 0 else 0.0
    submerge_req = d * (1 + 2.3 * fr)
    submerge_have = (water_depth_m or 0) - d
    submerge_ok = submerge_have >= submerge_req

    filter_um = mesh = None
    if orifice_mm:
        filter_um = (orifice_mm * 1000) / 4
        mesh = math.floor(25400 / (2 * filter_um) + 0.5)  # Math.round

    basin_l = plan_area_m2 * (water_depth_m or 0) * 1000
    turnover_min = basin_l / (q_all * 60000) if q_all and q_all > 0 else math.inf

    pw = w.pv / 1000
    pa = ((rh_pct if rh_pct is not None else 50) / 100) * water_si(air_c if air_c is not None else 22).pv / 1000
    evap_kg_s = (plan_area_m2 * _js.jmax(0.0, pw - pa) * (0.089 + 0.0782 * (wind_ms or 0))) / (w.latent / 1000)
    evap_lph = evap_kg_s * 3600
    spray_lph = spray_fraction * (q_all or 0) * 3.6e6
    makeup_lpd = (evap_lph + spray_lph) * (hours_per_day if hours_per_day is not None else 8)
    return {
        "supply_vol_l": supply_vol, "suction_vol_l": suction_vol, "manifold_vol_l": manifold_vol_l, "in_flight_l": in_flight_l,
        "drainback_l": drainback, "drainback_no_check_l": drainback_no_check, "has_check": check,
        "level_rise_mm": level_rise_mm, "freeboard_ok": freeboard_ok,
        "submerge_req_m": submerge_req, "submerge_have_m": submerge_have, "submerge_ok": submerge_ok, "froude": fr,
        "filter_um": filter_um, "mesh_no": mesh, "basin_l": basin_l, "turnover_min": turnover_min,
        "evap_lph": evap_lph, "spray_lph": spray_lph, "makeup_lpd": makeup_lpd,
    }


# ---------------------------------------------------------------------------
# Envelope (US)
# ---------------------------------------------------------------------------


def plan_area_sf(basin: dict) -> float:
    """A basin's plan area (sq ft) from the model-2 basin row: rectangular L x W, circular pi D^2/4, or
    ``area_sf`` for a freeform plan."""
    if basin.get("area_sf"):
        return float(basin["area_sf"])
    shape = (basin.get("shape") or "rectangular").lower()
    if shape.startswith(("cyl", "circ", "round")):
        dia = float(basin.get("diameter_in") or 0)
        return math.pi * dia * dia / 4 / 144
    return float(basin.get("length_in") or 0) * float(basin.get("width_in") or 0) / 144


def basin_checks(
    basin: dict,
    *,
    flow_gpm: float,
    supply=(),
    suction=None,
    manifold_gal: float = 0.0,
    in_flight=(),
    has_check: bool | None = None,
    orifice_in: float | None = None,
    water_temp_f: float = 68.0,
    air_temp_f: float = 72.0,
    rh_pct: float = 50.0,
    wind_mph: float = 0.0,
    hours_per_day: float = 8.0,
    pool_area_sf: float | None = None,
    spray_pct: float = SPRAY_FRACTION * 100,
) -> CalcResult:
    """The basin checks for a model-2 basin row (``depth_in`` is the water depth, ``freeboard_in`` the
    rim above it). ``in_flight``: ``[{flow_gpm, fall_s}]`` per feature the basin catches. ``spray_pct``:
    the spray-loss share of flow (house rule ``spray_loss_pct_<family>``; DRC's 0.5% by default)."""
    w = water_si(f_to_c(float(water_temp_f)))
    area_sf = plan_area_sf(basin)
    sup = [s if isinstance(s, Segment) else Segment.from_us(s) for s in supply or []]
    suc = (suction if isinstance(suction, Segment) else Segment.from_us(suction)) if suction else None
    depth_in = float(basin.get("depth_in") or 0)
    free_in = float(basin.get("freeboard_in") or 0)
    r = basin_checks_si(
        plan_area_m2=area_sf * M_PER_FT * M_PER_FT, water_depth_m=depth_in * MM_PER_IN / 1000, freeboard_m=free_in * MM_PER_IN / 1000,
        q_all=gpm_to_m3s(float(flow_gpm)), w=w, supply=sup, suction=suc, manifold_vol_l=float(manifold_gal or 0) * L_PER_GAL,
        in_flight=[(gpm_to_m3s(float(f.get("flow_gpm") or 0)), float(f.get("fall_s") or 0)) for f in in_flight or []],
        has_check=has_check, orifice_mm=float(orifice_in) * MM_PER_IN if orifice_in else None,
        air_c=f_to_c(float(air_temp_f)), rh_pct=float(rh_pct), wind_ms=float(wind_mph) * 0.44704, hours_per_day=float(hours_per_day),
        spray_fraction=float(spray_pct) / 100,
    )
    gal = 1 / L_PER_GAL
    rise_in = r["level_rise_mm"] / MM_PER_IN
    value = {
        "plan_area_sf": area_sf, "basin_gal": r["basin_l"] * gal, "supply_gal": r["supply_vol_l"] * gal,
        "suction_gal": r["suction_vol_l"] * gal, "manifold_gal": r["manifold_vol_l"] * gal, "in_flight_gal": r["in_flight_l"] * gal,
        "drainback_gal": r["drainback_l"] * gal, "drainback_no_check_gal": r["drainback_no_check_l"] * gal, "has_check": r["has_check"],
        "level_rise_in": rise_in, "freeboard_in": free_in, "freeboard_ok": r["freeboard_ok"],
        "submergence_required_in": r["submerge_req_m"] * 1000 / MM_PER_IN, "submergence_have_in": r["submerge_have_m"] * 1000 / MM_PER_IN,
        "submergence_ok": r["submerge_ok"], "froude": r["froude"], "strainer_opening_in": r["filter_um"] / 25400 if r["filter_um"] else None,
        "strainer_mesh": r["mesh_no"], "turnover_min": r["turnover_min"],
        "evaporation_gph": r["evap_lph"] * gal, "spray_loss_gph": r["spray_lph"] * gal, "makeup_gpd": r["makeup_lpd"] * gal,
    }
    checks = []
    bid = basin.get("id") or ""
    if suc is not None and not r["submerge_ok"]:
        checks.append(make_check("basin.submergence", BLOCKER, f"Basin {bid}: the intake can vortex",
                                 f"HI 9.8 wants {value['submergence_required_in']:.1f} in of water over the intake, it has "
                                 f"{value['submergence_have_in']:.1f} in: deepen the basin, upsize the suction, or add an anti-vortex plate.",
                                 "basin", ref=bid, value={"required_in": value["submergence_required_in"], "have_in": value["submergence_have_in"]}))
    if not r["freeboard_ok"]:
        checks.append(make_check("basin.freeboard", BLOCKER, f"Basin {bid} overflows when the pump stops",
                                 f"Drain-back and water in flight raise it {rise_in:.2f} in over {free_in:g} in of freeboard: "
                                 + ("add a check valve on the supply, " if not r["has_check"] else "") + "raise the rim or widen the basin.",
                                 "basin", ref=bid, value={"rise_in": rise_in, "freeboard_in": free_in}))
    if r["turnover_min"] < 1:
        checks.append(make_check("basin.turnover", WARNING, f"Basin {bid} turns over in {r['turnover_min'] * 60:.0f} s",
                                 "So little water for this flow that the level swings with every start: enlarge the basin or add a reservoir.",
                                 "basin", ref=bid))
    if r["mesh_no"]:
        checks.append(make_check("basin.strainer", INFO, f"Strainer: {r['mesh_no']} mesh or finer", "Keeps debris out of the smallest orifice.",
                                 "basin", ref=bid, value={"mesh": r["mesh_no"]}))
    # Cross-checks: the DOC-0049 surge-basin allowances and the ASHRAE evaporation figure.
    cross_surge = surge_basin_volume(float(pool_area_sf if pool_area_sf is not None else area_sf), area_sf)
    cross_evap = evaporation_rate(area_sf, float(water_temp_f), float(air_temp_f), float(rh_pct))
    value["crosscheck"] = {"surge_basin_gal": cross_surge.value, "evaporation_gpd_ashrae": cross_evap.value,
                           "evaporation_gpd_carrier": r["evap_lph"] * gal * 24}
    value["checks"] = checks
    return CalcResult(
        calc="basin_checks",
        value=value,
        unit="",
        inputs={
            "plan_area": make_input(area_sf, "sq ft", "prior_calc"),
            "depth": make_input(depth_in, "in", "user", "water depth"),
            "freeboard": make_input(free_in, "in", "user"),
            "flow": make_input(float(flow_gpm), "GPM", "prior_calc", "circulation"),
            "wind": make_input(float(wind_mph), "mph", "user"),
            "hours": make_input(float(hours_per_day), "h/day", "user"),
        },
        formula="rise = (drain-back + in-flight) / A ; S = D(1 + 2.3 V/sqrt(gD)) ; E = A (pw - pa)(0.089 + 0.0782 V)/h_fg ; spray = 0.5% Q",
        steps=[
            f"pipes: supply {value['supply_gal']:.1f} gal, suction {value['suction_gal']:.1f} gal, manifold {value['manifold_gal']:.1f} gal; in flight {value['in_flight_gal']:.1f} gal",
            f"drain-back {value['drainback_gal']:.1f} gal ({'held by a check valve' if r['has_check'] else 'no check valve'}) -> rise {rise_in:.2f} in vs freeboard {free_in:g} in",
            f"submergence: Fr {r['froude']:.2f} -> need {value['submergence_required_in']:.1f} in, have {value['submergence_have_in']:.1f} in",
            f"evaporation {value['evaporation_gph']:.2f} gph + spray {value['spray_loss_gph']:.2f} gph -> make-up {value['makeup_gpd']:.1f} gal/day",
            f"cross-checks: DOC-0049 surge basin {cross_surge.value:.0f} gal ; ASHRAE evaporation {cross_evap.value} gal/day",
        ],
        citations=[CIT_HI98, CIT_CARRIER, CIT_SPRAY, CIT_MESH, CIT_DRC],
        warnings=[c["title"] for c in checks if c["severity"] != INFO],
        status="Okay" if not any(c["severity"] == BLOCKER for c in checks) else "Blocked",
    )
