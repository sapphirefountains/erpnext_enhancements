// Marketing KPIs — focused, shareable, role-gated department KPI page.
// Renders the latest KPI Snapshot for the Marketing department via the shared
// renderer (public/js/kpi_dashboard_page.bundle.js).
frappe.pages['marketing-kpi'].on_page_load = function (wrapper) {
	erpnext_enhancements.kpi_page.render(wrapper, 'Marketing');
};
