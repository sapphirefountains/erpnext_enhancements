// Finance KPIs — focused, shareable, role-gated department KPI page.
// Renders the latest KPI Snapshot for the Finance department via the shared
// renderer (public/js/kpi_dashboard_page.bundle.js).
frappe.pages['finance-kpi'].on_page_load = function (wrapper) {
	erpnext_enhancements.kpi_page.render(wrapper, 'Finance');
};
