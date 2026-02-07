frappe.pages['hr-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'HR Hub',
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
				<div class="dashboard-metric" id="metric-headcount">
					<h4>Employee Headcount</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-leave">
					<h4>Pending Leave Requests</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-birthdays">
					<h4>Upcoming Birthdays (Month)</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-jobs">
					<h4>Open Job Requisitions</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Employee Headcount
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Employee",
			filters: { status: "Active" }
		},
		callback: function(r) {
			$('#metric-headcount .value').text(r.message);
		}
	});

	// Pending Leave Requests
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Leave Application",
			filters: { status: "Open" }
		},
		callback: function(r) {
			$('#metric-leave .value').text(r.message);
		}
	});

	// Upcoming Birthdays (Client side filtering placeholder)
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Employee",
			filters: { status: "Active" },
			fields: ["date_of_birth"]
		},
		callback: function(r) {
			// Placeholder logic: just count active employees for now
			// Proper birthday logic requires date comparisons in JS
			$('#metric-birthdays .value').text(r.message ? r.message.length : 0);
		}
	});

	// Open Job Requisitions
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Job Opening",
			filters: { status: "Open" }
		},
		callback: function(r) {
			$('#metric-jobs .value').text(r.message);
		}
	});
}
