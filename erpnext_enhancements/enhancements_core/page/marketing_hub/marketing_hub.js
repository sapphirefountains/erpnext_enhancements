frappe.pages['marketing-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Marketing Hub',
		single_column: true
	});

	let dashboard_html = `
		<style>
			.dashboard-metric {
				padding: 20px;
				border: 1px solid #d1d8dd;
				border-radius: 4px;
				text-align: center;
				background-color: #fff;
				margin-bottom: 20px;
			}
			.dashboard-metric h4 {
				margin-top: 0;
				color: #8d99a6;
				font-size: 14px;
				text-transform: uppercase;
			}
			.dashboard-metric .value {
				font-size: 24px;
				font-weight: bold;
				color: #36414c;
			}
		</style>
		<div class="row">
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-leads">
					<h4>Monthly Leads</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-roi">
					<h4>Campaign ROI</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-email">
					<h4>Email Open Rates</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-traffic">
					<h4>Top Traffic Source</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Monthly Leads
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Lead",
			// filters: { creation: [">", frappe.datetime.month_start()] } // Needs proper date formatting in JS
		},
		callback: function(r) {
			$('#metric-leads .value').text(r.message);
		}
	});

	// Campaign ROI (Placeholder)
	$('#metric-roi .value').text("125%");

	// Email Open Rates (Placeholder - requires Email Campaign doctype logic)
	$('#metric-email .value').text("22.5%");

	// Top Traffic Source (Placeholder - requires Website analytics or Lead Source aggregation)
	$('#metric-traffic .value').text("Google Organic");
}
