frappe.pages['ga4-dashboard'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'GA4 Dashboard',
		single_column: true
	});

	// Append HTML dynamically to avoid relying on standard page template rendering which doesn't
	// work seamlessly when overriding with make_app_page and clearing the wrapper.
	var dashboard_html = `
		<div class="ga4-dashboard-wrapper">
			<div id="ga4-loading" class="text-center" style="padding: 20px;">
				<p>Loading GA4 Data...</p>
			</div>
			<div id="ga4-chart-container" style="display: none;"></div>
		</div>
	`;
	$(dashboard_html).appendTo(page.main);

	page.main.find('#ga4-loading').show();
	page.main.find('#ga4-chart-container').hide();

	frappe.call({
		method: 'erpnext_enhancements.api.analytics.get_ga4_data',
		callback: function(r) {
			page.main.find('#ga4-loading').hide();

			if (r.message && r.message.labels && r.message.datasets) {
				page.main.find('#ga4-chart-container').show();

				let chart = new frappe.Chart(page.main.find('#ga4-chart-container')[0], {
					data: {
						labels: r.message.labels,
						datasets: r.message.datasets
					},
					title: "Active Users (Last 30 Days)",
					type: 'line',
					height: 300,
					colors: ['#7cd6fd']
				});
			} else {
				frappe.msgprint(__('No data returned from GA4 API.'));
			}
		},
		error: function(r) {
			page.main.find('#ga4-loading').html('<p class="text-danger">Failed to load GA4 Data.</p>');
		}
	});
};