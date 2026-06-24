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


# Pressure / weight specs from DOC-0049 "1, 2, 3 - Pipe Specs" (cols E-M): outside
# diameter, min wall, dry & wet weight per foot, max working temp, and max
# operating pressure at 73 deg F + at 110 deg F (PVC derates to half by 110 deg F).
# Type K copper has only one pressure column (rated at 250 deg F) and no 110 deg F /
# span columns -> psi_110f / max_span_ft are None. Pressure rating is the half of
# pipe selection the velocity check can't see: a run can pass on velocity yet be
# under-rated for the system pressure.
PIPE_PRESSURE_SPECS: dict[str, dict[str, dict]] = {
    "SCH40 PVC": {
        '3/4"': {"od_in": 1.05, "wall_in": 0.113, "id_in": 0.824, "dry_lb_ft": 0.21, "wet_lb_ft": 0.4411, "max_temp_f": 140, "psi_73f": 289, "psi_110f": 144.5, "max_span_ft": 2.5},
        '1"': {"od_in": 1.315, "wall_in": 0.133, "id_in": 1.049, "dry_lb_ft": 0.32, "wet_lb_ft": 0.6945, "max_temp_f": 140, "psi_73f": 270, "psi_110f": 135, "max_span_ft": 2.5},
        '1-1/4"': {"od_in": 1.66, "wall_in": 0.14, "id_in": 1.38, "dry_lb_ft": 0.43, "wet_lb_ft": 1.0781, "max_temp_f": 140, "psi_73f": 221, "psi_110f": 110.5, "max_span_ft": 3},
        '1-1/2"': {"od_in": 1.9, "wall_in": 0.145, "id_in": 1.61, "dry_lb_ft": 0.51, "wet_lb_ft": 1.3922, "max_temp_f": 140, "psi_73f": 198, "psi_110f": 99, "max_span_ft": 3},
        '2"': {"od_in": 2.375, "wall_in": 0.154, "id_in": 2.067, "dry_lb_ft": 0.68, "wet_lb_ft": 2.1341, "max_temp_f": 140, "psi_73f": 166, "psi_110f": 83, "max_span_ft": 3},
        '2-1/2"': {"od_in": 2.875, "wall_in": 0.203, "id_in": 2.469, "dry_lb_ft": 1.07, "wet_lb_ft": 3.1447, "max_temp_f": 140, "psi_73f": 182, "psi_110f": 91, "max_span_ft": 3},
        '3"': {"od_in": 3.5, "wall_in": 0.216, "id_in": 3.068, "dry_lb_ft": 1.41, "wet_lb_ft": 4.6135, "max_temp_f": 140, "psi_73f": 158, "psi_110f": 79, "max_span_ft": 3.5},
        '4"': {"od_in": 4.5, "wall_in": 0.237, "id_in": 4.026, "dry_lb_ft": 2.01, "wet_lb_ft": 7.5265, "max_temp_f": 140, "psi_73f": 133, "psi_110f": 66.5, "max_span_ft": 4},
        '6"': {"od_in": 6.625, "wall_in": 0.28, "id_in": 6.065, "dry_lb_ft": 3.53, "wet_lb_ft": 16.0491, "max_temp_f": 140, "psi_73f": 106, "psi_110f": 53, "max_span_ft": 4.5},
        '8"': {"od_in": 8.625, "wall_in": 0.322, "id_in": 7.981, "dry_lb_ft": 5.39, "wet_lb_ft": 27.0684, "max_temp_f": 140, "psi_73f": 93, "psi_110f": 46.5, "max_span_ft": 4.5},
        '10"': {"od_in": 10.75, "wall_in": 0.365, "id_in": 10.02, "dry_lb_ft": 7.55, "wet_lb_ft": 41.7203, "max_temp_f": 140, "psi_73f": 84, "psi_110f": 42, "max_span_ft": 4.5},
        '12"': {"od_in": 12.75, "wall_in": 0.406, "id_in": 11.938, "dry_lb_ft": 10.01, "wet_lb_ft": 58.5138, "max_temp_f": 140, "psi_73f": 79, "psi_110f": 39.5, "max_span_ft": 4.5},
    },
    "SCH80 PVC": {
        '3/4"': {"od_in": 1.05, "wall_in": 0.154, "id_in": 0.742, "dry_lb_ft": 0.27, "wet_lb_ft": 0.4574, "max_temp_f": 140, "psi_73f": 413, "psi_110f": 206.5, "max_span_ft": 2.5},
        '1"': {"od_in": 1.315, "wall_in": 0.179, "id_in": 0.957, "dry_lb_ft": 0.41, "wet_lb_ft": 0.7217, "max_temp_f": 140, "psi_73f": 378, "psi_110f": 189, "max_span_ft": 3},
        '1-1/4"': {"od_in": 1.66, "wall_in": 0.191, "id_in": 1.278, "dry_lb_ft": 0.52, "wet_lb_ft": 1.0759, "max_temp_f": 140, "psi_73f": 312, "psi_110f": 156, "max_span_ft": 3},
        '1-1/2"': {"od_in": 1.9, "wall_in": 0.2, "id_in": 1.5, "dry_lb_ft": 0.67, "wet_lb_ft": 1.4358, "max_temp_f": 140, "psi_73f": 282, "psi_110f": 141, "max_span_ft": 3.5},
        '2"': {"od_in": 2.375, "wall_in": 0.218, "id_in": 1.939, "dry_lb_ft": 0.95, "wet_lb_ft": 2.2296, "max_temp_f": 140, "psi_73f": 243, "psi_110f": 121.5, "max_span_ft": 3.5},
        '2-1/2"': {"od_in": 2.875, "wall_in": 0.276, "id_in": 2.323, "dry_lb_ft": 1.45, "wet_lb_ft": 3.2866, "max_temp_f": 140, "psi_73f": 255, "psi_110f": 127.5, "max_span_ft": 3.5},
        '3"': {"od_in": 3.5, "wall_in": 0.3, "id_in": 2.9, "dry_lb_ft": 1.94, "wet_lb_ft": 4.8023, "max_temp_f": 140, "psi_73f": 225, "psi_110f": 112.5, "max_span_ft": 4},
        '4"': {"od_in": 4.5, "wall_in": 0.337, "id_in": 3.826, "dry_lb_ft": 2.75, "wet_lb_ft": 7.732, "max_temp_f": 140, "psi_73f": 194, "psi_110f": 97, "max_span_ft": 4.5},
        '6"': {"od_in": 6.625, "wall_in": 0.432, "id_in": 5.761, "dry_lb_ft": 5.42, "wet_lb_ft": 16.7156, "max_temp_f": 140, "psi_73f": 167, "psi_110f": 83.5, "max_span_ft": 5},
        '8"': {"od_in": 8.625, "wall_in": 0.5, "id_in": 7.625, "dry_lb_ft": 8.05, "wet_lb_ft": 27.8376, "max_temp_f": 140, "psi_73f": 148, "psi_110f": 74, "max_span_ft": 5.5},
        '10"': {"od_in": 10.75, "wall_in": 0.593, "id_in": 9.564, "dry_lb_ft": 12, "wet_lb_ft": 43.1309, "max_temp_f": 140, "psi_73f": 140, "psi_110f": 70, "max_span_ft": 5.5},
        '12"': {"od_in": 12.75, "wall_in": 0.687, "id_in": 11.376, "dry_lb_ft": 16.5, "wet_lb_ft": 60.5445, "max_temp_f": 140, "psi_73f": 137, "psi_110f": 68.5, "max_span_ft": 5.5},
    },
    "Type K Copper": {
        '1/2"': {"od_in": 0.625, "wall_in": 0.049, "id_in": 0.527, "dry_lb_ft": 0.344, "wet_lb_ft": 0.438, "max_temp_f": 250, "psi_73f": 85, "psi_110f": None, "max_span_ft": None},
        '3/4"': {"od_in": 0.875, "wall_in": 0.065, "id_in": 0.745, "dry_lb_ft": 0.641, "wet_lb_ft": 0.829, "max_temp_f": 250, "psi_73f": 85, "psi_110f": None, "max_span_ft": None},
        '1"': {"od_in": 1.125, "wall_in": 0.065, "id_in": 0.995, "dry_lb_ft": 0.839, "wet_lb_ft": 1.18, "max_temp_f": 250, "psi_73f": 85, "psi_110f": None, "max_span_ft": None},
        '1-1/4"': {"od_in": 1.375, "wall_in": 0.065, "id_in": 1.245, "dry_lb_ft": 1.04, "wet_lb_ft": 1.57, "max_temp_f": 250, "psi_73f": 75, "psi_110f": None, "max_span_ft": None},
        '1-1/2"': {"od_in": 1.625, "wall_in": 0.072, "id_in": 1.481, "dry_lb_ft": 1.36, "wet_lb_ft": 2.1, "max_temp_f": 250, "psi_73f": 75, "psi_110f": None, "max_span_ft": None},
        '2"': {"od_in": 2.125, "wall_in": 0.083, "id_in": 1.959, "dry_lb_ft": 2.06, "wet_lb_ft": 3.36, "max_temp_f": 250, "psi_73f": 75, "psi_110f": None, "max_span_ft": None},
        '2-1/2"': {"od_in": 2.625, "wall_in": 0.095, "id_in": 2.435, "dry_lb_ft": 2.93, "wet_lb_ft": 4.94, "max_temp_f": 250, "psi_73f": 50, "psi_110f": None, "max_span_ft": None},
        '3"': {"od_in": 3.125, "wall_in": 0.109, "id_in": 2.907, "dry_lb_ft": 4, "wet_lb_ft": 6.87, "max_temp_f": 250, "psi_73f": 50, "psi_110f": None, "max_span_ft": None},
        '4"': {"od_in": 4.125, "wall_in": 0.134, "id_in": 3.857, "dry_lb_ft": 6.51, "wet_lb_ft": 11.6, "max_temp_f": 250, "psi_73f": 50, "psi_110f": None, "max_span_ft": None},
        '6"': {"od_in": 6.125, "wall_in": 0.192, "id_in": 5.741, "dry_lb_ft": 13.9, "wet_lb_ft": 25.1, "max_temp_f": 250, "psi_73f": 45, "psi_110f": None, "max_span_ft": None},
    },
}


def get_pipe_pressure(material: str, size: str) -> dict | None:
    """Pressure/weight spec {od_in, wall_in, dry_lb_ft, wet_lb_ft, max_temp_f,
    psi_73f, psi_110f, max_span_ft} for one material+size, or None."""
    return PIPE_PRESSURE_SPECS.get(material, {}).get(size)
