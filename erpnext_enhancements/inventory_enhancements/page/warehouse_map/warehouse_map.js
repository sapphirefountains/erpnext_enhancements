// Warehouse Map (v1.815.0). Nik, 2026-11-12: "we need a map for the location bins and a way to edit
// and create the map for our warehouse locations at our office."
//
// A map is one floor: a floor plan picture with boxes over it, each box a Warehouse (a bay, a room).
// What is inside a box is never drawn by hand. Tapping a bay opens its FRONT VIEW, the shelves and bins
// a person sees standing in front of it, built from the Warehouse tree; so a bin made in ERPNext later
// shows up inside its bay with nobody touching the map. Colors are the Bin Manager's own counts: has
// stock, empty, or a problem (an assigned item with none on hand, or stock nobody assigned there).
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/warehouse-map                    the first map
//   /desk/warehouse-map/<map>              one floor
//   /desk/warehouse-map/<map>/<warehouse>  that floor with one box open
//   /desk/warehouse-map/<map>/edit         the editor (Warehouse Map Editor, System Manager)
//
// What is being looked for rides in the address's query and is carried from screen to screen:
// ?item=<code> lights up where an item is, ?bin=<warehouse> is the bin picked in the open box. Picking
// a bin or an item rewrites the query in place (history.replaceState): it is not a screen. ?w=<warehouse>
// is the "Show on map" link from the Bin Manager, the Item and Warehouse forms, the reports and Stock
// Scan; it is answered by the server (api.warehouse_map.locate) and REPLACED by the screen that shows
// that warehouse, so Back from the map returns to where the link was pressed.
//
// frappe.route_options is read on every show (the address's query lands there since v16.50) and cleared;
// never location.search. Moves between screens are frappe.set_route(...parts, options): an array AND an
// options object as two arguments is not a form the router unwraps.
//
// THE EDITOR changes one Warehouse Floor Map record and nothing else: no Warehouse, no Putaway Rule, no stock.
// Save sends the whole list of boxes (api.warehouse_map.save_map) with the `modified` stamp it was
// opened with, so two people editing cannot silently overwrite each other. Leaving with unsaved boxes is
// not blocked (that would break Back): the boxes are kept while the Desk stays open, the page says so,
// and the browser asks before the tab is closed.
//
// The decisions (routes, the front view's order, moving and sizing a box, what Save sends) are pure:
// public/js/warehouse_map/logic.js, in warehouse_map.bundle.js (window.sf_warehouse_map), run under node
// by scripts/test_warehouse_map.mjs. This file draws and wires. Every request goes through
// sf_kit.call(..., { quiet: true }), so Frappe's own dialogs never cover the page. The CSS is this
// folder's warehouse_map.css, every selector under .wm- (a Desk Page's stylesheet is global once loaded).

frappe.pages["warehouse-map"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Warehouse Map"),
		single_column: true,
	});
	// v16's container.change_to ends every route change with frappe.utils.scroll_to(0) unless the page
	// wrapper sets disable_scroll_to_top (frappe/public/js/frappe/views/container.js, read at v16.56.0).
	// Every screen of this page is a route (a floor, an opened box, a place an item is), so tapping a
	// bay far down a phone's screen would jump to the top and away from the front view that just
	// opened. The page scrolls to the top itself only when it is arrived at from somewhere else
	// (on_page_show, below). Same as the Opportunity Board.
	wrapper.disable_scroll_to_top = true;
	wrapper.warehouse_map = new WarehouseMapPage(page, wrapper);
	$(wrapper).on("hide", () => {
		wrapper.warehouse_map.arriving = true;
	});
};

frappe.pages["warehouse-map"].on_page_show = function (wrapper) {
	const map = wrapper.warehouse_map;
	if (!map) return;
	if (map.arriving) {
		map.arriving = false;
		if (frappe.utils && typeof frappe.utils.scroll_to === "function") frappe.utils.scroll_to(0);
	}
	map.route_changed();
};

const WM_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "warehouse_map.bundle.js"];
const WM_SEARCH_DELAY_MS = 250;
const WM_TAP_SLOP_PX = 6;
const WM_STALE_MS = 60000;
// The Bin Manager's tabs, so the map reads as one more of its views. scripts/test_warehouse_map.mjs
// holds the first four to the Bin Manager's own list.
const WM_TABS = [
	{ label: "Bins", route: ["bin-manager"] },
	{ label: "By project", route: ["bin-manager", "by-project"] },
	{ label: "Job sites", route: ["bin-manager", "job-sites"] },
	{ label: "Problems", route: ["bin-manager", "problems"] },
	{ label: "Map", route: null },
];

class WarehouseMapPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $('<div class="wm-app"></div>').appendTo(page.main);
		this.sf = null;
		this.wm = null;
		this.boot = null; // { maps, can_edit }
		this.boot_error = "";
		this.maps = {}; // name -> { data, error, loading }
		this.target = null;
		this.item = ""; // the item being looked for
		this.found = null; // { item_code, answer | error | loading }
		this.bin = ""; // the bin picked in the open box
		this.bin_items = {}; // warehouse -> { rows, error, loading }
		this.views = {}; // map name -> the SVG viewBox
		this.notice = ""; // one sentence above the map (a warehouse no map shows)
		this.search = { query: "", rows: null, loading: false, seq: 0 };
		this.edit = null; // the editor's state; kept while the Desk stays open
		this.drag = null;
		this.pointers = new Map();
		this.arriving = true; // the page is being come to from another page (see on_page_show)
		// v16's frappe.require calls back even when a file failed to load, so both globals are checked.
		this.ready = new Promise((resolve) => frappe.require(WM_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_kit.call ? window.sf_kit : null;
			this.wm = window.sf_warehouse_map && window.sf_warehouse_map.parse_route ? window.sf_warehouse_map : null;
		});
		page.set_secondary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.bind();
		window.addEventListener("beforeunload", (e) => {
			if (this.edit_dirty()) {
				e.preventDefault();
				e.returnValue = "";
			}
		});
	}

	// ------------------------------------------------------------------ plumbing

	esc(value) {
		return this.sf ? this.sf.esc(value) : frappe.utils.escape_html(String(value === undefined || value === null ? "" : value));
	}

	call(method, args, get) {
		return this.sf.call(this.wm.API + method, args || {}, get ? { quiet: true, method: "GET" } : { quiet: true });
	}

	showing() {
		return (frappe.get_route() || [])[0] === (this.wm ? this.wm.PAGE : "warehouse-map");
	}

	// One screen to another. `carry` is what the next screen keeps of what is being looked for.
	navigate(target, carry, replace) {
		const route = this.wm.build_route(target);
		const options = this.wm.options_for(carry || {});
		if (replace) frappe.route_flags.replace_route = true;
		if (Object.keys(options).length) frappe.set_route(...route, options);
		else frappe.set_route(...route);
		// Read by push_state, which has already run; left set, the next screen would replace too.
		delete frappe.route_flags.replace_route;
	}

	carry() {
		return { item: this.item, bin: "" };
	}

	// The query of the screen on show, rewritten in place: picking a bin or an item is not a screen.
	write_query() {
		if (!this.showing() || !this.target || this.target.screen !== "floor") return;
		const query = this.wm.query_string({ item: this.item, bin: this.bin });
		try {
			window.history.replaceState(window.history.state, "", window.location.pathname + query + window.location.hash);
		} catch (e) {
			// The page still works; only the address is a step behind.
		}
	}

	route_changed() {
		this.ready.then(() => {
			if (!this.sf || !this.wm) {
				this.$root.html(`<p class="wm-fatal">${frappe.utils.escape_html(__("The map could not be loaded. Reload the page."))}</p>`);
				return;
			}
			const target = this.wm.parse_route(frappe.get_route());
			if (!target) return;
			const options = this.wm.read_options(frappe.route_options);
			frappe.route_options = null;
			this.load_boot().then(() => this.show(target, options));
		});
	}

	load_boot(force) {
		if (this.boot && !force) return Promise.resolve();
		if (!this.boot) this.render_state("loading", __("Loading the map…"));
		return this.call("boot", {}, true).then(
			(boot) => {
				this.boot = boot || { maps: [], can_edit: false };
				this.boot_error = "";
			},
			(err) => {
				this.boot_error = (err && err.message) || __("The map could not be loaded.");
			}
		);
	}

	refresh() {
		if (!this.wm || !this.target) return;
		const name = this.target.map;
		// In the editor, Refresh loads the stored map again. That is how an editor whose save was
		// refused (someone else saved first) gets their version; boxes not saved are asked about.
		if (this.target.screen === "edit") {
			const again = () => {
				this.edit = null;
				delete this.maps[name];
				this.load_boot(true).then(() => this.open_editor(name));
			};
			if (this.edit_dirty()) frappe.confirm(__("Load the saved map again? The changes you have not saved will be lost."), again);
			else again();
			return;
		}
		this.bin_items = {};
		this.found = null;
		this.load_boot(true).then(() => {
			if (this.target.screen === "floor" && name) delete this.maps[name];
			this.show(this.target, { w: "", item: this.item, bin: this.bin });
		});
	}

	show(target, options) {
		if (this.boot_error) {
			this.render_state("error", this.boot_error, () => this.load_boot(true).then(() => this.show(target, options)));
			return;
		}
		// "Show on map": the server says which map and box show the warehouse, and that screen
		// takes this entry's place.
		if (options.w) {
			this.locate(options.w, options.item);
			return;
		}
		if (!target.map) {
			const first = this.wm.default_map(this.boot);
			if (first) {
				this.navigate({ screen: "floor", map: first }, { item: options.item }, true);
				return;
			}
			this.target = target;
			this.render_none();
			return;
		}
		if (target.screen === "edit" && !this.boot.can_edit) {
			this.navigate({ screen: "floor", map: target.map }, {}, true);
			return;
		}
		const left_editor = this.target && this.target.screen === "edit" && target.screen !== "edit";
		// A box just opened is brought into view: on a phone its front view is below the map.
		this.reveal = target.screen === "floor" && !!target.shape && !(this.target && this.target.map === target.map && this.target.shape === target.shape);
		this.target = target;
		this.page.set_title(target.screen === "edit" ? __("Edit Warehouse Map") : __("Warehouse Map"));
		if (target.screen === "edit") {
			this.open_editor(target.map);
			return;
		}
		if (left_editor && this.edit_dirty()) {
			frappe.show_alert(
				{ message: __("Your changes to {0} are not saved yet. Open the editor again to save them.", [this.edit.map.title]), indicator: "orange" },
				8
			);
		}
		this.item = options.item;
		this.bin = options.bin;
		if (this.item && !(this.found && this.found.item_code === this.item)) this.load_item(this.item);
		if (!this.item) this.found = null;
		this.load_map(target.map);
		if (this.bin) this.load_bin(this.bin);
		this.render();
	}

	locate(warehouse, item) {
		this.render_state("loading", __("Finding {0} on the map…", [warehouse]));
		this.call("locate", { warehouse }, true).then(
			(found) => {
				if (found && found.map) {
					const bin = !found.is_group && found.shape !== found.warehouse ? found.warehouse : "";
					this.notice = "";
					this.navigate({ screen: "floor", map: found.map, shape: found.shape || "" }, { item, bin }, true);
					return;
				}
				this.notice = __("{0} is not on a map yet.", [(found && found.warehouse_name) || warehouse]);
				this.fall_back(item);
			},
			(err) => {
				this.notice = (err && err.message) || __("That location could not be found.");
				this.fall_back(item);
			}
		);
	}

	// Where a "Show on map" link lands when no map shows its warehouse: the first map, with the
	// sentence that says so. With no map at all there is no other address to go to (routing to the
	// address already on show changes nothing), so the empty page is drawn where it is.
	fall_back(item) {
		const first = this.wm.default_map(this.boot);
		if (first) {
			this.navigate({ screen: "floor", map: first }, { item }, true);
			return;
		}
		this.target = { screen: "floor", map: "", shape: "" };
		this.render_none();
	}

	load_map(name, force) {
		const held = this.maps[name];
		// What is drawn is kept while the page is used, and read again in the background once it is
		// a minute old: the counts move whenever anyone takes or receives a part.
		const fresh = held && held.at && Date.now() - held.at < WM_STALE_MS;
		if (held && (held.loading || (held.data && fresh)) && !force) return;
		this.maps[name] = { data: held && held.data, at: held && held.at, loading: true, error: "" };
		this.call("get_map", { name }, true).then(
			(data) => {
				this.maps[name] = { data, at: Date.now(), loading: false, error: "" };
				if (this.target && this.target.map === name && this.target.screen === "floor") this.render();
			},
			(err) => {
				// A background re-read that failed leaves what was drawn.
				this.maps[name] = held && held.data ? held : { data: null, loading: false, error: (err && err.message) || __("The map could not be loaded.") };
				if (held && held.data) held.loading = false;
				if (this.target && this.target.map === name && this.target.screen === "floor") this.render();
			}
		);
	}

	load_item(item_code) {
		this.found = { item_code, loading: true };
		this.call("find_item", { item_code }, true).then(
			(answer) => {
				if (this.item !== item_code) return;
				this.found = { item_code, answer };
				this.render();
			},
			(err) => {
				if (this.item !== item_code) return;
				this.found = { item_code, error: (err && err.message) || __("That item could not be looked up.") };
				this.render();
			}
		);
	}

	load_bin(warehouse) {
		if (this.bin_items[warehouse]) return;
		this.bin_items[warehouse] = { loading: true };
		this.sf.call(this.wm.SCAN + "get_location", { warehouse }, { quiet: true }).then(
			(loc) => {
				this.bin_items[warehouse] = { rows: (loc && loc.items) || [], truncated: !!(loc && loc.truncated) };
				if (this.bin === warehouse) this.render_detail();
			},
			(err) => {
				this.bin_items[warehouse] = { error: (err && err.message) || __("The bin's items could not be read.") };
				if (this.bin === warehouse) this.render_detail();
			}
		);
	}

	// ------------------------------------------------------------------ what is on screen

	data() {
		const held = this.target && this.maps[this.target.map];
		return held && held.data ? held.data : null;
	}

	view(map) {
		if (!this.views[map.name]) this.views[map.name] = this.wm.fit_view(map);
		return this.views[map.name];
	}

	open_shape() {
		const data = this.data();
		if (!data || !this.target || !this.target.shape) return null;
		return data.shapes.find((s) => s.warehouse === this.target.shape) || null;
	}

	hits() {
		const answer = this.found && this.found.answer;
		return this.wm.highlight(answer ? answer.places : [], this.target ? this.target.map : "");
	}

	render_state(kind, title, retry) {
		if (this.sf && this.sf.state) {
			this.sf.state.render(this.$root, kind, retry ? { title, on_action: retry } : { title });
			return;
		}
		this.$root.html(`<p class="wm-fatal">${frappe.utils.escape_html(title)}</p>`);
	}

	tabs_html() {
		const tabs = WM_TABS.map((t, i) => {
			const on = !t.route;
			return `<button type="button" class="wm-tab ${on ? "is-on" : ""}" role="tab" aria-selected="${on}" data-act="tab" data-tab="${i}">${this.esc(__(t.label))}</button>`;
		}).join("");
		return `<div class="wm-tabs" role="tablist" aria-label="${this.esc(__("Bin Manager views"))}">${tabs}</div>`;
	}

	render_none() {
		const can = this.boot && this.boot.can_edit;
		this.$root.html(
			`${this.tabs_html()}<div class="wm-none"><h3>${this.esc(__("There is no map yet"))}</h3>
			<p>${this.esc(
				can
					? __("A map is a floor plan with a box for each bay and room. Start one, upload the floor plan picture, and place the boxes.")
					: __("Someone with the Warehouse Map Editor role can draw one.")
			)}</p>
			${this.notice ? `<p class="wm-notice">${this.esc(this.notice)}</p>` : ""}
			${can ? `<button type="button" class="btn btn-primary btn-sm" data-act="new-map">${this.esc(__("Start the first map"))}</button>` : ""}</div>`
		);
	}

	floors_html() {
		const maps = (this.boot && this.boot.maps) || [];
		const buttons = maps
			.map(
				(m) =>
					`<button type="button" class="wm-floor ${m.name === this.target.map ? "is-on" : ""}" data-act="floor" data-map="${this.esc(m.name)}"
					aria-pressed="${m.name === this.target.map}">${this.esc(m.title)}${m.disabled ? ` <span class="wm-muted">(${this.esc(__("hidden"))})</span>` : ""}</button>`
			)
			.join("");
		return `<div class="wm-floors" role="group" aria-label="${this.esc(__("Floors"))}">${buttons}</div>`;
	}

	legend_html() {
		const keys = ["stock", "empty", "problem"];
		return `<div class="wm-legend" aria-label="${this.esc(__("What the colors mean"))}">${keys
			.map((k) => `<span class="wm-key"><span class="wm-swatch wm-s-${k}"></span>${this.esc(__(this.wm.STATUS[k].label))}</span>`)
			.join("")}${this.item ? `<span class="wm-key"><span class="wm-swatch wm-hit-swatch"></span>${this.esc(__("The item you searched"))}</span>` : ""}</div>`;
	}

	search_html() {
		const s = this.search;
		let list = "";
		if (s.query && s.loading && !s.rows) list = `<div class="wm-results"><p class="wm-muted">${this.esc(__("Searching…"))}</p></div>`;
		else if (s.query && s.rows) {
			list = `<div class="wm-results">${
				s.rows.length
					? s.rows
							.slice(0, 12)
							.map(
								(r) =>
									`<button type="button" class="wm-result" data-act="pick-item" data-item="${this.esc(r.item_code)}">
									<span class="wm-result-name">${this.esc(r.item_name || r.item_code)}</span>
									<span class="wm-muted">${this.esc(r.item_code)}</span></button>`
							)
							.join("")
					: `<p class="wm-muted">${this.esc(__("No item matches that."))}</p>`
			}</div>`;
		}
		return `<div class="wm-search"><input type="search" class="form-control wm-search-box" placeholder="${this.esc(
			__("Find an item on the map")
		)}" aria-label="${this.esc(__("Find an item on the map"))}" value="${this.esc(s.query)}" autocomplete="off">${list}</div>`;
	}

	item_html() {
		if (!this.item) return "";
		const f = this.found || {};
		let body = "";
		if (f.loading) body = `<span class="wm-muted">${this.esc(__("Looking for {0}…", [this.item]))}</span>`;
		else if (f.error) body = `<span class="wm-error">${this.esc(f.error)}</span>`;
		else if (f.answer) {
			const a = f.answer;
			const places = (a.places || [])
				.map((p) => {
					const where = [p.warehouse_name, p.trail].filter(Boolean).join(" · ");
					const what = this.wm.place_text(p, a.stock_uom);
					if (!p.map) return `<li class="wm-place"><span>${this.esc(where)}</span> <span class="wm-muted">${this.esc(what)} · ${this.esc(__("not on a map"))}</span></li>`;
					const title = ((this.boot.maps || []).find((m) => m.name === p.map) || {}).title || p.map;
					return `<li class="wm-place"><button type="button" class="wm-linkbtn" data-act="go-place" data-map="${this.esc(p.map)}"
						data-shape="${this.esc(p.shape || "")}" data-bin="${this.esc(p.warehouse)}">${this.esc(where)}</button>
						<span class="wm-muted">${this.esc(what)}${p.map !== this.target.map ? ` · ${this.esc(title)}` : ""}</span></li>`;
				})
				.join("");
			body = `<span>${this.esc(this.wm.item_sentence(a))}</span>${places ? `<ul class="wm-places">${places}</ul>` : ""}`;
		}
		return `<div class="wm-item" role="status"><div class="wm-item-body">${body}</div>
			<button type="button" class="btn btn-xs btn-default" data-act="clear-item">${this.esc(__("Clear"))}</button></div>`;
	}

	render() {
		if (!this.target || this.target.screen !== "floor") return;
		const held = this.maps[this.target.map] || {};
		const data = held.data;
		let body;
		if (!data && held.error) body = `<div class="wm-stage-msg wm-error">${this.esc(held.error)}</div>`;
		else if (!data) body = `<div class="wm-stage-msg wm-muted">${this.esc(__("Loading the map…"))}</div>`;
		else body = this.stage_html(data);
		const can = this.boot && this.boot.can_edit;
		const active = document.activeElement && $(document.activeElement).hasClass("wm-search-box");
		const caret = active ? document.activeElement.selectionStart : null;
		this.$root.html(
			`${this.tabs_html()}
			<div class="wm-bar">${this.floors_html()}${this.search_html()}
				<div class="wm-actions">
					${data ? `<button type="button" class="btn btn-sm btn-default" data-act="print">${this.esc(__("Print"))}</button>` : ""}
					${can && data ? `<button type="button" class="btn btn-sm btn-default" data-act="edit">${this.esc(__("Edit map"))}</button>` : ""}
					${can ? `<button type="button" class="btn btn-sm btn-default" data-act="new-map">${this.esc(__("New map"))}</button>` : ""}
				</div></div>
			${this.notice ? `<p class="wm-notice">${this.esc(this.notice)}</p>` : ""}
			${this.item_html()}
			${body}
			<div class="wm-detail-host"></div>
			${data ? this.off_map_html(data) : ""}`
		);
		if (active) {
			const box = this.$root.find(".wm-search-box")[0];
			if (box) {
				box.focus();
				try {
					box.setSelectionRange(caret, caret);
				} catch (e) {
					// A search box that cannot hold a caret position keeps the focus anyway.
				}
			}
		}
		this.render_detail();
	}

	stage_html(data) {
		const map = data.map;
		const view = this.view(map);
		const zoomed = this.wm.is_zoomed(view, map);
		const empty = !data.shapes.length
			? `<p class="wm-muted wm-stage-note">${this.esc(
					this.boot.can_edit ? __("Nothing is placed on this map yet. Choose Edit map to place the bays and rooms.") : __("Nothing is placed on this map yet.")
			  )}</p>`
			: "";
		return `<div class="wm-stage ${zoomed ? "is-zoomed" : ""}">
			<div class="wm-zoom" role="group" aria-label="${this.esc(__("Zoom"))}">
				<button type="button" class="btn btn-xs btn-default" data-act="zoom-in" aria-label="${this.esc(__("Zoom in"))}">+</button>
				<button type="button" class="btn btn-xs btn-default" data-act="zoom-out" aria-label="${this.esc(__("Zoom out"))}">&minus;</button>
				<button type="button" class="btn btn-xs btn-default" data-act="zoom-fit">${this.esc(__("Fit"))}</button>
			</div>
			${this.svg_html(map, data.shapes, { view, hits: this.hits(), open: this.target.shape })}
			${this.legend_html()}${empty}</div>`;
	}

	// The drawing. `opts.edit` draws the editor's version: every box can be picked up, the picked one
	// has its handle, and nothing is colored by stock.
	svg_html(map, shapes, opts) {
		const o = opts || {};
		const view = o.view || this.wm.fit_view(map);
		const scale = this.wm.scale_of(map);
		const order = { Room: 0, Rack: 1, Label: 2 };
		const list = shapes.map((s, i) => ({ s, i })).sort((a, b) => (order[a.s.shape_type] || 0) - (order[b.s.shape_type] || 0) || a.i - b.i);
		const layer = list.map(({ s, i }) => this.shape_svg(s, i, scale, o)).join("");
		const picture = map.background
			? `<image class="wm-picture" href="${this.esc(map.background)}" x="0" y="0" width="${map.width}" height="${map.height}" preserveAspectRatio="none"></image>`
			: "";
		return `<svg class="wm-svg ${o.edit ? "is-edit" : ""}" viewBox="${view.x} ${view.y} ${view.w} ${view.h}" preserveAspectRatio="xMidYMid meet"
			role="group" aria-label="${this.esc(map.title)}" xmlns="http://www.w3.org/2000/svg">
			<rect class="wm-paper" x="0" y="0" width="${map.width}" height="${map.height}"></rect>${picture}
			<g class="wm-layer">${layer}</g></svg>`;
	}

	shape_svg(s, i, scale, o) {
		const wm = this.wm;
		const kind = s.shape_type === wm.ROOM ? "room" : s.shape_type === wm.LABEL ? "label" : "rack";
		const status = o.edit ? (s.missing ? "missing" : "none") : wm.shape_status(s);
		const hit = !o.edit && o.hits && s.warehouse && o.hits.shapes[s.warehouse];
		const open = !o.edit && o.open && s.warehouse === o.open;
		const picked = o.edit && o.selected === i;
		const cx = s.x + s.w / 2;
		const cy = s.y + s.h / 2;
		const lay = wm.label_layout(s, scale);
		const turn = s.rotation ? ` transform="rotate(${s.rotation} ${cx} ${cy})"` : "";
		const can_open = !o.edit && !!s.warehouse && !s.missing;
		const name = wm.shape_label(s);
		const said = [name, o.edit ? "" : wm.summary_text(s)].filter(Boolean).join(": ");
		let label;
		if (kind === "room") {
			// A room's name sits in its top-left corner, out of the way of what is inside it.
			const size = Math.min(lay.size, 14 * scale);
			label = `<text class="wm-text" x="${s.x + 6 * scale}" y="${s.y + size + 4 * scale}" font-size="${size}">${this.esc(name)}</text>`;
		} else {
			const spin = lay.vertical ? ` transform="rotate(-90 ${cx} ${cy})"` : "";
			label = `<text class="wm-text" x="${cx}" y="${cy}" font-size="${lay.size}" text-anchor="middle" dominant-baseline="central"${spin}>${this.esc(name)}</text>`;
		}
		const handle = picked
			? `<rect class="wm-handle" data-handle="1" x="${s.x + s.w - 6 * scale}" y="${s.y + s.h - 6 * scale}" width="${12 * scale}" height="${12 * scale}"></rect>`
			: "";
		const box = kind === "label" ? `<rect class="wm-box" x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}"></rect>` : `<rect class="wm-box" x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="${2 * scale}"></rect>`;
		return `<g class="wm-shape wm-${kind} wm-s-${status} ${hit ? "is-hit" : ""} ${open ? "is-open" : ""} ${picked ? "is-picked" : ""} ${can_open || o.edit ? "is-live" : ""}"
			data-shape="${i}"${turn} ${can_open || o.edit ? 'tabindex="0" role="button"' : ""} aria-label="${this.esc(said)}">
			<title>${this.esc(said)}</title>${box}${label}${handle}</g>`;
	}

	off_map_html(data) {
		const rows = data.off_map || [];
		if (!rows.length) return "";
		const kinds = { vehicle: __("Vehicles"), job_site: __("Job sites") };
		const groups = ["vehicle", "job_site"]
			.map((kind) => {
				const list = rows.filter((r) => r.kind === kind);
				if (!list.length) return "";
				return `<div class="wm-off-group"><h4>${this.esc(kinds[kind])}</h4><ul>${list
					.map(
						(r) =>
							`<li><button type="button" class="wm-linkbtn" data-act="open-bin" data-bin="${this.esc(r.warehouse)}">${this.esc(
								r.project_name || r.warehouse_name
							)}</button> <span class="wm-muted">${this.esc(r.on_hand_items > 0 ? __("{0} on hand", [r.on_hand_items]) : __("nothing on hand"))}</span></li>`
					)
					.join("")}</ul></div>`;
			})
			.join("");
		return `<section class="wm-off"><h3>${this.esc(__("Not in the building"))}</h3><div class="wm-off-groups">${groups}</div></section>`;
	}

	// The open box: its front view, and the bin picked in it.
	render_detail() {
		const host = this.$root.find(".wm-detail-host");
		if (!host.length) return;
		const data = this.data();
		const shape = this.open_shape();
		if (!data || !this.target.shape) {
			host.empty();
			return;
		}
		if (!shape) {
			host.html(`<section class="wm-detail"><p class="wm-muted">${this.esc(__("{0} is not on this map.", [this.target.shape]))}</p></section>`);
			return;
		}
		const wm = this.wm;
		const hits = this.hits();
		const rows = wm.level_rows(shape, data.bins, hits.bins);
		// A box that is one bin (a room) has nothing inside to pick: it is the bin.
		const single = !shape.is_group ? shape.warehouse : "";
		const picked = single || this.bin;
		const levels = rows
			.map((row) => {
				const bins = row.bins.length
					? row.bins
							.map(
								(b) =>
									`<button type="button" class="wm-bin wm-s-${b.status} ${b.hit ? "is-hit" : ""} ${b.warehouse === picked ? "is-on" : ""}" data-act="bin"
									data-bin="${this.esc(b.warehouse)}" title="${this.esc(b.name)}" aria-pressed="${b.warehouse === picked}">${this.esc(b.label)}</button>`
							)
							.join("")
					: `<span class="wm-muted wm-nobins">${this.esc(__("no bins on this shelf"))}</span>`;
				return `<div class="wm-level"><span class="wm-level-name">${this.esc(row.label)}</span><div class="wm-bins">${bins}</div></div>`;
			})
			.join("");
		host.html(
			`<section class="wm-detail" aria-label="${this.esc(wm.shape_label(shape))}">
				<header class="wm-detail-head">
					<div><h3>${this.esc(shape.warehouse_name || wm.shape_label(shape))}</h3>
					<p class="wm-muted">${this.esc([shape.trail, wm.summary_text(shape)].filter(Boolean).join(" · "))}</p></div>
					<button type="button" class="btn btn-sm btn-default" data-act="close-shape">${this.esc(__("Close"))}</button>
				</header>
				${rows.length ? `<p class="wm-muted wm-facing">${this.esc(__("As you face it"))}</p><div class="wm-levels">${levels}</div>` : ""}
				${picked ? this.bin_html(picked, data) : rows.length ? `<p class="wm-muted">${this.esc(__("Pick a bin to see what is in it."))}</p>` : ""}
			</section>`
		);
		if (this.reveal) {
			this.reveal = false;
			const el = host[0];
			if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
		}
	}

	bin_html(warehouse, data) {
		const bin = data.bins[warehouse] || {};
		const held = this.bin_items[warehouse] || { loading: true };
		let list;
		if (held.loading) list = `<p class="wm-muted">${this.esc(__("Reading the bin…"))}</p>`;
		else if (held.error) list = `<p class="wm-error">${this.esc(held.error)}</p>`;
		else if (!held.rows.length) list = `<p class="wm-muted">${this.esc(__("Nothing is kept here."))}</p>`;
		else {
			list = `<ul class="wm-items">${held.rows
				.map((r) => {
					const qty = Number(r.on_hand) || 0;
					const what = qty > 0 ? `${Math.round(qty * 1000) / 1000} ${r.stock_uom || ""}`.trim() : r.assigned ? __("none on hand") : Number(r.on_order) > 0 ? __("on order") : __("none on hand");
					return `<li class="${r.item_code === this.item ? "is-hit" : ""}"><span>${this.esc(r.item_name || r.item_code)}</span><span class="wm-muted">${this.esc(what)}</span></li>`;
				})
				.join("")}</ul>${held.truncated ? `<p class="wm-muted">${this.esc(__("There are more; open the bin to see them all."))}</p>` : ""}`;
		}
		return `<div class="wm-bininfo"><div class="wm-bininfo-head"><div><strong>${this.esc(bin.warehouse_name || warehouse)}</strong>
			<span class="wm-muted">${this.esc(this.wm.bin_text(bin))}</span></div>
			<div class="wm-bininfo-acts">
				<button type="button" class="btn btn-xs btn-default" data-act="open-bin" data-bin="${this.esc(warehouse)}">${this.esc(__("Open in Bin Manager"))}</button>
				<a class="btn btn-xs btn-default" href="/stock-scan?w=${encodeURIComponent(warehouse)}">${this.esc(__("Open in Stock Scan"))}</a>
			</div></div>${list}</div>`;
	}

	// ------------------------------------------------------------------ events

	bind() {
		const $r = this.$root;
		$r.on("click", "[data-act]", (e) => this.act($(e.currentTarget), e));
		$r.on("input", ".wm-search-box", (e) => this.search_typed(e.currentTarget.value));
		$r.on("keydown", ".wm-search-box", (e) => {
			if (e.key === "Escape") this.search_typed("");
		});
		$r.on("keydown", ".wm-shape", (e) => this.shape_key(e));
		$r.on("pointerdown", ".wm-svg", (e) => this.pointer_down(e.originalEvent));
		$r.on("pointermove", ".wm-svg", (e) => this.pointer_move(e.originalEvent));
		$r.on("pointerup pointercancel", ".wm-svg", (e) => this.pointer_up(e.originalEvent));
		$r.on("wheel", ".wm-svg", (e) => this.wheel(e.originalEvent));
		$r.on("input change", "[data-field]", (e) => this.field_changed($(e.currentTarget), e.type));
		$r.on("input", ".wm-pick-box", (e) => this.pick_typed(e.currentTarget.value));
	}

	act($el, e) {
		const act = $el.data("act");
		const t = this.target || {};
		// A sentence about where a link landed has been read once anything is pressed.
		this.notice = "";
		if (act === "tab") {
			const tab = WM_TABS[Number($el.data("tab"))];
			if (tab && tab.route) frappe.set_route(...tab.route);
		} else if (act === "floor") this.navigate({ screen: "floor", map: String($el.data("map")) }, this.carry());
		else if (act === "close-shape") this.navigate({ screen: "floor", map: t.map }, this.carry());
		else if (act === "bin") this.pick_bin(String($el.data("bin")));
		else if (act === "open-bin") frappe.set_route(this.wm.BIN_MANAGER, String($el.data("bin")));
		else if (act === "pick-item") this.pick_item(String($el.data("item")));
		else if (act === "clear-item") this.pick_item("");
		else if (act === "go-place") {
			const shape = String($el.data("shape") || "");
			const bin = String($el.data("bin") || "");
			this.navigate({ screen: "floor", map: String($el.data("map")), shape }, { item: this.item, bin: shape && bin !== shape ? bin : "" });
		} else if (act === "zoom-in" || act === "zoom-out" || act === "zoom-fit") this.zoom(act);
		else if (act === "print") this.print();
		else if (act === "edit") this.navigate({ screen: "edit", map: t.map });
		else if (act === "new-map") this.new_map();
		else this.editor_act(act, $el, e);
	}

	shape_key(e) {
		if (e.key !== "Enter" && e.key !== " ") {
			if (this.target && this.target.screen === "edit") this.editor_key(e);
			return;
		}
		e.preventDefault();
		const i = Number($(e.currentTarget).data("shape"));
		if (this.target.screen === "edit") this.select(i);
		else this.open_index(i);
	}

	open_index(i) {
		const data = this.data();
		const s = data && data.shapes[i];
		if (!s || !s.warehouse || s.missing) return;
		if (this.target.shape === s.warehouse) return;
		this.navigate({ screen: "floor", map: this.target.map, shape: s.warehouse }, this.carry());
	}

	pick_bin(warehouse) {
		this.bin = this.bin === warehouse ? "" : warehouse;
		if (this.bin) this.load_bin(this.bin);
		this.write_query();
		this.render_detail();
	}

	pick_item(item_code) {
		this.item = item_code;
		this.search = { query: "", rows: null, loading: false, seq: this.search.seq + 1 };
		this.found = null;
		if (item_code) this.load_item(item_code);
		this.write_query();
		this.render();
	}

	search_typed(value) {
		const s = this.search;
		s.query = value;
		s.seq += 1;
		const seq = s.seq;
		clearTimeout(this._search_timer);
		if (!value.trim()) {
			s.rows = null;
			s.loading = false;
			this.render();
			return;
		}
		s.loading = true;
		this._search_timer = setTimeout(() => {
			this.sf.call(this.wm.SCAN + "search_items", { query: value }, { quiet: true }).then(
				(rows) => {
					if (this.search.seq !== seq) return;
					this.search.rows = Array.isArray(rows) ? rows : [];
					this.search.loading = false;
					this.render();
				},
				() => {
					if (this.search.seq !== seq) return;
					this.search.rows = [];
					this.search.loading = false;
					this.render();
				}
			);
		}, WM_SEARCH_DELAY_MS);
	}

	// ------------------------------------------------------------------ zoom, pan, and the pointer

	current_map() {
		if (this.target && this.target.screen === "edit") return this.edit ? this.edit.map : null;
		const data = this.data();
		return data ? data.map : null;
	}

	set_view(view) {
		const map = this.current_map();
		if (!map) return;
		this.views[map.name] = view;
		const svg = this.$root.find(".wm-svg")[0];
		if (svg) svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
		this.$root.find(".wm-stage").toggleClass("is-zoomed", this.wm.is_zoomed(view, map));
	}

	zoom(act) {
		const map = this.current_map();
		if (!map) return;
		if (act === "zoom-fit") this.set_view(this.wm.fit_view(map));
		else this.set_view(this.wm.zoom_view(this.view(map), act === "zoom-in" ? 1.5 : 1 / 1.5, null, null, map));
	}

	wheel(e) {
		// Only a pinch on a trackpad (which arrives as ctrl + wheel) or ctrl + wheel zooms; a plain
		// wheel scrolls the page, as everywhere else in the Desk.
		if (!e.ctrlKey && !e.metaKey) return;
		const map = this.current_map();
		if (!map) return;
		e.preventDefault();
		const svg = this.$root.find(".wm-svg")[0];
		const view = this.view(map);
		const at = this.wm.to_map_point(view, svg.getBoundingClientRect(), e.clientX, e.clientY);
		this.set_view(this.wm.zoom_view(view, e.deltaY < 0 ? 1.15 : 1 / 1.15, at.x, at.y, map));
	}

	pointer_down(e) {
		const map = this.current_map();
		if (!map || (e.button !== undefined && e.button > 0)) return;
		const svg = this.$root.find(".wm-svg")[0];
		this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
		if (this.pointers.size === 2) {
			const [a, b] = Array.from(this.pointers.values());
			this.drag = { mode: "pinch", view: Object.assign({}, this.view(map)), gap: Math.hypot(a.x - b.x, a.y - b.y) || 1, moved: true };
			return;
		}
		const editing = this.target.screen === "edit";
		const $shape = $(e.target).closest("[data-shape]");
		const index = $shape.length ? Number($shape.data("shape")) : -1;
		const on_handle = editing && $(e.target).closest("[data-handle]").length > 0;
		let mode = "pan";
		if (editing && index >= 0) {
			mode = on_handle ? "resize" : "move";
			if (this.edit.selected !== index) this.select(index);
		}
		this.drag = {
			mode,
			index,
			px: e.clientX,
			py: e.clientY,
			moved: false,
			view: Object.assign({}, this.view(map)),
			start: editing && index >= 0 ? Object.assign({}, this.edit.shapes[index]) : null,
		};
		try {
			svg.setPointerCapture(e.pointerId);
		} catch (err) {
			// Without capture the drag still works while the pointer stays over the drawing.
		}
	}

	pointer_move(e) {
		if (!this.drag) return;
		const map = this.current_map();
		if (!map) return;
		if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
		const d = this.drag;
		const svg = this.$root.find(".wm-svg")[0];
		const rect = svg.getBoundingClientRect();
		if (d.mode === "pinch") {
			if (this.pointers.size < 2) return;
			const [a, b] = Array.from(this.pointers.values());
			const gap = Math.hypot(a.x - b.x, a.y - b.y) || 1;
			const at = this.wm.to_map_point(d.view, rect, (a.x + b.x) / 2, (a.y + b.y) / 2);
			this.set_view(this.wm.zoom_view(d.view, gap / d.gap, at.x, at.y, map));
			return;
		}
		const sx = e.clientX - d.px;
		const sy = e.clientY - d.py;
		if (!d.moved && Math.hypot(sx, sy) < WM_TAP_SLOP_PX) return;
		d.moved = true;
		// Screen pixels to map units, at the zoom the drag began with. The SVG keeps the drawing's
		// shape (meet), so one side's ratio is the true one and the larger of the two is it.
		const k = Math.max(d.view.w / (rect.width || 1), d.view.h / (rect.height || 1));
		if (d.mode === "pan") {
			if (this.wm.is_zoomed(d.view, map)) this.set_view(this.wm.pan_view(d.view, sx * k, sy * k, map));
			return;
		}
		const grid = e.altKey ? 0 : this.wm.grid_of(map);
		const next = d.mode === "move" ? this.wm.move_shape(d.start, sx * k, sy * k, map, grid) : this.wm.resize_shape(d.start, sx * k, sy * k, map, grid);
		this.edit.shapes[d.index] = Object.assign({}, this.edit.shapes[d.index], next);
		this.redraw_layer();
	}

	pointer_up(e) {
		this.pointers.delete(e.pointerId);
		const d = this.drag;
		if (!d) return;
		if (d.mode === "pinch") {
			if (this.pointers.size < 2) this.drag = null;
			return;
		}
		this.drag = null;
		const editing = this.target.screen === "edit";
		if (editing) {
			if (d.moved && d.mode !== "pan") this.sync_box_inputs();
			else if (!d.moved && d.index < 0) this.select(-1);
			this.focus_picked();
			return;
		}
		if (!d.moved && d.index >= 0 && e.type === "pointerup") this.open_index(d.index);
	}

	// ------------------------------------------------------------------ print

	// One sheet per floor: the drawing with every box's name, then what is in each rack as a list. A
	// new window, because the Desk's own chrome cannot be printed around.
	print() {
		const data = this.data();
		if (!data) return;
		const wm = this.wm;
		const map = data.map;
		const svg = this.svg_html(map, data.shapes, { view: wm.fit_view(map), hits: { shapes: {}, bins: {} }, open: "" });
		const racks = data.shapes
			.filter((s) => s.warehouse && !s.missing && s.is_group)
			.map((s) => {
				const rows = wm
					.level_rows(s, data.bins, {})
					.map((r) => `<tr><th>${this.esc(r.label)}</th><td>${r.bins.map((b) => `<span class="b">${this.esc(b.label)}</span>`).join("") || "&nbsp;"}</td></tr>`)
					.join("");
				return `<div class="rack"><h3>${this.esc(s.warehouse_name)}</h3><table>${rows}</table></div>`;
			})
			.join("");
		const html = `<!doctype html><html><head><meta charset="utf-8"><base href="${this.esc(window.location.origin)}/">
			<title>${this.esc(map.title)}</title><style>
			@page { size: landscape; margin: 12mm; }
			body { font-family: Arial, Helvetica, sans-serif; color: #111; margin: 0; }
			h1 { font-size: 18pt; margin: 0 0 2mm; } p.when { font-size: 9pt; color: #555; margin: 0 0 4mm; }
			svg { width: 100%; max-height: 165mm; display: block; }
			.wm-paper { fill: #fff; stroke: #999; stroke-width: 1; vector-effect: non-scaling-stroke; }
			.wm-box { fill: #f2f2f2; stroke: #222; stroke-width: 1; vector-effect: non-scaling-stroke; }
			.wm-room .wm-box { fill: none; } .wm-label .wm-box { fill: none; stroke: none; }
			.wm-text { fill: #111; font-weight: bold; } .wm-handle { display: none; }
			.racks { page-break-before: always; column-count: 3; column-gap: 8mm; }
			.rack { break-inside: avoid; margin: 0 0 5mm; } .rack h3 { font-size: 11pt; margin: 0 0 1mm; }
			table { border-collapse: collapse; width: 100%; font-size: 8pt; }
			th, td { border: 1px solid #999; padding: 1mm; text-align: left; vertical-align: top; } th { width: 22mm; }
			.b { display: inline-block; min-width: 6mm; padding: 0 1mm; margin: 0 1mm 0.5mm 0; border: 1px solid #bbb; text-align: center; }
			</style></head><body><h1>${this.esc(map.title)}</h1>
			<p class="when">${this.esc(__("Printed {0}", [frappe.datetime.str_to_user(frappe.datetime.get_today())]))}</p>${svg}
			${racks ? `<div class="racks">${racks}</div>` : ""}</body></html>`;
		const win = window.open("", "_blank");
		if (!win) {
			frappe.show_alert({ message: __("The browser blocked the print window. Allow pop-ups for this site and try again."), indicator: "orange" }, 8);
			return;
		}
		win.document.open();
		win.document.write(html);
		win.document.close();
		// The floor plan picture is fetched after the document is written; printing waits for it.
		const go = () => {
			win.focus();
			win.print();
		};
		const picture = win.document.querySelector("image");
		if (picture && map.background) {
			const probe = new Image();
			probe.onload = go;
			probe.onerror = go;
			probe.src = map.background;
		} else setTimeout(go, 50);
	}

	// ------------------------------------------------------------------ the editor

	edit_dirty() {
		return !!(this.edit && this.wm && this.wm.is_dirty(this.edit, this.edit.saved));
	}

	new_map() {
		frappe.prompt(
			[{ fieldname: "map_name", fieldtype: "Data", label: __("Name of the map"), reqd: 1, description: __("For example: Shop Floor, or Upstairs.") }],
			(values) => {
				this.call("save_map", { payload: JSON.stringify({ map_name: values.map_name }) }).then(
					(saved) => {
						this.load_boot(true).then(() => this.navigate({ screen: "edit", map: saved.name }));
					},
					(err) => frappe.msgprint({ title: __("The map was not created"), message: this.esc((err && err.message) || ""), indicator: "red" })
				);
			},
			__("New map"),
			__("Create")
		);
	}

	open_editor(name) {
		if (this.edit && this.edit.name === name) {
			this.render_editor();
			return;
		}
		this.render_state("loading", __("Opening the editor…"));
		Promise.all([this.call("get_map", { name }, true), this.call("editor_data", {}, true)]).then(
			([data, extra]) => {
				this.edit = {
					name,
					map: Object.assign({}, data.map),
					shapes: data.shapes.map((s) => Object.assign({}, s)),
					selected: -1,
					units: (extra && extra.units) || [],
					warehouses: (extra && extra.warehouses) || [],
					pick: "",
					error: "",
					saving: false,
					added: 0,
					// warehouse -> { warehouse_name }, for the order list's labels (the shape of get_map's bins)
					names: Object.assign({}, data.bins),
					levels_loading: {},
					order_pick: "",
					uploading: false,
				};
				this.edit.saved = JSON.stringify(this.wm.save_payload(this.edit));
				if (this.target.screen === "edit" && this.target.map === name) this.render_editor();
			},
			(err) => this.render_state("error", (err && err.message) || __("The editor could not be opened."), () => this.open_editor(name))
		);
	}

	render_editor() {
		const ed = this.edit;
		const map = ed.map;
		const view = this.view(map);
		this.$root.html(
			`<div class="wm-editbar">
				<button type="button" class="btn btn-sm btn-default" data-act="edit-done">${this.esc(__("Back to the map"))}</button>
				<span class="wm-editbar-gap"></span>
				<button type="button" class="btn btn-sm btn-default" data-act="add" data-kind="Rack">${this.esc(__("Add rack"))}</button>
				<button type="button" class="btn btn-sm btn-default" data-act="add" data-kind="Room">${this.esc(__("Add room"))}</button>
				<button type="button" class="btn btn-sm btn-default" data-act="add" data-kind="Label">${this.esc(__("Add label"))}</button>
				<button type="button" class="btn btn-sm btn-default" data-act="upload">${this.esc(map.background ? __("Change floor plan picture") : __("Upload floor plan picture"))}</button>
				<span class="wm-editbar-gap"></span>
				<span class="wm-dirty" role="status"></span>
				<button type="button" class="btn btn-sm btn-primary" data-act="save">${this.esc(__("Save"))}</button>
			</div>
			<p class="wm-error wm-edit-error" role="alert"></p>
			<div class="wm-edit">
				<div class="wm-stage is-edit ${this.wm.is_zoomed(view, map) ? "is-zoomed" : ""}">
					<div class="wm-zoom" role="group" aria-label="${this.esc(__("Zoom"))}">
						<button type="button" class="btn btn-xs btn-default" data-act="zoom-in" aria-label="${this.esc(__("Zoom in"))}">+</button>
						<button type="button" class="btn btn-xs btn-default" data-act="zoom-out" aria-label="${this.esc(__("Zoom out"))}">&minus;</button>
						<button type="button" class="btn btn-xs btn-default" data-act="zoom-fit">${this.esc(__("Fit"))}</button>
					</div>
					${this.svg_html(map, ed.shapes, { view, edit: true, selected: ed.selected })}
					<p class="wm-muted wm-stage-note">${this.esc(
						__("Drag a box to move it; drag its corner square to size it. Arrow keys nudge the picked box. Hold Alt while dragging to place it freely.")
					)}</p>
				</div>
				<aside class="wm-panel"></aside>
			</div>`
		);
		this.render_panel();
		this.paint_edit_status();
	}

	redraw_layer() {
		const ed = this.edit;
		const scale = this.wm.scale_of(ed.map);
		const order = { Room: 0, Rack: 1, Label: 2 };
		const list = ed.shapes.map((s, i) => ({ s, i })).sort((a, b) => (order[a.s.shape_type] || 0) - (order[b.s.shape_type] || 0) || a.i - b.i);
		const layer = this.$root.find(".wm-layer")[0];
		if (layer) layer.innerHTML = list.map(({ s, i }) => this.shape_svg(s, i, scale, { edit: true, selected: ed.selected })).join("");
		this.paint_edit_status();
	}

	paint_edit_status() {
		const ed = this.edit;
		if (!ed) return;
		this.$root.find(".wm-dirty").text(
			ed.uploading ? __("Uploading the picture…") : ed.saving ? __("Saving…") : this.edit_dirty() ? __("Not saved yet") : __("Saved")
		);
		this.$root.find(".wm-edit-error").text(ed.error || "");
		this.$root.find('[data-act="save"]').prop("disabled", !!ed.saving);
	}

	select(index) {
		const ed = this.edit;
		ed.selected = index;
		ed.pick = "";
		ed.order_pick = "";
		this.redraw_layer();
		this.render_panel();
	}

	// The picked box takes the keyboard (arrow keys nudge it). Redrawing the boxes replaces their
	// elements, so this is asked again after each redraw that should keep it.
	focus_picked() {
		const ed = this.edit;
		if (!ed || ed.selected < 0) return;
		const el = this.$root.find(`.wm-shape[data-shape="${ed.selected}"]`)[0];
		if (el && typeof el.focus === "function") {
			try {
				el.focus({ preventScroll: true });
			} catch (e) {
				// A browser that cannot focus an SVG group keeps the mouse and the side panel.
			}
		}
	}

	// The picked box's numbers into the side panel's boxes, in place: drawing the panel again
	// would take the cursor out of the box being typed in.
	sync_box_inputs() {
		const ed = this.edit;
		const s = ed && ed.shapes[ed.selected];
		if (!s) return;
		["x", "y", "w", "h", "rotation"].forEach((key) => {
			const el = this.$root.find(`[data-field="shape.${key}"]`)[0];
			if (el && el !== document.activeElement) el.value = Math.round((Number(s[key]) || 0) * 10) / 10;
		});
	}

	render_panel() {
		const ed = this.edit;
		const host = this.$root.find(".wm-panel");
		if (!host.length) return;
		const s = ed.shapes[ed.selected];
		const lists = this.wm.unit_lists(ed.units, ed.shapes, ed.name);
		const map_fields = `<details class="wm-mapfields"><summary>${this.esc(__("This map"))}</summary>
			<label>${this.esc(__("Name"))}<input class="form-control input-sm" data-field="map.title" value="${this.esc(ed.map.title)}" maxlength="60"></label>
			<label>${this.esc(__("Order in the list of floors"))}<input type="number" class="form-control input-sm" data-field="map.sort_order" value="${this.esc(ed.map.sort_order)}"></label>
			<label class="wm-check"><input type="checkbox" data-field="map.disabled" ${ed.map.disabled ? "checked" : ""}> ${this.esc(__("Leave this map off the page for everyone but editors (while it is being drawn)"))}</label>
			<p class="wm-muted">${this.esc(__("Drawing size: {0} by {1}", [Math.round(ed.map.width), Math.round(ed.map.height)]))}</p>
			${ed.map.background ? `<button type="button" class="btn btn-xs btn-default" data-act="remove-picture">${this.esc(__("Remove the floor plan picture"))}</button>` : ""}
			<button type="button" class="btn btn-xs btn-danger" data-act="delete-map">${this.esc(__("Delete this map"))}</button></details>`;
		const todo = lists.todo.length
			? `<ul class="wm-units">${lists.todo
					.map(
						(u) =>
							`<li><button type="button" class="wm-linkbtn" data-act="add-unit" data-warehouse="${this.esc(u.warehouse)}">${this.esc(u.warehouse_name)}</button>
							<span class="wm-muted">${this.esc([u.trail, u.is_group ? __("{0} bins", [u.bins]) : __("one place")].filter(Boolean).join(" · "))}</span></li>`
					)
					.join("")}</ul>`
			: `<p class="wm-muted">${this.esc(__("Everything in the building is on a map."))}</p>`;
		const elsewhere = lists.elsewhere.length
			? `<details class="wm-elsewhere"><summary>${this.esc(__("On another map ({0})", [lists.elsewhere.length]))}</summary><ul class="wm-units">${lists.elsewhere
					.map((u) => `<li>${this.esc(u.warehouse_name)} <span class="wm-muted">${this.esc(u.on_map)}</span></li>`)
					.join("")}</ul></details>`
			: "";
		host.html(
			`${s ? this.box_form(s, ed.selected) : `<p class="wm-muted">${this.esc(__("Pick a box to change it, or add one from the list below."))}</p>`}
			<h4>${this.esc(__("Not on a map yet"))}</h4>${todo}${elsewhere}${map_fields}`
		);
	}

	box_form(s, index) {
		const wm = this.wm;
		const kinds = wm.SHAPE_TYPES.map((k) => `<option value="${k}" ${s.shape_type === k ? "selected" : ""}>${this.esc(__(k))}</option>`).join("");
		const is_label = s.shape_type === wm.LABEL;
		const tied = s.warehouse
			? `<div class="wm-tied"><span>${this.esc(s.warehouse_name || s.warehouse)}${s.missing ? ` <span class="wm-error">(${this.esc(__(wm.missing_words(s).short))})</span>` : ""}</span>
				<button type="button" class="btn btn-xs btn-default" data-act="untie">${this.esc(__("Change"))}</button></div>`
			: `<input class="form-control input-sm wm-pick-box" placeholder="${this.esc(__("Type a bay, shelf or room"))}" aria-label="${this.esc(__("Warehouse for this box"))}" value="${this.esc(
					this.edit.pick
			  )}" autocomplete="off"><div class="wm-picks">${this.picks_html()}</div>`;
		const n = (key, label) =>
			`<label>${this.esc(label)}<input type="number" step="1" class="form-control input-sm" data-field="shape.${key}" value="${this.esc(Math.round(s[key] * 10) / 10)}"></label>`;
		const facing =
			s.shape_type === wm.RACK || (s.warehouse && s.is_group)
				? `<label>${this.esc(__("Shelf 1 is at the"))}<select class="form-control input-sm" data-field="shape.shelves_from">
					<option value="Top" ${s.shelves_from !== "Bottom" ? "selected" : ""}>${this.esc(__("Top"))}</option><option value="Bottom" ${s.shelves_from === "Bottom" ? "selected" : ""}>${this.esc(__("Bottom"))}</option></select></label>
				<label>${this.esc(__("Bin 1 is at the"))}<select class="form-control input-sm" data-field="shape.bins_from">
					<option value="Left" ${s.bins_from !== "Right" ? "selected" : ""}>${this.esc(__("Left"))}</option><option value="Right" ${s.bins_from === "Right" ? "selected" : ""}>${this.esc(__("Right"))}</option></select></label>`
				: "";
		return `<div class="wm-boxform"><h4>${this.esc(__("Box {0}", [index + 1]))}</h4>
			<label>${this.esc(__("Kind"))}<select class="form-control input-sm" data-field="shape.shape_type">${kinds}</select></label>
			${is_label ? "" : `<div class="wm-field"><span>${this.esc(__("Stands for"))}</span>${tied}</div>`}
			<label>${this.esc(is_label ? __("Words") : __("Name on the map (blank uses the warehouse's name)"))}<input class="form-control input-sm" data-field="shape.label" maxlength="60" value="${this.esc(s.label || "")}"></label>
			<div class="wm-grid4">${n("x", __("Left"))}${n("y", __("Top"))}${n("w", __("Width"))}${n("h", __("Height"))}</div>
			<label>${this.esc(__("Turned (degrees, for a box that sits at an angle)"))}<input type="number" step="1" class="form-control input-sm" data-field="shape.rotation" value="${this.esc(s.rotation || 0)}"></label>
			${facing}
			${this.order_html(s)}
			<div class="wm-boxacts">
				<button type="button" class="btn btn-xs btn-default" data-act="turn">${this.esc(__("Turn 90°"))}</button>
				<button type="button" class="btn btn-xs btn-default" data-act="remove-box">${this.esc(__("Remove box"))}</button>
			</div></div>`;
	}

	// "Order as you face it": the picked box's shelves and bins, in the order the map will show them,
	// with arrows to put right what the automatic order cannot know (rows named for colors, say).
	// Nothing is stored until something is moved; then the box keeps its whole order.
	order_html(s) {
		const wm = this.wm;
		const ed = this.edit;
		if (!s.warehouse || s.shape_type === wm.LABEL || s.missing || !s.is_group) return "";
		if (!Array.isArray(s.levels) || s.order_loading) {
			this.load_levels(s.warehouse);
			return `<div class="wm-order"><h5>${this.esc(__("Order as you face it"))}</h5><p class="wm-muted">${this.esc(__("Reading the shelves…"))}</p></div>`;
		}
		const rows = wm.level_rows(s, ed.names, {});
		if (!rows.length) return "";
		const body = rows
			.map((row, i) => {
				const shelf = row.warehouse !== s.warehouse;
				const arrows = shelf
					? `<span class="wm-order-arrows"><button type="button" class="btn btn-xs btn-default" data-act="order-row" data-row="${i}" data-dir="-1" aria-label="${this.esc(__("Move {0} up", [row.label]))}">&#9650;</button>
						<button type="button" class="btn btn-xs btn-default" data-act="order-row" data-row="${i}" data-dir="1" aria-label="${this.esc(__("Move {0} down", [row.label]))}">&#9660;</button></span>`
					: `<span class="wm-order-arrows"></span>`;
				const bins = row.bins
					.map(
						(b) =>
							`<button type="button" class="wm-ochip ${b.warehouse === ed.order_pick ? "is-on" : ""}" data-act="order-pick" data-row="${i}" data-bin="${this.esc(b.warehouse)}"
							aria-pressed="${b.warehouse === ed.order_pick}" title="${this.esc(b.name)}">${this.esc(b.label)}</button>`
					)
					.join("");
				return `<div class="wm-orow">${arrows}<span class="wm-orow-name">${this.esc(row.label || __("(no shelf)"))}</span><div class="wm-ochips">${bins}</div></div>`;
			})
			.join("");
		const picked = ed.order_pick && wm.shape_bins(s).includes(ed.order_pick);
		return `<div class="wm-order"><h5>${this.esc(__("Order as you face it"))}</h5>
			<p class="wm-muted">${this.esc(__("Top shelf first, left bin first. If this is not what you see standing in front of it, move a shelf with its arrows, or pick a bin and move it."))}</p>
			${body}
			<div class="wm-order-acts">
				<button type="button" class="btn btn-xs btn-default" data-act="order-bin" data-dir="-1" ${picked ? "" : "disabled"}>&#9664; ${this.esc(__("Move bin left"))}</button>
				<button type="button" class="btn btn-xs btn-default" data-act="order-bin" data-dir="1" ${picked ? "" : "disabled"}>${this.esc(__("Move bin right"))} &#9654;</button>
				${(s.bin_order || []).length ? `<button type="button" class="btn btn-xs btn-default" data-act="order-auto">${this.esc(__("Use the automatic order"))}</button>` : ""}
			</div></div>`;
	}

	// The shelves and bins of a warehouse in the automatic order, for a box the stored map does not
	// describe yet (just added or just tied) or one put back to the automatic order.
	load_levels(warehouse) {
		const ed = this.edit;
		if (ed.levels_loading[warehouse]) return;
		ed.levels_loading[warehouse] = true;
		const put = (patch) => {
			delete ed.levels_loading[warehouse];
			const at = ed.shapes.findIndex((x) => x.warehouse === warehouse);
			if (at < 0) return;
			ed.shapes[at] = Object.assign({}, ed.shapes[at], patch, { order_loading: false });
			if (this.target.screen === "edit" && ed.selected === at) this.render_panel();
		};
		this.call("box_levels", { warehouse }, true).then(
			(answer) => {
				Object.keys((answer && answer.names) || {}).forEach((key) => {
					ed.names[key] = { warehouse_name: answer.names[key] };
				});
				put({ levels: (answer && answer.levels) || [], is_group: !!(answer && answer.is_group) });
			},
			() => put({ levels: [] })
		);
	}

	order_changed(index, levels) {
		const ed = this.edit;
		const s = ed.shapes[index];
		if (!s || levels === s.levels) return;
		const next = Object.assign({}, s, { levels });
		next.bin_order = this.wm.order_from_levels(next);
		ed.shapes[index] = next;
		this.render_panel();
		this.paint_edit_status();
	}

	picks_html() {
		const ed = this.edit;
		if (!ed.pick.trim()) return "";
		const taken = {};
		ed.shapes.forEach((x) => {
			if (x.warehouse) taken[x.warehouse] = true;
		});
		const rows = this.wm.filter_warehouses(ed.warehouses, ed.pick, 12);
		if (!rows.length) return `<p class="wm-muted">${this.esc(__("No warehouse matches that."))}</p>`;
		return rows
			.map((r) =>
				taken[r.name]
					? `<div class="wm-pick is-taken">${this.esc(r.warehouse_name)} <span class="wm-muted">${this.esc(__("already on this map"))}</span></div>`
					: `<button type="button" class="wm-pick" data-act="tie" data-warehouse="${this.esc(r.name)}">${this.esc(r.warehouse_name)} <span class="wm-muted">${this.esc(r.trail)}</span></button>`
			)
			.join("");
	}

	pick_typed(value) {
		this.edit.pick = value;
		this.$root.find(".wm-picks").html(this.picks_html());
	}

	field_changed($el, type) {
		const ed = this.edit;
		if (!ed) return;
		const [scope, key] = String($el.data("field")).split(".");
		const el = $el[0];
		if (scope === "map") {
			if (key === "disabled") ed.map.disabled = el.checked;
			else if (key === "sort_order") ed.map.sort_order = Number(el.value) || 0;
			else ed.map[key] = el.value;
			this.paint_edit_status();
			return;
		}
		const s = ed.shapes[ed.selected];
		if (!s) return;
		if (["x", "y", "w", "h", "rotation"].includes(key)) {
			const value = Number(el.value);
			if (!Number.isFinite(value)) return;
			// Held to the drawing when the box is left, not on every key: a number half typed is
			// not yet the number meant.
			ed.shapes[ed.selected] = type === "change" ? this.wm.hold_shape(Object.assign({}, s, { [key]: value }), ed.map) : Object.assign({}, s, { [key]: value });
		} else ed.shapes[ed.selected] = Object.assign({}, s, { [key]: el.value });
		this.redraw_layer();
		if (key === "shape_type" && type === "change") this.render_panel();
		else if (type === "change") this.sync_box_inputs();
	}

	editor_key(e) {
		const ed = this.edit;
		const s = ed && ed.shapes[ed.selected];
		if (!s) return;
		const step = this.wm.grid_of(ed.map) * (e.shiftKey ? 10 : 1);
		const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
		if (moves[e.key]) {
			e.preventDefault();
			ed.shapes[ed.selected] = Object.assign({}, s, this.wm.move_shape(s, moves[e.key][0], moves[e.key][1], ed.map, 0));
			this.redraw_layer();
			this.sync_box_inputs();
			this.focus_picked();
		} else if (e.key === "Delete") {
			e.preventDefault();
			this.remove_box();
		}
	}

	remove_box() {
		const ed = this.edit;
		if (!ed.shapes[ed.selected]) return;
		ed.shapes.splice(ed.selected, 1);
		ed.selected = -1;
		this.redraw_layer();
		this.render_panel();
	}

	add_box(kind, unit) {
		const ed = this.edit;
		const shape = this.wm.new_shape(kind, unit, ed.map, this.view(ed.map), ed.added);
		ed.added += 1;
		if (unit) shape.is_group = !!unit.is_group;
		ed.shapes.push(shape);
		this.select(ed.shapes.length - 1);
	}

	editor_act(act, $el) {
		const ed = this.edit;
		if (!ed || !this.target || this.target.screen !== "edit") return;
		const s = ed.shapes[ed.selected];
		if (act === "edit-done") this.navigate({ screen: "floor", map: ed.name });
		else if (act === "add") this.add_box(String($el.data("kind")), null);
		else if (act === "add-unit") {
			const unit = ed.units.find((u) => u.warehouse === String($el.data("warehouse")));
			if (unit) this.add_box(unit.suggested_type, unit);
		} else if (act === "remove-box") this.remove_box();
		else if (act === "turn" && s) {
			ed.shapes[ed.selected] = Object.assign({}, s, this.wm.turn_shape(s, ed.map));
			this.redraw_layer();
			this.render_panel();
		} else if (act === "untie" && s) {
			ed.shapes[ed.selected] = Object.assign({}, s, { warehouse: "", warehouse_name: "", missing: false, missing_reason: "", levels: undefined, bin_order: [] });
			ed.pick = "";
			this.redraw_layer();
			this.render_panel();
			this.$root.find(".wm-pick-box").trigger("focus");
		} else if (act === "tie" && s) {
			const row = ed.warehouses.find((w) => w.name === String($el.data("warehouse")));
			if (!row) return;
			ed.shapes[ed.selected] = Object.assign({}, s, {
				warehouse: row.name,
				warehouse_name: row.warehouse_name,
				is_group: !!row.is_group,
				missing: false,
				missing_reason: "",
				levels: undefined,
				bin_order: [],
			});
			ed.pick = "";
			this.redraw_layer();
			this.render_panel();
		} else if (act === "order-row" && s) {
			// A bin's row has a new place once the rows move, so the picked bin is let go.
			ed.order_pick = "";
			this.order_changed(ed.selected, this.wm.move_level(s, Number($el.data("row")), Number($el.data("dir"))));
		} else if (act === "order-pick" && s) {
			const bin = String($el.data("bin"));
			ed.order_pick = ed.order_pick === bin ? "" : bin;
			ed.order_row = Number($el.data("row"));
			this.render_panel();
		} else if (act === "order-bin" && s && ed.order_pick) {
			this.order_changed(ed.selected, this.wm.move_bin(s, ed.order_row, ed.order_pick, Number($el.data("dir"))));
		} else if (act === "order-auto" && s) {
			ed.shapes[ed.selected] = Object.assign({}, s, { bin_order: [], levels: undefined });
			ed.order_pick = "";
			this.render_panel();
			this.paint_edit_status();
		} else if (act === "save") this.save();
		else if (act === "upload") this.upload();
		else if (act === "remove-picture") {
			ed.map.background = "";
			this.render_editor();
		} else if (act === "delete-map") this.delete_map();
	}

	save() {
		const ed = this.edit;
		if (ed.saving) return;
		const problem = this.wm.check_shapes(ed.shapes);
		if (problem) {
			ed.error = __(problem.message);
			this.select(problem.index);
			this.paint_edit_status();
			return;
		}
		const payload = this.wm.save_payload(ed);
		ed.saving = true;
		ed.error = "";
		this.paint_edit_status();
		this.call("save_map", { name: ed.name, modified: ed.map.modified, payload: JSON.stringify(payload) }).then(
			(saved) => {
				ed.saving = false;
				ed.map.modified = saved.modified;
				ed.saved = JSON.stringify(payload);
				const renamed = saved.name !== ed.name;
				delete this.maps[ed.name];
				delete this.maps[saved.name];
				if (renamed) {
					this.views[saved.name] = this.views[ed.name];
					ed.name = saved.name;
					ed.map.name = saved.name;
				}
				frappe.show_alert({ message: __("Map saved"), indicator: "green" }, 4);
				this.load_boot(true).then(() => {
					if (renamed && this.target.screen === "edit") this.navigate({ screen: "edit", map: saved.name }, {}, true);
					else this.paint_edit_status();
				});
			},
			(err) => {
				ed.saving = false;
				ed.error = (err && err.message) || __("The map was not saved. Try again.");
				this.paint_edit_status();
			}
		);
	}

	// The floor plan picture: a PRIVATE file attached to this map's record, so it is served to exactly
	// the people who can read the map.
	//
	// Sent by this page itself (a file chooser and one POST to Frappe's own upload_file), NOT through
	// frappe.ui.FileUploader, for two reasons read in its source at v16.56.0:
	//
	//   * The dialog offers a Private switch and a web-link tab, both on by default. Switched off, the
	//     upload becomes a public file anyone can fetch without signing in. Here is_private is always
	//     1 and there is nothing to switch. (The server refuses a public File on a map as well:
	//     inventory_enhancements/warehouse_map_files.refuse_public_file.)
	//   * It ticks "Optimize" for every image over 200 KB, and the server then shrinks the picture to
	//     at most 1024 by 768 (frappe/utils/image.py optimize_image). A floor plan at that size is a
	//     blur once the map is zoomed in. No `optimize` is sent here, so the picture is kept as it is.
	upload() {
		const ed = this.edit;
		if (ed.uploading) return;
		const input = document.createElement("input");
		input.type = "file";
		input.accept = "image/png,image/jpeg,image/webp";
		input.addEventListener("change", () => {
			const file = input.files && input.files[0];
			if (file) this.send_picture(file);
		});
		input.click();
	}

	send_picture(file) {
		const ed = this.edit;
		const fail = (message) => {
			ed.uploading = false;
			ed.error = message || __("The picture was not uploaded. Try again.");
			this.paint_edit_status();
		};
		if (!/^image\/(png|jpeg|webp)$/.test(file.type || "")) {
			fail(__("Use a PNG, JPG or WebP picture of the floor plan."));
			return;
		}
		const form = new FormData();
		form.append("file", file, file.name);
		form.append("is_private", "1");
		form.append("doctype", "Warehouse Floor Map");
		form.append("docname", ed.name);
		form.append("fieldname", "background");
		form.append("folder", "Home/Attachments");
		ed.uploading = true;
		ed.error = "";
		this.paint_edit_status();
		window
			.fetch("/api/method/upload_file", {
				method: "POST",
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token || "" },
				credentials: "same-origin",
				body: form,
			})
			.then((response) => response.json().then((body) => ({ ok: response.ok, body }), () => ({ ok: false, body: null })))
			.then(({ ok, body }) => {
				const url = ok && body && body.message && body.message.file_url;
				if (!url) {
					fail(this.upload_error(body));
					return;
				}
				if (!String(url).startsWith("/private/files/")) {
					fail(__("That picture did not arrive as a private file, so it was not used. Tell a System Manager."));
					return;
				}
				const probe = new Image();
				probe.onload = () => {
					const to = { width: probe.naturalWidth || ed.map.width, height: probe.naturalHeight || ed.map.height };
					ed.shapes = this.wm.rescale_shapes(ed.shapes, ed.map, to).map((s, i) => Object.assign({}, ed.shapes[i], s));
					ed.map.width = to.width;
					ed.map.height = to.height;
					ed.map.background = url;
					ed.uploading = false;
					delete this.views[ed.name];
					this.render_editor();
				};
				probe.onerror = () => fail(__("That file could not be shown as a picture. Use a PNG or JPG."));
				probe.src = url;
			})
			.catch(() => fail());
	}

	// The server's own sentence for a refused upload (too large, not permitted), or nothing.
	upload_error(body) {
		try {
			const list = JSON.parse((body && body._server_messages) || "[]");
			const first = list.length ? JSON.parse(list[0]) : null;
			const words = first && first.message ? String(first.message).replace(/<[^>]+>/g, "").trim() : "";
			return words || "";
		} catch (e) {
			return "";
		}
	}

	delete_map() {
		const ed = this.edit;
		frappe.confirm(
			__("Delete the map {0} and its floor plan picture? The warehouses and their stock are not touched.", [this.esc(ed.map.title)]),
			() => {
				this.call("delete_map", { name: ed.name }).then(
					() => {
						delete this.maps[ed.name];
						this.edit = null;
						this.load_boot(true).then(() => this.navigate({ screen: "floor", map: "" }, {}, true));
					},
					(err) => {
						ed.error = (err && err.message) || __("The map was not deleted.");
						this.paint_edit_status();
					}
				);
			}
		);
	}
}
