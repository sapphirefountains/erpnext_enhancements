"""Gravity-drain pipe table.

GRAVITY_PIPES: nominal size -> inside diameter (in) + Manning's n, verified from
DOC-0049 ``SUPPORT`` named range ``GravityPipes``. The half-full area and
hydraulic radius are derived from the diameter in the engine (area = 3.14·D²/8/144,
R = (D/4)/12) so they reproduce the workbook cells exactly.
"""

from __future__ import annotations

# nominal size -> {"id_in": inside diameter, "n": Manning's roughness}
GRAVITY_PIPES: dict[str, dict] = {
    '1-1/2"': {"id_in": 1.61, "n": 0.012},
    '2"': {"id_in": 2.067, "n": 0.013},
    '2-1/2"': {"id_in": 2.469, "n": 0.013},
    '3"': {"id_in": 3.068, "n": 0.013},
    '4"': {"id_in": 4.026, "n": 0.014},
    '6"': {"id_in": 6.065, "n": 0.015},
    '8"': {"id_in": 7.981, "n": 0.016},
    '10"': {"id_in": 10.02, "n": 0.016},
    '12"': {"id_in": 11.938, "n": 0.016},
}
