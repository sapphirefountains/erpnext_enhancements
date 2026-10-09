"""Write every Project Planner Settings default that has no ``tabSingles`` row (v1.578.1).

The Settings had never been saved on production, so ``load_from_db`` built it from ``new_doc()``
with every JSON default. The routing module then cached the shop's coordinates by writing three
``tabSingles`` rows, the first rows the Single ever had, and from that write on every other field
was read from ``tabSingles`` instead. Fields without a row came back blank, and
``_fix_numeric_types`` turns a blank **Check** into 0: *Count drive time against people's hours*
and *Drive times from Google Routes* switched themselves off the first time routing worked.

``check_routes`` caught it on 2026-10-09 ("Google Routes is working ... turned off") from the
MCP sandbox, which rolls its writes back, so no planner had committed the rows yet. This patch
fills every missing default anyway: on a site where a planner load did commit them, it puts the
two switches back to their defaults; on a site that was never saved it makes the Single whole, so
no later write of one field can do this again. ``routing.start_point`` now calls the same function
before it caches anything.

Fill-only: a field with a row keeps its value, an unticked box included. Cannot raise; safe twice.
"""

from erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings import (
	materialize_defaults,
)


def execute():
	materialize_defaults()
