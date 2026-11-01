"""NEC Article 680 as typed checks: Part V (permanently installed fountains) and
Part II (permanently installed pools), which Part V borrows from and which a
fountain sharing water with a pool must meet in full (680.50).

Each check is a dict ``{key, part, article, title, requirement, status, detail,
note}`` with ``status`` one of ``pass`` / ``fail`` / ``verify`` / ``n/a``. A check
the model has no data for is ``verify``, never ``pass``: the checklist is what the
designer and the inspector walk, and a silent pass would hide the question.
Section numbers are the 2023 edition's with the 2020 / 2017 number where it
moved; a number marked "verify" in the note is one this module could not pin to
an edition with confidence.
"""

from __future__ import annotations

from ..data.nec_tables import (
    BONDING_MIN_AWG,
    DISCONNECT_MIN_FT,
    FOUNTAIN_CORD_MAX_FT,
    FOUNTAIN_LUMINAIRE_MAX_V,
    FOUNTAIN_RECEPTACLE_GFCI_FT,
    JBOX_ABOVE_GROUND_IN,
    JBOX_ABOVE_WATER_IN,
    JBOX_FROM_WALL_FT,
    LOW_VOLTAGE_CONTACT_LIMIT,
    NEC_EDITION,
    OVERHEAD_CLEARANCE_FT,
    POOL_LENS_LISTED_MIN_DEPTH_IN,
    POOL_LENS_MIN_DEPTH_IN,
    POOL_RECEPTACLE_GFCI_FT,
    POOL_RECEPTACLE_MIN_FT,
    VERIFY_EDITION,
)
from ..envelope import CalcResult, make_input
from ._util import num, phase_of

POOL_VENUES = ("pool", "spa", "wading", "therapy")
# 680.21(A)(1) wiring methods for pool-associated motors (insulated Cu EGC >= 12 AWG).
POOL_WIRING_METHODS = ("RMC", "IMC", "PVC", "RTRC", "LFNC", "MC")
CIT_680 = "NEC Article 680"


def venue_part(venue: str | None) -> str:
    """'pool' for pools and spas (Part II; spas also Part IV), else 'fountain' (Part V)."""
    v = (venue or "").strip().lower()
    return "pool" if any(p in v for p in POOL_VENUES) else "fountain"


def volts_to_ground(voltage: float, phase: int = 1) -> float:
    """Voltage to ground of the supply: 277 V on a 480Y/277 system, 120 V on
    120/240 and 208Y/120. A three-phase 220-250 V supply is taken as a delta with a
    208 V high leg (the worst case; a corner-grounded delta is 240 V)."""
    v = num(voltage)
    if v <= 0:
        return 0.0
    if v <= 150:
        return v
    if v >= 440:
        return 277.0
    if phase_of(phase) == 3 and 220 <= v <= 250:
        return 208.0
    return 120.0


def _is_low_voltage(volts: float) -> bool:
    return 0 < num(volts) <= LOW_VOLTAGE_CONTACT_LIMIT["dc_continuous"]


def gfci_requirement(load: dict, venue: str = "fountain") -> dict:
    """Whether Article 680 requires GFCI on this load's circuit:
    ``{required: True|False|None, article, note}`` (None = depends on site facts)."""
    part = venue_part(venue)
    kind = load.get("kind") or ""
    v = num(load.get("voltage"))
    vg = volts_to_ground(v, load.get("phase") or 1)
    over_150 = vg > 150
    spg = (
        " Over 150 V to ground no Class A GFCI exists; the 2023 NEC accepts special-purpose GFCI "
        "(SPGFCI, UL 943C) where Article 680 permits it. Verify."
        if over_150
        else ""
    )
    if kind in ("controls",):
        return {"required": False, "article": "", "note": "Control power is not fountain equipment."}
    if kind == "lighting_transformer":
        return {
            "required": False,
            "article": "680.23(A)(2)" if part == "pool" else "680.51(A)",
            "note": "Secondary at or below the low-voltage contact limit through a listed transformer needs "
            "no GFCI; GFCI on the primary is still recommended (24 V DC fixtures: confirm with the inspector).",
        }
    if kind == "valve_supply":
        return {"required": False, "article": "", "note": "Class 2 low-voltage supply."}
    if (load.get("submersible") or load.get("underwater")) and not _is_low_voltage(v):
        art = "680.23(A)(3)" if (part == "pool" and kind == "light") else ("680.21(C)" if part == "pool" else "680.51(A)")
        return {"required": True, "article": art, "note": "Submersible / underwater equipment over the "
                "low-voltage contact limit." + spg}
    if load.get("is_motor"):
        if part == "pool":
            return {"required": True, "article": "680.21(C)", "note": "Pool pump motor outlets and branch circuits "
                    "(2020: single- or three-phase, 150 V or less to ground, 60 A or less)." + spg}
        return {"required": True, "article": "680.59", "note": "Permanently installed nonsubmersible fountain "
                "pump (680.59, added 2017; verify the adopted edition)." + spg}
    if kind in ("uv", "chem_feed", "other"):
        ft = FOUNTAIN_RECEPTACLE_GFCI_FT if part == "fountain" else POOL_RECEPTACLE_GFCI_FT
        return {"required": None, "article": "680.58" if part == "fountain" else "680.22(A)(4)",
                "note": f"GFCI if it plugs into a receptacle within {ft} ft of the water's edge."}
    if kind == "light":
        return {"required": None, "article": "680.22(B)(4)" if part == "pool" else "680.51(A)",
                "note": "Line-voltage luminaire near the water: GFCI depending on distance and height."}
    return {"required": None, "article": "", "note": "Not equipment Article 680 names; confirm."}


def _check(key, part, article, title, requirement, status, detail="", note=VERIFY_EDITION) -> dict:
    return {
        "key": key, "part": part, "article": article, "title": title, "requirement": requirement,
        "status": status, "detail": detail, "note": note,
    }


def article680_checklist(
    loads: list[dict] | None = None,
    *,
    venue: str = "fountain",
    fixtures: list[dict] | None = None,
    site: dict | None = None,
) -> CalcResult:
    """The Article 680 checklist for a design. ``fixtures`` are the model's
    ``lighting.fixtures`` (``voltage``, ``lens_depth_in``, ``listed_above_water``,
    ``listed_shallow``, ``cord_ft``); ``site`` takes the measured facts
    (``disconnect_ft``, ``disconnect_in_sight``, ``nearest_receptacle_ft``,
    ``overhead_clearance_ft``, ``jbox_above_ground_in``, ``jbox_above_water_in``,
    ``jbox_from_wall_ft``, ``wiring_method``, ``bonding_designed``,
    ``bonding_in_bom``, ``cord_ft``). ``value`` is the list of checks."""
    loads = loads or []
    fixtures = fixtures or []
    site = site or {}
    part = venue_part(venue)
    pname = "Part II (pools)" if part == "pool" else "Part V (fountains)"
    checks: list[dict] = []
    is_interactive = "interactive" in (venue or "").lower() or "splash" in (venue or "").lower()

    # GFCI on every load that needs it (the design puts it in; this lists which).
    gfci_loads = []
    open_loads = []
    for ld in loads:
        g = gfci_requirement(ld, venue)
        if g["required"]:
            gfci_loads.append(f"{ld.get('label')} ({g['article']})")
        elif g["required"] is None:
            open_loads.append(f"{ld.get('label')} ({g['article']})")
    checks.append(_check(
        "gfci_equipment", pname, "680.51(A) / 680.59" if part == "fountain" else "680.21(C) / 680.23(A)(3)",
        "GFCI on luminaires, submersible and pump circuits",
        "GFCI on fountain luminaires, submersible pumps and other submersible equipment unless listed at or "
        "below the low-voltage contact limit and supplied per 680.23(A)(2); GFCI on nonsubmersible fountain "
        "pumps (680.59) and pool pump motors (680.21(C)).",
        "pass" if gfci_loads and not open_loads else ("verify" if open_loads else "n/a"),
        ("GFCI designed in: " + "; ".join(gfci_loads) if gfci_loads else "")
        + (" | depends on site: " + "; ".join(open_loads) if open_loads else ""),
    ))

    lim = LOW_VOLTAGE_CONTACT_LIMIT
    lv = [f for f in fixtures if 0 < num(f.get("voltage"), 12) <= lim["dc_continuous"]]
    checks.append(_check(
        "low_voltage_contact_limit", "Part I", "680.2 / 680.23(A)(2)", "Low-voltage contact limit",
        f"No GFCI needed on listed equipment at or below {lim['ac_sinusoidal_rms']:g} V AC (sinusoidal, RMS), "
        f"{lim['ac_nonsinusoidal_peak']:g} V peak (nonsinusoidal AC) or {lim['dc_continuous']:g} V DC, "
        "supplied by a listed transformer or power supply with an ungrounded secondary.",
        "verify" if lv else "n/a",
        f"{len(lv)} low-voltage fixture row(s). 12 V AC is under the 15 V AC limit; 24 V DC fixtures are "
        "under 30 V DC, but some inspectors still ask for GFCI." if lv else "",
    ))

    over = [f for f in fixtures if num(f.get("voltage"), 12) > FOUNTAIN_LUMINAIRE_MAX_V]
    checks.append(_check(
        "luminaire_voltage", pname, "680.51(B)" if part == "fountain" else "680.23(A)(4)",
        "Luminaire circuits 150 V or less",
        f"Luminaires, submersible pumps and other submersible equipment operate at {FOUNTAIN_LUMINAIRE_MAX_V} V "
        "or less between conductors.",
        "fail" if over else ("pass" if fixtures else "n/a"),
        ", ".join(f"{f.get('label') or 'fixture'} at {num(f.get('voltage')):g} V" for f in over),
    ))

    if part == "fountain":
        bad, unknown = [], []
        for f in fixtures:
            d = f.get("lens_depth_in")
            if d is None:
                unknown.append(f.get("label") or "fixture")
            elif num(d) <= 0 and not f.get("listed_above_water"):
                bad.append(f.get("label") or "fixture")
        checks.append(_check(
            "lens_below_water", pname, "680.51(C)", "Luminaire lens below the normal water level",
            "Luminaires installed with the top of the lens below the normal water level unless listed for "
            "above-water locations; facing upward lenses guarded or listed for use without a guard.",
            "fail" if bad else ("verify" if unknown else ("pass" if fixtures else "n/a")),
            ("above water and not listed: " + ", ".join(bad)) if bad else (
                ("no lens depth for: " + ", ".join(unknown)) if unknown else ""),
        ))
    else:
        bad, unknown = [], []
        for f in fixtures:
            d = f.get("lens_depth_in")
            need = POOL_LENS_LISTED_MIN_DEPTH_IN if f.get("listed_shallow") else POOL_LENS_MIN_DEPTH_IN
            if d is None:
                unknown.append(f.get("label") or "fixture")
            elif num(d) < need:
                bad.append(f"{f.get('label') or 'fixture'} ({num(d):g} in < {need} in)")
        checks.append(_check(
            "lens_depth", pname, "680.23(A)(5)", "Top of the lens 18 in below the normal water level",
            f"Top of the lens at least {POOL_LENS_MIN_DEPTH_IN} in below the normal water level, or "
            f"{POOL_LENS_LISTED_MIN_DEPTH_IN} in for a luminaire listed for that depth.",
            "fail" if bad else ("verify" if unknown else ("pass" if fixtures else "n/a")),
            "; ".join(bad) if bad else (("no lens depth for: " + ", ".join(unknown)) if unknown else ""),
        ))

    cord = site.get("cord_ft")
    cords = [num(f.get("cord_ft")) for f in fixtures if f.get("cord_ft") is not None]
    if cord is not None:
        cords.append(num(cord))
    checks.append(_check(
        "cord_length", pname, "680.51(E) / 680.56" if part == "fountain" else "680.21(A)(3)",
        "Flexible cord limits",
        f"Exposed flexible cord in a fountain {FOUNTAIN_CORD_MAX_FT} ft or less, extra-hard usage, cord-and-plug "
        "equipment GFCI-protected (680.56); a pool pump cord 3 ft to 10 ft with a copper EGC.",
        ("fail" if max(cords) > FOUNTAIN_CORD_MAX_FT else "pass") if cords else "verify",
        f"longest cord {max(cords):g} ft" if cords else "Cord lengths not entered.",
    ))

    checks.append(_check(
        "servicing", pname, "680.51(F)" if part == "fountain" else "680.23(B)(6)",
        "Equipment removable for relamping and service",
        "Luminaires and submersible equipment removable from the water for relamping or maintenance without "
        "entering the water (wet-niche cord long enough to set the luminaire on the deck).",
        "verify",
        "Leave a cord coil at each wet-niche or submersible fixture.",
    ))

    hg, hw, wf = site.get("jbox_above_ground_in"), site.get("jbox_above_water_in"), site.get("jbox_from_wall_ft")
    jfail = []
    if hg is not None and num(hg) < JBOX_ABOVE_GROUND_IN:
        jfail.append(f"{num(hg):g} in above grade < {JBOX_ABOVE_GROUND_IN} in")
    if hw is not None and num(hw) < JBOX_ABOVE_WATER_IN:
        jfail.append(f"{num(hw):g} in above water < {JBOX_ABOVE_WATER_IN} in")
    if wf is not None and num(wf) < JBOX_FROM_WALL_FT:
        jfail.append(f"{num(wf):g} ft from the wall < {JBOX_FROM_WALL_FT} ft")
    have_j = any(x is not None for x in (hg, hw, wf))
    checks.append(_check(
        "junction_boxes", pname, "680.24 / 680.52" if part == "fountain" else "680.24",
        "Junction boxes and enclosures",
        f"Deck boxes for wet-niche luminaires listed, at least {JBOX_ABOVE_GROUND_IN} in above grade and "
        f"{JBOX_ABOVE_WATER_IN} in above the maximum water level (whichever is greater), {JBOX_FROM_WALL_FT} ft "
        "from the inside wall unless behind a barrier (680.24); underwater boxes in a fountain filled with "
        "potting compound, of brass or copper or listed, threaded or hubbed entries, and bonded (680.52(B)).",
        "fail" if jfail else ("pass" if have_j else "verify"),
        "; ".join(jfail),
    ))

    bonding = site.get("bonding_designed")
    bonding_note = (
        "A bonding allowance is in the BOM; confirm the bonding design."
        if site.get("bonding_in_bom") and not bonding
        else ""
    )
    if part == "pool":
        checks.append(_check(
            "equipotential_bonding", pname, "680.26", "Equipotential bonding",
            f"Bond the conductive shell (rebar), perimeter surfaces 3 ft out, metal fittings, underwater "
            f"lighting, pump motors and fixed metal within 5 ft horizontally / 12 ft vertically with solid "
            f"copper {BONDING_MIN_AWG} AWG or larger; bond the water with 9 in^2 of conductive surface (680.26(C)).",
            "pass" if bonding else "verify",
            bonding_note,
        ))
    else:
        checks.append(_check(
            "bonding", pname, "680.53 / 680.54 (numbering moved in 2020; verify)", "Bonding of fountain metal",
            f"Bond all metal piping, metal fittings and equipment of the fountain, and the conductive basin "
            f"structure, to the branch-circuit EGC; solid copper {BONDING_MIN_AWG} AWG or larger. A fountain sharing "
            "water with a pool also needs 680.26 equipotential bonding.",
            "pass" if bonding else "verify",
            bonding_note,
        ))

    dist = site.get("disconnect_ft")
    in_sight = site.get("disconnect_in_sight")
    dstatus = "verify"
    if dist is not None:
        dstatus = "fail" if num(dist) < DISCONNECT_MIN_FT or in_sight is False else "pass"
    checks.append(_check(
        "maintenance_disconnect", "Part I", "680.13 (680.12 before 2020)", "Maintenance disconnecting means",
        f"A disconnect for all utilization equipment other than lighting, readily accessible, within sight of "
        f"the equipment and at least {DISCONNECT_MIN_FT} ft horizontally from the inside wall of the fountain or "
        "pool unless separated by a permanent barrier.",
        dstatus,
        f"disconnect at {num(dist):g} ft" if dist is not None else "Disconnect location not entered.",
    ))

    rec = site.get("nearest_receptacle_ft")
    if part == "pool":
        rstatus = "verify" if rec is None else ("fail" if num(rec) < POOL_RECEPTACLE_MIN_FT else "pass")
        checks.append(_check(
            "receptacles", pname, "680.22(A)", "Receptacle locations and GFCI",
            f"Receptacles at least {POOL_RECEPTACLE_MIN_FT} ft from the inside wall; 125-250 V receptacles within "
            f"{POOL_RECEPTACLE_GFCI_FT} ft GFCI-protected; one 125 V receptacle between {POOL_RECEPTACLE_MIN_FT} ft "
            f"and {POOL_RECEPTACLE_GFCI_FT} ft for a permanently installed pool.",
            rstatus, f"nearest receptacle {num(rec):g} ft" if rec is not None else "",
        ))
    else:
        checks.append(_check(
            "receptacles", pname, "680.58", "GFCI on adjacent receptacles",
            f"Receptacles (15/20 A, 125-250 V) within {FOUNTAIN_RECEPTACLE_GFCI_FT} ft of the fountain edge "
            "are GFCI-protected.",
            "verify" if rec is None else "pass",
            (f"nearest receptacle {num(rec):g} ft: "
             + ("GFCI required" if num(rec) <= FOUNTAIN_RECEPTACLE_GFCI_FT else "outside 20 ft"))
            if rec is not None else "",
        ))

    oc = site.get("overhead_clearance_ft")
    need_oc = OVERHEAD_CLEARANCE_FT["insulated_cable_on_messenger"]
    checks.append(_check(
        "overhead_clearance", "Part I", "680.9(A) / Table 680.9(A) (680.8 before 2020; verify)",
        "Overhead conductor clearance",
        f"Service drops and overhead conductors at least {need_oc:g} ft (insulated cable on a messenger) or "
        f"{OVERHEAD_CLEARANCE_FT['other_conductors']:g} ft (other conductors) above the water and its edge; the "
        "table names pools and spas, so whether it binds a fountain is the inspector's call.",
        "verify" if oc is None else ("pass" if num(oc) >= need_oc else "fail"),
        f"{num(oc):g} ft measured" if oc is not None else "",
    ))

    method = (site.get("wiring_method") or "").upper()
    if part == "pool":
        wstatus = "verify" if not method else ("pass" if any(m in method for m in POOL_WIRING_METHODS) else "fail")
        checks.append(_check(
            "wiring_methods", pname, "680.21(A)(1) / 680.23(F)(1)", "Wiring methods",
            "Pool motor and luminaire branch circuits in RMC, IMC, PVC, RTRC, LFNC or listed Type MC, with an "
            "insulated copper EGC of 12 AWG or larger; corrosive equipment rooms per 680.14.",
            wstatus, method,
        ))
    else:
        checks.append(_check(
            "wiring_methods", pname, "680.50 / Chapter 3 / 680.14", "Wiring methods",
            "Wet-location wiring methods (PVC or RMC conduit, THWN-2 conductors); corrosive environments per "
            "680.14; a fountain that shares water with a pool follows 680.21 and 680.23.",
            "verify" if not method else "pass", method,
        ))

    if is_interactive:
        checks.append(_check(
            "interactive_scope", pname, "680.50", "Interactive (splash pad) scope",
            "Whether the authority applies Part V alone or Part II as well to an interactive feature people "
            "walk through is a jurisdiction question; many treat it as a pool.",
            "verify",
        ))

    high = [ld.get("label") for ld in loads if volts_to_ground(ld.get("voltage"), ld.get("phase") or 1) > 150
            and gfci_requirement(ld, venue)["required"]]
    if high:
        checks.append(_check(
            "over_150v_to_ground", "Part I", "680.5 (2023)", "GFCI over 150 V to ground",
            "Personnel GFCI is not made over 150 V to ground; the 2023 NEC accepts special-purpose GFCI "
            "(SPGFCI) where Article 680 permits it.",
            "verify", ", ".join(high),
        ))

    fails = sum(1 for c in checks if c["status"] == "fail")
    verifies = sum(1 for c in checks if c["status"] == "verify")
    status = "bad" if fails else ("warn" if verifies else "good")
    return CalcResult(
        calc="article680_checklist",
        value=checks,
        unit="checks",
        inputs={
            "venue": make_input(venue, "", "user"),
            "part": make_input(pname, "", "prior_calc"),
            "fail": make_input(fails, "", "prior_calc"),
            "verify": make_input(verifies, "", "prior_calc"),
            "edition": make_input(NEC_EDITION, "", "standard"),
        },
        formula="one typed check per Article 680 requirement ; no data -> verify",
        steps=[f"{c['article']}: {c['title']} -> {c['status']}" for c in checks],
        citations=[f"{CIT_680} {pname}", "NEC 680.50 (scope of Part V)"],
        warnings=[VERIFY_EDITION, "An Article 680 checklist is a design aid; the inspector's reading governs."],
        status=status,
    )


__all__ = ["article680_checklist", "gfci_requirement", "venue_part", "volts_to_ground"]
