// Product KPIs — focused, shareable, role-gated department KPI page.
// Renders the latest KPI Snapshot for the Product department via the shared
// renderer (public/js/kpi_dashboard_page.bundle.js).
frappe.pages['product-kpi'].on_page_load = function (wrapper) {
	erpnext_enhancements.kpi_page.render(wrapper, 'Product');
};
