"""Water-chemistry reference tables.

CONTACT_TANKS: ozone contact-tank catalog (label -> volume gal, max GPM),
verified from DOC-0049 ``SUPPORT`` named range ``ContactTanks``.

CHEM_TARGETS: water-balance target ranges (free chlorine, pH, cyanuric acid) by
water type, from DOC-0119 guidance.
"""

from __future__ import annotations

# label -> {"volume_gal": ..., "max_gpm": ...}  (DOC-0049 SUPPORT!ContactTanks)
CONTACT_TANKS: dict[str, dict] = {
    "CNC15": {"volume_gal": 10, "max_gpm": 80, "label": "CNC15, 10 GAL Column, 2\" Ports"},
    "CNT30": {"volume_gal": 30, "max_gpm": 40, "label": "CNT30, 30 GAL Tank, 1.25\" Ports"},
    "CNT40": {"volume_gal": 40, "max_gpm": 40, "label": "CNT40, 40 GAL Tank, 1.25\" Ports"},
    "CNT80": {"volume_gal": 80, "max_gpm": 40, "label": "CNT80, 80 GAL Tank, 1.25\" Ports"},
    "CNT120": {"volume_gal": 120, "max_gpm": 40, "label": "CNT120, 120 GAL Tank, 1.25\" Ports"},
    "CNT264": {"volume_gal": 264, "max_gpm": 180, "label": "CNT264, 264 GAL Tank, 3\" Ports"},
    "CNT463": {"volume_gal": 463, "max_gpm": 180, "label": "CNT463, 463 GAL Tank, 3\" Ports"},
    "CNT850": {"volume_gal": 850, "max_gpm": 300, "label": "CNT850, 850 GAL Tank, 4\" Ports"},
}

# Water-balance target ranges by water type (DOC-0119). Each range is (min, max);
# the free-chlorine floor also tracks ~7.5% of the cyanuric-acid (CYA) level.
CHEM_TARGETS: dict[str, dict] = {
    "outdoor": {"free_cl_ppm": (1.0, 3.0), "ph": (7.2, 7.8), "cya_ppm": (30, 50)},
    "indoor": {"free_cl_ppm": (2.0, 4.0), "ph": (7.2, 7.8), "cya_ppm": (0, 0)},
    "saltwater": {"free_cl_ppm": (1.0, 3.0), "ph": (7.2, 7.8), "cya_ppm": (60, 80)},
}
