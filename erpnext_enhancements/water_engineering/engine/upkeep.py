"""Upkeep: the water plan, the maintenance schedule, and what a fountain costs to run each year.

* :func:`water_plan` ports Digital Rain Curtain ``src/upkeep.js`` ``waterPlan`` (golden file
  ``tests/golden/drc/upkeep.json``, 1e-12): minerals concentrate as water evaporates and leave
  only with the spray, ``C(t) = Css - (Css - 1) e^(-spray t / V)`` with ``Css = (evap + spray) /
  spray``, so the basin is drained and refilled before they reach ``max_cycles`` times the fill
  water, or every 30 days for hygiene (14 inside the Legionella growth band, 25 to 45 C / 77 to
  113 F, ASHRAE 188 and the CDC toolkit, which covers decorative fountains). The chlorine figure
  that governs is ``chemistry.chlorinator_feed`` (IBC 3133B.1 minimum feed); DRC's practical
  fill and daily doses are shown beside it.
* :func:`maintenance_schedule` is ``maintenanceSchedule`` without the rain curtain's valve-life
  and rigging rows, plus what other families need (filters, UV, lights, winterizing).
* :func:`om_per_year` is water, power, chemicals and parts at the site's rates. Power governs
  through ``workbook.electric_cost`` (the DOC-0049 formula) at each pump's operating point; the
  operating point's own electrical kW is the cross-check.

Rates and allowances with no public source (chemical price, parts allowance) come from the
``rules`` dict (Water Engineering Settings, PLAN.md 2.3) and say "Sapphire standard" where they
default.
"""

from __future__ import annotations

import math

from .chemistry import chlorinator_feed
from .constants import DAYS_PER_YEAR, DEFAULT_MOTOR_EFF, DEFAULT_PUMP_EFF
from .envelope import CalcResult, make_input
from .hydraulics import _js
from .hydraulics._checks import INFO, WARNING, make_check
from .hydraulics.units import f_to_c
from .workbook import electric_cost

CIT_CYCLES = "Cycles of concentration with the level held by make-up (mass balance; Cooling Technology Institute)"
CIT_LEGIONELLA = "ASHRAE Standard 188-2018 and CDC Legionella toolkit (decorative fountains): growth range 25-45 C"
CIT_DRC_UPKEEP = "Digital Rain Curtain src/upkeep.js (ported under golden vectors)"
CIT_RULES = "Sapphire standard 2026-11-01 (Water Engineering Settings house rules; confirm with the designers)"

TREATMENTS = {
    "chlorine": {"label": "Liquid chlorine (12.5%)", "kind": "chlorine", "pct": 12.5, "range": (1, 3)},
    "bleach": {"label": "Unscented bleach (8.25%)", "kind": "chlorine", "pct": 8.25, "range": (1, 3)},
    "bromine": {"label": "Bromine tablets in a feeder", "kind": "bromine", "pct": 0, "range": (3, 5)},
    "none": {"label": "No disinfectant", "kind": "none", "pct": 0, "range": (0, 0)},
}
HYGIENE_DAYS, WARM_DAYS = 30, 14
LEGIONELLA_C = (25.0, 45.0)
GAL_PER_CCF = 748.051948
L_PER_GAL = 3.785411784

RULE_DEFAULTS = {
    "kwh_rate": 0.1049,  # $/kWh, engine/rules.py utility default (Rocky Mountain Power commercial)
    "water_rate_per_ccf": 3.53,  # $/CCF, engine/rules.py utility default (SLC commercial FY26)
    "chlorine_per_gal": 6.50,  # $/gal of 12.5% sodium hypochlorite
    "parts_pct_of_equipment": 0.03,  # a year's wear parts as a share of the equipment cost
    "parts_per_year": 250.0,  # used when no equipment cost is known
    "days_per_year": DAYS_PER_YEAR,
}


def water_plan_si(*, vol_l: float, evap_lph: float, spray_lph: float, hours_per_day: float, water_c: float, outdoor: bool,
                  treatment: str = "chlorine", pct: float = 0.0, ppm: float = 0.0, loss_ppm: float = 0.0, max_cycles: float = 3.0) -> dict:
    """``waterPlan`` (liters, ppm, days)."""
    hours = _js.jmax(0.0, _js.or_(_js.num(hours_per_day), 0.0))
    t = TREATMENTS.get(treatment) or TREATMENTS["chlorine"]
    evap_lpd, spray_lpd = (evap_lph or 0) * hours, (spray_lph or 0) * hours
    makeup_lpd = evap_lpd + spray_lpd
    ppm = float(ppm) if ppm and ppm > 0 else (t["range"][0] + t["range"][1]) / 2
    loss_ppm = float(loss_ppm) if loss_ppm and loss_ppm > 0 else (3 if outdoor else 1)
    pct = (float(pct) if pct and pct > 0 else t["pct"]) if t["kind"] == "chlorine" else 0

    def ml(d_ppm: float, liters: float) -> float:
        return (d_ppm * liters) / (pct * 10) if pct > 0 else 0.0

    dose = {"fill_ml": ml(ppm, vol_l), "daily_ml": ml(loss_ppm, vol_l) + ml(ppm, makeup_lpd)} if t["kind"] == "chlorine" else None
    k = _js.jmax(1.2, _js.or_(_js.num(max_cycles), 3))
    mineral_days = math.inf
    if vol_l > 0 and evap_lpd > 0:
        if not (spray_lpd > 0):
            mineral_days = ((k - 1) * vol_l) / evap_lpd
        else:
            css = makeup_lpd / spray_lpd
            if css > k:
                mineral_days = (vol_l / spray_lpd) * math.log((css - 1) / (css - k))
    wc = _js.or_(_js.num(water_c), 20)
    warm = LEGIONELLA_C[0] <= wc <= LEGIONELLA_C[1]
    hygiene = WARM_DAYS if warm else HYGIENE_DAYS
    refresh = max(1, math.floor(min(mineral_days, hygiene)))
    return {
        "key": treatment if treatment in TREATMENTS else "chlorine", "kind": t["kind"], "vol_l": vol_l, "evap_lpd": evap_lpd,
        "spray_lpd": spray_lpd, "makeup_lpd": makeup_lpd, "outdoor": bool(outdoor), "ppm": ppm, "loss_ppm": loss_ppm, "pct": pct,
        "dose": dose, "max_cycles": k, "mineral_days": mineral_days, "hygiene_days": hygiene, "refresh_days": refresh,
        "refresh_by": "minerals" if mineral_days < hygiene else "hygiene", "water_c": wc, "warm": warm,
        "makeup_share": makeup_lpd / vol_l if vol_l > 0 else 0.0,
    }


def water_plan(volume_gal: float, *, evaporation_gph: float, spray_gph: float, hours_per_day: float = 8.0, water_temp_f: float = 68.0,
               outdoor: bool = True, treatment: str = "chlorine", product_pct: float = 0.0, residual_ppm: float = 0.0,
               loss_ppm: float = 0.0, max_cycles: float = 3.0) -> CalcResult:
    """Drain-and-refill interval, the Legionella band and the chlorine figures for a fountain's water.

    ``volume_gal``: everything the system holds (basin plus pipes); ``evaporation_gph`` and
    ``spray_gph`` from :func:`.hydraulics.basin_checks.basin_checks` while running."""
    vol_l = float(volume_gal) * L_PER_GAL
    r = water_plan_si(vol_l=vol_l, evap_lph=float(evaporation_gph) * L_PER_GAL, spray_lph=float(spray_gph) * L_PER_GAL,
                      hours_per_day=hours_per_day, water_c=f_to_c(float(water_temp_f)), outdoor=outdoor, treatment=treatment,
                      pct=product_pct, ppm=residual_ppm, loss_ppm=loss_ppm, max_cycles=max_cycles)
    feed = chlorinator_feed(float(volume_gal), r["pct"] or 12.5) if r["kind"] == "chlorine" else None
    checks = []
    if r["kind"] == "none":
        checks.append(make_check("upkeep.no_disinfectant", WARNING, "No disinfectant",
                                 "A fountain makes droplets people breathe: hold a chlorine or bromine residual at all times.", "treatment"))
    if r["warm"]:
        checks.append(make_check("upkeep.legionella_band", WARNING, f"The water runs at {float(water_temp_f):.0f} F, inside the Legionella band",
                                 f"77-113 F is where Legionella grows: keep the residual up, drain and clean every {r['hygiene_days']} days, "
                                 "cool the water if you can.", "treatment"))
    checks.append(make_check("upkeep.refresh", INFO, f"Drain, clean and refill every {r['refresh_days']} days",
                             "Before minerals left by evaporation reach "
                             f"{r['max_cycles']:g}x the fill water" if r["refresh_by"] == "minerals" else "For hygiene: scrub the basin and flush the lines.",
                             "treatment", value={"days": r["refresh_days"], "by": r["refresh_by"]}))
    gal = 1 / L_PER_GAL
    value = {
        "refresh_days": r["refresh_days"], "refresh_by": r["refresh_by"], "mineral_days": r["mineral_days"], "hygiene_days": r["hygiene_days"],
        "warm": r["warm"], "makeup_gpd": r["makeup_lpd"] * gal, "evaporation_gpd": r["evap_lpd"] * gal, "spray_gpd": r["spray_lpd"] * gal,
        "residual_ppm": r["ppm"], "treatment": r["key"],
        "chlorinator_feed_gph": feed.value if feed else None,
        "fill_dose_floz": r["dose"]["fill_ml"] / 29.5735295625 if r["dose"] else None,
        "daily_dose_floz": r["dose"]["daily_ml"] / 29.5735295625 if r["dose"] else None,
        "checks": checks,
    }
    steps = [
        f"make-up {value['makeup_gpd']:.1f} gal/day (evaporation {value['evaporation_gpd']:.1f} + spray {value['spray_gpd']:.1f})",
        f"minerals reach {r['max_cycles']:g}x in {r['mineral_days']:.0f} days ; hygiene every {r['hygiene_days']} days -> refresh every {r['refresh_days']} days",
    ]
    if feed:
        steps.append(f"governing chlorinator feed (IBC 3133B.1): {feed.value:.4f} gal/hr ; practical doses (DRC): fill "
                     f"{value['fill_dose_floz']:.1f} fl oz, then {value['daily_dose_floz']:.1f} fl oz/day")
    return CalcResult(
        calc="water_plan",
        value=value,
        unit="",
        inputs={
            "volume": make_input(float(volume_gal), "gal", "prior_calc"),
            "evaporation": make_input(float(evaporation_gph), "gph", "prior_calc"),
            "spray": make_input(float(spray_gph), "gph", "prior_calc"),
            "hours": make_input(float(hours_per_day), "h/day", "user"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
            "max_cycles": make_input(float(max_cycles), "x", "user"),
        },
        formula="C(t) = Css - (Css - 1) e^(-spray t / V), Css = (evap + spray)/spray ; refresh = min(t(C = k), hygiene days)",
        steps=steps,
        citations=[CIT_CYCLES, CIT_LEGIONELLA, CIT_DRC_UPKEEP] + (feed.citations if feed else []),
        warnings=[c["title"] for c in checks if c["severity"] != INFO],
    )


def maintenance_schedule(plan: dict, *, outdoor: bool = True, frost: bool = False, filters: int = 0, uv: bool = False,
                         lights: int = 0, vfd: bool = False, nozzles: int = 0, switched_valves: int = 0,
                         run_hours_year: float = 0.0) -> list[dict]:
    """Every recurring task and how often, for the O&M manual. ``plan`` is :func:`water_plan`'s value."""
    out = []

    def add(task: str, every: str, note: str = "") -> None:
        out.append({"task": task, "every": every, "note": note})

    t = plan.get("treatment")
    if t in ("chlorine", "bleach"):
        add("Test free chlorine and pH", "Daily, before the feature runs", f"Hold {plan.get('residual_ppm')} ppm and pH 7.2-7.8")
        if plan.get("daily_dose_floz") is not None:
            add("Dose chlorine", "Daily", f"About {plan['daily_dose_floz']:.1f} fl oz a day; adjust to the test")
    elif t == "bromine":
        add("Test bromine and pH", "Daily, before the feature runs", "Hold 3-5 ppm and pH 7.2-7.8")
        add("Refill the bromine feeder", "Weekly")
    add("Check the water level and the auto-fill", "Daily", f"About {plan.get('makeup_gpd', 0):.0f} gal/day of make-up")
    add("Clean the strainer and skimmer baskets", "Every 3 days" if outdoor else "Weekly", "More often after leaves, dust or events")
    if filters:
        add("Clean or backwash the filter", "Weekly, or at 8-10 psi over clean", f"{filters} filter(s)")
    if nozzles:
        add("Check every nozzle's pattern", "Weekly", "A weak or bent jet means a blocked nozzle")
        add("Descale the nozzles", "Monthly", "Soak in a descaler if scale shows")
    add("Drain, clean and refill", f"Every {plan.get('refresh_days', HYGIENE_DAYS)} days",
        "Before minerals concentrate" if plan.get("refresh_by") == "minerals" else "Scrub the basin; flush the lines")
    add("Press TEST on every GFCI", "Monthly", "Power must drop; reset it")
    if uv:
        add("Replace the UV lamp", "Every 9,000 running hours (about a year)", "Clean the quartz sleeve quarterly")
    if lights:
        add("Inspect the underwater lights", "Quarterly", f"{lights} fixture(s): lens, gasket, cord")
    if switched_valves:
        add("Run every valve through a test sweep", "Monthly", f"{switched_valves} valve(s): replace any that stick")
    if vfd:
        add("Clean the VFD's heat sink and check its fault log", "Quarterly")
    add("Service the pump", "Yearly", f"About {round(run_hours_year):,} running hours a year: seal, bearings, impeller" if run_hours_year else "Seal, bearings, impeller")
    if outdoor and frost:
        add("Winterize", "Before the first freeze", "Drain the basin, pumps, pipes and nozzles; pull the lights; store the controller dry")
        add("Start up", "Each spring", "Refill, prime, check every terminal, rebalance the chemistry")
    return out


def om_per_year(plan: dict, pumps: list[dict], *, volume_gal: float, rules: dict | None = None, equipment_cost: float | None = None) -> CalcResult:
    """Water, power, chemicals and parts per year.

    ``plan``: :func:`water_plan`'s value. ``pumps``: ``[{flow_gpm, head_ft, efficiency_pct,
    motor_eff_pct, hours_per_day, electrical_kw}]`` at their operating points (the network result).
    ``rules``: ``kwh_rate``, ``water_rate_per_ccf``, ``chlorine_per_gal``, ``parts_pct_of_equipment`` /
    ``parts_per_year``, ``days_per_year`` (defaults in :data:`RULE_DEFAULTS`)."""
    rr = {**RULE_DEFAULTS, **{k: v for k, v in (rules or {}).items() if v is not None}}
    days = float(rr["days_per_year"])
    refills = days / max(1, plan.get("refresh_days") or HYGIENE_DAYS)
    water_gal = plan.get("makeup_gpd", 0) * days + refills * float(volume_gal)
    water_cost = water_gal / GAL_PER_CCF * float(rr["water_rate_per_ccf"])
    power_cost, power_cross, kwh = 0.0, 0.0, 0.0
    for p in pumps:
        hrs = float(p.get("hours_per_day") or 8.0)
        eff = float(p["efficiency_pct"]) / 100 if p.get("efficiency_pct") else DEFAULT_PUMP_EFF
        motor = float(p["motor_eff_pct"]) / 100 if p.get("motor_eff_pct") else DEFAULT_MOTOR_EFF
        ec = electric_cost(float(p.get("flow_gpm") or 0), float(p.get("head_ft") or 0), hours_per_day=hrs, rate_per_kwh=float(rr["kwh_rate"]),
                           pump_eff=max(eff, 0.05), motor_eff=motor)
        power_cost += ec.value * days / DAYS_PER_YEAR
        if p.get("electrical_kw") is not None:
            kwh += float(p["electrical_kw"]) * hrs * days
    power_cross = kwh * float(rr["kwh_rate"])
    chem_gal = 0.0
    if plan.get("chlorinator_feed_gph"):
        hrs = max((float(p.get("hours_per_day") or 8.0) for p in pumps), default=8.0)
        chem_gal = plan["chlorinator_feed_gph"] * hrs * days
    chem_cost = chem_gal * float(rr["chlorine_per_gal"])
    parts = float(equipment_cost) * float(rr["parts_pct_of_equipment"]) if equipment_cost else float(rr["parts_per_year"])
    total = water_cost + power_cost + chem_cost + parts
    return CalcResult(
        calc="om_per_year",
        value={"total": total, "water": water_cost, "power": power_cost, "chemicals": chem_cost, "parts": parts, "water_gal": water_gal,
               "chlorine_gal": chem_gal, "power_crosscheck": power_cross, "kwh": kwh, "refills": refills},
        unit="$/yr",
        inputs={
            "kwh_rate": make_input(float(rr["kwh_rate"]), "$/kWh", "user", "site"),
            "water_rate": make_input(float(rr["water_rate_per_ccf"]), "$/CCF", "user", "site"),
            "chlorine_price": make_input(float(rr["chlorine_per_gal"]), "$/gal", "default", CIT_RULES),
            "parts": make_input(parts, "$/yr", "default", CIT_RULES),
            "days": make_input(days, "days/yr", "user"),
        },
        formula="water = (make-up x days + refills x volume)/748 x rate ; power = electric_cost per pump ; chlorine = feed x hours x days x price",
        steps=[
            f"water {water_gal:,.0f} gal ({refills:.1f} refills) -> ${water_cost:,.2f}",
            f"power (workbook electric_cost) ${power_cost:,.2f} ; operating-point cross-check {kwh:,.0f} kWh -> ${power_cross:,.2f}",
            f"chlorine {chem_gal:,.1f} gal -> ${chem_cost:,.2f} ; parts ${parts:,.2f}",
            f"total ${total:,.2f}/yr",
        ],
        citations=[CIT_RULES, CIT_DRC_UPKEEP, *electric_cost(1, 1).citations],
    )
