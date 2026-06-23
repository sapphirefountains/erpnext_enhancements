"""Pipe inside-diameter + velocity-limit tables.

Verified from DOC-0049 ``SUPPORT`` named range ``PipeType`` ($G$5:$K$38):
each row = (size label, inside diameter in, max suction FPS, max discharge FPS,
legally-defensible FPS). Suction limit is 4.5 FPS for every material; discharge
and legal limits differ between PVC (6.5 / 8.0) and copper (4.9 / 5.0).
"""

from __future__ import annotations

_MAX_SUCTION_FPS = 4.5


def _material(max_discharge_fps: float, legal_fps: float, ids: dict[str, float]) -> dict[str, dict]:
    return {
        size: {
            "id_in": id_in,
            "max_suction_fps": _MAX_SUCTION_FPS,
            "max_discharge_fps": max_discharge_fps,
            "legal_fps": legal_fps,
        }
        for size, id_in in ids.items()
    }


# Inside diameters (inches) by nominal size, in ascending order.
PIPE_SPECS: dict[str, dict[str, dict]] = {
    "SCH40 PVC": _material(
        6.5,
        8.0,
        {
            '3/4"': 0.824,
            '1"': 1.049,
            '1-1/4"': 1.38,
            '1-1/2"': 1.61,
            '2"': 2.067,
            '2-1/2"': 2.469,
            '3"': 3.068,
            '4"': 4.026,
            '6"': 6.065,
            '8"': 7.981,
            '10"': 10.02,
            '12"': 11.938,
        },
    ),
    "SCH80 PVC": _material(
        6.5,
        8.0,
        {
            '3/4"': 0.742,
            '1"': 0.957,
            '1-1/4"': 1.278,
            '1-1/2"': 1.5,
            '2"': 1.939,
            '2-1/2"': 2.323,
            '3"': 2.9,
            '4"': 3.826,
            '6"': 5.761,
            '8"': 7.625,
            '10"': 9.564,
            '12"': 11.376,
        },
    ),
    "Type K Copper": _material(
        4.9,
        5.0,
        {
            '1/2"': 0.527,
            '3/4"': 0.745,
            '1"': 0.995,
            '1-1/4"': 1.245,
            '1-1/2"': 1.481,
            '2"': 1.959,
            '2-1/2"': 2.435,
            '3"': 2.907,
            '4"': 3.857,
            '6"': 5.741,
        },
    ),
}

# Default material used when a caller doesn't specify one.
DEFAULT_PIPE_MATERIAL = "SCH40 PVC"


def get_pipe_spec(material: str, size: str) -> dict | None:
    """Return the {id_in, limits} spec for one material+size, or None."""
    return PIPE_SPECS.get(material, {}).get(size)


def get_pipe_id(material: str, size: str) -> float | None:
    """Inside diameter (inches) for one material+size, or None if unknown."""
    spec = get_pipe_spec(material, size)
    return spec["id_in"] if spec else None
