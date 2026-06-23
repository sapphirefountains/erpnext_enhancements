"""Water Engineering module.

Fountain / water-feature engineering calculations (the "hydraulic spine":
basin volume & turnover, feature/weir flow, pipe sizing & velocity,
Hazen-Williams friction loss, Total Dynamic Head, pump + electrical selection)
plus the persistent design DocTypes and the desk wizard.

The ``engine`` subpackage is PURE Python (stdlib only) so it is bench-free
unit-testable and can be imported by both the FAC assistant tools and the
whitelisted desk endpoints — one source of truth for the math.
"""
