frappe.pages['finance-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Finance Hub',
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
				<div class="dashboard-metric" id="metric-ar-ap">
					<h4>AR vs AP</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-cash-flow">
					<h4>Cash Flow Status</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-pnl">
					<h4>P&L (MTD)</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-expenses">
					<h4>Pending Expense Claims</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// AR vs AP (Placeholder - requires complex query)
	// Example: Fetch outstanding Sales Invoices vs Purchase Invoices
	$('#metric-ar-ap .value').html('<span class="text-success">$50k</span> / <span class="text-danger">$30k</span>');

	// Cash Flow (Placeholder)
	$('#metric-cash-flow .value').text("Positive");

	// P&L MTD (Placeholder)
	$('#metric-pnl .value').text("+$12,500");

	// Pending Expense Claims
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Expense Claim",
			filters: { approval_status: "Draft" } // or "Pending" based on workflow
		},
		callback: function(r) {
			$('#metric-expenses .value').text(r.message);
		}
	});
}
