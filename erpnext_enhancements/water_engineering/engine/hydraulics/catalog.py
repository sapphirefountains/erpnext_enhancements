"""Pipe materials, sizes and the resolved pipe spec the solver runs on.

Port of Digital Rain Curtain ``src/hydro/common.js`` ``MATERIALS``, ``sizesFor``,
``pipeSpec``; golden file ``tests/golden/drc/catalog.json`` (1e-12).

**DOC-0049 governs the bore** (Nikolas, 2026-11-01, audit decision D2). DRC builds
each bore as NPS outside diameter minus two walls from its own metric table (Sch 40
2" = 52.48 mm = 2.066"). This port takes the inside diameter straight from DOC-0049
"1, 2, 3 - Pipe Specs" (``engine/data/pipe_specs.py``: Sch 40 2" = 2.067"), with its
OD and wall, for every material DOC-0049 tabulates: Sch 40 PVC, Sch 80 PVC, Type K
copper, and galvanized steel Sch 40 (ASME B36.10 Sch 40 shares OD and wall with
ASTM D1785 Sch 40, so it takes the Sch 40 rows; it does *not* take the PVC pressure
ratings). DRC's OD-minus-wall figures are not used for those. The golden generator
patches the same DOC-0049 rows into DRC's catalog before it runs
(``tools/golden/drc/drc.mjs``), so every golden that touches one of these materials
was regenerated with the DOC-0049 ID. Sizes DOC-0049 does not list (PVC 1/4"-1/2")
are not offered; a custom bore covers them.

Copper type L, PEX and hose keep DRC's tables (DOC-0049 has no rows for them).

The DOC-0049 velocity limits and PSI ratings stay the authority: ``pipe_spec_si``
attaches them from ``engine/data/pipe_specs.py`` where DOC-0049 has them, and
leaves them ``None`` where it does not (steel ratings, PEX, hose).

Absolute roughness and elastic modulus are DRC's: PVC and drawn copper 0.0015 mm
(smooth), PEX 0.007 mm, galvanized steel 0.15 mm, hose 0.005 mm.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..data.pipe_specs import PIPE_PRESSURE_SPECS, PIPE_SPECS
from ..envelope import CalcResult, make_input
from . import _js
from .units import CIT_DRC, MM_PER_IN
from .water import area_of_si

CIT_PIPE_SPECS = "DOC-0049 / 1,2,3 - Pipe Specs (OD, wall, ID, ratings) ; SUPPORT PipeType (velocity limits)"
CIT_ROUGHNESS = "Absolute roughness: Moody (1944) / Crane TP-410 smooth-pipe values, as tabulated in DRC common.js"

# DRC common.js tables for the materials DOC-0049 does not tabulate: [nominal, OD mm, wall mm].
_CU_L = [
    ("1/4", 9.53, 0.76), ("3/8", 12.7, 0.89), ("1/2", 15.9, 1.02), ("3/4", 22.2, 1.14), ("1", 28.6, 1.27),
    ("1-1/4", 34.9, 1.40), ("1-1/2", 41.3, 1.52), ("2", 54.0, 1.78), ("2-1/2", 66.7, 2.03), ("3", 79.4, 2.29),
    ("4", 104.8, 2.79),
]
_PEX = [
    ("3/8", 12.7, 1.78), ("1/2", 15.9, 1.78), ("3/4", 22.2, 2.47), ("1", 28.6, 3.18), ("1-1/4", 34.9, 3.88),
    ("1-1/2", 41.3, 4.59), ("2", 54.0, 6.0),
]
_HOSE = [
    (n, bore + 7, 3.5)
    for n, bore in (("1/2", 12.7), ("5/8", 15.9), ("3/4", 19.1), ("1", 25.4), ("1-1/4", 31.8), ("1-1/2", 38.1), ("2", 50.8))
]


def _doc0049_rows(material: str) -> list[tuple]:
    """[(nominal, od_mm, wall_mm, id_mm)] from DOC-0049, nominal without the inch mark."""
    return [
        (size.rstrip('"'), row["od_in"] * MM_PER_IN, row["wall_in"] * MM_PER_IN, row["id_in"] * MM_PER_IN)
        for size, row in PIPE_PRESSURE_SPECS[material].items()
    ]


def _drc_rows(rows: list[tuple]) -> list[tuple]:
    """DRC rows with DRC's own bore, OD - 2 * wall (unrounded, as pipeSpec uses it)."""
    return [(n, od, wall, od - 2 * wall) for n, od, wall in rows]


# Materials in DRC's order. ``drc`` is the DRC key (and what saved DRC projects carry), ``limits`` the
# DOC-0049 material whose velocity limits apply, ``ratings`` the DOC-0049 material whose PSI ratings,
# weights and spans apply (None where DOC-0049 has none).
MATERIALS: dict[str, dict] = {
    "SCH40 PVC": {"drc": "pvc40", "label": "PVC Sch 40", "eps_mm": 0.0015, "e_gpa": 2.9, "density": 1400,
                  "sizes": _doc0049_rows("SCH40 PVC"), "source": "DOC-0049", "limits": "SCH40 PVC", "ratings": "SCH40 PVC"},
    "SCH80 PVC": {"drc": "pvc80", "label": "PVC Sch 80", "eps_mm": 0.0015, "e_gpa": 2.9, "density": 1400,
                  "sizes": _doc0049_rows("SCH80 PVC"), "source": "DOC-0049", "limits": "SCH80 PVC", "ratings": "SCH80 PVC"},
    "Type L Copper": {"drc": "copperL", "label": "Copper type L", "eps_mm": 0.0015, "e_gpa": 117, "density": 8940,
                      "sizes": _drc_rows(_CU_L), "source": "DRC common.js (ASTM B88 type L)", "limits": "Type K Copper",
                      "ratings": None},
    "PEX": {"drc": "pex", "label": "PEX (CTS, SDR 9)", "eps_mm": 0.007, "e_gpa": 0.8, "density": 940,
            "sizes": _drc_rows(_PEX), "source": "DRC common.js (ASTM F876 CTS SDR 9)", "limits": None, "ratings": None},
    "SCH40 Galvanized Steel": {"drc": "steel40", "label": "Galvanized steel Sch 40", "eps_mm": 0.15, "e_gpa": 200,
                               "density": 7850, "sizes": _doc0049_rows("SCH40 PVC"),
                               "source": "DOC-0049 Sch 40 dimensions (ASME B36.10)", "limits": None, "ratings": None},
    "Hose": {"drc": "hose", "label": "Flexible PVC / rubber hose", "eps_mm": 0.005, "e_gpa": 0.05, "density": 1300,
             "sizes": _drc_rows(_HOSE), "source": "DRC common.js (nominal bore + 7 mm OD)", "limits": None, "ratings": None},
    "Type K Copper": {"drc": "copperK", "label": "Copper type K", "eps_mm": 0.0015, "e_gpa": 117, "density": 8940,
                      "sizes": _doc0049_rows("Type K Copper"), "source": "DOC-0049", "limits": "Type K Copper",
                      "ratings": "Type K Copper"},
    "Custom": {"drc": "custom", "label": "Custom bore", "eps_mm": 0.0015, "e_gpa": 2.9, "density": 1400, "sizes": [],
               "source": "user", "limits": None, "ratings": None},
}

_ALIASES = {k.lower(): k for k in MATERIALS}
_ALIASES.update({m["drc"].lower(): k for k, m in MATERIALS.items()})
_ALIASES.update({m["label"].lower(): k for k, m in MATERIALS.items()})


def resolve_material(material) -> str | None:
    """Canonical material name for a canonical name, DRC key or label (any case), else None."""
    if not isinstance(material, str):
        return None
    return _ALIASES.get(material.strip().lower())


def normalize_nominal(nominal) -> str | None:
    """'2"', '2', '2 in', '1-1/2"' -> '2', '1-1/2' (DRC's key form); None for anything else."""
    if not isinstance(nominal, str) or not nominal.strip():
        return None
    n = nominal.strip()
    for suffix in ('"', "in.", "in", "''"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n.replace(" ", "-") or None


@dataclass(frozen=True, slots=True)
class PipeSpec:
    """A resolved pipe: bore, wall, roughness, modulus, and the DOC-0049 limits that apply."""

    material: str
    nominal: str | None  # '2"' form, None for a custom bore
    label: str
    od_mm: float
    id_mm: float
    wall_mm: float
    eps_mm: float
    e_gpa: float
    density: float
    id_m: float
    area: float  # m^2
    source: str
    max_suction_fps: float | None = None
    max_discharge_fps: float | None = None
    legal_fps: float | None = None
    psi_73f: float | None = None
    psi_110f: float | None = None
    wet_lb_ft: float | None = None
    max_span_ft: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def sizes_for(material) -> list[dict]:
    """``sizesFor``: the catalog sizes of a material, smallest first.

    DOC-0049 materials report the DOC-0049 ID unrounded; the others report DRC's
    ``+(od - 2 * wall).toFixed(2)``, as DRC's size picker shows it."""
    key = resolve_material(material)
    if not key:
        return []
    m = MATERIALS[key]
    doc = m["source"].startswith("DOC-0049")
    return [
        {"nps": n, "nominal": f'{n}"', "od_mm": od, "id_mm": bore if doc else _js.to_fixed(od - 2 * wall, 2), "wall_mm": wall}
        for n, od, wall, bore in m["sizes"]
    ]


def pipe_spec_si(material, nominal=None, id_mm_fallback=25, wall_mm_fallback=3) -> PipeSpec:
    """``pipeSpec``: the catalog size when (material, nominal) is known, else the fallback bore and wall."""
    key = resolve_material(material)
    m = MATERIALS[key] if key else MATERIALS["Custom"]
    nps = normalize_nominal(nominal)
    row = None
    if key != "Custom" and nps is not None:
        row = next((r for r in m["sizes"] if r[0] == nps), None)
    if row:
        od_mm, wall_mm, id_mm = row[1], row[2], row[3]
    else:
        fb_id, fb_wall = _js.num(id_mm_fallback), _js.num(wall_mm_fallback)
        id_mm = fb_id if fb_id > 0 else 25.0
        wall_mm = fb_wall if fb_wall > 0 else 3.0
        od_mm = id_mm + 2 * wall_mm
    nominal_out = f'{nps}"' if row else None
    limits = PIPE_SPECS.get(m["limits"] or "", {}).get(nominal_out or "") if row else None
    ratings = PIPE_PRESSURE_SPECS.get(m["ratings"] or "", {}).get(nominal_out or "") if row else None
    return PipeSpec(
        material=key or "Custom",
        nominal=nominal_out,
        label=m["label"],
        od_mm=od_mm,
        id_mm=id_mm,
        wall_mm=wall_mm,
        eps_mm=m["eps_mm"],
        e_gpa=m["e_gpa"],
        density=m["density"],
        id_m=id_mm / 1000,
        area=area_of_si(id_mm / 1000),
        source=m["source"] if row else "custom bore",
        max_suction_fps=limits["max_suction_fps"] if limits else None,
        max_discharge_fps=limits["max_discharge_fps"] if limits else None,
        legal_fps=limits["legal_fps"] if limits else None,
        psi_73f=ratings["psi_73f"] if ratings else None,
        psi_110f=ratings["psi_110f"] if ratings else None,
        wet_lb_ft=ratings["wet_lb_ft"] if ratings else None,
        max_span_ft=ratings["max_span_ft"] if ratings else None,
    )


def pipe_spec(material: str, nominal_size: str = "", id_in: float | None = None, wall_in: float | None = None) -> CalcResult:
    """The resolved pipe (US units): ID, OD, wall, roughness and the DOC-0049 limits that apply.

    A size the catalog does not hold falls back to ``id_in`` / ``wall_in`` (a custom bore)."""
    spec = pipe_spec_si(
        material,
        nominal_size,
        id_in * MM_PER_IN if id_in else 0,
        wall_in * MM_PER_IN if wall_in else 0,
    )
    warnings = []
    if spec.nominal is None:
        if id_in:
            warnings.append(f"{material} {nominal_size or ''} is not in the catalog; using a custom bore of {id_in:g} in.")
        else:
            warnings.append(
                f"{material} {nominal_size or ''} is not in the catalog and no bore was given; "
                "the solver's 25 mm default bore was used. Pick a catalog size or enter the ID."
            )
    if spec.max_discharge_fps is None and spec.nominal:
        warnings.append(f"DOC-0049 has no velocity limits for {spec.label}; size it against the manufacturer's data.")
    value = {
        "material": spec.material,
        "nominal_size": spec.nominal,
        "id_in": spec.id_mm / MM_PER_IN,
        "od_in": spec.od_mm / MM_PER_IN,
        "wall_in": spec.wall_mm / MM_PER_IN,
        "roughness_in": spec.eps_mm / MM_PER_IN,
        "area_in2": spec.area / (MM_PER_IN / 1000) ** 2,
        "modulus_gpa": spec.e_gpa,
        "max_suction_fps": spec.max_suction_fps,
        "max_discharge_fps": spec.max_discharge_fps,
        "legal_fps": spec.legal_fps,
        "psi_73f": spec.psi_73f,
        "psi_110f": spec.psi_110f,
        "wet_lb_ft": spec.wet_lb_ft,
        "max_span_ft": spec.max_span_ft,
        "source": spec.source,
    }
    return CalcResult(
        calc="pipe_spec",
        value=value,
        unit="",
        inputs={
            "material": make_input(material, "", "user"),
            "nominal_size": make_input(nominal_size, "", "user"),
        },
        formula="ID, OD, wall from DOC-0049 Pipe Specs where tabulated; roughness eps from the material",
        steps=[
            f"{spec.label} {spec.nominal or 'custom'}: ID {value['id_in']:.3f} in, OD {value['od_in']:.3f} in, "
            f"wall {value['wall_in']:.3f} in ({spec.source})",
            f"eps = {spec.eps_mm:g} mm -> eps/D = {spec.eps_mm / spec.id_mm:.3g}",
        ],
        citations=[CIT_PIPE_SPECS, CIT_ROUGHNESS, CIT_DRC],
        warnings=warnings,
    )
