"""Helpers for the DRC golden-vector suites (``test_hydraulics_*.py``).

The Python counterpart of ``aquafect/crates/aquafect-core/tests/support/mod.rs``.
The vectors in ``tests/golden/drc/*.json`` are written by
``tools/golden/drc/gen_<module>.mjs`` from the Digital Rain Curtain's JavaScript,
with the DOC-0049 pipe table patched in (see ``tools/golden/drc/drc.mjs``). CI needs
no Node: the JSON is committed and regenerating it is a manual step.

* ``close(got, want, rel)``: relative to the larger magnitude; NaN equals NaN;
  +-inf must match exactly.
* ``REL = 1e-12`` for closed-form math, ``SOLVE = 1e-9`` for bisection results.
* ``close_f32``: a JS ``Float32Array`` value, compared within one float32 ulp.
* The generators write NaN and +-Infinity as the strings "NaN", "Infinity",
  "-Infinity"; ``num`` decodes them.
* ``seg_from_drc`` / ``pump_from_drc`` turn a DRC segment or pump into the
  kernels' inputs (DRC material and fitting keys mapped to the Python ones).
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden" / "drc"

REL = 1e-12
SOLVE = 1e-9


def golden(name: str) -> dict:
    """``tests/golden/drc/<name>.json`` (key order kept, which the fittings sums depend on)."""
    with open(GOLDEN / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def num(v) -> float:
    """A number from the vectors; decodes the generators' "NaN" / "Infinity" / "-Infinity"."""
    if isinstance(v, str):
        return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[v]
    if v is None:
        raise AssertionError("expected a number, got null")
    return float(v)


def close(got, want, rel: float, what: str = "") -> None:
    """``got`` within ``rel`` of ``want``, relative to the larger magnitude."""
    w = num(want)
    g = float(got)
    if math.isnan(w):
        ok = math.isnan(g)
    elif math.isinf(w) or g == w:
        ok = g == w
    else:
        ok = abs(g - w) <= rel * max(abs(g), abs(w))
    if not ok:
        diff = abs((g - w) / w) if w and math.isfinite(w) else float("nan")
        raise AssertionError(f"{what}: python {g!r} vs js {w!r} (rel diff {diff:.3e})")


def close_all(got, want, rel: float, what: str = "") -> None:
    assert len(got) == len(want), f"{what}: length {len(got)} vs {len(want)}"
    for i, (g, w) in enumerate(zip(got, want, strict=False)):
        close(g, w, rel, f"{what}[{i}]")


def _f32_bits(x: float) -> int:
    return struct.unpack("<i", struct.pack("<f", x))[0]


def close_f32(got, want, what: str = "") -> None:
    """Float32 values (a JS ``Float32Array``) within one float32 ulp of each other."""
    assert len(got) == len(want), f"{what}: length"
    for i, (g, w) in enumerate(zip(got, want, strict=False)):
        g32 = struct.unpack("<f", struct.pack("<f", float(g)))[0]
        w32 = struct.unpack("<f", struct.pack("<f", num(w)))[0]
        if g32 == w32:
            continue
        ulps = abs(_f32_bits(g32) - _f32_bits(w32))
        assert (g32 < 0) == (w32 < 0) and ulps <= 1, f"{what}[{i}]: python {g32!r} vs js {w32!r} ({ulps} ulp)"


# ---------------------------------------------------------------------------
# DRC inputs -> kernel inputs
# ---------------------------------------------------------------------------


def seg_from_drc(s: dict):
    """A DRC segment ``{mat, nps, idMm, wallMm, lenM, share, kExtra, fit}`` as a hydraulics Segment."""
    from erpnext_enhancements.water_engineering.engine.hydraulics.catalog import pipe_spec_si
    from erpnext_enhancements.water_engineering.engine.hydraulics.segment import Segment

    spec = pipe_spec_si(s.get("mat"), s.get("nps"), s.get("idMm", 25), s.get("wallMm", 3))
    length = s.get("lenM", 0)
    return Segment(
        spec=spec,
        length_m=math.nan if length is None else num(length),
        branches=2 if s.get("share") == "half" else 1,
        k_extra=num(s.get("kExtra") or 0),
        fittings=tuple((s.get("fit") or {}).items()),
        label=s.get("name", ""),
    )


def pump_from_drc(pump: dict, legacy: dict | None = None, water_c: float = 20.0):
    """A DRC pump ``{curve, eff, npshr, motorEff, speedPct}`` as a hydraulics PumpCurve; ``legacy``
    (``legacyPump(P)``: pShut Pa, qMax m^3/s) becomes the quadratic fallback, as head at the water's rho."""
    from erpnext_enhancements.water_engineering.engine.hydraulics.pump import PumpCurve
    from erpnext_enhancements.water_engineering.engine.hydraulics.units import G
    from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si

    quad = None
    if legacy:
        quad = (legacy["pShut"] / (water_si(water_c).rho * G), legacy["qMax"])
    return PumpCurve(
        curve=pump.get("curve") or [],
        eff=pump.get("eff") or [],
        npshr=pump.get("npshr") or [],
        motor_eff_pct=80 if pump.get("motorEff") is None else pump["motorEff"],
        speed_pct=100 if pump.get("speedPct") is None else pump["speedPct"],
        quad=quad,
    )


def check_fields(got: dict, want: dict, rel: float, what: str, exact=()) -> None:
    """Every key of ``want``: numbers within ``rel``, keys in ``exact`` (bools, strings) equal."""
    for k, w in want.items():
        assert k in got, f"{what}.{k}: missing"
        if k in exact or isinstance(w, bool) or (isinstance(w, str) and w not in ("NaN", "Infinity", "-Infinity")):
            assert got[k] == w, f"{what}.{k}: python {got[k]!r} vs js {w!r}"
        else:
            close(got[k], w, rel, f"{what}.{k}")


def manifold_from_drc(P: dict):
    """The rain-curtain manifold of a DRC project, from the fields the manifold, surge and basin
    generators record (``valves, pitchMm, orificeMm, cv, v0, nozzleCc, ..., manMat, manNps, pipeIdMm``)."""
    from erpnext_enhancements.water_engineering.engine.hydraulics.manifold import (
        curtain_manifold,
        curtain_spec,
    )

    spec = curtain_spec(P["manMat"], P["manNps"], P["pipeIdMm"], P["manWallMm"])
    return curtain_manifold(
        spec=spec, valves=P["valves"], pitch_mm=P["pitchMm"], orifice_mm=P["orificeMm"], w_c=P["waterC"], cc=P["nozzleCc"],
        cv=P["cv"], v0=P["v0"], valve_kv=P["valveKv"], tube_id_mm=P["tubeIdMm"], tube_len_m=P["tubeLenM"], tilt_mm=P["tiltMm"],
        sag=P["sag"], hanger_m=P["hangerM"], nozzle_below_m=P["nozzleBelowM"], regain=P["regain"], feed=P["feed"],
    )
