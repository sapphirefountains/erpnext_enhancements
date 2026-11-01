"""Electrical bill-of-materials lines, in the shape phase B's ``engine/bom.py``
merges: ``{section: "Electrical", key, description, qty, uom, item_hint}``.

Conductors and conduit carry 10% waste. ``item_hint`` is a search phrase for the
matching ERPNext Item (the page offers the Items it finds); ``key`` is stable
across derives so a designer's Item pick can stick to the line.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input
from ._util import num

SECTION = "Electrical"
WASTE = 0.10
DISCONNECT_SIZES_A = [30, 60, 100, 200, 400, 600]
CIT_BOM = "Takeoff from the branch circuits; 10% waste on wire and conduit"
CIT_DISCONNECT = "NEC 430.102 / 430.110 (motor disconnect >= 115% FLC) / 680.13 (maintenance disconnect)"


def _line(key: str, description: str, qty: float, uom: str, item_hint: str = "") -> dict:
    return {"section": SECTION, "key": key, "description": description, "qty": qty, "uom": uom,
            "item_hint": item_hint or description}


def _ft(length: float) -> int:
    return int(math.ceil(num(length) * (1 + WASTE) - 1e-9))


def electrical_bom(
    circuits: list[dict] | None,
    *,
    feeder: dict | None = None,
    main_breaker_a: float | None = None,
    transformers: list[dict] | None = None,
    low_voltage: list[dict] | None = None,
    control_transformer_va: float | None = None,
    junction_boxes: int = 0,
    bonding_ft: float = 0.0,
    material: str = "Cu",
) -> CalcResult:
    """BOM lines for the electrical design. ``feeder`` = ``{conductor, egc,
    conduit, conduit_type, length_ft, current_carrying}``; ``transformers`` =
    lighting-transformer results ``{va_each, units}``; ``low_voltage`` =
    ``low_voltage_run`` values (wire, wire_ft, fuse_a, branch_count) with an
    optional ``supply_w``. ``value`` is the list of lines."""
    circuits = circuits or []
    wire: dict[str, float] = {}
    conduit: dict[tuple, float] = {}
    breakers: dict[tuple, int] = {}
    disconnects: dict[tuple, int] = {}
    vfds: dict[float, int] = {}

    def add_wire(size, ft, n):
        if size and num(ft) > 0 and n:
            wire[str(size)] = wire.get(str(size), 0.0) + num(ft) * n

    for c in circuits:
        run = num(c.get("run_ft"))
        add_wire(c.get("conductor"), run, int(num(c.get("current_carrying"), 2)))
        add_wire(c.get("egc"), run, 1)
        if c.get("conduit") and run > 0:
            k = (c.get("conduit_type") or "PVC-40", c["conduit"])
            conduit[k] = conduit.get(k, 0.0) + run
        bk = (int(num(c.get("breaker_a"))), int(num(c.get("poles"), 1)), c.get("gfci_device") or ("GFCI breaker" if c.get("gfci") else ""))
        breakers[bk] = breakers.get(bk, 0) + 1
        if c.get("is_motor"):
            need = 1.15 * num(c.get("load_a"))
            size = next((s for s in DISCONNECT_SIZES_A if s >= need), DISCONNECT_SIZES_A[-1])
            dk = (size, int(num(c.get("poles"), 1)))
            disconnects[dk] = disconnects.get(dk, 0) + 1
        if c.get("vfd_hp"):
            vfds[num(c["vfd_hp"])] = vfds.get(num(c["vfd_hp"]), 0) + 1

    if feeder and num(feeder.get("length_ft")) > 0:
        fl = num(feeder["length_ft"])
        add_wire(feeder.get("conductor"), fl, int(num(feeder.get("current_carrying"), 2)))
        add_wire(feeder.get("egc"), fl, 1)
        if feeder.get("conduit"):
            k = (feeder.get("conduit_type") or "PVC-40", feeder["conduit"])
            conduit[k] = conduit.get(k, 0.0) + fl

    lines = []
    for size, ft in sorted(wire.items(), key=lambda kv: (len(kv[0]), kv[0])):
        lines.append(_line(f"wire:{material}:{size}", f"Building wire THWN-2 {material}, #{size}", _ft(ft), "ft",
                           f"THWN-2 {size} AWG {material}"))
    for (ctype, size), ft in sorted(conduit.items()):
        lines.append(_line(f"conduit:{ctype}:{size}", f"Conduit {ctype} {size}", _ft(ft), "ft", f"{ctype} conduit {size}"))
    if main_breaker_a:
        lines.append(_line(f"main:{int(num(main_breaker_a))}", f"Main breaker {int(num(main_breaker_a))} A", 1, "ea",
                           f"main breaker {int(num(main_breaker_a))}A"))
    for (amps, poles, gdev), n in sorted(breakers.items()):
        if gdev == "SPGFCI / GFPE":
            lines.append(_line(f"breaker:{amps}:{poles}:spgfci", f"Circuit breaker {poles}P {amps} A with SPGFCI / GFPE "
                               "ground-fault protection", n, "ea", f"breaker {poles}P {amps}A ground fault"))
            continue
        kind = "GFCI circuit breaker" if gdev else "Circuit breaker"
        lines.append(_line(f"breaker:{amps}:{poles}:{int(bool(gdev))}", f"{kind} {poles}P {amps} A", n, "ea",
                           f"{'GFCI ' if gdev else ''}breaker {poles}P {amps}A"))
    for (size, poles), n in sorted(disconnects.items()):
        lines.append(_line(f"disconnect:{size}:{poles}", f"Disconnect switch {poles}P {size} A, NEMA 4X, HP-rated",
                           n, "ea", f"disconnect {size}A NEMA 4X"))
    for hp, n in sorted(vfds.items()):
        lines.append(_line(f"vfd:{hp:g}", f"Variable frequency drive {hp:g} HP", n, "ea", f"VFD {hp:g} HP"))
    for t in transformers or []:
        va, units = int(num(t.get("va_each"))), int(num(t.get("units"), 1))
        if va and units:
            lines.append(_line(f"xfmr:lighting:{va}", f"Low-voltage lighting transformer {va} VA (listed, 680.23(A)(2))",
                               units, "ea", f"pool light transformer {va}VA"))
    if control_transformer_va:
        va = int(num(control_transformer_va))
        lines.append(_line(f"xfmr:control:{va}", f"Control transformer {va} VA", 1, "ea", f"control transformer {va}VA"))
    for i, lv in enumerate(low_voltage or [], start=1):
        if not lv or not lv.get("wire"):
            continue
        tag = lv.get("tag") or f"lv{i}"
        lines.append(_line(f"lvwire:{tag}:{lv['wire']}", f"Low-voltage cable, 2-conductor, #{lv['wire']} ({tag})",
                           _ft(lv.get("wire_ft")), "ft", f"{lv['wire']} AWG 2-conductor direct burial"))
        if lv.get("branch_count"):
            lines.append(_line(f"lvblock:{tag}", f"Fused distribution block, {lv['branch_count']} positions ({tag})",
                               1, "ea", "fused distribution block"))
            lines.append(_line(f"lvfuse:{tag}:{lv.get('fuse_a')}", f"Blade fuse {lv.get('fuse_a')} A ({tag})",
                               lv["branch_count"], "ea", f"ATO blade fuse {lv.get('fuse_a')}A"))
        if lv.get("supply_w"):
            lines.append(_line(f"lvpsu:{tag}", f"DC power supply {lv['supply_w']} W ({tag})",
                               int(num(lv.get("supply_n"), 1)) or 1, "ea",
                               f"{lv.get('voltage', 24):g}V DC power supply {lv['supply_w']}W"))
    if junction_boxes:
        lines.append(_line("jbox", "Junction box, listed for pool/fountain luminaires (680.24 / 680.52)",
                           int(junction_boxes), "ea", "pool light junction box"))
    if num(bonding_ft) > 0:
        lines.append(_line("bond:wire", "Bonding conductor, #8 solid bare copper", _ft(bonding_ft), "ft",
                           "8 AWG solid bare copper"))
        lines.append(_line("bond:kit", "Bonding lugs and clamps (listed, direct burial)", 1, "lot", "bonding lug"))
    return CalcResult(
        calc="electrical_bom",
        value=lines,
        unit="lines",
        inputs={
            "circuits": make_input(len(circuits), "", "prior_calc"),
            "waste": make_input(WASTE, "", "default"),
        },
        formula="wire ft = run x conductors x 1.1 by size ; conduit ft = run x 1.1 by type and size ; devices by rating",
        steps=[f"{ln['qty']} {ln['uom']} {ln['description']}" for ln in lines],
        citations=[CIT_BOM, CIT_DISCONNECT],
        warnings=["Lengths are one-way runs from the model; add vertical rises and panel makeup on site."],
    )


__all__ = ["SECTION", "electrical_bom"]
