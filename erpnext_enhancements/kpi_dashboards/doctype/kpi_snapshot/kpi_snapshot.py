"""KPI Snapshot — one precomputed row per (department, period, day).

The durable backbone of the department KPI dashboards. The nightly scheduler
(``kpi_dashboards.snapshots.scheduled_kpi_run``) writes one snapshot per
department; each carries a child ``values`` table of the individual KPIs with
their value, target, status, trend, and source-freshness flags.

Durable (not ``frappe.cache``) for the same reason as Daily Briefing: Redis is
flushed by ``bench migrate`` / ``clear-cache``, exactly when a deploy churns.
The ``format:KPI-{department}-{period}-{snapshot_date}`` autoname enforces one
row per department/period/day (idempotent re-runs). Old rows are purged by
``snapshots.purge_old_snapshots``.
"""

from frappe.model.document import Document


class KPISnapshot(Document):
	pass
