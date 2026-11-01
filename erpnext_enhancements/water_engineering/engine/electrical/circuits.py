"""Branch circuits: one per motor (each unit of a multi-pump row gets its own) and
one per other load row, each with its OCPD, conductor, EGC, conduit, voltage drop,
poles and GFCI call. This is where ``ocpd``, ``conductors``, ``conduit``,
``article680`` and ``vfd`` meet; ``feeders.panel_schedule`` turns the result into
the panel rows.
"""

from __future__ import annotations

from ..envelope import CalcResult, make_input
from ._util import edition_warning, num, phase_of, poles_for
from .article680 import gfci_requirement, venue_part
from .conductors import wire_for_load
from .conduit import DEFAULT_CONDUIT, conduit_for_circuit
from .ocpd import continuous_ocpd, motor_branch_ocpd
from .vfd import select_vfd
from .voltage_drop import BRANCH_DROP_PCT

TAG_PREFIX = {"pump": "M", "aerator": "M", "motor": "M", "light": "L", "lighting_transformer": "L", "controls": "X"}
CIT_BRANCH = "NEC 430 Part II-IV (motor circuits) / 210.19-210.20 (other branch circuits)"


def branch_circuit(
    load: dict,
    *,
    tag: str = "",
    venue: str = "fountain",
    conduit_type: str = DEFAULT_CONDUIT,
    ambient_f: float = 86.0,
    device: str = "inverse_time_breaker",
    max_drop_pct: float = BRANCH_DROP_PCT,
    material: str = "Cu",
    termination_c: int | None = None,
) -> dict:
    """One branch circuit for one motor unit or one non-motor load row. Returns a
    plain dict (the panel-schedule fields plus ``calcs``: the CalcResult dicts
    behind it)."""
    ph = phase_of(load.get("phase"))
    v = num(load.get("voltage"), 240)
    motor = bool(load.get("is_motor"))
    qty = 1 if motor else int(num(load.get("qty"), 1))
    amps = num(load.get("amps")) * qty
    gfci = gfci_requirement(load, venue)
    vfd = None
    cond_a = amps
    calcs: dict = {}
    if motor:
        is_vfd = (load.get("control") or "") == "vfd"
        if is_vfd:
            vfd = select_vfd(load.get("hp") or 0, v, ph, motor_phase=3, cable_ft=load.get("run_ft"),
                             submersible=bool(load.get("submersible")))
            calcs["vfd"] = vfd.to_dict()
            # The drive's input current sets the conductors (430.122); FLC still seeds the 430.52 placeholder.
            cond_a = num(vfd.inputs["input_a_est"]["value"]) or amps
        oc = motor_branch_ocpd(amps, device=device, phase=ph, hp=load.get("hp") or 0, voltage=v)
        basis = "430.122 / drive maker" if is_vfd else "430.52 max"
        control = "VFD" if is_vfd else "Motor Starter"
        wire = wire_for_load(
            cond_a, voltage=v, phase=ph, length_ft=load.get("run_ft"), is_motor=True, ocpd_a=oc.value,
            material=material, ambient_f=ambient_f, max_drop_pct=max_drop_pct, termination_c=termination_c,
            pool=venue_part(venue) == "pool",
        )
    else:
        oc = continuous_ocpd(amps, continuous=bool(load.get("continuous")))
        basis = "210.20(A)"
        control = ""
        wire = wire_for_load(
            amps, voltage=v, phase=ph, length_ft=load.get("run_ft"), continuous=bool(load.get("continuous")),
            ocpd_a=oc.value, material=material, ambient_f=ambient_f, max_drop_pct=max_drop_pct,
            termination_c=termination_c, pool=venue_part(venue) == "pool" and load.get("kind") == "light",
        )
    calcs["ocpd"] = oc.to_dict()
    calcs["wire"] = wire.to_dict()
    # Hot + neutral at 120 V, two hots at 208/240 V, three on three-phase.
    ccc = 3 if ph == 3 else 2
    egc = wire.inputs.get("egc", {}).get("value") if wire.inputs else None
    cd = conduit_for_circuit(wire.value, ccc, egc, conduit_type)
    calcs["conduit"] = cd.to_dict()
    return {
        "tag": tag,
        "key": load.get("key"),
        "description": load.get("label"),
        "kind": load.get("kind"),
        "is_motor": motor,
        "hp": load.get("hp"),
        "load_a": round(amps, 3),
        "voltage": v,
        "phase": ph,
        "poles": poles_for(v, ph),
        "control_method": control,
        "breaker_a": oc.value,
        "breaker_basis": basis,
        "conductor_min_a": round(1.25 * cond_a, 2) if motor else None,
        "conductor": wire.value,
        "conductor_ampacity_size": wire.inputs.get("ampacity_size", {}).get("value"),
        "current_carrying": ccc,
        "egc": egc,
        "conduit": cd.value,
        "conduit_type": conduit_type,
        "run_ft": num(load.get("run_ft")),
        "drop_pct": wire.inputs.get("drop_pct", {}).get("value"),
        "gfci": bool(gfci["required"]),
        "gfci_basis": gfci["article"],
        "gfci_note": gfci["note"],
        "va": num(load.get("va")) * qty,
        "vfd_hp": vfd.value if vfd else None,
        "calcs": calcs,
    }


def branch_circuits(
    loads: list[dict] | None,
    *,
    venue: str = "fountain",
    conduit_type: str = DEFAULT_CONDUIT,
    ambient_f: float = 86.0,
    device: str = "inverse_time_breaker",
    max_drop_pct: float = BRANCH_DROP_PCT,
    material: str = "Cu",
    termination_c: int | None = None,
) -> CalcResult:
    """Branch circuits for the whole load list. ``value`` is the list of circuit
    dicts (see :func:`branch_circuit`), tagged M1.. (motors), L1.. (lighting),
    E1.. (other equipment), X1 (controls)."""
    counters: dict[str, int] = {}
    circuits = []
    for ld in loads or []:
        if num(ld.get("amps")) <= 0:
            continue
        prefix = TAG_PREFIX.get(ld.get("kind") or "", "E")
        units = int(num(ld.get("qty"), 1)) if ld.get("is_motor") else 1
        for u in range(units):
            counters[prefix] = counters.get(prefix, 0) + 1
            c = branch_circuit(
                ld, tag=f"{prefix}{counters[prefix]}", venue=venue, conduit_type=conduit_type,
                ambient_f=ambient_f, device=device, max_drop_pct=max_drop_pct, material=material,
                termination_c=termination_c,
            )
            if units > 1:
                c["description"] = f"{c['description']} ({u + 1} of {units})"
            circuits.append(c)
    warnings = []
    for c in circuits:
        if (c["drop_pct"] or 0) > max_drop_pct:
            warnings.append(f"{c['tag']} {c['description']}: {c['drop_pct']:.1f}% drop even in {c['conductor']}.")
        for w in c["calcs"].get("vfd", {}).get("warnings", [])[:1]:
            warnings.append(f"{c['tag']}: {w}")
    warnings.append(edition_warning())
    return CalcResult(
        calc="branch_circuits",
        value=circuits,
        unit="circuits",
        inputs={
            "venue": make_input(venue, "", "user"),
            "conduit_type": make_input(conduit_type, "", "user"),
            "ambient_f": make_input(num(ambient_f), "F", "user"),
            "device": make_input(device, "", "user"),
            "max_drop_pct": make_input(max_drop_pct, "%", "default"),
        },
        formula="per circuit: OCPD (430.52 or 210.20) -> conductor (430.22 / 210.19 + drop) -> EGC -> conduit",
        steps=[
            f"{c['tag']} {c['description']}: {c['load_a']:.2f} A, {c['breaker_a']} A {c['poles']}P "
            f"({c['breaker_basis']}), {c['conductor']} + {c['egc']} EGC in {c['conduit']} {c['conduit_type']}, "
            f"{c['drop_pct']}% over {c['run_ft']:g} ft{', GFCI' if c['gfci'] else ''}"
            for c in circuits
        ],
        citations=[CIT_BRANCH, "NEC 430.52(C)(1)", "NEC 430.22", "NEC 250.122", "NEC Chapter 9 Tables 1, 4, 5"],
        warnings=warnings,
    )


__all__ = ["branch_circuit", "branch_circuits"]
