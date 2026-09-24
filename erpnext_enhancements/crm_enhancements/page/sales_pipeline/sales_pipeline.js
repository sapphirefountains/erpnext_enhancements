/**
 * @file Client controller for the "Sales Pipeline" desk page (/desk/sales-pipeline).
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
 *  - TV mode: /desk/sales-pipeline/tv (or the header button) hides the desk
 *    chrome and scales typography for across-the-room reading. The Raspberry
 *    Pi kiosk should bookmark the /tv route. The route IS the mode: the button
 *    routes to /tv, and on_page_show reads the route on every show, so Back
 *    from TV mode returns to the board with its navbar and Forward goes back
 *    into TV mode. Escape (leaving the fullscreen the button asked for) steps
 *    back the same way, because TV mode hides the button that would.
 *
 * Card and hand-off links are frappe.utils.get_form_link paths (/desk/...), which
 * the desk routes in place; an /app/... href is a full page load on v16.
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
		// This page asked for fullscreen on the way into TV mode (a click, the only
		// time it may). Cleared by on_page_show whenever the route is not /tv.
		fullscreen_asked: false,
	};
	wrapper.sales_pipeline_state = state;

	const board = $('<div class="sales-pipeline-board"></div>');
	const rail = $('<div class="sales-pipeline-rail" style="display:none"></div>');
	const footer = $('<div class="sales-pipeline-footer text-muted"></div>');
	$(page.body).addClass("sales-pipeline-page").append(board).append(rail).append(footer);

	page.set_secondary_action(__("Refresh"), () => refresh(), "refresh");
	// The button only routes; on_page_show sets the mode from the route. Fullscreen is
	// asked for here because it needs this click; leaving it needs no gesture, so
	// on_page_show and the "hide" handler do that part.
	page.add_inner_button(__("TV Mode"), () => {
		if (!state.tv_mode && document.documentElement.requestFullscreen) {
			state.fullscreen_asked = true;
			document.documentElement.requestFullscreen().catch(() => {});
		}
		frappe.set_route(state.tv_mode ? ["sales-pipeline"] : ["sales-pipeline", "tv"]);
	});

	// Escape ends the fullscreen and leaves TV mode's chrome on, and the TV Mode button
	// is hidden with the page head, so there would be no way out but Back. So leaving the
	// fullscreen this page asked for IS Back: the entry behind /tv is the board it was
	// pushed from. A kiosk that loads /tv directly never asks for fullscreen, so this
	// never moves it.
	document.addEventListener("fullscreenchange", () => {
		if (document.fullscreenElement || !state.fullscreen_asked) return;
		state.fullscreen_asked = false;
		const route = frappe.get_route() || [];
		if (route[0] === "sales-pipeline" && route[1] === "tv") window.history.back();
	});

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
					<a class="pipeline-card ${stale_class}" href="${frappe.utils.get_form_link("Opportunity", opp.name)}">
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

		render_rail(data.handoff);

		footer.text(
			__("Updated {0} — amber after {1} days in stage, red after {2}", [
				frappe.datetime.now_time(),
				data.thresholds.amber,
				data.thresholds.red,
			])
		);
	}

	// Post-won extension: projects mid hand-off (PRO-0204), overdue steps glow.
	function render_rail(handoff) {
		const esc = frappe.utils.escape_html;
		rail.empty();
		if (!handoff || !handoff.projects || !handoff.projects.length) {
			rail.hide();
			return;
		}
		rail.show();
		rail.append(`<span class="rail-title">${__("Hand-off in progress")}</span>`);
		handoff.projects.forEach((p) => {
			rail.append(`
				<a class="rail-chip ${p.overdue ? "rail-overdue" : ""}" href="${frappe.utils.get_form_link("Project", p.project)}">
					<span class="rail-chip-label">${esc(p.label)}</span>
					<span class="rail-chip-step">${__("Step {0}/{1}", [p.step_number, p.total])} · ${esc(p.step_title)}</span>
				</a>
			`);
		});
		if (handoff.overflow) {
			rail.append(`<span class="rail-overflow text-muted">+${handoff.overflow}</span>`);
		}
	}

	// Master switch: the suite (and this board) is dormant until enabled in
	// ERPNext Enhancements Settings — show a plain explanation, not an error.
	if (!frappe.boot.ee_process_automation) {
		board.html(`
			<div class="alert alert-info m-4">
				${__("The Sales Pipeline board is part of the process-automation suite, which is currently switched off (ERPNext Enhancements Settings → Process Automation). Flip it on to use this board.")}
			</div>
		`);
		return;
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
	// Every show — the TV Mode button, Back, Forward, a kiosk auto-reload landing on
	// /tv — sets TV mode from the route and from nothing else. It used to be
	// `tv || state.tv_mode`, which kept the chrome-less TV view (no navbar, so no way
	// out) on the plain board after Back from /tv, until a reload.
	const route = frappe.get_route();
	const tv = route[1] === "tv";
	const state = wrapper.sales_pipeline_state;
	if (state) {
		state.tv_mode = tv;
		if (!tv) state.fullscreen_asked = false;
	}
	$("body").toggleClass("sales-pipeline-tv", tv);
	if (!tv) sales_pipeline_exit_fullscreen();
	// Leaving the page must always drop the TV chrome class, and the fullscreen with
	// it. Namespaced so each show replaces the handler rather than adding another.
	$(wrapper)
		.off("hide.sales_pipeline_tv")
		.one("hide.sales_pipeline_tv", () => {
			if (state) state.fullscreen_asked = false;
			$("body").removeClass("sales-pipeline-tv");
			sales_pipeline_exit_fullscreen();
		});
};

// Only the fullscreen TV mode asked for: the whole document. Anything else that is
// fullscreen (a video, say) is not this page's to end.
function sales_pipeline_exit_fullscreen() {
	if (document.fullscreenElement === document.documentElement && document.exitFullscreen) {
		document.exitFullscreen().catch(() => {});
	}
}
