"""Pipe velocity, velocity-status banding, Hazen-Williams friction loss, and a
size-walker that recommends the smallest pipe that runs within limits.

Verified against DOC-0049 ``A - Pipe Size``:
    velocity  C7 = GPM * 0.4085 / ID^2
    head loss G7 = 10.44 * L * Q^1.85 / (C^1.85 * D^4.8655)   (C default 130)
Status bands (per material limits in SUPPORT): V > legal -> "Exceeds Legal
Limit"; V > line limit (suction 4.5 / discharge 6.5 PVC, 4.9 copper) ->
"Increase Size"; otherwise "Okay".
"""

from __future__ import annotations

from .constants import (
    CIT_PIPE,
    CIT_PIPE_SPECS,
    FT_PER_PSI,
    HW_C_PVC,
    HW_CONSTANT,
    HW_EXPONENT_D,
    HW_EXPONENT_Q,
    SELF_CLEANING_MIN_FPS,
    VELOCITY_COEFF,
)
from .data.pipe_specs import DEFAULT_PIPE_MATERIAL, PIPE_SPECS, get_pipe_pressure
from .envelope import CalcOption, CalcResult, make_input

STATUS_OKAY = "Okay"
STATUS_INCREASE = "Increase Size"
STATUS_EXCEEDS = "Exceeds Legal Limit"
STATUS_OVER_PRESSURE = "Exceeds Pressure Rating"
STATUS_SETTLING = "Below Self-Cleaning"


def pipe_pressure_rating(material: str, size: str, temp_f: float = 73.0) -> CalcResult:
    """Max operating pressure (psi) for a pipe at a temperature. PVC is rated at
    73 deg F and derates linearly to half by 110 deg F; copper carries a single
    rating. (DOC-0049 1,2,3 - Pipe Specs.)"""
    spec = get_pipe_pressure(material, size)
    base = {"material": make_input(material, "", "user"), "size": make_input(size, "", "user")}
    if not spec or spec.get("psi_73f") is None:
        return CalcResult(
            calc="pipe_pressure_rating", unit="psi", inputs=base, citations=[CIT_PIPE_SPECS],
            warnings=[f"No pressure rating on file for {material} {size}."],
        )
    temp_f = float(temp_f or 73.0)
    psi73, psi110, max_temp = spec["psi_73f"], spec.get("psi_110f"), spec.get("max_temp_f")
    if psi110 is None:
        rated = float(psi73)
        steps = [f"{material} {size}: {psi73} psi (single rating, max {max_temp} F)"]
    elif temp_f <= 73:
        rated, steps = float(psi73), [f"at {temp_f:g} F (<=73 F): {psi73} psi"]
    elif temp_f >= 110:
        rated, steps = float(psi110), [f"at {temp_f:g} F (>=110 F): {psi110} psi"]
    else:
        frac = (temp_f - 73) / (110 - 73)
        rated = psi73 + (psi110 - psi73) * frac
        steps = [f"derate 73->110 F: {psi73} + ({psi110}-{psi73})*{frac:.3f} = {rated:.1f} psi"]
    warnings = []
    if max_temp and temp_f > max_temp:
        warnings.append(f"{temp_f:g} F exceeds the {max_temp} F max working temperature for {material} {size}.")
    return CalcResult(
        calc="pipe_pressure_rating", value=round(rated, 1), unit="psi",
        inputs={**base, "temp": make_input(temp_f, "F", "user"),
                "psi_73f": make_input(psi73, "psi", "lookup", "1,2,3 - Pipe Specs"),
                "psi_110f": make_input(psi110, "psi", "lookup", "1,2,3 - Pipe Specs")},
        formula="rated psi at temperature (PVC derates linearly 73->110 F to half)",
        steps=steps, citations=[CIT_PIPE_SPECS], warnings=warnings,
    )


def pipe_pressure_check(material: str, size: str, system_psi: float, temp_f: float = 73.0) -> CalcResult:
    """Pass/fail a pipe's pressure rating against the system pressure (psi). The
    ``value`` is the margin (rated - system); negative means under-rated."""
    rating = pipe_pressure_rating(material, size, temp_f)
    if rating.value is None:
        return rating
    system_psi = float(system_psi or 0)
    rated = rating.value
    ok = system_psi <= rated
    status = STATUS_OKAY if ok else STATUS_OVER_PRESSURE
    warnings = list(rating.warnings)
    if not ok:
        warnings.append(
            f"System {system_psi:.0f} psi exceeds the {rated:.0f} psi rating of {material} {size} "
            f"at {float(temp_f or 73):g} F — use a heavier wall (e.g. SCH80) or larger pipe."
        )
    return CalcResult(
        calc="pipe_pressure_check", value=round(rated - system_psi, 1), unit="psi margin",
        inputs={"material": make_input(material, "", "user"), "size": make_input(size, "", "user"),
                "system_psi": make_input(round(system_psi, 1), "psi", "prior_calc"),
                "rated_psi": make_input(rated, "psi", "calc")},
        formula="margin = rated_psi - system_psi  (>= 0 ok)",
        steps=rating.steps + [f"margin = {rated:.0f} - {system_psi:.0f} = {rated - system_psi:.0f} psi -> {status}"],
        citations=[CIT_PIPE_SPECS], status=status, warnings=warnings,
    )


def pipe_velocity(flow_gpm: float, id_in: float) -> CalcResult:
    """Flow velocity (ft/s) in a pipe of the given inside diameter."""
    flow_gpm = float(flow_gpm)
    id_in = float(id_in)
    if id_in <= 0:
        return CalcResult(
            calc="pipe_velocity",
            unit="FPS",
            inputs={"flow": make_input(flow_gpm, "GPM", "prior_calc"), "id": make_input(id_in, "in", "user")},
            formula="V = GPM * 0.4085 / ID^2",
            citations=[CIT_PIPE],
            warnings=["Inside diameter must be > 0 to compute velocity."],
        )
    v = flow_gpm * VELOCITY_COEFF / id_in**2
    return CalcResult(
        calc="pipe_velocity",
        value=v,
        unit="FPS",
        inputs={
            "flow": make_input(flow_gpm, "GPM", "prior_calc"),
            "id": make_input(id_in, "in", "lookup", "SUPPORT NominalSizeID"),
        },
        formula="V = GPM * 0.4085 / ID^2",
        steps=[f"V = {flow_gpm} * 0.4085 / {id_in}^2 = {v:.4f} FPS"],
        citations=[CIT_PIPE],
    )


def velocity_status(
    velocity_fps: float,
    line: str,
    max_suction_fps: float,
    max_discharge_fps: float,
    legal_fps: float,
) -> str:
    """Band a velocity for a suction or discharge line. Below ~0.5 FPS the run
    is under the self-cleaning velocity (solids settle; DOC-0049's major-loss
    tables are blank there) — flagged as its own advisory band."""
    v = float(velocity_fps)
    if v > legal_fps:
        return STATUS_EXCEEDS
    limit = max_suction_fps if (line or "").lower() == "suction" else max_discharge_fps
    if v > limit:
        return STATUS_INCREASE
    if 0 < v < SELF_CLEANING_MIN_FPS:
        return STATUS_SETTLING
    return STATUS_OKAY


def hazen_williams_loss(
    flow_gpm: float,
    length_ft: float,
    id_in: float,
    c: float = HW_C_PVC,
    constant: float = HW_CONSTANT,
) -> CalcResult:
    """Friction head loss (ft) over a straight pipe run (Hazen-Williams)."""
    flow_gpm = float(flow_gpm)
    length_ft = float(length_ft)
    id_in = float(id_in)
    if id_in <= 0 or float(c) <= 0:
        return CalcResult(
            calc="hazen_williams_loss",
            unit="ft",
            inputs={"flow": make_input(flow_gpm, "GPM", "prior_calc"), "id": make_input(id_in, "in", "user")},
            formula="hf = K * L * Q^1.85 / (C^1.85 * D^4.8655)   [K=10.44]",
            citations=[CIT_PIPE],
            warnings=["Inside diameter and Hazen-Williams C must be > 0 to compute friction loss."],
        )
    hf = constant * length_ft * flow_gpm**HW_EXPONENT_Q / (float(c) ** HW_EXPONENT_Q * id_in**HW_EXPONENT_D)
    return CalcResult(
        calc="hazen_williams_loss",
        value=hf,
        unit="ft",
        inputs={
            "flow": make_input(flow_gpm, "GPM", "prior_calc"),
            "length": make_input(length_ft, "ft", "user"),
            "id": make_input(id_in, "in", "lookup", "SUPPORT NominalSizeID"),
            "c": make_input(c, "", "default", "PVC = 130"),
            "constant": make_input(constant, "", "lookup", "A - Pipe Size!G7"),
        },
        formula="hf = K * L * Q^1.85 / (C^1.85 * D^4.8655)   [K=10.44]",
        steps=[
            f"hf = {constant} * {length_ft} * {flow_gpm}^1.85 / ({c}^1.85 * {id_in}^4.8655)",
            f"hf = {hf:.4f} ft",
        ],
        citations=[CIT_PIPE],
    )


def size_pipe(
    flow_gpm: float,
    length_ft: float,
    material: str = DEFAULT_PIPE_MATERIAL,
    line: str = "discharge",
    c: float = HW_C_PVC,
) -> CalcResult:
    """Walk every nominal size for a material; recommend the smallest pipe whose
    velocity is within limits. Every size is returned in ``options`` with its
    velocity, status, and friction loss."""
    flow_gpm = float(flow_gpm)
    specs = PIPE_SPECS.get(material)
    if not specs:
        return CalcResult(
            calc="size_pipe",
            unit="nominal size",
            inputs={"material": make_input(material, "", "user")},
            citations=[CIT_PIPE],
            warnings=[f"Unknown pipe material {material!r}. Known: {list(PIPE_SPECS)}."],
        )

    options: list[CalcOption] = []
    recommended: str | None = None
    for size, spec in specs.items():
        id_in = spec["id_in"]
        v = flow_gpm * VELOCITY_COEFF / id_in**2
        status = velocity_status(
            v, line, spec["max_suction_fps"], spec["max_discharge_fps"], spec["legal_fps"]
        )
        head_loss = hazen_williams_loss(flow_gpm, length_ft, id_in, c).value
        is_first_ok = recommended is None and status == STATUS_OKAY
        if is_first_ok:
            recommended = size
        options.append(
            CalcOption(
                key=size,
                label=f"{size} {material}",
                value=size,
                recommended=is_first_ok,
                detail={
                    "id_in": id_in,
                    "velocity_fps": round(v, 3),
                    "status": status,
                    "head_loss_ft": round(head_loss, 3),
                },
            )
        )

    warnings = []
    if recommended is None:
        # A tiny flow can be below the self-cleaning band in EVERY size — the
        # smallest run is still the right pick; it just needs a flushing plan.
        settling = next((o for o in options if o.detail.get("status") == STATUS_SETTLING), None)
        if settling:
            recommended = settling.key
            settling.recommended = True
            warnings.append(
                f"{flow_gpm} GPM runs below the {SELF_CLEANING_MIN_FPS:g} FPS self-cleaning "
                f"velocity in every {material} size — use the smallest run and plan for "
                "sediment flushing."
            )
        else:
            warnings.append(
                f"No {material} size keeps {flow_gpm} GPM within the {line} velocity limit; "
                "split the flow across parallel runs or reduce it."
            )
    rec_detail = next((o.detail for o in options if o.key == recommended), {})
    return CalcResult(
        calc="size_pipe",
        value=recommended,
        unit="nominal size",
        inputs={
            "flow": make_input(flow_gpm, "GPM", "prior_calc"),
            "length": make_input(float(length_ft), "ft", "user"),
            "material": make_input(material, "", "user"),
            "line": make_input(line, "", "user"),
        },
        formula="recommend smallest size where V = GPM*0.4085/ID^2 stays within the velocity limit",
        steps=[f"{o.label}: V={o.detail['velocity_fps']} FPS -> {o.detail['status']}" for o in options],
        citations=[CIT_PIPE],
        options=options,
        status=rec_detail.get("status"),
        warnings=warnings,
    )
