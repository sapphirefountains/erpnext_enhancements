/**
 * @file Client controller for the "Sales Pipeline" desk page (/app/sales-pipeline).
 * @description
 * TV-friendly realtime funnel board. Columns and cards come fully shaped from
 * the whitelisted `get_pipeline_data` (sales_pipeline.py) — this file only
 * renders and schedules refreshes:
 *
 *  - Realtime: subscribes to "sales_pipeline_updated" (published by
 *    publish_pipeline_update on Opportunity on_update) with a debounce, so a
 *    burst of saves causes one refetch.
 *  - Fallback polling every 5 minutes (wall TVs can miss socket reconnects),
 *    skipped while the tab is hidden or routed away from this page.
 *  - TV mode: /app/sales-pipeline/tv (or the header button) hides the desk
 *    chrome and scales typography for across-the-room reading. The Raspberry
 *    Pi kiosk should bookmark the /tv route.
 *
 * Staleness: cards carry `stale` 0/1/2 from the server (days-in-stage vs the
 * thresholds in ERPNext Enhancements Settings); this maps to amber/red card
 * classes defined in sales_pipeline.css (theme-aware, per the app's
 * dark-theme convention).
 */

frappe.pages["sales-pipeline"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sales Pipeline"),
		single_column: true,
	});

	const state = {
		timer: null,
		refresh_inflight: false,
		tv_mode: false,
	};
	wrapper.sales_pipeline_state = state;

	const board = $('<div class="sales-pipeline-board"></div>');
	const footer = $('<div class="sales-pipeline-footer text-muted"></div>');
	$(page.body).addClass("sales-pipeline-page").append(board).append(footer);

	page.set_secondary_action(__("Refresh"), () => refresh(), "refresh");
	page.add_inner_button(__("TV Mode"), () => set_tv_mode(!state.tv_mode));

	function set_tv_mode(on) {
		state.tv_mode = on;
		$("body").toggleClass("sales-pipeline-tv", on);
		if (on && document.documentElement.requestFullscreen) {
			document.documentElement.requestFullscreen().catch(() => {});
		} else if (!on && document.fullscreenElement) {
			document.exitFullscreen().catch(() => {});
		}
	}

	function is_visible() {
		return !document.hidden && frappe.get_route()[0] === "sales-pipeline";
	}

	function refresh() {
		if (state.refresh_inflight) return;
		state.refresh_inflight = true;
		frappe
			.call("erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.get_pipeline_data")
			.then((r) => render(r.message))
			.catch((err) => {
				console.error("Sales Pipeline refresh failed:", err);
				footer.text(__("Refresh failed — retrying on the next cycle."));
			})
			.finally(() => {
				state.refresh_inflight = false;
			});
	}

	function render(data) {
		if (!data) return;
		const esc = frappe.utils.escape_html;
		board.empty();

		data.stages.forEach((stage) => {
			const column = $(`
				<div class="pipeline-column pipeline-column-${stage.kind}">
					<div class="pipeline-column-head">
						<div class="pipeline-column-title">${esc(stage.label)}</div>
						<div class="pipeline-column-meta">
							<span class="pipeline-count">${stage.count}</span>
							<span class="pipeline-total">${format_currency(stage.total, data.currency, 0)}</span>
						</div>
					</div>
					<div class="pipeline-column-cards"></div>
				</div>
			`);
			const cards = column.find(".pipeline-column-cards");

			if (!stage.opportunities.length) {
				cards.append(`<div class="pipeline-empty text-muted">${__("Empty")}</div>`);
			}

			stage.opportunities.forEach((opp) => {
				const stale_class = opp.stale === 2 ? "stale-red" : opp.stale === 1 ? "stale-amber" : "";
				const days_label =
					opp.days_in_stage === 1 ? __("1 day") : __("{0} days", [opp.days_in_stage]);
				cards.append(`
					<a class="pipeline-card ${stale_class}" href="/app/opportunity/${encodeURIComponent(opp.name)}">
						<div class="pipeline-card-top">
							<span class="pipeline-card-customer">${esc(opp.customer)}</span>
							<span class="pipeline-card-days" title="${__("Time in this stage")}">${days_label}</span>
						</div>
						${opp.summary ? `<div class="pipeline-card-summary">${esc(opp.summary)}</div>` : ""}
						<div class="pipeline-card-bottom">
							${opp.amount ? `<span class="pipeline-card-amount">${format_currency(opp.amount, data.currency, 0)}</span>` : "<span></span>"}
							<span class="pipeline-card-owner">${esc(opp.owner)}</span>
						</div>
					</a>
				`);
			});

			if (stage.overflow) {
				cards.append(`<div class="pipeline-overflow text-muted">${__("+{0} more", [stage.overflow])}</div>`);
			}

			board.append(column);
		});

		footer.text(
			__("Updated {0} — amber after {1} days in stage, red after {2}", [
				frappe.datetime.now_time(),
				data.thresholds.amber,
				data.thresholds.red,
			])
		);
	}

	// Access check, then first paint + schedules.
	frappe
		.call("erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.check_permission")
		.then((r) => {
			if (!r.message) {
				page.set_title(__("Access Denied"));
				board.html(`
					<div class="alert alert-danger m-4">
						${__("You do not have permission to view the Sales Pipeline. Ask an administrator for access.")}
					</div>
				`);
				return;
			}
			refresh();

			const debounced = frappe.utils.debounce(() => {
				if (is_visible()) refresh();
			}, 2000);
			frappe.realtime.on("sales_pipeline_updated", debounced);

			state.timer = setInterval(() => {
				if (is_visible()) refresh();
			}, 5 * 60 * 1000);
		});
};

frappe.pages["sales-pipeline"].on_page_show = function (wrapper) {
	// Returning to the page (or kiosk auto-reload landing on /tv): re-read the
	// route for TV mode and repaint with fresh data.
	const route = frappe.get_route();
	const tv = route[1] === "tv";
	const state = wrapper.sales_pipeline_state;
	if (state) {
		state.tv_mode = tv || state.tv_mode;
		$("body").toggleClass("sales-pipeline-tv", state.tv_mode);
	}
	// Leaving the page must always drop the TV chrome class.
	$(wrapper).one("hide", () => $("body").removeClass("sales-pipeline-tv"));
};
