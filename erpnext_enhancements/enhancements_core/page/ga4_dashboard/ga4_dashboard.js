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
				<p>Loading Dashboard Data...</p>
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
					.ga4-section-title {
						margin-top: 20px;
						margin-bottom: 10px;
						font-size: 1.2em;
						font-weight: bold;
						grid-column: 1 / -1;
					}
					.gsc-table {
						width: 100%;
						border-collapse: collapse;
						margin-top: 10px;
					}
					.gsc-table th, .gsc-table td {
						border: 1px solid var(--border-color);
						padding: 8px;
						text-align: left;
					}
					.gsc-table th {
						background-color: var(--table-bg);
					}
				</style>
				<div class="ga4-grid-container">
					<div class="ga4-section-title">Google Analytics 4</div>
					<div class="ga4-grid-item-full ga4-chart-box">
						<div id="ga4-traffic-chart"></div>
					</div>
					<div class="ga4-chart-box">
						<div id="ga4-acquisition-chart"></div>
					</div>
					<div class="ga4-chart-box">
						<div id="ga4-conversions-chart"></div>
					</div>
					<div class="ga4-chart-box">
						<div id="ga4-device-chart"></div>
					</div>
					<div class="ga4-chart-box">
						<div id="ga4-geo-chart"></div>
					</div>
					<div class="ga4-grid-item-full ga4-chart-box">
						<h4>Top Pages (Last 30 Days)</h4>
						<div id="ga4-top-pages-table-container"></div>
					</div>

					<div class="ga4-section-title">Search Performance</div>
					<div class="ga4-grid-item-full ga4-chart-box">
						<div id="gsc-timeline-chart"></div>
					</div>
					<div class="ga4-grid-item-full ga4-chart-box">
						<h4>Top Queries (Last 30 Days)</h4>
						<div id="gsc-keywords-table-container"></div>
					</div>
					<div class="ga4-grid-item-full ga4-chart-box">
						<h4>Top Landing Pages (Last 30 Days)</h4>
						<div id="gsc-landing-pages-table-container"></div>
					</div>
				</div>
			</div>
		</div>
	`;
	$(dashboard_html).appendTo(page.main);

	page.main.find('#ga4-loading').show();
	page.main.find('#ga4-dashboard-grid').hide();

	const ga4_promise = new Promise((resolve, reject) => {
		frappe.call({
			method: 'erpnext_enhancements.api.analytics.get_ga4_data',
			callback: function(r) {
				if (r.message && !r.exc) {
					resolve(r.message);
				} else {
					reject(r.exc || "Unknown GA4 error");
				}
			},
			error: function(err) {
				reject(err);
			}
		});
	});

	const gsc_promise = new Promise((resolve, reject) => {
		frappe.call({
			method: 'erpnext_enhancements.api.analytics.get_gsc_data',
			callback: function(r) {
				if (r.message && !r.exc) {
					resolve(r.message);
				} else {
					reject(r.exc || "Unknown GSC error");
				}
			},
			error: function(err) {
				reject(err);
			}
		});
	});

	Promise.all([ga4_promise, gsc_promise])
		.then(([ga4_data, gsc_data]) => {
			page.main.find('#ga4-loading').hide();
			page.main.find('#ga4-dashboard-grid').show();

			// Render GA4 Charts
			if (ga4_data.traffic_timeline) {
				new frappe.Chart(page.main.find('#ga4-traffic-chart')[0], {
					data: ga4_data.traffic_timeline,
					title: "Traffic Timeline (Last 30 Days)",
					type: 'line',
					height: 300,
					colors: ['#7cd6fd', '#743ee2']
				});
			}

			if (ga4_data.acquisition_channels) {
				new frappe.Chart(page.main.find('#ga4-acquisition-chart')[0], {
					data: ga4_data.acquisition_channels,
					title: "Acquisition Channels (Last 30 Days)",
					type: 'donut',
					height: 300,
					colors: ['#7cd6fd', '#743ee2', '#5e64ff', '#28a745', '#ff5858', '#ffa00a']
				});
			}

			if (ga4_data.conversions) {
				new frappe.Chart(page.main.find('#ga4-conversions-chart')[0], {
					data: ga4_data.conversions,
					title: "Conversions (Last 30 Days)",
					type: 'bar',
					height: 300,
					colors: ['#28a745']
				});
			}

			if (ga4_data.device_breakdown) {
				new frappe.Chart(page.main.find('#ga4-device-chart')[0], {
					data: ga4_data.device_breakdown,
					title: "Device Breakdown (Last 30 Days)",
					type: 'donut',
					height: 300,
					colors: ['#5e64ff', '#7cd6fd', '#ffa00a']
				});
			}

			if (ga4_data.user_geography) {
				new frappe.Chart(page.main.find('#ga4-geo-chart')[0], {
					data: ga4_data.user_geography,
					title: "User Geography (Last 30 Days)",
					type: 'bar',
					height: 300,
					colors: ['#743ee2']
				});
			}

			if (ga4_data.top_pages && ga4_data.top_pages.length > 0) {
				let table_html = `<table class="gsc-table table table-bordered">
					<thead>
						<tr>
							<th>Page Title</th>
							<th>Views</th>
						</tr>
					</thead>
					<tbody>`;
				ga4_data.top_pages.forEach(row => {
					let safe_page = frappe.utils.escape_html(row.pageTitle);
					table_html += `
						<tr>
							<td>${safe_page}</td>
							<td>${row.screenPageViews}</td>
						</tr>`;
				});
				table_html += `</tbody></table>`;
				page.main.find('#ga4-top-pages-table-container').html(table_html);
			} else {
				page.main.find('#ga4-top-pages-table-container').html("<p>No top pages found for the past 30 days.</p>");
			}


			// Render GSC Charts and Tables
			if (gsc_data.search_timeline) {
				new frappe.Chart(page.main.find('#gsc-timeline-chart')[0], {
					data: gsc_data.search_timeline,
					title: "Search Performance Timeline (Last 30 Days)",
					type: 'axis-mixed',
					height: 300,
					colors: ['#5e64ff', '#ff5858']
				});
			}

			if (gsc_data.top_queries && gsc_data.top_queries.length > 0) {
				let table_html = `<table class="gsc-table table table-bordered">
					<thead>
						<tr>
							<th>Query</th>
							<th>Clicks</th>
							<th>Impressions</th>
							<th>CTR (%)</th>
							<th>Avg. Position</th>
						</tr>
					</thead>
					<tbody>`;
				gsc_data.top_queries.forEach(row => {
					let safe_query = frappe.utils.escape_html(row.query);
					table_html += `
						<tr>
							<td>${safe_query}</td>
							<td>${row.clicks}</td>
							<td>${row.impressions}</td>
							<td>${row.ctr}%</td>
							<td>${row.position}</td>
						</tr>`;
				});
				table_html += `</tbody></table>`;
				page.main.find('#gsc-keywords-table-container').html(table_html);
			} else {
				page.main.find('#gsc-keywords-table-container').html("<p>No search queries found for the past 30 days.</p>");
			}

			if (gsc_data.top_pages && gsc_data.top_pages.length > 0) {
				let table_html = `<table class="gsc-table table table-bordered">
					<thead>
						<tr>
							<th>URL</th>
							<th>Clicks</th>
							<th>Impressions</th>
							<th>CTR (%)</th>
							<th>Avg. Position</th>
						</tr>
					</thead>
					<tbody>`;
				gsc_data.top_pages.forEach(row => {
					let safe_url = frappe.utils.escape_html(row.page);
					table_html += `
						<tr>
							<td>${safe_url}</td>
							<td>${row.clicks}</td>
							<td>${row.impressions}</td>
							<td>${row.ctr}%</td>
							<td>${row.position}</td>
						</tr>`;
				});
				table_html += `</tbody></table>`;
				page.main.find('#gsc-landing-pages-table-container').html(table_html);
			} else {
				page.main.find('#gsc-landing-pages-table-container').html("<p>No landing pages found for the past 30 days.</p>");
			}

		})
		.catch(error => {
			page.main.find('#ga4-loading').html('<p class="text-danger">Failed to load Dashboard Data. Check console for details.</p>');
			console.error("Dashboard Load Error:", error);
		});
};