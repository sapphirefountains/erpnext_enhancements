"""Where the pump curve meets the system: delivered flow, head, power, NPSH margin.

Port of Digital Rain Curtain ``src/hydro/flow.js`` ``operatingPoint`` (the ``system``
model), ``systemCurve``, ``bypassFlow``, ``npshaM``; golden file
``tests/golden/drc/operating_point.json`` (1e-9 for the bisections, 1e-12 for the rest).

This is the calculation the workbook engine never had: ``engine/pump.select_pump`` only
checks ``head(design flow) >= TDH``, so an oversized pump "passes" and is never asked
what it actually delivers. Here the inlet pressure of the terminal is bisected until

    rho g H_pump(Q + Q_bypass) = rho g z_static + dp_supply(Q) + p_inlet,   Q = demand.q(p_inlet)

with the same caps and tolerances as DRC (60 halvings or 1e-6 Pa), so results agree to
1e-9 and not just to the tolerance. Then, as DRC does:

* **cannot lift**: the pump's shut-off head (with the bypass open) cannot reach the
  static lift; the pump dead-heads against the bypass and delivers nothing.
* **dead head**: pump flow under 10% of its best-efficiency flow.
* **PRV**: when the uncontrolled inlet pressure exceeds the set pressure, the valve
  holds the set pressure and the excess is dropped across it.
* **VFD**: the drive bisects speed (50 halvings) so the inlet holds the set pressure,
  down to a 15% speed floor.
* **bypass**: a recirculation orifice from the discharge back to the basin,
  ``Q = 0.61 A sqrt(2 p_d / rho)``.

Two things are new:

* **Open-discharge terminals** (``demand.p_fixed``): the inlet pressure is pinned at the
  overflow lift, so the solve bisects on flow instead (same monotone residual).
* **A starved fixed-flow terminal** (``demand.p_floor``): if the pump cannot hold the
  minimum pressure at the fixed flow, the flow it can deliver at that pressure is solved.

**NPSHa uses the site elevation.** DRC computes ``(PATM - pv) / (rho g)`` with
``PATM = 101325 Pa``, fixed at sea level, and has no elevation anywhere in its
hydraulics. At Salt Lake City (about 4,300 ft) the atmosphere is about 4.9 ft of water
lower, so DRC overstates NPSHa by about 1.5 m, three times its own 0.5 m margin (audit
E5). So the kernel has **no default atmosphere**: ``System.atm_pa`` must be given, and
``operating_point_si`` / ``npsha_si`` raise without it. Pass ``site_atmosphere(elevation,
temp)`` for a design (the envelope :func:`operating_point` and :func:`build_system` do:
``engine.safety._atm_head_ft`` for the barometric figure, ``engine.safety._vapor_pressure_psia``
for the vapor pressure, so NPSH here and in ``npsh_available`` agree), or ``units.PATM``
explicitly to reproduce DRC, as the goldens do. ``pv_pa`` may stay None (the water's own
Antoine vapor pressure).

**A control set below an open terminal's overflow** shuts that terminal off: the PRV or VFD
holds the inlet under the lift, so the weir gets nothing. That is what the controls do (and
it matches DRC's arithmetic), but it is never what a designer meant, so
``OpPoint.control_below_overflow`` flags it and the envelope reports it as its status. A VFD
that holds a *flow* (the way a weir is really run) is not built yet.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

from ..constants import FT_PER_PSI, NPSH_DEFAULT_MARGIN_FT
from ..envelope import CalcResult, make_input
from ..safety import _atm_head_ft, _vapor_pressure_psia
from . import _js
from .pump import (
    PumpCurve,
    bep_flow_si,
    max_flow_si,
    npshr_si,
    pump_curve_si,
    pump_eff_si,
    pump_head_si,
    shaft_power_si,
)
from .segment import Segment, suction_loss_si, supply_loss_si
from .terminals import build_demand
from .units import (
    CIT_DRC,
    M_PER_FT,
    MM_PER_IN,
    PA_PER_PSI,
    PATM,
    W_PER_HP,
    G,
    f_to_c,
    gpm_to_m3s,
    m3s_to_gpm,
)
from .water import Water, area_of_si, water_si

CIT_OPERATING_POINT = "Pump curve meets system curve (Karassik, Pump Handbook 4th ed., ch. 9)"
CIT_NPSH_SITE = "NPSHa = (p_atm(elevation) - p_v) / rho g - lift - h_f,suction (ANSI/HI 9.6.1)"
CIT_BYPASS = "Sharp-edged orifice, Cd = 0.61 (Idelchik)"

VFD_FLOOR = 0.15
DEAD_HEAD_FRACTION = 0.1

STATUS_OK = "Okay"
STATUS_MARGINAL = "Marginal"
STATUS_CAVITATION = "Cavitation Risk"
STATUS_CANNOT_LIFT = "Cannot Lift"
STATUS_DEAD_HEAD = "Dead Head"
STATUS_STARVED = "Starved"
STATUS_CONTROL_BELOW_OVERFLOW = "Control Below Overflow"

_NO_ATM = (
    "System.atm_pa is required: pass site_atmosphere(elevation_ft, water_temp_f)[0] for a design, "
    "or units.PATM explicitly for sea level (DRC parity). A silent sea-level default is audit error E5."
)


@dataclass(frozen=True)
class System:
    """Everything between the basin and the terminal inlet (SI).

    ``static_m`` is the basin water surface to the terminal inlet node; ``lift_m`` the pump
    inlet above the basin surface (negative = flooded suction)."""

    pump: PumpCurve
    w: Water
    supply: tuple = ()
    suction: Segment | None = None
    static_m: float = 0.0
    lift_m: float = 0.0
    clog: float = 0.0
    control: str = "none"  # "none" | "prv" | "vfd"
    set_pa: float | None = None  # control set pressure at the terminal inlet
    bypass_d_m: float = 0.0
    atm_pa: float | None = None  # required by the solvers: site_atmosphere(elevation)[0], or units.PATM for DRC parity
    pv_pa: float | None = None  # None -> the water's own (Antoine) vapor pressure


@dataclass
class OpPoint:
    """``operatingPoint``'s result (SI), snake_case."""

    q: float
    q_pump: float
    q_bypass: float
    p_in: float
    p_discharge: float
    static_pa: float
    loss_supply_pa: float
    prv_drop_pa: float
    head_m: float
    speed: float
    control: str
    set_pa: float
    eff: float
    power_hyd_w: float
    power_shaft_w: float
    power_elec_w: float
    npsha: float
    npshr: float
    npsh_margin: float
    dead_head: bool
    cannot_lift: bool
    starved: bool = False
    control_below_overflow: bool = False  # a PRV / VFD set under an open terminal's overflow shut it off
    open_discharge: bool = False
    model: str = "system"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def bypass_flow_si(bypass_d_m: float, p_discharge: float, w: Water) -> float:
    """``bypassFlow``: recirculation through the bypass orifice (m^3/s), Cd 0.61."""
    if not (bypass_d_m > 0):
        return 0.0
    return 0.61 * area_of_si(bypass_d_m) * math.sqrt((2 * _js.jmax(0.0, p_discharge)) / w.rho)


def npsha_si(system: System, q_pump: float) -> float:
    """``npshaM``: NPSH available (m) at pump flow ``q_pump``, at the system's atmosphere."""
    w = system.w
    if system.atm_pa is None:
        raise ValueError(_NO_ATM)
    pv = w.pv if system.pv_pa is None else system.pv_pa
    rho = w.rho
    return (
        (system.atm_pa - pv) / (rho * G)
        - _js.or_(system.lift_m, 0.0)
        - suction_loss_si(system.suction, q_pump, w, system.clog) / (rho * G)
    )


def operating_point_si(system: System, demand, speed: float | None = None) -> OpPoint:
    """``operatingPoint`` (system model) plus open-discharge and starved fixed-flow terminals."""
    if system.atm_pa is None:
        raise ValueError(_NO_ATM)
    pump, w = system.pump, system.w
    rho = w.rho
    rg = rho * G
    supply, clog, bypass_d = system.supply, system.clog, system.bypass_d_m

    def head(qp, s):
        return pump_head_si(pump, qp, s)

    def bypass(pd):
        return bypass_flow_si(bypass_d, pd, w)

    def loss(q):
        return supply_loss_si(supply, q, w, clog)

    static_pa = rg * system.static_m
    user_speed = pump.default_speed if speed is None else speed
    s = _js.jmax(user_speed, 1e-3)
    set_pa = system.set_pa if system.set_pa is not None else math.nan
    control = system.control or "none"
    acting = control if system.set_pa is not None else "none"
    p_fixed = getattr(demand, "p_fixed", None)
    p_floor = getattr(demand, "p_floor", None)

    def dead_head_against_bypass(s_cur):
        """Discharge pressure where the pump's head balances its own bypass flow."""
        p_max = rg * head(0, s_cur)
        b_lo, b_hi = 0.0, p_max
        it = 0
        while it < 60 and (b_hi - b_lo) >= 1e-6:
            mid = 0.5 * (b_lo + b_hi)
            if rg * head(bypass(mid), s_cur) - mid >= 0:
                b_lo = mid
            else:
                b_hi = mid
            it += 1
        return 0.5 * (b_lo + b_hi)

    def f_at(p_in, s_cur):
        q = demand.q(p_in)
        ls = loss(q)
        pd = static_pa + ls + p_in
        qb = bypass(pd)
        qp = q + qb
        return rg * head(qp, s_cur) - pd, q, ls, pd, qb, qp

    def q_bisect(p_node, s_cur, q_lo, q_cap=None):
        """Flow the pump delivers with the terminal inlet pinned at ``p_node`` (residual falls with Q)."""

        def g(q):
            ls = loss(q)
            pd = static_pa + ls + p_node
            qb = bypass(pd)
            return rg * head(q + qb, s_cur) - pd, ls, pd, qb

        lo = q_lo
        if q_cap is not None:
            hi = q_cap
        else:
            hi = _js.jmax(max_flow_si(pump, s_cur), 2 * lo, 1e-6)
            for _ in range(60):
                if g(hi)[0] < 0:
                    break
                hi *= 2
        if g(hi)[0] >= 0:
            lo = hi
        for _ in range(200):
            if hi - lo <= 1e-13 * _js.jmax(hi, 1e-9):
                break
            mid = 0.5 * (lo + hi)
            if g(mid)[0] >= 0:
                lo = mid
            else:
                hi = mid
        q = 0.5 * (lo + hi)
        _, ls, pd, qb = g(q)
        return q, ls, pd, qb

    def solve_uncontrolled(s_cur):
        if not demand.any_open:
            pd = dead_head_against_bypass(s_cur)
            qb = bypass(pd)
            return dict(Q=0.0, pIn=pd - static_pa, pd=pd, qb=qb, Qpump=qb, headM=head(qb, s_cur), loss=0.0,
                        cannotLift=False, starved=False, open=False)

        lo = -demand.pg
        hi = rg * head(0, s_cur) - static_pa + 1
        qb_at_lo = bypass(static_pa + lo)
        if hi <= lo or rg * head(qb_at_lo, s_cur) - (static_pa + lo) < 0:
            # Pump shut-off head cannot overcome the static lift
            pd = dead_head_against_bypass(s_cur)
            qb = bypass(pd)
            return dict(Q=0.0, pIn=lo, pd=pd, qb=qb, Qpump=qb, headM=head(qb, s_cur), loss=0.0,
                        cannotLift=True, starved=False, open=False)

        if p_fixed is not None:
            if f_at(p_fixed, s_cur)[0] >= 0:
                # The node reaches the overflow: the open terminal takes the rest of the pump's flow.
                q, ls, pd, qb = q_bisect(p_fixed, s_cur, demand.q(p_fixed))
                return dict(Q=q, pIn=p_fixed, pd=pd, qb=qb, Qpump=q + qb, headM=head(q + qb, s_cur), loss=ls,
                            cannotLift=False, starved=False, open=True)
            hi = _js.jmin(hi, p_fixed)

        b_lo, b_hi = lo, hi
        it = 0
        while it < 60 and (b_hi - b_lo) >= 1e-6:
            mid = 0.5 * (b_lo + b_hi)
            if f_at(mid, s_cur)[0] >= 0:
                b_lo = mid
            else:
                b_hi = mid
            it += 1
        p_in = 0.5 * (b_lo + b_hi)
        f, q, ls, pd, qb, qp = f_at(p_in, s_cur)
        if p_floor is not None and q > 0 and f < -max(1.0, 1e-6 * abs(pd)):
            # A fixed-flow terminal the pump cannot hold at its minimum pressure.
            q, ls, pd, qb = q_bisect(p_floor, s_cur, 0.0, q)
            return dict(Q=q, pIn=p_floor, pd=pd, qb=qb, Qpump=q + qb, headM=head(q + qb, s_cur), loss=ls,
                        cannotLift=False, starved=True, open=False)
        return dict(Q=q, pIn=p_in, pd=pd, qb=qb, Qpump=qp, headM=head(qp, s_cur), loss=ls,
                    cannotLift=False, starved=False, open=False)

    final_speed = s
    r = solve_uncontrolled(s)
    p_in, q, pd, qb = r["pIn"], r["Q"], r["pd"], r["qb"]
    q_pump, head_m, loss_supply = r["Qpump"], r["headM"], r["loss"]
    prv_drop = 0.0
    cannot_lift, starved, opened = r["cannotLift"], r["starved"], r["open"]

    if acting == "prv" and not cannot_lift and r["pIn"] > set_pa:
        p_in = set_pa
        q = demand.q(set_pa)
        loss_supply = loss(q)
        p_max = rg * head(0, s)
        lo, hi = 0.0, p_max + 1
        it = 0
        while it < 60 and (hi - lo) >= 1e-6:
            mid = 0.5 * (lo + hi)
            q_trial = q + bypass(mid)
            if rg * head(q_trial, s) - mid >= 0:
                lo = mid
            else:
                hi = mid
            it += 1
        pd = 0.5 * (lo + hi)
        qb = bypass(pd)
        q_pump = q + qb
        head_m = head(q_pump, s)
        prv_drop = _js.jmax(0.0, pd - static_pa - loss_supply - set_pa)
        starved, opened = False, False
    elif acting == "vfd" and not cannot_lift:
        if r["pIn"] < set_pa:
            final_speed = s
        else:
            pt_min = solve_uncontrolled(VFD_FLOOR)
            if pt_min["pIn"] > set_pa:
                final_speed = VFD_FLOOR
                p_in, q, pd, qb = pt_min["pIn"], pt_min["Q"], pt_min["pd"], pt_min["qb"]
                q_pump, head_m, loss_supply = pt_min["Qpump"], pt_min["headM"], pt_min["loss"]
                cannot_lift, starved, opened = pt_min["cannotLift"], pt_min["starved"], pt_min["open"]
            else:
                # At the set pressure the flow and the discharge pressure it needs are known, so only the
                # pump curve is solved for speed (head rises with speed at fixed flow); no nested solves.
                qs = demand.q(set_pa)
                ls = loss(qs)
                pd_set = static_pa + ls + set_pa
                qb_set = bypass(pd_set)
                s_lo, s_hi = VFD_FLOOR, s
                for _ in range(50):
                    s_mid = 0.5 * (s_lo + s_hi)
                    if rg * head(qs + qb_set, s_mid) < pd_set:
                        s_lo = s_mid
                    else:
                        s_hi = s_mid
                final_speed = 0.5 * (s_lo + s_hi)
                p_in, q, pd, qb, q_pump = set_pa, qs, pd_set, qb_set, qs + qb_set
                head_m = head(q_pump, final_speed)
                loss_supply = ls
                cannot_lift, starved, opened = False, False, False

    eff = pump_eff_si(pump, q_pump, final_speed)
    power_hyd = q_pump * rho * G * head_m
    power_shaft = shaft_power_si(pump, q_pump, final_speed, w)
    motor = 80 if pump.motor_eff_pct is None else pump.motor_eff_pct
    power_elec = power_shaft / _js.jmax(motor / 100, 0.1)
    npsha = npsha_si(system, q_pump)
    npshr = npshr_si(pump, q_pump, final_speed)
    return OpPoint(
        q=q, q_pump=q_pump, q_bypass=qb, p_in=p_in, p_discharge=pd, static_pa=static_pa,
        loss_supply_pa=loss_supply, prv_drop_pa=prv_drop, head_m=head_m, speed=final_speed, control=control,
        set_pa=set_pa, eff=eff, power_hyd_w=power_hyd, power_shaft_w=power_shaft, power_elec_w=power_elec,
        npsha=npsha, npshr=npshr, npsh_margin=npsha - npshr,
        dead_head=q_pump < DEAD_HEAD_FRACTION * bep_flow_si(pump, final_speed), cannot_lift=cannot_lift,
        starved=starved, open_discharge=opened,
        control_below_overflow=(
            acting in ("prv", "vfd") and p_fixed is not None and not r["cannotLift"] and set_pa < p_fixed
        ),
    )


def system_curve_si(system: System, demand, n: int = 40, q_max: float | None = None) -> list[dict]:
    """``systemCurve``: head required (static + supply loss + terminal pressure) from 0 to ``q_max``
    (default 1.2x the pump's run-out at its set speed). Points where the terminal needs an infinite
    pressure are skipped."""
    w = system.w
    rho = w.rho
    qm = q_max if q_max is not None else 1.2 * max_flow_si(system.pump, system.pump.default_speed)
    static_pa = rho * G * system.static_m
    count = max(2, n)
    out = []
    for i in range(count):
        q = (i / (count - 1)) * qm
        p_for = demand.p_for(q)
        if not _js.is_number(p_for):
            continue
        ls = supply_loss_si(system.supply, q, w, system.clog)
        out.append({"Q": q, "headM": (static_pa + ls + p_for) / (rho * G)})
    return out


# ---------------------------------------------------------------------------
# Envelopes (US: gpm, ft, psi, hp)
# ---------------------------------------------------------------------------


VAPOR_TABLE_F = (40.0, 120.0)  # the span of constants.VAPOR_PRESSURE_PSIA


def site_atmosphere(elevation_ft: float, water_temp_f: float) -> tuple[float, float]:
    """(atmospheric pressure, vapor pressure) in Pa from the workbook engine's own models:
    ``safety._atm_head_ft`` (barometric lapse) and ``safety._vapor_pressure_psia`` (table).

    The table spans 40-120 F and clamps outside it, which would understate the vapor pressure of
    hotter water (2.89 psia at 140 F, not 1.69) and overstate NPSHa; outside its span the Antoine
    figure from ``water_si`` is used instead."""
    atm_psia = _atm_head_ft(elevation_ft) / FT_PER_PSI
    t = float(water_temp_f)
    if VAPOR_TABLE_F[0] <= t <= VAPOR_TABLE_F[1]:
        pv = _vapor_pressure_psia(t) * PA_PER_PSI
    else:
        pv = water_si(f_to_c(t)).pv
    return atm_psia * PA_PER_PSI, pv


def build_system(
    pump,
    *,
    supply: list | None = None,
    suction: dict | None = None,
    static_ft: float = 0.0,
    suction_lift_ft: float = 0.0,
    water_temp_f: float = 68.0,
    elevation_ft: float = 0.0,
    clog_pct: float = 0.0,
    control: str = "none",
    set_psi: float | None = None,
    bypass_in: float = 0.0,
) -> System:
    """A :class:`System` from US inputs; NPSHa evaluated at ``elevation_ft``."""
    w = water_si(f_to_c(float(water_temp_f)))
    atm_pa, pv_pa = site_atmosphere(float(elevation_ft or 0), float(water_temp_f))
    return System(
        pump=pump if isinstance(pump, PumpCurve) else PumpCurve.from_us(list(pump or [])),
        w=w,
        supply=tuple(s if isinstance(s, Segment) else Segment.from_us(s) for s in supply or []),
        suction=(suction if isinstance(suction, Segment) else Segment.from_us(suction)) if suction else None,
        static_m=float(static_ft or 0) * M_PER_FT,
        lift_m=float(suction_lift_ft or 0) * M_PER_FT,
        clog=float(clog_pct or 0) / 100,
        control=control or "none",
        set_pa=float(set_psi) * PA_PER_PSI if set_psi else None,
        bypass_d_m=float(bypass_in or 0) * MM_PER_IN / 1000,
        atm_pa=atm_pa,
        pv_pa=pv_pa,
    )


def _ft(pa: float, w: Water) -> float:
    return pa / (w.rho * G) / M_PER_FT


def operating_point(
    pump,
    terminal,
    *,
    supply: list | None = None,
    suction: dict | None = None,
    static_ft: float = 0.0,
    suction_lift_ft: float = 0.0,
    water_temp_f: float = 68.0,
    elevation_ft: float = 0.0,
    clog_pct: float = 0.0,
    control: str = "none",
    set_psi: float | None = None,
    bypass_in: float = 0.0,
    speed_pct: float | None = None,
    design_flow_gpm: float | None = None,
    npsh_margin_ft: float = NPSH_DEFAULT_MARGIN_FT,
) -> CalcResult:
    """The delivered flow, head, power and NPSH margin of a pump on a system.

    ``pump``: a :class:`PumpCurve` or Pump Curve Point rows (gpm, ft). ``terminal``: a demand
    object or a model-2 terminal row (``terminals.build_demand``). ``supply`` / ``suction``:
    US segment rows (``Segment.from_us``). ``static_ft``: basin surface to the terminal inlet.
    ``suction_lift_ft``: pump inlet above the basin surface (negative = flooded).
    ``design_flow_gpm``: what the feature needs; the result says which flow the safety checks
    must use (the larger of design and operating, decision 2026-11-01)."""
    system = build_system(
        pump, supply=supply, suction=suction, static_ft=static_ft, suction_lift_ft=suction_lift_ft,
        water_temp_f=water_temp_f, elevation_ft=elevation_ft, clog_pct=clog_pct, control=control,
        set_psi=set_psi, bypass_in=bypass_in,
    )
    w = system.w
    demand = terminal if hasattr(terminal, "p_for") else build_demand(terminal, w.rho)
    op = operating_point_si(system, demand, None if speed_pct is None else float(speed_pct) / 100)
    p = system.pump
    q_gpm = m3s_to_gpm(op.q)
    warnings: list[str] = []
    if control in ("prv", "vfd") and system.set_pa is None:
        warnings.append(f"Control {control} needs a set pressure; solved as uncontrolled.")
    if len(p.head_points) < 2:
        warnings.append("No pump curve (fewer than two head points): the operating point rests on the quadratic estimate.")

    margin_ft = op.npsh_margin / M_PER_FT
    if op.control_below_overflow:
        status = STATUS_CONTROL_BELOW_OVERFLOW
        lift_psi = getattr(demand, "p_fixed", 0) / PA_PER_PSI
        warnings.append(
            f"The {control.upper()} holds {op.set_pa / PA_PER_PSI:.2f} psi, under the {lift_psi:.2f} psi it takes to reach "
            f"the overflow, so the open terminal gets nothing ({q_gpm:.1f} GPM in all). Raise the set pressure to at "
            "least the lift, or run the pump without the control; a VFD that holds a flow is not available yet."
        )
    elif op.cannot_lift:
        status = STATUS_CANNOT_LIFT
        warnings.append(
            f"The pump's shut-off head ({pump_head_si(p, 0, op.speed) / M_PER_FT:.1f} ft) "
            f"cannot reach the static lift plus the terminal ({_ft(op.static_pa, w) + _ft(op.p_in, w):.1f} ft). "
            "Pick a pump with more head."
        )
    elif op.starved:
        status = STATUS_STARVED
        warnings.append(f"The pump cannot hold the terminal's minimum pressure; it delivers {q_gpm:.1f} GPM.")
    elif op.dead_head:
        status = STATUS_DEAD_HEAD
        warnings.append("Pump flow is under 10% of its best-efficiency flow: it will overheat. Add a bypass or resize.")
    elif not p.npshr_points:
        status = STATUS_OK
        warnings.append("No NPSHr curve: NPSHa computed but not checked.")
    elif margin_ft >= npsh_margin_ft:
        status = STATUS_OK
    elif margin_ft >= 0:
        status = STATUS_MARGINAL
        warnings.append(f"NPSH margin {margin_ft:.1f} ft is under the {npsh_margin_ft:g} ft safety margin.")
    else:
        status = STATUS_CAVITATION
        warnings.append(f"NPSHa {op.npsha / M_PER_FT:.1f} ft is below NPSHr {op.npshr / M_PER_FT:.1f} ft: the pump will cavitate.")

    q_design = getattr(demand, "q_design", None)
    if design_flow_gpm is None and q_design:
        design_flow_gpm = m3s_to_gpm(q_design)
    safety_flow = max(q_gpm, float(design_flow_gpm or 0))
    if design_flow_gpm:
        ratio = q_gpm / float(design_flow_gpm)
        if ratio < 0.95:
            warnings.append(f"The pump delivers {q_gpm:.1f} GPM, {1 - ratio:.0%} short of the {design_flow_gpm:g} GPM design flow.")
        elif ratio > 1.10:
            warnings.append(
                f"The pump delivers {q_gpm:.1f} GPM, {ratio - 1:.0%} over the {design_flow_gpm:g} GPM design flow: "
                "velocity, VGB and pipe-pressure checks must use the operating flow."
            )

    shutoff_ft = pump_head_si(p, 0, op.speed) / M_PER_FT
    value = {
        "flow_gpm": q_gpm,
        "pump_flow_gpm": m3s_to_gpm(op.q_pump),
        "bypass_gpm": m3s_to_gpm(op.q_bypass),
        "head_ft": op.head_m / M_PER_FT,
        "inlet_psi": op.p_in / PA_PER_PSI,
        "discharge_psi": op.p_discharge / PA_PER_PSI,
        "static_ft": _ft(op.static_pa, w),
        "supply_loss_ft": _ft(op.loss_supply_pa, w),
        "prv_drop_psi": op.prv_drop_pa / PA_PER_PSI,
        "speed_pct": op.speed * 100,
        "efficiency_pct": op.eff * 100,
        "hydraulic_hp": op.power_hyd_w / W_PER_HP,
        "shaft_hp": op.power_shaft_w / W_PER_HP,
        "electrical_kw": op.power_elec_w / 1000,
        "npsha_ft": op.npsha / M_PER_FT,
        "npshr_ft": op.npshr / M_PER_FT,
        "npsh_margin_ft": margin_ft,
        "shutoff_head_ft": shutoff_ft,
        "design_flow_gpm": float(design_flow_gpm) if design_flow_gpm else None,
        "safety_flow_gpm": safety_flow,
        "dead_head": op.dead_head,
        "cannot_lift": op.cannot_lift,
        "starved": op.starved,
        "open_discharge": op.open_discharge,
        "control": op.control,
    }
    atm_ft = _atm_head_ft(float(elevation_ft or 0))
    npsha_sea_ft = npsha_si(replace(system, atm_pa=PATM, pv_pa=None), op.q_pump) / M_PER_FT
    steps = [
        f"static {value['static_ft']:.2f} ft + supply loss {value['supply_loss_ft']:.2f} ft + terminal "
        f"{_ft(op.p_in, w):.2f} ft = pump head {value['head_ft']:.2f} ft at {value['pump_flow_gpm']:.1f} GPM",
        f"speed {value['speed_pct']:.1f}% ; efficiency {value['efficiency_pct']:.1f}% ; shaft {value['shaft_hp']:.2f} hp ; "
        f"input {value['electrical_kw']:.2f} kW",
        f"NPSHa @ {float(elevation_ft or 0):g} ft: atm {atm_ft:.2f} ft - vapor {_ft(system.pv_pa, w):.2f} ft "
        f"- lift {float(suction_lift_ft or 0):g} ft - suction loss {_ft(suction_loss_si(system.suction, op.q_pump, w, system.clog), w):.2f} ft "
        f"= {value['npsha_ft']:.2f} ft (DRC's sea-level figure: {npsha_sea_ft:.2f} ft)",
        f"NPSHr {value['npshr_ft']:.2f} ft ; margin {margin_ft:.2f} ft",
    ]
    if op.q_bypass:
        steps.append(f"bypass {value['bypass_gpm']:.1f} GPM through a {float(bypass_in):g} in orifice")
    if op.prv_drop_pa:
        steps.append(f"PRV drops {value['prv_drop_psi']:.2f} psi to hold {op.set_pa / PA_PER_PSI:.2f} psi")
    if op.open_discharge:
        steps.append("open discharge: inlet pinned at the overflow lift; flow solved on the pump curve")
    return CalcResult(
        calc="operating_point",
        value=value,
        unit="",
        inputs={
            "static": make_input(float(static_ft or 0), "ft", "user", "basin surface to terminal inlet"),
            "suction_lift": make_input(float(suction_lift_ft or 0), "ft", "user", "+lift / -flooded"),
            "elevation": make_input(float(elevation_ft or 0), "ft", "user", "site altitude"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
            "control": make_input(control, "", "user"),
            "set_pressure": make_input(set_psi, "psi", "user"),
            "bypass": make_input(float(bypass_in or 0), "in", "user"),
            "clog": make_input(float(clog_pct or 0), "%", "user"),
        },
        formula="rho g H_pump(Q + Q_bypass, s) = rho g z + dp_supply(Q) + p_terminal(Q)",
        steps=steps,
        citations=[CIT_OPERATING_POINT, CIT_NPSH_SITE, CIT_DRC],
        warnings=warnings,
        status=status,
    )


def system_curve(terminal, *, pump=None, supply=None, static_ft=0.0, water_temp_f=68.0, clog_pct=0.0,
                 n: int = 40, max_flow_gpm: float | None = None) -> CalcResult:
    """Head required (ft) against flow (GPM): static + supply loss + terminal pressure."""
    system = build_system(pump or [], supply=supply, static_ft=static_ft, water_temp_f=water_temp_f, clog_pct=clog_pct)
    demand = terminal if hasattr(terminal, "p_for") else build_demand(terminal, system.w.rho)
    q_max = gpm_to_m3s(float(max_flow_gpm)) if max_flow_gpm else None
    warnings = []
    if q_max is None and not system.pump.head_points and not system.pump.quad:
        warnings.append("No pump and no maximum flow: give max_flow_gpm.")
        q_max = 0.0
    pts = system_curve_si(system, demand, n, q_max)
    value = [{"flow_gpm": m3s_to_gpm(p["Q"]), "head_ft": p["headM"] / M_PER_FT} for p in pts]
    return CalcResult(
        calc="system_curve",
        value=value,
        unit="ft",
        inputs={
            "static": make_input(float(static_ft or 0), "ft", "user"),
            "water_temp": make_input(float(water_temp_f), "degF", "user"),
            "points": make_input(len(value), "", "default"),
        },
        formula="H_sys(Q) = z + dp_supply(Q) / rho g + p_terminal(Q) / rho g",
        steps=[f"{len(value)} points, {value[0]['flow_gpm']:.0f}-{value[-1]['flow_gpm']:.0f} GPM" if value else "no points"],
        citations=[CIT_OPERATING_POINT, CIT_DRC],
        warnings=warnings,
    )


def pump_curve(pump, speed_pct: float | None = None, n: int = 40) -> CalcResult:
    """The pump's head (ft) against flow (GPM) from shut-off to run-out at a speed."""
    p = pump if isinstance(pump, PumpCurve) else PumpCurve.from_us(list(pump or []))
    speed = None if speed_pct is None else float(speed_pct) / 100
    pts = pump_curve_si(p, speed, n)
    value = [{"flow_gpm": m3s_to_gpm(x["Q"]), "head_ft": x["headM"] / M_PER_FT} for x in pts]
    return CalcResult(
        calc="pump_curve",
        value=value,
        unit="ft",
        inputs={"speed": make_input(speed_pct if speed_pct is not None else p.speed_pct, "%", "user")},
        formula="H(Q, s) = s^2 H(Q/s), Q from 0 to run-out",
        steps=[f"shut-off {value[0]['head_ft']:.1f} ft ; run-out {value[-1]['flow_gpm']:.0f} GPM"],
        citations=[CIT_DRC],
    )


def bypass_flow(bypass_in: float, discharge_psi: float, water_temp_f: float = 68.0) -> CalcResult:
    """Recirculation (GPM) through a bypass orifice of ``bypass_in`` at a discharge pressure."""
    w = water_si(f_to_c(float(water_temp_f)))
    q = bypass_flow_si(float(bypass_in) * MM_PER_IN / 1000, float(discharge_psi) * PA_PER_PSI, w)
    return CalcResult(
        calc="bypass_flow",
        value=m3s_to_gpm(q),
        unit="GPM",
        inputs={
            "bypass": make_input(float(bypass_in), "in", "user"),
            "discharge": make_input(float(discharge_psi), "psi", "prior_calc"),
        },
        formula="Q = 0.61 A sqrt(2 p_d / rho)",
        steps=[f"Q = {m3s_to_gpm(q):.2f} GPM"],
        citations=[CIT_BYPASS, CIT_DRC],
    )
