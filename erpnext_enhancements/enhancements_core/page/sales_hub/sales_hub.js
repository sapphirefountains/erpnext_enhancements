frappe.pages['sales-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Sales Hub',
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
			.chart-container {
				height: 300px;
				margin-bottom: 20px;
			}
		</style>
		<div class="row">
			<div class="col-md-12">
				<div class="dashboard-metric">
					<h4>Sales Volume</h4>
					<div id="sales-volume-chart" class="chart-container"></div>
				</div>
			</div>
		</div>
		<div class="row">
			<div class="col-md-6">
				<div class="dashboard-metric" id="metric-territory">
					<h4>Territory Gross vs. Net</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-6">
				<div class="dashboard-metric" id="metric-opportunities">
					<h4>Opportunities (Created vs. Won)</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Render Chart
	const chart = new frappe.Chart("#sales-volume-chart", {
		title: "Sales Volume (Last 30 Days)",
		data: {
			labels: ["Week 1", "Week 2", "Week 3", "Week 4"],
			datasets: [
				{
					name: "Sales", type: "bar",
					values: [25000, 40000, 30000, 35000]
				}
			]
		},
		type: 'bar', 
		height: 250,
		colors: ['#7cd6fd']
	});

	// Territory Gross vs Net (Placeholder logic)
	// In a real scenario, call a python method to aggregate data
	$('#metric-territory .value').html('Gross: $120k <br> Net: $95k');

	// Opportunities Created vs Won
	frappe.call({
		method: "frappe.client.get_count",
		args: { doctype: "Opportunity" },
		callback: function(r_total) {
			let total = r_total.message;
			frappe.call({
				method: "frappe.client.get_count",
				args: { doctype: "Opportunity", filters: { status: "Won" } },
				callback: function(r_won) {
					let won = r_won.message;
					$('#metric-opportunities .value').text(`${total} Created / ${won} Won`);
				}
			});
		}
	});
}
