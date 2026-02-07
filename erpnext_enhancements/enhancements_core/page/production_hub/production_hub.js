frappe.pages['production-hub'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Production Hub',
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
				font-size: 32px;
				font-weight: bold;
				color: #36414c;
			}
		</style>
		<div class="row">
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-active-tasks">
					<h4>Active Tasks</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-active-projects">
					<h4>Active Projects</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-assigned-docs">
					<h4>Assigned Documents</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
			<div class="col-md-3">
				<div class="dashboard-metric" id="metric-pending-material">
					<h4>Pending Material Requests</h4>
					<div class="value">Loading...</div>
				</div>
			</div>
		</div>
	`;

	$(dashboard_html).appendTo(page.main);

	// Fetch Counts using frappe.db.count (available via frappe.call to 'frappe.client.get_count')
	
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

	// Active Projects
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Project",
			filters: { status: "Open" }
		},
		callback: function(r) {
			$('#metric-active-projects .value').text(r.message);
		}
	});

	// Assigned Documents (ToDo)
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "ToDo",
			filters: { status: "Open", owner: frappe.session.user }
		},
		callback: function(r) {
			$('#metric-assigned-docs .value').text(r.message);
		}
	});

	// Pending Material Requests
	frappe.call({
		method: "frappe.client.get_count",
		args: {
			doctype: "Material Request",
			filters: { docstatus: 0 }
		},
		callback: function(r) {
			$('#metric-pending-material .value').text(r.message);
		}
	});
}
