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
			<div id="ga4-dashboard-grid" style="display: none; padding: 15px;">
				<style>
					.ga4-grid-container {
						display: grid;
						grid-template-columns: 1fr 1fr;
						grid-gap: 20px;
					}
					.ga4-grid-item-full {
						grid-column: 1 / -1;
					}
					.ga4-chart-box {
						background: var(--card-bg);
						border: 1px solid var(--border-color);
						border-radius: var(--border-radius);
						padding: 15px;
					}
				</style>
				<div class="ga4-grid-container">
					<div class="ga4-grid-item-full ga4-chart-box">
						<div id="ga4-traffic-chart"></div>
					</div>
					<div class="ga4-chart-box">
						<div id="ga4-acquisition-chart"></div>
					</div>
					<div class="ga4-chart-box">
						<div id="ga4-conversions-chart"></div>
					</div>
				</div>
			</div>
		</div>
	`;
	$(dashboard_html).appendTo(page.main);

	page.main.find('#ga4-loading').show();
	page.main.find('#ga4-dashboard-grid').hide();

	frappe.call({
		method: 'erpnext_enhancements.api.analytics.get_ga4_data',
		callback: function(r) {
			page.main.find('#ga4-loading').hide();

			if (r.message && r.message.traffic_timeline && r.message.acquisition_channels && r.message.conversions) {
				page.main.find('#ga4-dashboard-grid').show();

				// Traffic Timeline Chart (Line)
				new frappe.Chart(page.main.find('#ga4-traffic-chart')[0], {
					data: r.message.traffic_timeline,
					title: "Traffic Timeline (Last 30 Days)",
					type: 'line',
					height: 300,
					colors: ['#7cd6fd', '#743ee2']
				});

				// Acquisition Channels Chart (Donut/Pie)
				new frappe.Chart(page.main.find('#ga4-acquisition-chart')[0], {
					data: r.message.acquisition_channels,
					title: "Acquisition Channels (Last 30 Days)",
					type: 'donut',
					height: 300,
					colors: ['#7cd6fd', '#743ee2', '#5e64ff', '#28a745', '#ff5858', '#ffa00a']
				});

				// Conversions Chart (Bar)
				new frappe.Chart(page.main.find('#ga4-conversions-chart')[0], {
					data: r.message.conversions,
					title: "Conversions (Last 30 Days)",
					type: 'bar',
					height: 300,
					colors: ['#28a745']
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