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
	def apply_fieldlevel_read_permissions(self):
		"""Every document read (desk form, ``/api/resource``, ``frappe.client.get``) ends here.
		A caller without Accounts Manager or System Manager reads the snapshot without its
		revenue and margin figures (``kpi_dashboards/money.py``)."""
		super().apply_fieldlevel_read_permissions()
		from erpnext_enhancements.kpi_dashboards import money

		if not money.can_see_revenue():
			self.values = money.without_revenue(self.values)
