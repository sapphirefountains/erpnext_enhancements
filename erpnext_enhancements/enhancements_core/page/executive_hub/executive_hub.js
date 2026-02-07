frappe.pages['executive-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Executive Hub',
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
			<div class="col-md-4">
				<div class="dashboard-metric" id="metric-revenue">
					<h4>Company-wide Revenue</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-4">
				<div class="dashboard-metric" id="metric-profit">
					<h4>Net Profit</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-4">
				<div class="dashboard-metric" id="metric-budget">
					<h4>Departmental Budget vs Actual</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Placeholders for Executive Metrics (complex financial reports)
	$('#metric-revenue .value').text("$2.4M");
	$('#metric-profit .value').text("$450k");
	$('#metric-budget .value').html('<span class="text-success">Within Budget</span>');
}
