/* global erpnext_enhancements, frappe, $ */
frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Project Dashboard tab — Dashboard (Projects-module overview).
 *
 * Targets: the "Dashboard" tab of the Project Dashboard page. Loaded via lazy
 * `frappe.require` from project_dashboard.js, which constructs this class (by
 * name) and calls render()/unmount() as the tab is shown/hidden.
 *
 * Renders a native (no-iframe) module overview: a row of headline number cards
 * plus charts for active projects by status, by type, and by completion bucket.
 * Data comes from the whitelisted `get_dashboard_metrics`. Charts use the desk
 * global `frappe.Chart` (frappe-charts) when available, with a CSS-bar fallback
 * so the tab never hard-fails if the charting global is missing. An
 * AbortController cancels the in-flight request when the user switches tabs.
 */
erpnext_enhancements.dashboard_components.DashboardOverview = class DashboardOverview {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.abortController = null;
	}

	async render() {
		this.wrapper.empty();
		this.show_skeleton();
		try {
			await this.fetch_and_render();
		} catch (error) {
			this.handle_error(error);
		}
	}

	async fetch_and_render() {
		this.abortController = new AbortController();
		const signal = this.abortController.signal;

		try {
			const r = await erpnext_enhancements.dashboard_api.call(
				{
					method: "erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.get_dashboard_metrics",
				},
				signal
			);

			if (signal.aborted) return;

			const data = r.message;
			if (!data || data.error) {
				throw new Error(data ? data.error : "Unknown error fetching dashboard metrics");
			}
			this.render_dashboard(data);
		} finally {
			this.abortController = null;
		}
	}

	render_dashboard(data) {
		this.wrapper.empty();
		const cards = data.cards || {};

		const cardDefs = [
			{ label: "Active Projects", value: cards.active_projects, color: "var(--blue-500, #2490ef)" },
			{ label: "Overdue", value: cards.overdue_projects, color: "var(--red-500, #e24c4c)" },
			{
				label: "Avg % Complete",
				value: `${cards.avg_percent_complete != null ? cards.avg_percent_complete : 0}%`,
				color: "var(--green-500, #28a745)",
			},
			{ label: "Open Tasks", value: cards.open_tasks, color: "var(--orange-500, #f5a623)" },
			{ label: "Master Projects", value: cards.master_projects, color: "var(--purple-500, #7574d6)" },
			{ label: "Completed", value: cards.completed_projects, color: "var(--gray-600, #6c757d)" },
		];

		const cardRow = $('<div class="row dashboard-cards mb-2"></div>').appendTo(this.wrapper);
		cardDefs.forEach((c) => {
			cardRow.append(`
				<div class="col-6 col-md-4 col-lg-2 mb-3">
					<div style="background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; height: 100%;">
						<div class="text-muted" style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em;">${frappe.utils.escape_html(
							c.label
						)}</div>
						<div style="font-size: 1.8rem; font-weight: 700; color: ${c.color}; line-height: 1.3;">${
				c.value != null ? c.value : 0
			}</div>
					</div>
				</div>
			`);
		});

		const chartRow = $('<div class="row"></div>').appendTo(this.wrapper);
		this.render_chart(
			$('<div class="col-12 col-lg-6 mb-4"></div>').appendTo(chartRow),
			"Active Projects by Status",
			"percentage",
			data.by_status
		);
		this.render_chart(
			$('<div class="col-12 col-lg-6 mb-4"></div>').appendTo(chartRow),
			"Active Projects by Type",
			"bar",
			data.by_type
		);
		// frappe.Chart colors become SVG fill values, which don't accept CSS
		// variables — use a literal hex here (the CSS-bar fallback uses the var).
		this.render_chart(
			$('<div class="col-12 mb-4"></div>').appendTo(this.wrapper),
			"Active Projects by Completion",
			"bar",
			data.completion_buckets,
			["#2490ef"]
		);
	}

	render_chart(col, title, type, obj, colors) {
		const labels = Object.keys(obj || {});
		const values = labels.map((l) => obj[l]);

		const card = $(`
			<div style="background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; height: 100%;">
				<h6 class="text-muted mb-3">${frappe.utils.escape_html(title)}</h6>
				<div class="dashboard-chart-target"></div>
			</div>
		`).appendTo(col);
		const target = card.find(".dashboard-chart-target")[0];

		if (!labels.length || values.every((v) => !v)) {
			$(target).html('<p class="text-muted text-center py-4 mb-0">No data</p>');
			return;
		}

		if (typeof frappe !== "undefined" && typeof frappe.Chart === "function") {
			try {
				new frappe.Chart(target, {
					data: { labels, datasets: [{ name: title, values }] },
					type: type === "percentage" ? "percentage" : "bar",
					height: 250,
					colors: colors || ["#2490ef", "#28a745", "#f5a623", "#e24c4c", "#7574d6", "#00b0b0", "#98d85b"],
					axisOptions: { xAxisMode: "tick" },
					barOptions: { spaceRatio: 0.4 },
					tooltipOptions: {},
				});
				return;
			} catch (e) {
				// Fall through to the CSS-bar fallback below.
				console.warn("frappe.Chart failed, using CSS fallback:", e);
			}
		}

		this.render_css_bars(target, labels, values);
	}

	render_css_bars(target, labels, values) {
		const max = Math.max(...values, 1);
		const $t = $(target).empty();
		labels.forEach((label, i) => {
			const pct = Math.round((values[i] / max) * 100);
			$t.append(`
				<div class="mb-2">
					<div class="d-flex justify-content-between" style="font-size: 0.85rem;">
						<span>${frappe.utils.escape_html(label)}</span>
						<span class="text-muted">${values[i]}</span>
					</div>
					<div class="progress" style="height: 8px;">
						<div class="progress-bar" role="progressbar" style="width: ${pct}%; background: var(--blue-500, #2490ef);"></div>
					</div>
				</div>
			`);
		});
	}

	show_skeleton() {
		this.wrapper.html(`
			<div class="skeleton-list p-2">
				<div class="skeleton-line" style="width: 100%; height: 90px; margin-bottom: 16px;"></div>
				<div class="skeleton-line" style="width: 100%; height: 250px;"></div>
			</div>
		`);
	}

	handle_error(error) {
		if (error && error.name === "CancellationError") {
			console.log("Dashboard request aborted due to context switch.");
			return;
		}

		console.error("Dashboard Overview Error:", error);

		this.wrapper.html(`
			<div class="alert alert-danger p-4 text-center">
				<h4><i class="fa fa-exclamation-triangle mr-2"></i> Failed to Load Dashboard</h4>
				<p>${(error && error.message) || "An unexpected error occurred."}</p>
				<button class="btn btn-primary btn-sm mt-3 retry-btn">Retry</button>
			</div>
		`);

		this.wrapper.find(".retry-btn").on("click", () => {
			this.render();
		});
	}

	unmount() {
		if (this.abortController) {
			this.abortController.abort();
		}
		this.wrapper.empty();
	}
};
