"""Water Engineering module.

The Fountain Designer: fountain, pool and water-feature engineering (hydraulics,
electrical and controls, structure, treatment), the schema-2 design model stored on
Water Feature Design, the guided page, the prints and the AI tools. README.md is the
map; ADR 0018 records the rebuild, and the retirement of the old desk wizard and its
nine child DocTypes in v1.771.0.

The ``engine`` subpackage is PURE Python (stdlib only) so it is bench-free
unit-testable and is called by the page's API, the record's save and the FAC
assistant tools alike — one source of truth for the math.
"""
