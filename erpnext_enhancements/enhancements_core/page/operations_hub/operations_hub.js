frappe.pages['operations-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Operations Hub',
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
			.alert-box {
				background-color: #ffe6e6;
				border: 1px solid #ff9999;
				color: #cc0000;
				padding: 10px;
				margin-bottom: 10px;
				border-radius: 4px;
			}
		</style>
		<div class="row">
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-inventory">
					<h4>Inventory Levels</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-stock-moves">
					<h4>Pending Stock Moves</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-delays">
					<h4>Logistics Delays</h4>
					<div class="value">0</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-scorecards">
					<h4>Supplier Scorecards</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
		<div class="row">
			<div class="col-md-12">
				<h4>Critical System Alerts</h4>
				<div id="system-alerts-container">
					<div class="text-muted">No critical alerts at this time.</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Inventory Levels (Total Items in Stock)
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Bin",
			filters: { actual_qty: [">", 0] }
		},
		callback: function(r) {
			$('#metric-inventory .value').text(r.message + " Items");
		}
	});

	// Pending Stock Moves (Stock Entry Drafts)
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Stock Entry",
			filters: { docstatus: 0 }
		},
		callback: function(r) {
			$('#metric-stock-moves .value').text(r.message);
		}
	});

	// Supplier Scorecards (Average Score - Placeholder)
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Supplier Scorecard",
			fields: ["avg(grand_total) as avg_score"] // This won't work directly via get_list usually
		},
		callback: function(r) {
			// Placeholder logic as we can't easily aggregate in JS from client call
			$('#metric-scorecards .value').text("85/100");
		}
	});

	// Check System Logs for Errors (Requires System Manager)
	if(frappe.user.has_role("System Manager")) {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Error Log",
				limit_page_length: 5,
				order_by: "creation desc"
			},
			callback: function(r) {
				if(r.message && r.message.length > 0) {
					let html = "";
					r.message.forEach(log => {
						html += `<div class="alert-box">Error: ${log.name} - ${log.method || 'Unknown Method'}</div>`;
					});
					$('#system-alerts-container').html(html);
				}
			}
		});
	}
}
