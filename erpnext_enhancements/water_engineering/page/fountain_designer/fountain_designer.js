// Fountain Designer (phase F of the rebuild, docs/fountain-designer/PLAN.md 3.5, TASK-2026-03018): the
// guided single-page designer. Two modes on one page: Quick for sales (a kit, a name, the address, the
// kit's headline inputs, then the verdict and the budget) and Full for the design team (every section,
// its setup policy, an Advanced reveal per card, catalog picks, outputs).
//
// LAYOUT: a step rail (Start, Site, Features, Basins, Pump room, Review, Outputs; quick mode shows Start,
// Site, Features, Review, Outputs), the step in the middle, and the verdict panel always beside it
// (chips, the top issues with a way to the step that fixes each, the rollups, the duty-point chart). On a
// phone the rail is a bar across the top, a strip of chips sits above the step, and the verdict follows.
//
// LIVE RESULTS: every edit sends the whole model to derive(), debounced 400 ms, and only the reply to the
// newest model is drawn (api.create_deriver). A section the person pins, or edits, is "custom" and no
// reply ever writes into it (model.merge_derived). The design saves itself 1.5 s after the last edit and
// at once on a step change.
//
// THE ADDRESS: /desk/fountain-designer?design=<name>&step=<key>, plus &demo=1 in demo mode. Each step and
// each design opened is a history entry, so Back and Forward walk the steps and a reload or a pasted link
// opens the same place. The router copies the query into frappe.route_options and fires on_page_show
// for every entry; the page reads its place there and writes its own address (router.js says why).
//
// THE SERVER SIDE is phase E's api/designer.py (boot, derive, get_design, save_design, push_quotation,
// push_material_request, catalog_search, acknowledge_issue, email_draft, run_calc), built after this page,
// against the contract in public/js/fountain_designer/README.md. ?demo=1 answers every call in the browser
// from a fixture (a lobby sheet wall, a plaza interactive, a pond aerator), labeled "Demo data" in the
// head, for training. Access is gated on read of Water Feature Design (api/designer.check_permission), in
// demo mode too. The Water Engineering Wizard this page replaced was retired in v1.771.0 (phase H); its
// quick calculator is the Calculator drawer on the Review step, and /desk/water-engineering-wizard comes
// here (public/js/global_enhancements/retired_routes.js).
//
// The code is public/js/fountain_designer/ (fountain_designer.bundle.js, window.sf_fountain_designer),
// the styles public/css/fountain_designer.bundle.css (every selector .fd-), states, chips and toasts from
// sf_kit. Every request goes through sf_kit.call's quiet path, so Frappe's own dialogs never cover the page.

frappe.pages["fountain-designer"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Fountain Designer"),
		single_column: true,
	});
	wrapper.fountain_designer = new FountainDesignerPage(page, wrapper);
};

frappe.pages["fountain-designer"].on_page_show = function (wrapper) {
	if (wrapper.fountain_designer) wrapper.fountain_designer.show();
};

const FD_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "fountain_designer.bundle.js", "fountain_designer.bundle.css"];
const FD_DEMO_ASSETS = ["fountain_designer_demo.bundle.js"];

class FountainDesignerPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.root = $('<div class="fd-root"></div>').appendTo(page.main).get(0);
		this.app = null;
		// v16's frappe.require calls back even when a file failed to load (assets.js "resolve even on
		// error"), so both globals are checked; a page without them says so instead of waiting.
		this.ready = new Promise((resolve) => frappe.require(FD_ASSETS, resolve)).then(() => {
			const sf = window.sf_kit && window.sf_kit.state ? window.sf_kit : null;
			const fd = window.sf_fountain_designer && window.sf_fountain_designer.mount ? window.sf_fountain_designer : null;
			if (!sf || !fd) {
				$(this.root).empty().append($('<p class="text-muted"></p>').text(__("The Fountain Designer could not load. Reload the page to try again.")));
				return;
			}
			this.app = fd.mount({
				page,
				root: this.root,
				sf,
				frappe,
				win: window,
				doc: document,
				__,
				demo_transport: () => this.demo_transport(),
			});
			page.set_primary_action(__("New design"), () => this.app.new_design(), "add");
		});
	}

	show() {
		this.ready.then(() => this.app && this.app.show());
	}

	// The fixture's transport, fetched the first time demo mode is asked for.
	demo_transport() {
		return new Promise((resolve) => frappe.require(FD_DEMO_ASSETS, resolve)).then(() => {
			const demo = window.sf_fountain_designer_demo;
			if (!demo || typeof demo.create !== "function") {
				return Promise.reject({ status: 0, message: __("The demo data could not load."), exc_type: "", session_expired: false });
			}
			let storage = null;
			try {
				storage = window.sessionStorage;
			} catch (e) {
				// No storage in this browser: the demo forgets its saves on reload.
			}
			return demo.create({ set_timeout: (fn, ms) => window.setTimeout(fn, ms), delay: 250, storage });
		});
	}
}
