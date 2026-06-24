"""Water-feature flow requirements: weirs/slots (Francis), nozzle arrays, and
orifice nozzles driven by a Nozzle Profile catalog.

Verified against DOC-0049 ``I - Weir``:
    Q_gpm = (36 * L_ft * h_in^1.5) - (0.3 * n * h_in^2.5)   (n = end contractions)

The SUPPORT ``WeirInfo`` "GPM per foot" table is this same formula evaluated at
L=1, n=2 -- so we compute Francis directly rather than store a lookup that could
drift.

Orifice nozzles: the orifice equation Q = Cd*A*sqrt(2gh) is textbook physics, but
the discharge coefficient and orifice size are NOT in the Sapphire source docs —
they come from a ``Nozzle Profile`` (manufacturer cut sheet). :func:`nozzle_flow`
computes from those sourced coefficients (Cd + orifice area/diameter), or scales a
rated GPM by sqrt(head); with neither it returns a clear "needs a Nozzle Profile"
warning rather than an invented number.
"""

from __future__ import annotations

import math

from .constants import (
    CIT_WEIR,
    CIT_WEIR_EDGE,
    DEFAULT_WEIR_CONTRACTIONS,
    FT_PER_PSI,
    JET_EFFICIENCY,
    JET_EFFICIENCY_DEFAULT,
    WEIR_EDGE_BANDS,
    WEIR_ENGINEER_GPM_PER_FT,
    WEIR_FRANCIS_COEFF,
    WEIR_FRANCIS_CONTRACTION_COEFF,
    WEIR_OPERATE_GPM_PER_FT,
)
from .envelope import CalcResult, make_input


def edge_sheet_guidance(gpm_per_ft: float, head_in: float = 0.0) -> str:
    """One-line design advisory for a weir/edge running at ``gpm_per_ft`` per
    linear foot: the wind band it tolerates plus the DOC-0049 B operate-vs-engineer
    rule (operate ~0.5 GPM/ft; size plumbing & water-in-transit for 4-6 GPM/ft)."""
    band = ""
    for at_least, label in WEIR_EDGE_BANDS:
        if head_in >= at_least:
            band = label
    lo, hi = WEIR_ENGINEER_GPM_PER_FT
    band_txt = f"{band}; " if band else ""
    return (
        f"{gpm_per_ft:.2f} GPM/ft of edge ({band_txt}operate near "
        f"{WEIR_OPERATE_GPM_PER_FT:g} GPM/ft, engineer plumbing for {lo:g}-{hi:g} GPM/ft)"
    )

CIT_JET = "Bernoulli free-jet height (engineering standard; not in source docs)"

CIT_ORIFICE = "Orifice equation Q=Cd*A*sqrt(2gh) (textbook); coefficients from Nozzle Profile catalog"


def feature_flow_category(feature_type: str) -> str:
    """Classify a feature type to the flow calc that sizes it:

    * ``"tiered"``  — a tiered (cascading) fountain: a stack of tiers, each a
      circular weir; the recirculated flow must sheet the largest tier (sized
      from the design's tier rows, not a single feature row).
    * ``"weir"``    — water spilling over a crest as a sheet: weirs, spilling
      weirs, vanishing/slot edges, and waterwalls (sized by the Francis weir
      formula over the crest length).
    * ``"array"``   — discrete jets/streams: nozzle arrays, splash pads, and rain
      curtains (sized by count * GPM-each).
    * ``"orifice"`` — a single orifice nozzle (Q = Cd*A*sqrt(2gh)).
    """
    t = (feature_type or "").lower()
    if "tier" in t:
        return "tiered"
    if any(k in t for k in ("weir", "slot", "vanish", "wall", "spill")):
        return "weir"
    if any(k in t for k in ("array", "splash", "rain", "curtain")):
        return "array"
    return "orifice"


def feature_visual_kind(feature_type: str) -> str:
    """Classify a feature type to the schematic the canvas draws for it:
    ``"tiered" | "waterwall" | "splash_pad" | "rain_curtain" | "spilling_weir" |
    "weir" | "jet"``. Order matters (tier/waterwall/spilling before plain weir)."""
    t = (feature_type or "").lower()
    if "tier" in t:
        return "tiered"
    if "waterwall" in t or "water wall" in t or "wall" in t:
        return "waterwall"
    if "splash" in t:
        return "splash_pad"
    if "rain" in t or "curtain" in t:
        return "rain_curtain"
    if "spill" in t:
        return "spilling_weir"
    if "weir" in t or "slot" in t or "vanish" in t:
        return "weir"
    return "jet"


def tiered_fountain_flow(tiers, gpm_per_ft: float = 0.5) -> CalcResult:
    """Required circulation for a tiered (cascading) fountain.

    ``tiers`` is a list of ``{diameter_in}`` rows. Each tier is a circular weir
    whose rim needs ``gpm_per_ft`` per linear foot of circumference to hold a
    continuous sheet. The same water cascades through every tier in series, so
    the circulation must satisfy the most demanding (largest-circumference) tier
    — that maximum is the required flow."""
    tiers = tiers or []
    gpm_per_ft = float(gpm_per_ft) or 0.5
    if not tiers:
        return CalcResult(
            calc="tiered_fountain_flow",
            unit="GPM",
            citations=[CIT_WEIR],
            warnings=["Add at least one tier (diameter) to size the cascade."],
        )
    steps = []
    required = 0.0
    for i, t in enumerate(tiers, start=1):
        d_in = max(float(t.get("diameter_in") or 0), 0.0)
        circ_ft = math.pi * d_in / 12.0
        q = circ_ft * gpm_per_ft
        required = max(required, q)
        steps.append(f"tier {i}: dia {d_in:g} in -> circumference {circ_ft:.2f} ft -> {q:.2f} GPM to sheet")
    steps.append(
        f"cascade flow = largest tier = {required:.2f} GPM "
        "(the same water sheets every tier in series, so the biggest rim governs)"
    )
    steps.append(edge_sheet_guidance(gpm_per_ft))
    return CalcResult(
        calc="tiered_fountain_flow",
        value=required,
        unit="GPM",
        inputs={
            "tier_count": make_input(len(tiers), "", "user"),
            "gpm_per_ft": make_input(gpm_per_ft, "GPM/ft", "user", "edge sheet rate (DOC-0049 B ~0.5)"),
        },
        formula="Q = max over tiers of (pi * D_in / 12) * gpm_per_ft  (circular weir, series cascade)",
        steps=steps,
        citations=[CIT_WEIR, CIT_WEIR_EDGE],
    )


def weir_flow(
    length_ft: float,
    head_in: float,
    contractions: int = DEFAULT_WEIR_CONTRACTIONS,
) -> CalcResult:
    """Flow (GPM) over a weir / slot / vanishing edge via the Francis formula."""
    length_ft = float(length_ft)
    head_in = float(head_in)
    q = (
        WEIR_FRANCIS_COEFF * length_ft * head_in**1.5
        - WEIR_FRANCIS_CONTRACTION_COEFF * contractions * head_in**2.5
    )
    warnings = []
    if q < 0:
        q = 0.0
        warnings.append("Francis formula went negative (head too small for this length); clamped to 0.")
    steps = [
        f"Q = (36 * {length_ft} * {head_in}^1.5) - (0.3 * {contractions} * {head_in}^2.5)",
        f"Q = {q:.4f} GPM",
    ]
    # Edge-sheet design advisory (DOC-0049 B): flow per linear foot + wind band +
    # the operate-vs-engineer rule, so an under-sheeted or under-plumbed edge shows.
    if length_ft > 0:
        per_ft = q / length_ft
        steps.append(edge_sheet_guidance(per_ft, head_in))
        if 0 < per_ft < WEIR_OPERATE_GPM_PER_FT:
            warnings.append(
                f"Edge runs at {per_ft:.2f} GPM/ft, below the ~{WEIR_OPERATE_GPM_PER_FT:g} GPM/ft "
                "minimum for a continuous sheet (DOC-0049 B) — it may break into rivulets; "
                "increase head or shorten the crest."
            )
    return CalcResult(
        calc="weir_flow",
        value=q,
        unit="GPM",
        inputs={
            "length": make_input(length_ft, "ft", "user", "I - Weir!C12"),
            "head": make_input(head_in, "in", "user", "I - Weir!C14"),
            "contractions": make_input(contractions, "", "user", "I - Weir!C15 (default 2)"),
        },
        formula="Q_gpm = (36 * L_ft * h_in^1.5) - (0.3 * n * h_in^2.5)",
        steps=steps,
        citations=[CIT_WEIR, CIT_WEIR_EDGE],
        warnings=warnings,
    )


def nozzle_array_flow(nozzle_count: int, gpm_each: float) -> CalcResult:
    """Total flow (GPM) for an array of identical nozzles at a known per-nozzle rate."""
    nozzle_count = int(nozzle_count or 0)
    gpm_each = float(gpm_each or 0.0)
    warnings = []
    if nozzle_count < 0 or gpm_each < 0:
        warnings.append("Nozzle count and GPM-each must be >= 0.")
        nozzle_count = max(nozzle_count, 0)
        gpm_each = max(gpm_each, 0.0)
    q = nozzle_count * gpm_each
    return CalcResult(
        calc="nozzle_array_flow",
        value=q,
        unit="GPM",
        inputs={
            "nozzle_count": make_input(nozzle_count, "", "user"),
            "gpm_each": make_input(gpm_each, "GPM", "user", "manufacturer cut sheet"),
        },
        formula="Q_gpm = nozzle_count * gpm_each",
        steps=[f"Q = {nozzle_count} * {gpm_each} = {q:.4f} GPM"],
        warnings=warnings,
    )


def nozzle_flow(
    head_ft: float = 0.0,
    *,
    cd: float | None = None,
    orifice_area_in2: float | None = None,
    orifice_diameter_in: float | None = None,
    rated_gpm: float | None = None,
    rated_head_ft: float | None = None,
    nozzle_profile: str = "",
) -> CalcResult:
    """Orifice nozzle flow (GPM) from a Nozzle Profile's sourced coefficients.

    Method 1 (Cd + orifice): Q = Cd * A * sqrt(2 g h), A from orifice area or
    diameter (A = pi/4 * d^2). Method 2 (rated): Q = rated_gpm * sqrt(h / h_rated)
    — orifice flow scales with the square root of head. ``head_ft`` is the nozzle
    supply head. With neither method's coefficients, returns a warning stub.
    """
    head_ft = float(head_ft or 0)
    area = None
    if orifice_area_in2:
        area = float(orifice_area_in2)
    elif orifice_diameter_in:
        area = math.pi / 4 * float(orifice_diameter_in) ** 2

    if cd and area and head_ft > 0:
        g = 9.80665  # m/s^2
        h_m = head_ft * 0.3048
        area_m2 = area * 0.00064516  # in^2 -> m^2
        q = float(cd) * area_m2 * math.sqrt(2 * g * h_m) * 15850.32314  # m^3/s -> US GPM
        return CalcResult(
            calc="nozzle_flow",
            value=q,
            unit="GPM",
            inputs={
                "supply_head": make_input(head_ft, "ft", "user"),
                "cd": make_input(cd, "", "lookup", "Nozzle Profile"),
                "orifice_area": make_input(round(area, 4), "in^2", "lookup", "Nozzle Profile"),
                "nozzle_profile": make_input(nozzle_profile, "", "user"),
            },
            formula="Q = Cd * A * sqrt(2 g h)",
            steps=[
                f"A = {area:.4f} in^2 ; h = {head_ft} ft",
                f"Q = {cd} * A * sqrt(2*9.80665*{h_m:.4f}) = {q:.4f} GPM",
            ],
            citations=[CIT_ORIFICE],
        )

    if rated_gpm and rated_head_ft and head_ft > 0:
        q = float(rated_gpm) * math.sqrt(head_ft / float(rated_head_ft))
        return CalcResult(
            calc="nozzle_flow",
            value=q,
            unit="GPM",
            inputs={
                "supply_head": make_input(head_ft, "ft", "user"),
                "rated_gpm": make_input(rated_gpm, "GPM", "lookup", "Nozzle Profile"),
                "rated_head": make_input(rated_head_ft, "ft", "lookup", "Nozzle Profile"),
                "nozzle_profile": make_input(nozzle_profile, "", "user"),
            },
            formula="Q = rated_gpm * sqrt(head / rated_head)",
            steps=[f"Q = {rated_gpm} * sqrt({head_ft}/{rated_head_ft}) = {q:.4f} GPM"],
            citations=[CIT_ORIFICE],
        )

    return CalcResult(
        calc="nozzle_flow",
        value=None,
        unit="GPM",
        inputs={
            "supply_head": make_input(head_ft, "ft", "user"),
            "nozzle_profile": make_input(nozzle_profile, "", "user"),
        },
        formula="Q = Cd * A * sqrt(2 g h)  (needs a Nozzle Profile + supply head)",
        warnings=[
            "Orifice nozzle flow needs a Nozzle Profile (discharge coefficient + "
            "orifice size, or a rated GPM @ head) and a positive supply head. Pick a "
            "Nozzle Profile, enter the supply head, or use a weir / nozzle_array_flow "
            "with a rated GPM.",
        ],
    )


def jet_trajectory(
    target_height_ft: float = 0.0,
    supply_head_ft: float = 0.0,
    supply_psi: float = 0.0,
    nozzle_type: str = "smooth",
) -> CalcResult:
    """Jet spray height <-> required pressure, with a basin-setback recommendation.

    A free jet rises to ``k * supply_head`` (k de-rates for drag/aeration: ~0.9
    solid, ~0.6 aerated). Give a supply head/pressure to get the realistic plume
    height, or a ``target_height_ft`` to back-solve the supply pressure that
    drives the existing TDH + pump chain. Basin edge should be >= the jet height."""
    k = JET_EFFICIENCY.get((nozzle_type or "smooth").strip().lower(), JET_EFFICIENCY_DEFAULT)
    head = float(supply_head_ft) or (float(supply_psi) * FT_PER_PSI if supply_psi else 0.0)

    if head > 0:  # forward: achievable height from supply pressure
        jet_h = k * head
        return CalcResult(
            calc="jet_trajectory",
            value=round(jet_h, 2),
            unit="ft (jet height)",
            inputs={
                "supply_head_ft": make_input(head, "ft", "user", "head at the nozzle"),
                "nozzle_type": make_input(nozzle_type, "", "user"),
                "k": make_input(k, "", "standard", "jet efficiency"),
            },
            formula="jet_height = k * supply_head  (V^2/2g = head)",
            steps=[
                f"k({nozzle_type}) = {k:g}",
                f"jet height = {k:g} * {head:.2f} ft = {jet_h:.2f} ft",
                f"basin edge should be >= {jet_h:.2f} ft from the jet (add splash margin for wind)",
            ],
            citations=[CIT_JET],
            warnings=["Allow extra downwind basin margin (or anemometer trim) — wind blows spray well past the jet height."],
        )
    if target_height_ft and float(target_height_ft) > 0:  # inverse: pressure for a target plume
        th = float(target_height_ft)
        req_head = th / k
        req_psi = req_head / FT_PER_PSI
        return CalcResult(
            calc="jet_trajectory",
            value=round(req_psi, 2),
            unit="psi (required at nozzle)",
            inputs={
                "target_height_ft": make_input(th, "ft", "user"),
                "nozzle_type": make_input(nozzle_type, "", "user"),
                "k": make_input(k, "", "standard", "jet efficiency"),
            },
            formula="required_head = target_height / k ; psi = head / 2.31",
            steps=[
                f"k({nozzle_type}) = {k:g}",
                f"required head = {th:g} / {k:g} = {req_head:.2f} ft = {req_psi:.2f} psi at the nozzle",
                f"basin edge should be >= {th:g} ft from the jet (add splash margin for wind)",
            ],
            citations=[CIT_JET],
            warnings=["Feed this required head into the TDH + pump selection; allow downwind basin margin for wind."],
        )
    return CalcResult(
        calc="jet_trajectory",
        unit="ft",
        citations=[CIT_JET],
        warnings=["Give a supply head/pressure (for the achievable height) or a target_height_ft (for the required pressure)."],
    )
