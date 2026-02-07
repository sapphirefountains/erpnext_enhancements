frappe.pages['design-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Design Hub',
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
				<div class="dashboard-metric" id="metric-active-tasks">
					<h4>Active Project Tasks</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-4">
				<div class="dashboard-metric" id="metric-time-logged">
					<h4>Time Logged (Today)</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-4">
				<div class="dashboard-metric" id="metric-pending-review">
					<h4>Pending Review Items</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Active Tasks
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Task",
			filters: { status: "Open" }
		},
		callback: function(r) {
			$('#metric-active-tasks .value').text(r.message);
		}
	});

	// Time Logged (Timesheets submitted today)
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Timesheet",
			filters: { 
				start_date: frappe.datetime.get_today(),
				docstatus: 1 
			}
		},
		callback: function(r) {
			$('#metric-time-logged .value').text(r.message + " Sheets");
		}
	});

	// Pending Review Items (Assumes a status 'In Review' on tasks or similar)
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Task",
			filters: { status: "Working" } // Placeholder status
		},
		callback: function(r) {
			$('#metric-pending-review .value').text(r.message);
		}
	});
}
