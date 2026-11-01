"""Autosizing: pipe sizes, the pump, its control, bypass and arrestor, and the basin, from the design.

DRC's ``src/hydro/presets.js`` *pattern* (audit 1.20): evaluate every candidate through the real
model and list why each one fails, then pick the best that passes. Its *thresholds* are the rain
curtain's (jet-speed uniformity), so the checks here are Sapphire's and the trade's:

* :func:`pick_pipe`: the smallest catalog size within the **DOC-0049 velocity limit** for the line
  (suction 4.5 ft/s; discharge 6.5 PVC / 4.9 copper; Nikolas 2026-11-01: DOC-0049 governs) **and**
  DRC's **friction-gradient cap** of 0.04 ft/ft (Darcy). A material DOC-0049 has no limits for uses
  DRC's 1.5 m/s. :func:`pick_suction` goes one size up from the discharge trunk, as DRC's
  ``pipingLayout`` does, so the pump inlet has NPSH to spare. ``drc_pick_pipe`` is DRC's
  ``pickPipe`` itself (golden ``tests/golden/drc/autosize.json``).
* :func:`eval_pump` runs a catalog curve through the **network solve** (``hydraulics.network``) and
  fails it for: under 8% head margin at the design flow (what a regulator can throttle away as the
  strainer clogs), an NPSH margin under the site's at its elevation, a duty outside the BEP window
  (HI 9.6.3 preferred operating region, 70-120%), run-out, backflow or a dead head, a starved or
  short terminal, a velocity over the DOC-0049 limit or a cover over its VGB rating **at the
  operating flow** (decision 2026-11-01: safety checks use the real flow, the larger of design and
  operating), and pump shut-off head plus surge over a pipe's pressure rating.
  :func:`rank_pumps` orders a catalog: passing pumps smallest first, then the nearest misses.
* :func:`auto_bypass`: a minimum-flow orifice passing 15% of BEP at shut-off head (DRC
  ``autoBypassMm``), none with a VFD (the drive idles the pump instead).
* :func:`auto_surge`: an arrestor only when the Joukowsky + Michaud surge would put the line over
  its rating (or the allowable rise), rounded up to a stock gas volume (DRC ``autoSurge``).
* :func:`auto_basin`: water deep enough for the intake (HI 9.8 submergence plus one pipe diameter
  plus a margin) and freeboard 1.3 x the drain-back rise (DRC ``autoBasin``; the 1.3 is a house rule).
* :func:`derive` runs PLAN.md 2.2 steps 5 to 8 in order (basin and surge, routes and pipe sizes,
  operating point and pump, control / bypass / surge) and writes only into the sections whose
  ``setup`` entry is ``auto``, returning the derived model and every ``CalcResult``.

Rules with no public source come from ``rules`` (Water Engineering Settings house rules, PLAN.md
2.3) and default to :data:`RULE_DEFAULTS`, each cited "Sapphire standard".
"""

from __future__ import annotations

import copy
import math

from .constants import NPSH_DEFAULT_MARGIN_FT
from .envelope import CalcOption, CalcResult, make_input
from .hydraulics import _js
from .hydraulics._checks import BLOCKER, INFO, WARNING, make_check
from .hydraulics.basin_checks import basin_checks
from .hydraulics.catalog import MATERIALS, pipe_spec_si, resolve_material, sizes_for
from .hydraulics.network import CLOSED, _members, _report, build_network, solve_network_si
from .hydraulics.pump import PumpCurve, bep_flow_si, pump_head_si
from .hydraulics.segment import Segment
from .hydraulics.surge import ceil_to, surge_for_si
from .hydraulics.surge import surge as surge_envelope
from .hydraulics.units import CIT_DRC, M_PER_FT, MM_PER_IN, PA_PER_PSI, G, f_to_c, gpm_to_m3s, m3s_to_gpm
from .hydraulics.water import Water, area_of_si, friction_factor_si, water_si
from .pipe import pipe_pressure_rating, size_pipe
from .safety import suction_outlet_vgb

CIT_PICK = "DOC-0049 SUPPORT PipeType velocity limits (govern) + DRC presets.js pickPipe friction-gradient cap 0.04 m/m"
CIT_POR = "ANSI/HI 9.6.3-2017 preferred operating region: 70-120% of BEP flow"
CIT_RULES = "Sapphire standard 2026-11-01 (Water Engineering Settings house rules; confirm with the designers)"
CIT_BYPASS = "Minimum-flow bypass at 15% of BEP (DRC presets.js autoBypassMm; HI 9.6.3 minimum continuous flow)"

RULE_DEFAULTS = {
    "head_margin_pct": 8.0,  # DRC evalPump: what a regulator throttles away as the strainer clogs
    "npsh_margin_ft": NPSH_DEFAULT_MARGIN_FT,
    "bep_min": 0.70,
    "bep_max": 1.20,
    "grad_max": 0.04,  # ft of head per ft of pipe (DRC pickPipe)
    "drc_v_max_ms": 1.5,  # m/s, where DOC-0049 has no limit for the material
    "freeboard_multiplier": 1.3,  # house rule (DRC autoBasin)
    "depth_margin_in": 1.2,  # water over the submergence requirement (DRC: 0.03 m)
    "min_depth_in": 4.0,  # DRC: 0.1 m
    "min_freeboard_in": 2.0,  # DRC: 0.05 m
    "bypass_fraction": 0.15,
    "over_ratio": 0.10,  # an uncontrolled pump more than 10% over design gets a control
    "vfd_min_hp": 1.0,  # below this a PRV, at and above it a VFD
    "valve_close_ms": 5.0,  # solenoid closure (DRC valveCloseMs)
    "pump_stop_ms": 2000.0,  # pump trip run-down when nothing on the line slams shut
    "surge_allow_psi": None,  # None: the line's rating less its operating pressure
    "design_temp_f": None,  # None: the site's water temperature, for pipe ratings
}


def _rules(rules: dict | None) -> dict:
    return {**RULE_DEFAULTS, **{k: v for k, v in (rules or {}).items() if v is not None or k in ("surge_allow_psi",)}}


# ---------------------------------------------------------------------------
# Pipe sizing
# ---------------------------------------------------------------------------


def drc_pick_pipe(material, q: float, *, v_max: float = 1.5, grad_max: float = 0.04, min_id_mm: float = 0.0, w: Water | None = None) -> dict | None:
    """``pickPipe``: DRC's smallest size with velocity <= ``v_max`` (m/s) and friction gradient <= ``grad_max``
    (m/m) at ``q`` (m^3/s), on DRC's 2-decimal bores; the largest when none passes."""
    w = w or water_si(20)
    key = resolve_material(material)
    if not key or not MATERIALS[key]["sizes"]:
        return None
    eps = MATERIALS[key]["eps_mm"]
    rows = [{"nps": n, "id_mm": _js.to_fixed(od - 2 * wall, 2)} for n, od, wall, _bore in MATERIALS[key]["sizes"]]
    for s in rows:
        if s["id_mm"] < min_id_mm:
            continue
        d = s["id_mm"] / 1000
        u = q / area_of_si(d)
        f = friction_factor_si((u * d) / w.nu, eps / s["id_mm"])
        if u <= v_max and (f / d) * (u * u) / (2 * G) <= grad_max:
            return s
    return rows[-1]


def _size_rows(material: str, flow_gpm: float, line: str, branches: int, w: Water, rules: dict) -> list[dict]:
    q = gpm_to_m3s(float(flow_gpm)) / max(1, int(branches or 1))
    out = []
    for s in sizes_for(material):
        spec = pipe_spec_si(material, s["nominal"])
        u = q / spec.area
        f = friction_factor_si((u * spec.id_m) / w.nu, spec.eps_mm / spec.id_mm) if u > 0 else 0.0
        grad = (f / spec.id_m) * u * u / (2 * G)
        if spec.max_discharge_fps is not None:
            limit_fps = spec.max_suction_fps if line == "suction" else spec.max_discharge_fps
            basis = f"DOC-0049 {line} limit"
        else:
            limit_fps = float(rules["drc_v_max_ms"]) / M_PER_FT
            basis = "DRC 1.5 m/s (no DOC-0049 limit for this material)"
        v_fps = u / M_PER_FT
        ok_v = v_fps <= limit_fps
        ok_g = grad <= float(rules["grad_max"])
        out.append({"nominal": s["nominal"], "id_in": spec.id_mm / MM_PER_IN, "velocity_fps": v_fps, "limit_fps": limit_fps,
                    "gradient_ft_ft": grad, "ok": ok_v and ok_g, "basis": basis,
                    "status": "Okay" if ok_v and ok_g else ("Over Velocity" if not ok_v else "Over Gradient")})
    return out


def pick_pipe(flow_gpm: float, material: str = "SCH40 PVC", line: str = "discharge", *, branches: int = 1, min_size: str | None = None,
              water_temp_f: float = 68.0, rules: dict | None = None) -> CalcResult:
    """The smallest size within the DOC-0049 velocity limit and the 0.04 ft/ft friction-gradient cap."""
    rr = _rules(rules)
    w = water_si(f_to_c(float(water_temp_f)))
    rows = _size_rows(material, flow_gpm, line, branches, w, rr)
    min_id = pipe_spec_si(material, min_size).id_mm / MM_PER_IN if min_size else 0.0
    pick = next((r for r in rows if r["ok"] and r["id_in"] >= min_id - 1e-9), None)
    warnings = []
    if not rows:
        return CalcResult(calc="pick_pipe", unit="nominal size", citations=[CIT_PICK], warnings=[f"No catalog sizes for {material!r}."])
    if pick is None:
        pick = rows[-1]
        warnings.append(f"No {material} size keeps {flow_gpm:g} GPM within the {line} limits; the largest is listed: split the flow.")
    workbook = size_pipe(float(flow_gpm) / max(1, int(branches or 1)), 100.0, resolve_material(material) or material, line)
    options = [CalcOption(key=r["nominal"], label=f"{r['nominal']} {material}", value=r["nominal"], recommended=r is pick, detail=r) for r in rows]
    return CalcResult(
        calc="pick_pipe",
        value=pick["nominal"],
        unit="nominal size",
        inputs={
            "flow": make_input(float(flow_gpm), "GPM", "prior_calc"),
            "material": make_input(material, "", "user"),
            "line": make_input(line, "", "user"),
            "branches": make_input(int(branches or 1), "", "user"),
            "gradient_cap": make_input(float(rr["grad_max"]), "ft/ft", "standard", "DRC pickPipe"),
        },
        formula="smallest size with V <= DOC-0049 limit(line) and f/D V^2/2g <= 0.04",
        steps=[f"{r['nominal']}: {r['velocity_fps']:.2f} ft/s (limit {r['limit_fps']:g}), {r['gradient_ft_ft']:.4f} ft/ft -> {r['status']}" for r in rows]
        + [f"workbook (velocity only, Hazen-Williams): {workbook.value}"],
        citations=[CIT_PICK, CIT_DRC],
        options=options,
        warnings=warnings,
        status=pick["status"],
    )


def pick_suction(flow_gpm: float, trunk_size: str | None, material: str = "SCH40 PVC", **kw) -> CalcResult:
    """Suction: within its own (4.5 ft/s) limit and at least one size above the discharge trunk."""
    sizes = [s["nominal"] for s in sizes_for(material)]
    up = None
    if trunk_size:
        trunk = pipe_spec_si(material, trunk_size).nominal
        if trunk in sizes:
            up = sizes[min(len(sizes) - 1, sizes.index(trunk) + 1)]
    r = pick_pipe(flow_gpm, material, "suction", min_size=up, **kw)
    r.steps.append(f"one size up from the {trunk_size or '?'} trunk: at least {up or 'any'}")
    return r


# ---------------------------------------------------------------------------
# Pump evaluation
# ---------------------------------------------------------------------------


def _terminal_flow(net) -> float:
    total = 0.0
    for t in net.terminals.values():
        for li in t["links"]:
            lk = net.links[li]
            if lk.status != CLOSED:
                total += lk.q
    return total


def _design_total(net) -> float | None:
    qs = [t["design_q"] for t in net.terminals.values() if t["design_q"]]
    return sum(qs) if qs else None


def _design_point(net, pump_ids: list, q_design: float) -> float | None:
    """The common speed at which the pumps deliver exactly ``q_design`` (controls off), or None when
    full speed falls short. Illinois regula falsi on speed; the solved state stays on ``net``."""
    targets = [pp for pp in net.pumps if pp.id in pump_ids]
    saved = [(pp, pp.control, pp.speed, pp.prv_drop) for pp in targets]
    for pp in targets:
        pp.control, pp.prv_drop = "none", 0.0

    def f(s: float) -> float:
        for pp in targets:
            pp.speed = s
        solve_network_si(net)
        return _terminal_flow(net) - q_design

    try:
        hi = 1.0
        f_hi = f(hi)
        if f_hi < 0:
            return None
        lo, f_lo = 0.05, f(0.05)
        if f_lo >= 0:
            return lo
        s = hi
        for _ in range(60):
            s = hi - f_hi * (hi - lo) / (f_hi - f_lo) if f_hi != f_lo else 0.5 * (lo + hi)
            if not (lo < s < hi):
                s = 0.5 * (lo + hi)
            fs = f(s)
            if abs(fs) <= 1e-10 * q_design or hi - lo < 1e-12:
                break
            if fs > 0:
                hi, f_hi = s, fs
                f_lo /= 2
            else:
                lo, f_lo = s, fs
                f_hi /= 2
        return s
    finally:
        for pp, control, speed, drop in saved:
            pp.control, pp.speed, pp.prv_drop = control, speed, drop


def _shutoff_and_surge(net, res, rr: dict, water_temp_f: float, switched: bool) -> list[dict]:
    """Pipe-pressure fails: the operating pressure, and shut-off head plus surge, against each segment's rating.

    The shut-off head is the pump's at its own full speed, not the speed a VFD settled on: a drive that
    fails (or is bypassed) runs it there, and the pipe has to hold that."""
    fails = []
    w = net.w
    rg = w.rho * G
    temp = float(rr["design_temp_f"] or water_temp_f)
    close_ms = float(rr["valve_close_ms"] if switched else rr["pump_stop_ms"])
    # Each segment sees the shut-off head of the pumps that can push into it (downstream of their outlet).
    out_links: dict = {}
    for i, lk in enumerate(net.links):
        if lk.kind in ("segment", "prv", "pump"):
            out_links.setdefault(lk.a, []).append(i)
            if lk.kind == "segment":
                out_links.setdefault(lk.b, []).append(i)  # a pipe carries either way (a ring)
    reach: dict = {}
    for pp in net.pumps:
        if not (pp.running and math.isfinite(net.nodes[pp.i_in].H)):
            continue
        h_pp = pp.h0_full + net.nodes[pp.i_in].H
        seen, stack = {pp.i_in}, [pp.i_out]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for li in out_links.get(node, ()):
                lk = net.links[li]
                if lk.kind == "pump":
                    continue  # another pump's own head is its own
                reach[li] = max(reach.get(li, -math.inf), h_pp)
                stack.append(lk.b if lk.a == node else lk.a)
    for li, lk in enumerate(net.links):
        if lk.kind != "segment" or lk.seg.spec.nominal is None or net.nodes[lk.b].kind == "pump_in":
            continue
        h_ref = reach.get(li)
        spec = lk.seg.spec
        rating = pipe_pressure_rating(spec.material, spec.nominal, temp).value
        if rating is None:
            continue
        ends = [net.nodes[lk.a], net.nodes[lk.b]]
        p_op = max((rg * (n.H - n.z) for n in ends if math.isfinite(n.H)), default=0.0) / PA_PER_PSI
        z_low = min(n.z for n in ends)
        surge = surge_for_si(abs(lk.q), w, close_ms / 1000, [lk.seg])["pa"] / PA_PER_PSI
        if p_op > rating:
            fails.append({"code": "pipe_pressure", "ref": lk.ref,
                          "text": f"segment {lk.ref} runs at {p_op:.0f} psi, over its {rating:g} psi rating"})
        if h_ref is not None:
            p_shut = rg * (h_ref - z_low) / PA_PER_PSI
            if p_shut + surge > rating:
                fails.append({"code": "shutoff_surge", "ref": lk.ref,
                              "text": f"segment {lk.ref}: shut-off {p_shut:.0f} psi + surge {surge:.0f} psi over its {rating:g} psi rating"})
    return fails


def control_node(net, pp, row: dict | None = None) -> int:
    """Where a control holds its pressure: the pump's ``control_node``, else the only terminal node unless
    it is an open discharge (pinned), else the pump's discharge. Matches ``build_network``'s default."""
    cn = (row or {}).get("control_node")
    if cn and cn in net.node_index:
        return net.node_index[cn]
    nodes = {t["node"] for t in net.terminals.values()}
    pinned = {t["node"] for t in net.terminals.values() if any(getattr(m, "p_fixed", None) is not None for m in _members(t["demand"]))}
    if len(nodes) == 1 and not (nodes & pinned):
        return net.node_index[next(iter(nodes))]
    return pp.discharge


def _node_ref(net, i: int) -> str:
    nd = net.nodes[i]
    return nd.ref if nd.kind.startswith("pump") else nd.id


def eval_pump(network: dict, curve, *, pump_ids: list | None = None, name: str = "", control: str | None = None,
              water_temp_f: float = 68.0, elevation_ft: float = 0.0, clog_pct: float = 0.0, rules: dict | None = None,
              vgb: list | None = None, hp: float | None = None, memo: dict | None = None) -> dict:
    """How a candidate curve runs this network, with the reasons it fails (``fails``, empty = ok).

    ``pump_ids``: the pump slots the candidate fills (default: every pump; identical pumps on a
    header). ``control``: ``none``, ``vfd`` or ``prv`` to try the candidate with that control holding
    the pressure it gives at the design flow (None: each pump's own control). ``vgb``: suction-outlet
    covers ``[{pump, cover_length_in, cover_width_in, open_area_fraction, outlets}]``, checked at the
    larger of design and operating flow. ``memo``: a dict shared by the trials of one curve, so a
    control trial reuses the uncontrolled design point instead of searching for it again."""
    rr = _rules(rules)
    ids = pump_ids or [p["id"] for p in network.get("pumps") or []]
    pc = curve if isinstance(curve, PumpCurve) else None
    curves = {pid: curve for pid in ids}
    overrides = {pid: {"control": "none", "setpoint_psi": None} for pid in ids} if control else {}
    fails: list[dict] = []

    def fail(code: str, text: str, ref: str = "") -> None:
        fails.append({"code": code, "ref": ref, "text": text})

    cached = memo.get("design") if memo is not None and control else None
    if cached is not None:
        q_des, margin_pct, setpoints = cached["q_des"], cached["margin_pct"], cached["setpoints"]
        fails.extend(cached["fails"])
        net = None
    else:
        net = build_network(network, water_temp_f=water_temp_f, elevation_ft=elevation_ft, clog_pct=clog_pct, curves=curves,
                            pump_overrides=overrides)
        solve_network_si(net)
        q_op = _terminal_flow(net)
        q_des = _design_total(net)
        margin_pct = None
        setpoints: dict = {}
    if cached is None and q_des:
        pump_states = {pp.id: (pp.speed, net.links[pp.link].q) for pp in net.pumps}
        s_star = _design_point(net, ids, q_des)
        if s_star is None:
            fail("head_margin", f"delivers {m3s_to_gpm(q_op):.1f} of the {m3s_to_gpm(q_des):.1f} GPM design flow at full speed")
            margin_pct = -100.0 * (1 - q_op / q_des)
        else:
            margins = []
            rows = {p["id"]: p for p in network.get("pumps") or []}
            for pp in net.pumps:
                if pp.id not in ids or pp.curve is None:
                    continue
                q_p = net.links[pp.link].q
                need = pump_head_si(pp.curve, q_p, s_star)
                have = pump_head_si(pp.curve, q_p, pump_states[pp.id][0])
                margins.append(100 * (have - need) / _js.jmax(need, 1e-6))
                ci = control_node(net, pp, rows.get(pp.id))
                nd = net.nodes[ci]
                setpoints[pp.id] = {"setpoint_psi": net.w.rho * G * (nd.H - nd.z) / PA_PER_PSI, "control_node": _node_ref(net, ci)}
            margin_pct = min(margins) if margins else None
            if margin_pct is not None and margin_pct < float(rr["head_margin_pct"]):
                fail("head_margin", f"only {max(0.0, margin_pct):.0f}% head to spare at the design flow "
                     f"({rr['head_margin_pct']:g}% keeps it working as the strainer clogs)")
    if memo is not None and control and cached is None:
        memo["design"] = {"q_des": q_des, "margin_pct": margin_pct, "setpoints": setpoints, "fails": list(fails)}
    if control and control != "none" and setpoints:
        overrides = {pid: {"control": control, **setpoints[pid]} for pid in ids if pid in setpoints}
        net = build_network(network, water_temp_f=water_temp_f, elevation_ft=elevation_ft, clog_pct=clog_pct, curves=curves,
                            pump_overrides=overrides)
    elif net is None:
        net = build_network(network, water_temp_f=water_temp_f, elevation_ft=elevation_ft, clog_pct=clog_pct, curves=curves,
                            pump_overrides=overrides)
    res = _report(net, solve_network_si(net), float(rr["npsh_margin_ft"]))
    q_op = _terminal_flow(net)
    for c in res.checks:
        if c["severity"] == BLOCKER and c["code"].split(".")[0] in ("pump", "terminal", "network"):
            fail(c["code"], c["title"][0].lower() + c["title"][1:], c["ref"])
        elif c["code"] in ("piping.velocity", "pump.npsh_margin"):
            fail(c["code"], c["title"][0].lower() + c["title"][1:], c["ref"])
    for pid in ids:
        p = res.pumps.get(pid) or {}
        ratio = p.get("bep_ratio")
        if ratio is not None and p.get("flow_gpm", 0) > 0 and not (float(rr["bep_min"]) <= ratio <= float(rr["bep_max"])):
            fail("bep_window", f"{pid} runs at {ratio:.0%} of its best-efficiency flow (keep {rr['bep_min']:.0%}-{rr['bep_max']:.0%})", pid)
    for cover in vgb or []:
        pid = cover.get("pump")
        q_gpm = max(res.pumps.get(pid, {}).get("flow_gpm", 0.0), m3s_to_gpm(q_des or 0) / max(1, len(ids)))
        r = suction_outlet_vgb(q_gpm, float(cover["cover_length_in"]), float(cover["cover_width_in"]), float(cover["open_area_fraction"]),
                               int(cover.get("outlets") or 1))
        if r.status != "Okay":
            why = r.status or ("cover too small to evaluate: " + "; ".join(r.warnings) if r.warnings else "cover too small to evaluate")
            fail("vgb", f"suction cover for {pid} at {q_gpm:.0f} GPM: {why}", pid or "")
    switched = any((t["row"].get("params") or {}).get("switched") for t in net.terminals.values())
    fails.extend(_shutoff_and_surge(net, res, rr, water_temp_f, switched))
    power_kw = sum(res.pumps[pid].get("electrical_kw") or 0.0 for pid in ids if pid in res.pumps)
    npsh = [res.pumps[pid].get("npsh_margin_ft") for pid in ids if res.pumps.get(pid, {}).get("npsh_margin_ft") is not None]
    short = max(0.0, 1 - q_op / q_des) if q_des else 0.0
    miss = 100.0 if any(f["code"] in ("pump.dead_head", "terminal.no_flow") for f in fails) else (
        len(fails) + short + (max(0.0, float(rr["head_margin_pct"]) - (margin_pct or 0)) / 100 if margin_pct is not None else 0))
    used = control if control else ",".join(sorted({res.pumps.get(pid, {}).get("control") or "none" for pid in ids}))
    return {
        "name": name or (pc.name if pc else ""), "ok": not fails, "fails": fails, "reasons": [f["text"] for f in fails],
        "control": used, "setpoints": setpoints if control and control != "none" else {},
        "flow_gpm": m3s_to_gpm(q_op), "design_gpm": m3s_to_gpm(q_des) if q_des else None, "margin_pct": margin_pct,
        "power_kw": power_kw, "npsh_margin_ft": min(npsh) if npsh else None, "hp": hp, "miss": miss, "network": res,
    }


def rank_pumps(network: dict, catalog: list[dict], *, pump_ids: list | None = None, generic_class: str | None = None,
               controls: list | None = None, rules: dict | None = None, **kw) -> list[dict]:
    """Every catalog curve through :func:`eval_pump`: passing pumps first (smallest motor, then lowest
    power at the duty: not the strongest, which overspeeds an uncontrolled system), then the rest,
    nearest miss first. ``catalog``: ``[{item, name, curve | points, shutoff_head_ft, max_flow_gpm,
    motor_eff_pct, hp, generic_class}]``. ``controls``: the controls to try in order, the first that
    passes kept (DRC ``rankPumps``); ``["auto"]`` tries none, then a PRV under ``vfd_min_hp`` or a VFD
    from it up; None evaluates each pump with its own control."""
    rr = _rules(rules)
    out = []
    for row in catalog or []:
        if generic_class and row.get("generic_class") and row["generic_class"] != generic_class:
            continue
        curve = _catalog_curve(row)
        if curve is None:
            continue
        tries = controls or [None]
        if tries == ["auto"]:
            tries = ["none", "vfd" if (row.get("hp") or 0) >= float(rr["vfd_min_hp"]) else "prv"]
        e = None
        memo: dict = {}
        for c in tries:
            trial = eval_pump(network, curve, pump_ids=pump_ids, name=row.get("name") or row.get("item") or "", hp=row.get("hp"),
                              control=c, rules=rules, memo=memo, **kw)
            if e is None or trial["ok"]:
                e = trial
            if trial["ok"]:
                break
        e["item"] = row.get("item")
        e["row"] = row
        out.append(e)
    big = 1e9
    out.sort(key=lambda e: (0, e["hp"] if e["hp"] is not None else big, e["power_kw"]) if e["ok"]
             else (1, e["miss"], e["hp"] if e["hp"] is not None else big))
    return out


def _curve_rows(pc: PumpCurve) -> list[dict]:
    """A PumpCurve back as Pump Curve Point rows (US), so the derived model stays plain JSON."""
    from .hydraulics.units import LPM_PER_GPM

    eff = dict(pc.eff_points)
    npshr = dict(pc.npshr_points)
    rows = []
    for lpm, h in pc.head_points:
        row = {"flow_gpm": lpm / LPM_PER_GPM, "head_ft": h / M_PER_FT}
        if lpm in eff:
            row["efficiency_pct"] = eff[lpm]
        if lpm in npshr:
            row["npshr_ft"] = npshr[lpm] / M_PER_FT
        rows.append(row)
    return rows


def _catalog_curve(row: dict) -> PumpCurve | None:
    if isinstance(row.get("curve"), PumpCurve):
        return row["curve"]
    pts = row.get("points") or row.get("curve") or []
    if isinstance(pts, dict):
        row = {**row, **pts}
        pts = pts.get("points") or []
    c = PumpCurve.from_us(list(pts), motor_eff_pct=float(row.get("motor_eff_pct") or 80.0), shutoff_head_ft=row.get("shutoff_head_ft"),
                          max_flow_gpm=row.get("max_flow_gpm"), name=row.get("name") or row.get("item") or "")
    return c if len(c.head_points) >= 2 or c.quad else None


# ---------------------------------------------------------------------------
# Bypass, surge, basin
# ---------------------------------------------------------------------------


def drc_auto_bypass_mm(curve: PumpCurve, control: str, speed: float | None = None) -> float:
    """``autoBypassMm``: the orifice (mm, up to 0.5 mm) passing 15% of BEP at shut-off; none with a VFD."""
    if control == "vfd":
        return 0
    s = curve.default_speed if speed is None else speed
    q_min = 0.15 * bep_flow_si(curve, s)
    h0 = pump_head_si(curve, 0, s)
    if not (q_min > 0 and h0 > 0):
        return 0
    a = q_min / (0.61 * math.sqrt(2 * G * h0))
    return ceil_to(math.sqrt((4 * a) / math.pi) * 1000, 0.5)


def auto_bypass(curve, control: str = "none", *, speed_pct: float | None = None, rules: dict | None = None) -> CalcResult:
    """The minimum-flow bypass orifice (in) for a pump; zero with a VFD."""
    rr = _rules(rules)
    pc = curve if isinstance(curve, PumpCurve) else _catalog_curve(curve if isinstance(curve, dict) else {"points": curve})
    s = float(speed_pct) / 100 if speed_pct is not None else pc.default_speed
    frac = float(rr["bypass_fraction"])
    if control == "vfd":
        mm = 0.0
    else:
        q_min = frac * bep_flow_si(pc, s)
        h0 = pump_head_si(pc, 0, s)
        mm = ceil_to(math.sqrt(4 * (q_min / (0.61 * math.sqrt(2 * G * h0))) / math.pi) * 1000, 0.5) if q_min > 0 and h0 > 0 else 0.0
    return CalcResult(
        calc="auto_bypass",
        value=mm / MM_PER_IN,
        unit="in",
        inputs={"control": make_input(control, "", "user"), "fraction": make_input(frac, "of BEP", "standard", CIT_BYPASS)},
        formula="A = Q_min / (0.61 sqrt(2 g H0)), Q_min = 15% Q_BEP ; d rounded up to 0.5 mm",
        steps=["VFD: the drive idles the pump at minimum speed instead of wasting flow" if control == "vfd" else
               f"Q_min {m3s_to_gpm(frac * bep_flow_si(pc, s)):.1f} GPM at H0 {pump_head_si(pc, 0, s) / M_PER_FT:.1f} ft -> {mm:g} mm ({mm / MM_PER_IN:.3f} in)"],
        citations=[CIT_BYPASS, CIT_DRC],
    )


def auto_surge(flow_gpm: float, supply: list, *, operating_psi: float, rating_psi: float | None, closure_ms: float, rules: dict | None = None,
               water_temp_f: float = 68.0, elevation_ft: float = 0.0) -> CalcResult:
    """An arrestor only when the surge would exceed the allowable; its stock gas volume and precharge."""
    rr = _rules(rules)
    r = surge_envelope(flow_gpm, supply, closure_ms=closure_ms, operating_psi=operating_psi, rating_psi=rating_psi,
                       allow_psi=rr.get("surge_allow_psi"), water_temp_f=water_temp_f, elevation_ft=elevation_ft)
    r.calc = "auto_surge"
    return r


def auto_basin(basin: dict, checks_value: dict, *, suction_id_in: float, rules: dict | None = None) -> dict:
    """Depth and freeboard (in) for a basin from its checks: water over the intake per HI 9.8 plus one pipe
    diameter and a margin; freeboard ``freeboard_multiplier`` x the drain-back rise (DRC ``autoBasin``)."""
    rr = _rules(rules)
    need = float(suction_id_in or 0) + float(checks_value.get("submergence_required_in") or 0) + float(rr["depth_margin_in"])
    depth = max(float(rr["min_depth_in"]), ceil_to(need, 0.5))
    free = max(float(rr["min_freeboard_in"]), ceil_to(float(rr["freeboard_multiplier"]) * float(checks_value.get("level_rise_in") or 0), 0.5))
    return {"depth_in": depth, "freeboard_in": free}


# ---------------------------------------------------------------------------
# derive: PLAN.md 2.2 steps 5-8
# ---------------------------------------------------------------------------


def _is_auto(setup: dict, key: str) -> bool:
    return (setup or {}).get(key, "auto") == "auto"


def _design_flows(network: dict, *, water_temp_f: float, provisional: str = '2"') -> tuple[dict, list]:
    """Each segment's design flow (GPM): pumps as ideal sources, terminals as fixed flows at their design
    flow; unsized segments at a provisional size, then once more at the sizes that gives (loops split by
    resistance). Returns ({segment id: gpm}, checks)."""
    checks = []
    net = copy.deepcopy(network)
    terms = []
    probe = build_network(network, water_temp_f=water_temp_f) if network.get("pumps") else None
    for t in net.get("terminals") or []:
        tid = t.get("id") or t.get("node")
        dq = probe.terminals[tid]["design_q"] if probe else None
        if not dq:
            checks.append(make_check("piping.no_design_flow", WARNING, f"Terminal {tid} has no design flow",
                                     "Its pipes are sized without it: give the feature a flow.", "piping", ref=tid))
            continue
        terms.append({"id": tid, "node": probe.terminals[tid]["node"], "feature": t.get("feature"), "kind": "fixed_flow",
                      "params": {"flow_gpm": m3s_to_gpm(dq), "min_psi": 0}})
    net["terminals"] = terms
    total = sum(t["params"]["flow_gpm"] for t in terms) or 1.0
    for p in net.get("pumps") or []:
        p.update(curve={"points": [], "shutoff_head_ft": 3000.0, "max_flow_gpm": 20 * total}, control="none", bypass_in=0, speed_pct=100,
                 running=True)
    for s in net.get("segments") or []:
        if not s.get("nominal_size") and not s.get("id_in"):
            s["nominal_size"] = provisional
    flows: dict = {}
    for _ in range(2):
        n2 = build_network(net, water_temp_f=water_temp_f)
        solve_network_si(n2)
        flows = {lk.ref: abs(m3s_to_gpm(lk.q)) for lk in n2.links if lk.kind == "segment"}
        for s, orig in zip(net.get("segments") or [], network.get("segments") or [], strict=False):
            if not orig.get("nominal_size") and not orig.get("id_in"):
                line = s.get("line") or ("suction" if (s.get("to") in {p["id"] for p in net.get("pumps") or []}) else "discharge")
                s["nominal_size"] = pick_pipe(flows.get(s.get("id"), 0.0), s.get("material") or "SCH40 PVC", line,
                                              branches=int(s.get("branches") or 1), water_temp_f=water_temp_f).value
    return flows, checks


def _finite(x):
    """The result tree with every non-finite float as None, so it is strict JSON (``allow_nan=False``)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x


def derive(model: dict, setup: dict | None = None, catalogs: dict | None = None, rules: dict | None = None) -> dict:
    """Run PLAN.md 2.2 steps 5-8 and return ``{model, results, checks, network, ranking}``.

    ``model``: design model schema 2 (PLAN.md 3.3). ``setup``: section -> ``auto`` | ``custom``
    (default ``model["setup"]``); only ``auto`` sections are written. ``catalogs``: ``{"pumps": [...]}``
    (see :func:`rank_pumps`). ``rules``: house rules over :data:`RULE_DEFAULTS`. ``results`` is a list
    of ``CalcResult`` dicts in ladder order; ``checks`` every typed check (``hydraulics._checks``).

    The page calls this on every edit, so a half-drawn model never raises: anything the network cannot
    be built from comes back as one ``network.invalid`` blocker with the model untouched. The output
    is strict JSON (an infinite figure is None)."""
    try:
        out = _derive(model, setup, catalogs, rules)
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        bad = make_check("network.invalid", BLOCKER, "The network cannot be solved yet", str(exc), "piping")
        out = {"model": copy.deepcopy(model), "results": [], "checks": [bad], "network": None, "ranking": []}
    return _finite(out)


def _derive(model: dict, setup: dict | None, catalogs: dict | None, rules: dict | None) -> dict:
    rr = _rules(rules)
    derived = copy.deepcopy(model)
    setup = setup if setup is not None else (derived.get("setup") or {})
    site = derived.get("site") or {}
    temp = float(site.get("water_temp_f") or 68.0)
    elev = float(site.get("elevation_ft") or 0.0)
    air = float(site.get("air_temp_f") or 72.0)
    rh = float(site.get("rh_pct") or 40.0)
    wind = float(site.get("wind_mph") or 0.0)
    hours = float(site.get("hours_per_day") or 12.0)
    net_d = derived.get("network") or {}
    results: list = []
    checks: list = []
    pump_ids = [p["id"] for p in net_d.get("pumps") or []]
    seg_rows = net_d.get("segments") or []
    suction_to = set(pump_ids)

    def line_of(s: dict) -> str:
        return s.get("line") or ("suction" if s.get("to") in suction_to else "discharge")

    # Design flows per segment (used by step 5's volumes and step 6's sizes, so the two agree).
    flows, fchecks = _design_flows(net_d, water_temp_f=temp) if pump_ids else ({}, [])
    checks.extend(fchecks)

    # ---- step 6 is needed for the volumes of step 5: size first, write only if piping is auto
    sized: dict = {}
    trunk_of: dict = {}
    for s in seg_rows:
        if line_of(s) == "discharge" and s.get("from") in suction_to:
            trunk_of.setdefault(s["from"], s.get("nominal_size"))
    for s in seg_rows:
        if s.get("nominal_size") or s.get("id_in"):
            continue
        gpm = flows.get(s.get("id"), 0.0)
        mat = s.get("material") or "SCH40 PVC"
        if line_of(s) == "suction":
            continue  # after the trunks are known
        r = pick_pipe(gpm, mat, "discharge", branches=int(s.get("branches") or 1), water_temp_f=temp, rules=rr)
        sized[s.get("id")] = r
        if s.get("from") in suction_to and not trunk_of.get(s["from"]):
            trunk_of[s["from"]] = r.value
    for s in seg_rows:
        if (s.get("nominal_size") or s.get("id_in")) or line_of(s) != "suction":
            continue
        r = pick_suction(flows.get(s.get("id"), 0.0), trunk_of.get(s.get("to")), s.get("material") or "SCH40 PVC",
                         branches=int(s.get("branches") or 1), water_temp_f=temp, rules=rr)
        sized[s.get("id")] = r
    proposed = [dict(s, nominal_size=sized[s.get("id")].value) if s.get("id") in sized else s for s in seg_rows]

    # ---- step 5: basin and surge volumes
    basins = derived.get("basins") or []
    q_total = m3s_to_gpm(_design_total(build_network(net_d, water_temp_f=temp)) or 0.0) if pump_ids else 0.0
    features = derived.get("features") or []
    for b in basins:
        bid = b.get("id")
        node_ids = {n["id"] for n in net_d.get("nodes") or [] if n.get("kind") == "basin" and (n.get("basin") == bid or n["id"] == bid)}
        suction = next((s for s in proposed if line_of(s) == "suction" and s.get("from") in node_ids), None)
        supply = [s for s in proposed if line_of(s) == "discharge"]
        in_flight = []
        for f in features:
            if f.get("basin") not in (bid, None):
                continue
            fl = (f.get("in_flight") or {})
            fall_s = fl.get("fall_s")
            if fall_s is None and (f.get("inputs") or {}).get("fall_ft"):
                fall_s = math.sqrt(2 * float(f["inputs"]["fall_ft"]) * M_PER_FT / G)
            if fall_s:
                in_flight.append({"flow_gpm": fl.get("flow_gpm") or q_total / max(1, len(features)), "fall_s": fall_s})
        orifices = [float((t.get("params") or {}).get("orifice_diameter_in")) for t in net_d.get("terminals") or []
                    if (t.get("params") or {}).get("orifice_diameter_in")]
        sprays = [float(rr[f"spray_loss_pct_{f.get('family')}"]) for f in features
                  if f.get("basin") in (bid, None) and rr.get(f"spray_loss_pct_{f.get('family')}") is not None]
        spray_pct = max(sprays) if sprays else 0.5
        bc = basin_checks(b, flow_gpm=q_total, supply=supply, suction=suction, in_flight=in_flight, orifice_in=min(orifices) if orifices else None,
                          water_temp_f=temp, air_temp_f=air, rh_pct=rh, wind_mph=wind, hours_per_day=hours, spray_pct=spray_pct)
        if _is_auto(setup, "basin"):
            # With no suction segment (a pump drawing straight from the basin) there is no intake to submerge.
            sid_in = pipe_spec_si(suction.get("material") or "SCH40 PVC", suction.get("nominal_size")).id_mm / MM_PER_IN if suction else 0.0
            if suction is None:
                checks.append(make_check("basin.no_intake", INFO, f"Basin {bid}: no suction segment, so intake submergence is not checked",
                                         "A submersible or flooded pump: set its depth over the intake from the pump's manual.", "basin", ref=bid or ""))
            pick = auto_basin(b, bc.value, suction_id_in=sid_in, rules=rr)
            b.update(pick)
            bc = basin_checks(b, flow_gpm=q_total, supply=supply, suction=suction, in_flight=in_flight,
                              orifice_in=min(orifices) if orifices else None, water_temp_f=temp, air_temp_f=air, rh_pct=rh, wind_mph=wind,
                              hours_per_day=hours, spray_pct=spray_pct)
            bc.steps.append(f"auto: depth {pick['depth_in']:g} in, freeboard {pick['freeboard_in']:g} in")
        results.append(bc)
        checks.extend(bc.value["checks"])

    # ---- step 6: routes and pipe sizes
    if _is_auto(setup, "piping"):
        for s in seg_rows:
            if s.get("id") in sized:
                s["nominal_size"] = sized[s.get("id")].value
        results.extend(sized.values())
    else:
        for s in seg_rows:
            if not (s.get("nominal_size") or s.get("id_in")):
                checks.append(make_check("piping.unsized", BLOCKER, f"Segment {s.get('id')} has no size",
                                         "Piping is custom: pick its size, or set piping to auto.", "piping", ref=s.get("id") or ""))
        seg_rows = proposed  # solve with the proposal; the model keeps the designer's (unsized) rows
    net_solve = dict(net_d, segments=seg_rows)  # shares the pumps list, so pump picks land in the model

    # ---- step 7: operating point and pump
    ranking = []
    chosen_control = False
    vgb = derived.get("vgb") or net_d.get("suction_outlets") or []
    if _is_auto(setup, "pump") and pump_ids:
        catalog = (catalogs or {}).get("pumps") or []
        klass = next((p.get("generic_class") for p in net_d.get("pumps") or [] if p.get("generic_class")), None)
        ranking = rank_pumps(net_solve, catalog, pump_ids=pump_ids, generic_class=klass, water_temp_f=temp, elevation_ft=elev, rules=rr, vgb=vgb,
                             controls=["auto"] if _is_auto(setup, "control") else None)
        if ranking:
            best = ranking[0]
            for p in net_d.get("pumps") or []:
                p["item"] = best.get("item")
                raw = best["row"].get("points") or best["row"].get("curve")
                if isinstance(raw, PumpCurve):
                    raw = _curve_rows(raw)
                if not raw:  # a generic estimate: only a shut-off head and a maximum flow on file
                    raw = {"points": [], **{k: best["row"].get(k) for k in ("shutoff_head_ft", "max_flow_gpm", "motor_eff_pct")}}
                p["curve"] = raw
                for k in ("shutoff_head_ft", "max_flow_gpm", "motor_eff_pct"):
                    if best["row"].get(k) is not None:
                        p[k] = best["row"][k]
                if _is_auto(setup, "control"):
                    sp = best["setpoints"].get(p["id"])
                    p["control"] = best["control"] if sp else "none"
                    p["setpoint_psi"] = round(sp["setpoint_psi"], 2) if sp else None
                    if sp:
                        p["control_node"] = sp["control_node"]
            chosen_control = bool(best["setpoints"])
            results.append(CalcResult(
                calc="rank_pumps", value=[{"item": e.get("item"), "name": e["name"], "ok": e["ok"], "reasons": e["reasons"],
                                           "flow_gpm": e["flow_gpm"], "margin_pct": e["margin_pct"], "power_kw": e["power_kw"],
                                           "npsh_margin_ft": e["npsh_margin_ft"]} for e in ranking],
                unit="", inputs={"candidates": make_input(len(ranking), "", "lookup", "pump catalog")},
                formula="each curve through the network solve; fails = margin, NPSH, BEP window, run-out, velocity, VGB, shut-off + surge",
                steps=[f"{e['name'] or e.get('item')}: {'ok' if e['ok'] else '; '.join(e['reasons'])}" for e in ranking[:10]],
                citations=[CIT_POR, CIT_RULES, CIT_DRC], status="Okay" if best["ok"] else "No Pump Passes",
                warnings=[] if best["ok"] else [f"No catalog pump passes; the nearest is {best['name'] or best.get('item')}: {'; '.join(best['reasons'])}"]))
            if not best["ok"]:
                checks.append(make_check("pump.none_passes", BLOCKER, "No catalog pump passes every check",
                                         f"Nearest: {best['name'] or best.get('item')} ({'; '.join(best['reasons'])}). Add a pump to the catalog or change the design.",
                                         "pump", value={"reasons": best["reasons"]}))
        else:
            checks.append(make_check("pump.no_catalog", WARNING, "No pump catalog to choose from",
                                     "The pumps keep the curves on the model; add catalog pumps to let the engine pick.", "pump"))

    def solve() -> tuple:
        net = build_network(net_solve, water_temp_f=temp, elevation_ft=elev)
        st = solve_network_si(net)
        return net, _report(net, st, float(rr["npsh_margin_ft"]))

    net, res = solve() if pump_ids else (None, None)

    # ---- step 8: control, bypass, surge
    if net is not None:
        q_des = _design_total(net)
        q_op = _terminal_flow(net)
        if _is_auto(setup, "control") and not chosen_control:
            over = q_des and q_op > (1 + float(rr["over_ratio"])) * q_des
            s_star = _design_point(net, pump_ids, q_des) if over else None
            for p in net_d.get("pumps") or []:
                if over and s_star is not None:
                    pp = next(x for x in net.pumps if x.id == p["id"])
                    ci = control_node(net, pp, p)
                    nd = net.nodes[ci]
                    p["control_node"] = _node_ref(net, ci)
                    set_psi = net.w.rho * G * (nd.H - nd.z) / PA_PER_PSI
                    hp = (res.pumps.get(p["id"]) or {}).get("shaft_hp") or 0
                    p["control"] = "vfd" if hp >= float(rr["vfd_min_hp"]) else "prv"
                    p["setpoint_psi"] = round(set_psi, 2)
                else:
                    p["control"], p["setpoint_psi"] = "none", None
            if over:
                checks.append(make_check("control.added", INFO, f"Control added: the pump delivers {m3s_to_gpm(q_op):.0f} GPM against {m3s_to_gpm(q_des):.0f} GPM design",
                                         "A VFD (or a PRV on a small pump) holds the design pressure.", "control"))
        switched = any((t.get("params") or {}).get("switched") for t in net_d.get("terminals") or [])
        if _is_auto(setup, "bypass"):
            for p in net_d.get("pumps") or []:
                pp = next(x for x in net.pumps if x.id == p["id"])
                if switched and pp.curve is not None:
                    r = auto_bypass(pp.curve, p.get("control") or "none", rules=rr)
                    p["bypass_in"] = r.value
                    results.append(r)
                else:
                    p["bypass_in"] = 0
        net, res = solve()
        if _is_auto(setup, "surge"):
            arrestors = []
            close_ms = float(rr["valve_close_ms"] if switched else rr["pump_stop_ms"])
            for p in net_d.get("pumps") or []:
                line = [s for s in seg_rows if s.get("from") == p["id"]]
                frontier = [s.get("to") for s in line]
                seen = {p["id"]}
                while frontier:  # follow single runs downstream: the trunk the hammer travels
                    nxt = frontier.pop()
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    outs = [s for s in seg_rows if s.get("from") == nxt]
                    if len(outs) == 1:
                        line.append(outs[0])
                        frontier.append(outs[0].get("to"))
                if not line:
                    continue
                pr = res.pumps.get(p["id"]) or {}
                specs = [Segment.from_us(s).spec for s in line]
                ratings = [pipe_pressure_rating(sp.material, sp.nominal, temp).value for sp in specs if sp.nominal]
                rating = min((x for x in ratings if x), default=None)
                r = auto_surge(pr.get("flow_gpm") or 0.0, line, operating_psi=pr.get("discharge_psi") or 0.0, rating_psi=rating,
                               closure_ms=close_ms, rules=rr, water_temp_f=temp, elevation_ft=elev)
                results.append(r)
                if r.value["arrestor_gas_l"]:
                    arrestors.append({"pump": p["id"], "gas_l": r.value["arrestor_gas_l"], "precharge_psi": r.value["precharge_psi"]})
                    checks.append(make_check("surge.arrestor", INFO, f"Arrestor on {p['id']}: {r.value['arrestor_gas_l']:g} L gas",
                                             f"Surge {r.value['surge_psi']:.0f} psi would exceed the allowable; precharge {r.value['precharge_psi']:.0f} psi.",
                                             "surge", ref=p["id"]))
            derived["surge"] = {"arrestors": arrestors, "closure_ms": close_ms}
        op = CalcResult(calc="network_operating_point", value=res.to_dict(), unit="",
                        inputs={"water_temp": make_input(temp, "degF", "user"), "elevation": make_input(elev, "ft", "user")},
                        formula="h_link(Q) = H_from - H_to ; sum Q = 0 at every node (global gradient)",
                        steps=[f"{pid}: {v.get('flow_gpm', 0):.1f} GPM @ {v.get('head_ft') or 0:.1f} ft ({v.get('status')})" for pid, v in res.pumps.items()],
                        citations=[CIT_DRC], status="Okay" if not any(c["severity"] == BLOCKER for c in res.checks) else "Blocked")
        results.append(op)
        checks.extend(res.checks)
    derived["network"] = net_d
    seen_keys = set()
    unique = []
    for c in checks:
        if c["key"] not in seen_keys:
            seen_keys.add(c["key"])
            unique.append(c)
    return {
        "model": derived,
        "results": [r.to_dict() for r in results],
        "checks": unique,
        "network": res.to_dict() if res is not None else None,
        "ranking": [{k: v for k, v in e.items() if k not in ("network", "row")} for e in ranking],
    }


__all__ = [
    "RULE_DEFAULTS", "auto_basin", "auto_bypass", "auto_surge", "control_node", "derive", "drc_auto_bypass_mm", "drc_pick_pipe", "eval_pump",
    "pick_pipe", "pick_suction", "rank_pumps",
]
