"""Fitting losses: Darby 3-K, Crane L/D, fixed K, the clog factor, and equipment curves.

Port of Digital Rain Curtain ``src/hydro/common.js`` ``FITTINGS``, ``fittingK``,
``fitSumK``; golden file ``tests/golden/drc/fittings.json`` (1e-12).

Why 3-K instead of the workbook's fixed K: DOC-0049 ``H - TDH`` gives one K per
fitting (a 90 deg elbow is 0.81), which fits roughly 3/4"-1" pipe. A real fitting's
K falls with size and with Reynolds number; Darby's 3-K form
``K = K1/Re + Ki (1 + Kd / Dn^0.3)`` (Dn in inches) captures both, so a 4" elbow
comes out near 0.5. Fixed K therefore overstates large-pipe losses (conservative,
but it inflates pumps on large features) and understates small bores at low Re.

* :data:`FITTINGS` keeps DRC's entries in DRC's order, with snake_case keys, then
  appends the DOC-0049 fittings DRC has no form for (mitered elbows, the entrance
  shapes, exit, reducers and increasers by diameter ratio). Those are geometric
  losses with no Reynolds dependence worth modelling, so they keep DOC-0049's K.
* :data:`FITTING_K_TO_3K` maps every ``engine/data/fittings.py`` ``FITTING_K`` name
  to a key here, so a segment written for the workbook TDH runs through this
  module unchanged.
* Equipment (filters, skimmers, multiport valves, heaters) keeps the manufacturer
  head-loss curves in ``engine/data/fittings.py`` ``COMPONENT_CURVES``; the clog
  factor ``1 / (1 - clog)^2`` that DRC applies to strainers is applied to the
  filter curves too, giving a clean-to-dirty spread.
"""

from __future__ import annotations

from ..data.fittings import COMPONENT_COEFF, COMPONENT_CURVES, FITTING_K
from ..envelope import CalcResult, make_input
from ..tdh import _interp_curve  # the workbook's curve semantics: scale from 0 below, extend above
from . import _js
from .units import CIT_DRC, MM_PER_IN

CIT_DARBY = "Darby, R. (1999) 'Correlate pressure drops through fittings', Chem. Eng. 106(7) (3-K method)"
CIT_CRANE = "Crane TP-410 (equivalent length L/D times fully rough f_T)"
CIT_TDH_K = "DOC-0049 / H - TDH (fixed K, B26:C53)"
CIT_COMPONENTS = "DOC-0049 / 7 - Component Loss (manufacturer curves)"

# k3 = Darby [K1, Ki, Kd]; ld = Crane L/D; k = fixed K; filter = scaled by clogging.
# All K values are referred to the velocity in the pipe they sit in.
FITTINGS: dict[str, dict] = {
    # --- DRC common.js, in its order ---
    "elbow_90": {"label": "90° elbow", "k3": (800, 0.14, 4.0)},
    "elbow_90_lr": {"label": "90° sweep (long radius)", "k3": (800, 0.071, 4.2)},
    "elbow_45": {"label": "45° elbow", "k3": (500, 0.071, 4.2)},
    "bend_180": {"label": "180° return bend", "k3": (1000, 0.23, 4.0)},
    "tee_run": {"label": "Tee, straight through", "k3": (200, 0.091, 4.0)},
    "tee_branch": {"label": "Tee, through the branch", "k3": (500, 0.274, 4.0)},
    "coupling": {"label": "Coupling / union", "k": 0.04},
    "ball": {"label": "Ball / plug valve (open)", "k3": (300, 0.084, 3.9)},
    "gate": {"label": "Gate valve (open)", "k3": (300, 0.037, 3.9)},
    "globe": {"label": "Globe valve (open)", "k3": (1500, 1.7, 3.6)},
    "butterfly": {"label": "Butterfly valve (open)", "k3": (800, 0.25, 4.0)},
    "check": {"label": "Swing check valve", "k3": (1500, 0.46, 4.0)},
    "lift_check": {"label": "Spring / lift check valve", "k3": (2000, 2.85, 3.8)},
    "foot_valve": {"label": "Foot valve with strainer", "ld": 420, "filter": True},
    "strainer": {"label": "Y-strainer (clean)", "k": 2.0, "filter": True},
    "filter": {"label": "Cartridge filter (clean, approx.)", "k": 10, "filter": True},
    "quick": {"label": "Cam-lock / quick-disconnect", "k": 0.8},
    "barb": {"label": "Hose barb (insert fitting)", "k": 0.5},
    "reducer": {"label": "Reducer (one size down)", "k": 0.15},
    "expander": {"label": "Expansion (one size up)", "k": 0.2},
    "entrance": {"label": "Pipe entrance from basin", "k": 0.5},
    # --- DOC-0049 H - TDH fittings with no 3-K form: fixed K, as the workbook ---
    "elbow_90_mitered": {"label": "90° mitered elbow", "k": FITTING_K["ELL 90 MITERED"], "source": "DOC-0049"},
    "elbow_45_mitered": {"label": "45° mitered elbow", "k": FITTING_K["ELL 45 MITERED"], "source": "DOC-0049"},
    "entrance_reentrant": {"label": "Entrance, re-entrant", "k": FITTING_K["ENTRANCE REENTRANT"], "source": "DOC-0049"},
    "entrance_r002": {"label": "Entrance, rounded r/D 0.02", "k": FITTING_K["ENTRANCE r/D=0.02"], "source": "DOC-0049"},
    "entrance_r006": {"label": "Entrance, rounded r/D 0.06", "k": FITTING_K["ENTRANCE r/D=0.06"], "source": "DOC-0049"},
    "entrance_r015": {"label": "Entrance, rounded r/D >= 0.15", "k": FITTING_K["ENTRANCE r/D>=0.15"], "source": "DOC-0049"},
    "exit": {"label": "Exit into a basin", "k": FITTING_K["EXIT"], "source": "DOC-0049"},
    "reducer_075": {"label": "Reducer d1/d2 0.75", "k": FITTING_K["REDUCER d1/d2=0.75"], "source": "DOC-0049"},
    "reducer_050": {"label": "Reducer d1/d2 0.50", "k": FITTING_K["REDUCER d1/d2=0.50"], "source": "DOC-0049"},
    "reducer_025": {"label": "Reducer d1/d2 0.25", "k": FITTING_K["REDUCER d1/d2=0.25"], "source": "DOC-0049"},
    "reducer_010": {"label": "Reducer d1/d2 0.10", "k": FITTING_K["REDUCER d1/d2=0.10"], "source": "DOC-0049"},
    "increaser_133": {"label": "Increaser d1/d2 1.33", "k": FITTING_K["INCREASER d1/d2=1.33"], "source": "DOC-0049"},
    "increaser_200": {"label": "Increaser d1/d2 2.00", "k": FITTING_K["INCREASER d1/d2=2.00"], "source": "DOC-0049"},
    "increaser_400": {"label": "Increaser d1/d2 4.00", "k": FITTING_K["INCREASER d1/d2=4.00"], "source": "DOC-0049"},
    "increaser_1000": {"label": "Increaser d1/d2 10.0", "k": FITTING_K["INCREASER d1/d2=10.0"], "source": "DOC-0049"},
}

# engine/data/fittings.py FITTING_K name -> FITTINGS key. Every workbook name has a home here.
FITTING_K_TO_3K: dict[str, str] = {
    "ELL 90": "elbow_90",
    "ELL 90 LONG RADIUS": "elbow_90_lr",
    "ELL 90 MITERED": "elbow_90_mitered",
    "ELL 45": "elbow_45",
    "ELL 45 MITERED": "elbow_45_mitered",
    "TEE LINE 180": "tee_run",
    "TEE BRANCH 90": "tee_branch",
    "BUTTERFLY VALVE": "butterfly",
    "BALL VALVE": "ball",
    "GLOBE VALVE": "globe",
    "GATE VALVE": "gate",
    "SWING CHECK VALVE": "check",
    "ENTRANCE REENTRANT": "entrance_reentrant",
    "ENTRANCE FLUSH": "entrance",
    "ENTRANCE r/D=0.02": "entrance_r002",
    "ENTRANCE r/D=0.06": "entrance_r006",
    "ENTRANCE r/D>=0.15": "entrance_r015",
    "EXIT": "exit",
    "REDUCER d1/d2=0.75": "reducer_075",
    "REDUCER d1/d2=0.50": "reducer_050",
    "REDUCER d1/d2=0.25": "reducer_025",
    "REDUCER d1/d2=0.10": "reducer_010",
    "INCREASER d1/d2=1.33": "increaser_133",
    "INCREASER d1/d2=2.00": "increaser_200",
    "INCREASER d1/d2=4.00": "increaser_400",
    "INCREASER d1/d2=10.0": "increaser_1000",
}

# DRC's camelCase keys (saved DRC projects, the goldens) -> FITTINGS keys.
DRC_KEYS: dict[str, str] = {
    "elbow90": "elbow_90", "elbow90lr": "elbow_90_lr", "elbow45": "elbow_45", "bend180": "bend_180",
    "teeRun": "tee_run", "teeBranch": "tee_branch", "coupling": "coupling", "ball": "ball", "gate": "gate",
    "globe": "globe", "butterfly": "butterfly", "check": "check", "liftCheck": "lift_check",
    "footValve": "foot_valve", "strainer": "strainer", "filter": "filter", "quick": "quick", "barb": "barb",
    "reducer": "reducer", "expander": "expander", "entrance": "entrance",
}


def resolve_fitting(key) -> str | None:
    """A FITTINGS key for a FITTINGS key, a DRC key, or a workbook FITTING_K name; else None."""
    if not isinstance(key, str):
        return None
    if key in FITTINGS:
        return key
    return DRC_KEYS.get(key) or FITTING_K_TO_3K.get(key)


def fitting_k_si(key: str, re: float, id_mm: float) -> float:
    """``fittingK``: K of one fitting at Reynolds number ``re`` in a bore of ``id_mm``."""
    f = FITTINGS.get(key)
    if not f:
        return 0.0
    if "k3" in f:
        k1, ki, kd = f["k3"]
        dn = _js.jmax(id_mm, 1) / 25.4
        return k1 / _js.jmax(re, 1) + ki * (1 + kd / dn**0.3)
    if "ld" in f:
        f_t = 0.25 / _js.jlog10(0.045 / (3.7 * _js.jmax(id_mm, 1))) ** 2  # f_T: clean steel, fully rough
        return f["ld"] * f_t
    return _js.or_(f.get("k"), 0.0)


def _pairs(fit) -> list[tuple]:
    """A fittings map as ordered (key, count) pairs: a dict (insertion order) or a list of pairs / rows."""
    if not fit:
        return []
    if isinstance(fit, dict):
        return list(fit.items())
    out = []
    for row in fit:
        if isinstance(row, dict):
            out.append((row.get("key") or row.get("type"), row.get("qty", row.get("count", 1))))
        else:
            out.append((row[0], row[1]))
    return out


def fit_sum_k_si(fit, re: float, id_mm: float, clog: float = 0.0) -> float:
    """``fitSumK``: sum of K over ``{key: count}`` in insertion order; clog (0-0.9) shrinks filter area.

    Keys may be FITTINGS keys, DRC keys or workbook names; unknown keys and counts
    that are not > 0 are skipped, as in DRC."""
    total = 0.0
    for raw, n in _pairs(fit):
        key = resolve_fitting(raw)
        n = _js.num(n)
        if not (n > 0) or key is None:
            continue
        k = fitting_k_si(key, re, id_mm)
        if FITTINGS[key].get("filter"):
            k /= (1 - _js.jmin(0.9, _js.jmax(0.0, clog))) ** 2
        total += n * k
    return total


def is_filter_component(name: str) -> bool:
    """Equipment whose loss rises as it loads (the clog factor applies): every filter curve."""
    return "FILTER" in (name or "").upper()


def component_loss_ft(name: str, flow_gpm: float, clog: float = 0.0) -> tuple[float, str | None]:
    """Head loss (ft) of one piece of equipment at ``flow_gpm``, and a warning (or None).

    Interpolates the DOC-0049 sheet 7 manufacturer curve, falls back to the linear
    ft/GPM coefficient, and divides a filter's loss by (1 - clog)^2."""
    curve = COMPONENT_CURVES.get(name)
    warning = None
    if curve:
        h = _interp_curve(curve["points"], flow_gpm)
        if curve.get("max_gpm") and flow_gpm > curve["max_gpm"]:
            warning = f"{name} is rated to {curve['max_gpm']:g} GPM but carries {flow_gpm:.1f} GPM."
    elif name in COMPONENT_COEFF:
        h = COMPONENT_COEFF[name] * flow_gpm
    else:
        return 0.0, f"Unknown component {name!r} ignored."
    if is_filter_component(name) and clog:
        h /= (1 - _js.jmin(0.9, _js.jmax(0.0, clog))) ** 2
    return h, warning


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


def fitting_k(key: str, reynolds: float, id_in: float) -> CalcResult:
    """K of one fitting (Darby 3-K, Crane L/D or fixed) at a Reynolds number and bore (in)."""
    k_name = resolve_fitting(key)
    id_mm = float(id_in) * MM_PER_IN
    k = fitting_k_si(k_name, float(reynolds), id_mm) if k_name else 0.0
    f = FITTINGS.get(k_name or "", {})
    if "k3" in f:
        formula, cite = "K = K1/Re + Ki (1 + Kd / Dn^0.3), Dn in inches", CIT_DARBY
        step = f"K1, Ki, Kd = {f['k3']} ; Dn = {max(id_mm, 1) / 25.4:.3f} in"
    elif "ld" in f:
        formula, cite = "K = (L/D) f_T, f_T = 0.25 / log10(0.045 mm / (3.7 D))^2", CIT_CRANE
        step = f"L/D = {f['ld']}"
    else:
        formula, cite = "K fixed", CIT_TDH_K if f.get("source") == "DOC-0049" else CIT_DRC
        step = "fixed K"
    return CalcResult(
        calc="fitting_k",
        value=k,
        unit="",
        inputs={
            "fitting": make_input(key, "", "user"),
            "reynolds": make_input(float(reynolds), "", "prior_calc"),
            "id": make_input(float(id_in), "in", "lookup", "pipe spec"),
        },
        formula=formula,
        steps=[step, f"K = {k:.4f}"],
        citations=[cite, CIT_DRC],
        warnings=[] if k_name else [f"Unknown fitting {key!r}; K = 0."],
    )


def fit_sum_k(fittings, reynolds: float, id_in: float, clog_pct: float = 0.0) -> CalcResult:
    """Sum of K for a list of fittings ``[{key|type, qty}]`` (or ``{key: qty}``), with clogging (%)."""
    pairs = _pairs(fittings)
    id_mm, re, clog = float(id_in) * MM_PER_IN, float(reynolds), float(clog_pct or 0) / 100
    total = fit_sum_k_si(pairs, re, id_mm, clog)
    unknown = [str(k) for k, _ in pairs if resolve_fitting(k) is None]
    parts = []
    for raw, n in pairs:
        key = resolve_fitting(raw)
        if key and _js.num(n) > 0:
            parts.append(f"{_js.num(n):g}x{key}({fitting_k_si(key, re, id_mm):.3f})")
    steps = [f"sum K = {' + '.join(parts) if parts else '0'} = {total:.4f}"]
    if clog:
        steps.append(f"strainers, foot valves and filters divided by (1 - {clog:g})^2 for clogging")
    return CalcResult(
        calc="fit_sum_k",
        value=total,
        unit="",
        inputs={
            "fittings": make_input(len(pairs), "rows", "user"),
            "reynolds": make_input(re, "", "prior_calc"),
            "id": make_input(float(id_in), "in", "lookup", "pipe spec"),
            "clog": make_input(float(clog_pct or 0), "%", "user"),
        },
        formula="sum K = Σ n_i K_i(Re, D) ; K_filter /= (1 - clog)^2",
        steps=steps,
        citations=[CIT_DARBY, CIT_CRANE, CIT_TDH_K, CIT_DRC],
        warnings=[f"Unknown fitting(s) ignored: {unknown}"] if unknown else [],
    )
