// Bin Manager (v1.693.0, ER-2026-1320545, TASK-2026-02827): one bin's items on a PC, without
// scanning. The stock lead picks a bin and sees every item there with its quantity, and from there
// changes a quantity (a Stock Reconciliation: a count correction, never a Material Issue), moves an
// item to another bin, unassigns it (with stock still there: Move or Send to boneyard first), adds
// an existing item, or creates a new one already on the bin. Every save writes a Stock Scan Log row
// and can be undone from the page, as on /stock-scan, whose endpoints this calls:
//
//   bin_assignments.set_count / move_to / send_to_boneyard / unassign / assign   (PR 1, v1.689.0)
//   bin_assignments.create_and_assign, bin_assignments.manager_boot             (this page)
//   api.stock_scan.get_location / get_recent / search_items / check_new_item / undo
//
// The decisions (routes, which buttons show, the make-home box, each request) are pure:
// public/js/bin_manager/logic.js, and the screen state and server calls are the controller,
// public/js/bin_manager/controller.js, both in bin_manager.bundle.js (window.sf_bin_manager) and run
// under node by scripts/test_bin_manager.mjs. This file draws and wires.
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/bin-manager               the picker: every bin, filterable, with its counts
//   /desk/bin-manager/<warehouse>   one bin: its items, the actions, and this bin's recent saves
//   /desk/bin-manager?warehouse=X   a deep link: opens X in place of the picker entry
//
// Picking a bin is one frappe.set_route; Back is the browser's and lands on the picker; Forward lands
// on the bin. Dialogs (frappe.ui.Dialog) push nothing, and the router closes an open one on any route
// change (router.set_history -> frappe.ui.hide_open_dialog), so Back with a dialog open closes it and
// returns to the picker. frappe.route_options is read on every show (the address's query lands there
// too since v16.50), never location.search.
//
// WHO SEES WHAT comes from the server (manager_boot), never a role list here: assign, unassign, keep,
// boneyard, new item and "make it the item's bin" need the bin-assignment gate (can_manage); a count
// needs Stock Reconciliation create and submit (can_count); a move needs Stock Entry create and
// submit (can_move); New item needs Item create too. The boneyard is read by the server from
// Inventory Scanner Settings: the page never reads a Settings Single. A refusal is the server's own
// sentence, shown inline in the dialog that asked.
//
// Every request goes through sf_kit.call(..., { quiet: true }), so Frappe's own dialogs never cover
// the page. The CSS is this folder's bin_manager.css, every selector under .bm- (a Desk Page's
// stylesheet is global once loaded).

frappe.pages["bin-manager"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Bin Manager"),
		single_column: true,
	});
	wrapper.bin_manager = new BinManagerPage(page, wrapper);
};

frappe.pages["bin-manager"].on_page_show = function (wrapper) {
	if (wrapper.bin_manager) wrapper.bin_manager.route_changed();
};

const BM_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "bin_manager.bundle.js"];
// The reorder boxes (v1.720.0): asked for apart from the page's own files, and optional. A page whose
// copy of them did not load shows no Reorder column and works as before.
const BM_REORDER_ASSETS = ["reorder_levels.bundle.js", "reorder_levels.bundle.css"];
const BM_PAGE = "bin-manager";
const BM_SEARCH_DELAY_MS = 250;

class BinManagerPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $('<div class="bm-app"></div>').appendTo(page.main);
		this.sf = null;
		this.bm = null;
		this.ctl = null;
		this.drawn = "";
		this._route_timer = null;
		this.reorder = null; // the reorder editor (public/js/reorder_levels), once its bundle is here
		this.reorder_for = null; // the location object the editor last read the rules for
		this.redraw_after_blur = false; // a table redraw is waiting for a reorder box to be left
		// v16's frappe.require calls back even when a file failed to load (assets.js "resolve even on
		// error"), so both globals are checked; a page without them says so instead of waiting.
		this.ready = new Promise((resolve) => frappe.require(BM_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_kit.state ? window.sf_kit : null;
			this.bm = window.sf_bin_manager && window.sf_bin_manager.Controller ? window.sf_bin_manager : null;
			if (this.sf && this.bm) this.ctl = new this.bm.Controller(this.env());
		});
		// The reorder boxes are not part of `ready`: the bins draw as soon as the page's own files are here, and
		// the Reorder column joins them when its bundle arrives (a slow or failed bundle holds nothing up).
		this.ready.then(() => this.load_reorder_bundle());
		page.set_secondary_action(__("Refresh"), () => this.ctl && this.ctl.refresh(), "refresh");
		this.bind();
	}

	env() {
		return {
			call: (method, args, opts) => this.sf.call(method, args, opts),
			get_route: () => frappe.get_route(),
			set_route: (route, replace) => {
				if (replace) frappe.route_flags.replace_route = true;
				frappe.set_route(route);
				// Read by push_state, which has already run; left set, the next screen would replace too.
				delete frappe.route_flags.replace_route;
			},
			route_options: () => frappe.route_options,
			clear_route_options: () => {
				frappe.route_options = null;
			},
			history_state: () => window.history.state,
			tag_state: (tag) => {
				try {
					window.history.replaceState(Object.assign({}, window.history.state, tag), "");
				} catch (e) {
					// Untagged: "All bins" pushes the picker instead of stepping back, and Back still works.
				}
			},
			history_back: () => window.history.back(),
			render: () => this.render(),
			now: () => Date.now(),
			random: () => Math.random(),
		};
	}

	// The reorder boxes (v1.720.0): the shared editor, once its bundle has arrived, wired to this page's
	// root. Everything about them is optional: with no bundle there is no Reorder column.
	load_reorder_bundle() {
		new Promise((resolve) => {
			try {
				frappe.require(BM_REORDER_ASSETS, resolve);
			} catch (e) {
				resolve();
			}
		}).then(() => {
			this.start_reorder();
			// The bin or picker already on screen is drawn again, now with the column.
			if (this.reorder) this.render();
		});
	}

	start_reorder() {
		const ro = window.sf_reorder && window.sf_reorder.version >= 1 ? window.sf_reorder : null;
		if (!ro || !this.sf) return;
		try {
			this.reorder = new ro.ReorderEditor({
				call: (method, args, opts) => this.sf.call(method, args, opts),
				read: (code) => ro.read_boxes(this.$root[0], code),
				paint: (code, force) => this.paint_reorder(code, force),
				paint_all: () => this.paint_reorder(null),
				later: (fn, ms) => setTimeout(fn, ms),
				tr: (text) => __(text),
				confirm: (text, yes, no) => frappe.confirm(text, yes, no),
			});
			ro.bind(this.$root[0], this.reorder);
		} catch (e) {
			this.reorder = null;
		}
	}

	paint_reorder(code, force) {
		const ro = window.sf_reorder;
		if (!ro || !this.reorder) return;
		ro.paint(this.$root[0], this.reorder, (v) => this.esc(v), (t) => __(t), code, document.activeElement, !!force, this.reorder_labels());
	}

	// The items on screen, by code, for the boxes' accessible names.
	reorder_labels() {
		const loc = this.ctl && this.ctl.state.location;
		const out = {};
		((loc && loc.items) || []).forEach((r) => {
			out[r.item_code] = r.item_name;
		});
		return out;
	}

	// The rules are read once per list of items on screen: a new location object (a bin opened, a save
	// that reloaded it) is a new list, and the same one drawn again is not asked about twice.
	sync_reorder(loc) {
		if (!this.reorder || !loc || this.reorder_for === loc) return;
		this.reorder_for = loc;
		this.reorder.load(loc.warehouse, (loc.items || []).map((r) => r.item_code));
	}

	showing() {
		return (frappe.get_route() || [])[0] === BM_PAGE;
	}

	// One show per task: a replace followed by a push fires on_page_show twice.
	route_changed() {
		if (this._route_timer) return;
		this._route_timer = setTimeout(() => {
			this._route_timer = null;
			this.ready.then(() => {
				if (!this.showing()) return;
				if (!this.ctl) {
					this.broken();
					return;
				}
				this.ctl.show();
			});
		}, 0);
	}

	broken() {
		this.drawn = "";
		this.$root.html(
			`<div class="bm-broken">${frappe.utils.escape_html(
				__("The Bin Manager could not load its files. Reload the page to try again.")
			)}</div>`
		);
	}

	esc(value) {
		return this.sf.esc(value);
	}

	// For a name that goes into frappe's own HTML: a dialog title (Dialog.set_title is .html()), a
	// field's label or description. Item, warehouse and bin names are data, never markup.
	safe(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	// ------------------------------------------------------------------ clicks

	bind() {
		this.$root.on("click", "[data-act]", (e) => {
			e.preventDefault();
			if (!this.ctl) return;
			const el = e.currentTarget;
			const act = el.getAttribute("data-act");
			const code = el.getAttribute("data-item");
			const row = code ? this.row(code) : null;
			if (act === "bin") this.ctl.open_bin(el.getAttribute("data-bin"));
			else if (act === "leave") this.ctl.leave_bin();
			else if (act === "undo") this.undo(el.getAttribute("data-log"), el);
			else if (act === "retry") this.ctl.refresh();
			else if (act === "add") this.add_dialog();
			else if (act === "create") this.create_dialog();
			else if (row && act === "count") this.count_dialog(row);
			else if (row && act === "move") this.move_dialog(row);
			else if (row && act === "boneyard") this.boneyard_dialog(row);
			else if (row && act === "unassign") this.unassign_dialog(row);
			else if (row && act === "keep") this.keep(row, el);
		});
		// A table redraw that waited for a reorder box (draw_items): run it once focus has left the boxes.
		this.$root.on("focusout", ".ro-input", () => {
			setTimeout(() => {
				const active = document.activeElement;
				if (!this.redraw_after_blur || (active && active.classList && active.classList.contains("ro-input"))) return;
				this.redraw_after_blur = false;
				this.render();
			}, 0);
		});
		this.$root.on("input", ".bm-filter", (e) => {
			if (!this.ctl) return;
			this.ctl.state.filter = e.currentTarget.value;
			this.draw_bins();
		});
		this.$root.on("keydown", ".bm-filter", (e) => {
			if (e.key !== "Enter" || !this.ctl) return;
			const bins = this.bm.filter_bins(this.boot_bins(), this.ctl.state.filter);
			if (bins.length === 1) this.ctl.open_bin(bins[0].warehouse);
		});
	}

	row(code) {
		const loc = this.ctl.state.location;
		return ((loc && loc.items) || []).find((r) => r.item_code === code) || null;
	}

	boot_bins() {
		const boot = this.ctl.state.boot;
		return (boot && boot.bins) || [];
	}

	perms() {
		return this.ctl.perms();
	}

	here() {
		const t = this.ctl.state.target;
		return t && t.screen === "bin" ? t.warehouse : null;
	}

	// ------------------------------------------------------------------ drawing

	render() {
		if (!this.ctl || !this.showing()) return;
		const t = this.ctl.state.target;
		if (!t) return;
		if (t.screen === "picker") this.render_picker();
		else this.render_bin();
	}

	failed(el, err, retry, title) {
		err = err || {};
		if (err.session_expired) {
			return this.sf.state.render(el, "error", {
				title: __("You have been signed out."),
				body: __("Reload the page to sign in again."),
				action_label: __("Reload"),
				on_action: () => window.location.reload(),
			});
		}
		if (err.status === 403) {
			return this.sf.state.render(el, "no-access", { title: __("You do not have access to the Bin Manager."), body: err.message });
		}
		return this.sf.state.render(el, "error", { title, body: err.message, on_action: retry });
	}

	render_picker() {
		this.page.set_title(__("Bin Manager"));
		if (this.drawn !== "picker") {
			this.drawn = "picker";
			this.$root.html(`
				<div class="bm-picker">
					<div class="bm-toolbar">
						<input type="search" class="form-control bm-filter" autocomplete="off"
							placeholder="${this.esc(__("Find a bin: type part of its name"))}"
							aria-label="${this.esc(__("Find a bin"))}">
					</div>
					<div class="bm-list"></div>
				</div>`);
			this.$root.find(".bm-filter").val(this.ctl.state.filter || "");
			setTimeout(() => this.$root.find(".bm-filter").trigger("focus"), 0);
		}
		this.draw_bins();
	}

	draw_bins() {
		const list = this.$root.find(".bm-list")[0];
		if (!list) return;
		const st = this.ctl.state;
		if (!st.boot) {
			if (st.boot_error) this.failed(list, st.boot_error, () => this.ctl.load_boot(true), __("Could not load the bins."));
			else this.sf.state.render(list, "loading", { title: __("Loading the bins…") });
			return;
		}
		const bins = this.bm.filter_bins(st.boot.bins || [], st.filter);
		if (!bins.length) {
			this.sf.state.render(list, "empty", {
				title: st.filter ? __("No bin matches {0}.", [st.filter]) : __("There are no bins yet."),
			});
			return;
		}
		const parts = this.bm.group_bins(bins).map((group) => {
			const items = group.bins
				.map((b) => {
					const counts = [];
					counts.push(__("{0} on hand", [b.on_hand_items]));
					counts.push(__("{0} assigned", [b.assigned_items]));
					const chip = b.is_boneyard ? this.sf.chip(__("Boneyard"), "warn") : "";
					return `<li><button type="button" class="bm-bin" data-act="bin" data-bin="${this.esc(b.warehouse)}">
						<span class="bm-bin-name">${this.esc(b.warehouse_name)}</span> ${chip}
						<span class="bm-bin-counts">${this.esc(counts.join(" · "))}</span>
					</button></li>`;
				})
				.join("");
			const title = group.trail ? `<h3 class="bm-group-title">${this.esc(group.trail)}</h3>` : "";
			return `<section class="bm-group">${title}<ul class="bm-bins">${items}</ul></section>`;
		});
		list.innerHTML = parts.join("");
	}

	render_bin() {
		const st = this.ctl.state;
		const warehouse = st.target.warehouse;
		const loc = st.location && st.location.warehouse === warehouse ? st.location : null;
		this.page.set_title(loc ? loc.warehouse_name : __("Bin Manager"));
		if (this.drawn !== `bin:${warehouse}`) {
			this.drawn = `bin:${warehouse}`;
			this.$root.html(`
				<div class="bm-binview">
					<div class="bm-head">
						<button type="button" class="btn btn-default btn-sm bm-back" data-act="leave">← ${this.esc(__("All bins"))}</button>
						<div class="bm-title">
							<h2 class="bm-name"></h2>
							<div class="bm-trail"></div>
						</div>
						<div class="bm-bin-actions"></div>
					</div>
					<div class="bm-notice"></div>
					<div class="bm-items"></div>
					<section class="bm-recent" aria-label="${this.esc(__("Recent saves on this bin"))}">
						<h3 class="bm-section-title">${this.esc(__("Your recent saves on this bin"))}</h3>
						<div class="bm-recent-list"></div>
					</section>
				</div>`);
		}
		const perms = this.perms();
		this.$root.find(".bm-name").text(loc ? loc.warehouse_name : warehouse);
		this.$root.find(".bm-trail").text(loc ? loc.trail || "" : "");
		const actions = st.boot ? this.bm.bin_actions(perms, warehouse) : [];
		this.$root.find(".bm-bin-actions").html(
			actions
				.map(
					(a) =>
						`<button type="button" class="btn btn-sm ${a === "add" ? "btn-primary" : "btn-default"}" data-act="${a}">${this.esc(
							__(this.bm.ACTION_LABELS[a])
						)}</button>`
				)
				.join(" ")
		);
		this.draw_notice();
		this.draw_items(loc);
		this.sync_reorder(loc);
		this.draw_recent();
	}

	draw_notice() {
		const box = this.$root.find(".bm-notice");
		const notice = this.ctl.state.notice;
		if (!notice || !notice.message) {
			box.empty();
			return;
		}
		const log = notice.log;
		const undo =
			log && log.status === "Posted" && log.can_undo
				? ` <button type="button" class="btn btn-xs btn-default" data-act="undo" data-log="${this.esc(log.name)}">${this.esc(__("Undo"))}</button>`
				: "";
		const tone = notice.error ? "bm-notice-box--error" : "";
		box.html(`<div class="bm-notice-box ${tone}" role="${notice.error ? "alert" : "status"}">${this.esc(notice.message)}${undo}</div>`);
	}

	draw_items(loc) {
		const el = this.$root.find(".bm-items")[0];
		// A redraw replaces the table, and with it a reorder box being typed in: it waits until the box is
		// left (the focusout handler in bind() draws it then), so nothing on screen goes stale.
		const active = document.activeElement;
		if (this.reorder && el && active && active.classList && active.classList.contains("ro-input") && el.contains(active)) {
			this.redraw_after_blur = true;
			return;
		}
		this.redraw_after_blur = false;
		const st = this.ctl.state;
		if (!loc) {
			if (st.location_error) this.failed(el, st.location_error, () => this.ctl.load_location(), __("Could not load this bin."));
			else this.sf.state.render(el, "loading", { title: __("Loading the bin…") });
			return;
		}
		const perms = this.perms();
		const warehouse = loc.warehouse;
		const notes = [];
		if (perms.boneyard && perms.boneyard === warehouse) {
			notes.push(__("This is the boneyard: stock here lists because it is on hand, and nothing is assigned to it."));
		}
		if (loc.truncated) notes.push(__("This bin lists more items than the page shows. Search with Add existing item to find one."));
		const note = notes.map((n) => `<p class="bm-note">${this.esc(n)}</p>`).join("");
		if (!loc.items || !loc.items.length) {
			el.innerHTML = note + this.sf.state.html("empty", { title: __("Nothing is on this bin yet.") });
			return;
		}
		const rows = loc.items
			.map((r) => {
				const chips = this.bm
					.why_listed(r)
					.map((w) => this.sf.chip(__(w.label), w.tone))
					.join(" ");
				const buttons = st.boot
					? this.bm
							.row_actions(r, perms, warehouse)
							.map(
								(a) =>
									`<button type="button" class="btn btn-xs btn-default" data-act="${a}" data-item="${this.esc(r.item_code)}">${this.esc(
										__(this.bm.ACTION_LABELS[a])
									)}</button>`
							)
							.join(" ")
					: "";
				return `<tr>
					<td class="bm-item"><div class="bm-item-name">${this.esc(r.item_name)}</div><div class="bm-item-code">${this.esc(r.item_code)}</div></td>
					<td class="bm-qty">${this.esc(this.bm.amount(r.on_hand, r.stock_uom))}</td>
					${this.reorder ? `<td class="bm-reorder">${window.sf_reorder.cell_html((v) => this.esc(v), (t) => __(t), this.reorder.view(r.item_code), r.item_code, r.item_name)}</td>` : ""}
					<td class="bm-why">${chips}</td>
					<td class="bm-row-actions">${buttons}</td>
				</tr>`;
			})
			.join("");
		el.innerHTML = `${note}<table class="bm-table">
			<thead><tr>
				<th scope="col">${this.esc(__("Item"))}</th>
				<th scope="col" class="bm-qty">${this.esc(__("On hand"))}</th>
				${this.reorder ? `<th scope="col">${this.esc(__("Reorder"))}</th>` : ""}
				<th scope="col">${this.esc(__("Listed because"))}</th>
				<th scope="col"><span class="bm-sr">${this.esc(__("Actions"))}</span></th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table>`;
	}

	draw_recent() {
		const el = this.$root.find(".bm-recent-list")[0];
		if (!el) return;
		const rows = this.bm.bin_recent(this.ctl.state.recent, this.here());
		if (!rows.length) {
			el.innerHTML = `<p class="bm-muted">${this.esc(__("Nothing yet. Every change you make here is listed, and can be undone for a while."))}</p>`;
			return;
		}
		el.innerHTML = `<ul class="bm-recent-rows">${rows
			.map((r) => {
				const when = r.posted_at ? frappe.datetime.comment_when(r.posted_at) : "";
				const status =
					r.status === "Undone"
						? this.sf.chip(__("Undone"), "neutral")
						: r.can_undo
						? `<button type="button" class="btn btn-xs btn-default" data-act="undo" data-log="${this.esc(r.name)}">${this.esc(__("Undo"))}</button>`
						: `<span class="bm-muted" title="${this.esc(r.undo_refusal || "")}">${this.esc(__("Can't undo"))}</span>`;
				return `<li class="bm-recent-row"><span class="bm-recent-what">${this.esc(this.bm.describe_log(r))}</span>
					<span class="bm-recent-when">${when}</span> ${status}</li>`;
			})
			.join("")}</ul>`;
	}

	// ------------------------------------------------------------------ saves without a dialog

	undo(log, el) {
		if (!log) return;
		if (el) el.disabled = true;
		this.ctl.undo(log).catch((err) => {
			this.ctl.state.notice = { message: err.message || __("Could not undo."), error: true };
			this.render();
		});
	}

	// "Keep on this bin": one click, undoable from the notice. Its reference is the controller's
	// (quick): the same one again after a dropped reply, so a second click cannot save twice.
	keep(row, el) {
		if (el) el.disabled = true;
		this.ctl.quick("keep", row, {}).catch((err) => {
			this.ctl.state.notice = { message: err.message || __("Could not save."), error: true };
			this.render();
		});
	}

	// ------------------------------------------------------------------ dialogs

	// A dialog whose primary action is one save. Its reference is minted when it opens and sent again
	// on a retry the server may already have saved (logic.keep_ref); a refusal is shown inside it.
	save_dialog({ kind, row, title, fields, primary_label, values, on_build }) {
		let ref = this.ctl.new_ref();
		let busy = false;
		const d = new frappe.ui.Dialog({
			title,
			fields: [{ fieldtype: "HTML", fieldname: "bm_msg" }].concat(fields || []),
			primary_action_label: primary_label,
			primary_action: () => {
				if (busy) return;
				busy = true;
				this.dialog_message(d, "");
				const button = d.get_primary_btn();
				button.prop("disabled", true);
				this.ctl.submit(kind, row, values ? values(d) : d.get_values(true) || {}, ref).then(
					() => {
						busy = false;
						button.prop("disabled", false);
						d.hide();
					},
					(err) => {
						busy = false;
						button.prop("disabled", false);
						if (!err.keep_ref) ref = this.ctl.new_ref();
						this.dialog_message(d, err.message || __("The server refused this request."));
					}
				);
			},
		});
		if (on_build) on_build(d);
		d.show();
		return d;
	}

	dialog_message(d, text) {
		const field = d.fields_dict.bm_msg;
		if (!field) return;
		field.$wrapper.html(text ? `<div class="bm-dialog-msg" role="alert">${this.esc(text)}</div>` : "");
	}

	count_dialog(row) {
		this.save_dialog({
			kind: "count",
			row,
			title: __("Change the quantity of {0}", [this.safe(row.item_name)]),
			primary_label: __("Save count"),
			fields: [
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Counted quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: __(
						"The system shows {0}. Saving posts a Stock Reconciliation: a count correction, not parts used, so it charges no job and can go up as well as down.",
						[this.safe(this.bm.amount(row.on_hand, row.stock_uom))]
					),
				},
			],
		});
	}

	boneyard_dialog(row) {
		const perms = this.perms();
		this.save_dialog({
			kind: "boneyard",
			row,
			title: __("Send {0} to the boneyard", [this.safe(row.item_name)]),
			primary_label: __("Send to boneyard"),
			fields: [
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: __("Moves the stock to {0}. If that empties this bin, the item comes off it.", [this.safe(perms.boneyard_name)]),
				},
			],
		});
	}

	move_dialog(row) {
		const perms = this.perms();
		let chosen = null;
		let make_home = this.bm.make_home_after_qty(null, row.on_hand, row.on_hand);
		const sync_home = (d) => {
			const offered = this.bm.make_home_offered(perms, chosen && chosen.warehouse);
			d.set_df_property("make_home", "hidden", offered ? 0 : 1);
			if (!offered) return;
			d.set_df_property("make_home", "label", __("Make {0} this item's bin", [this.safe(chosen.warehouse_name)]));
			if (!!d.get_value("make_home") !== make_home.value) d.set_value("make_home", make_home.value ? 1 : 0);
		};
		const draw_dest = (d) => {
			const wrap = d.fields_dict.dest_list.$wrapper;
			if (chosen) {
				wrap.html(
					`<div class="bm-chosen">${this.esc(__("Moving to"))} <b>${this.esc(chosen.warehouse_name)}</b>
					<button type="button" class="btn btn-xs btn-default" data-bm-change="1">${this.esc(__("Change"))}</button></div>`
				);
				return;
			}
			const list = this.ctl.destinations(d.get_value("dest_search") || "");
			wrap.html(
				list.length
					? `<ul class="bm-pick-list">${list
							.map(
								(b) =>
									`<li><button type="button" class="bm-pick" data-bm-dest="${this.esc(b.warehouse)}">${this.esc(b.warehouse_name)}
									<span class="bm-muted">${this.esc(b.trail || "")}</span></button></li>`
							)
							.join("")}</ul>`
					: `<p class="bm-muted">${this.esc(__("No other bin matches."))}</p>`
			);
		};
		this.save_dialog({
			kind: "move",
			row,
			title: __("Move {0}", [this.safe(row.item_name)]),
			primary_label: __("Move"),
			fields: [
				{ fieldtype: "Data", fieldname: "dest_search", label: __("Move to bin"), placeholder: __("Type part of a bin's name") },
				{ fieldtype: "HTML", fieldname: "dest_list" },
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: __("{0} here.", [this.safe(this.bm.amount(row.on_hand, row.stock_uom))]),
				},
				{
					fieldtype: "Check",
					fieldname: "make_home",
					label: __("Make the destination this item's bin"),
					hidden: 1,
					description: __("Ticked when everything here moves. Stock left behind keeps this bin's claim on the item."),
				},
			],
			values: (d) => ({
				to_warehouse: chosen && chosen.warehouse,
				qty: d.get_value("qty"),
				make_home: this.bm.make_home_offered(perms, chosen && chosen.warehouse) && !!d.get_value("make_home"),
			}),
			on_build: (d) => {
				d.fields_dict.dest_search.$input.on("input", () => draw_dest(d));
				d.fields_dict.dest_list.$wrapper.on("click", "[data-bm-dest]", (e) => {
					const name = e.currentTarget.getAttribute("data-bm-dest");
					// The full list, never the capped one on screen: a bin past the first 30 is still a bin.
					chosen = this.ctl.bin(name);
					draw_dest(d);
					sync_home(d);
				});
				d.fields_dict.dest_list.$wrapper.on("click", "[data-bm-change]", () => {
					chosen = null;
					draw_dest(d);
					sync_home(d);
					d.fields_dict.dest_search.$input.trigger("focus");
				});
				// The box is decided again on every change of quantity, until the person touches it.
				d.fields_dict.qty.$input.on("input change", () => {
					make_home = this.bm.make_home_after_qty(make_home, d.fields_dict.qty.$input.val(), row.on_hand);
					sync_home(d);
				});
				d.fields_dict.make_home.$input.on("change", () => {
					make_home = this.bm.make_home_touched(d.fields_dict.make_home.$input.prop("checked"));
				});
				draw_dest(d);
				sync_home(d);
			},
		});
	}

	unassign_dialog(row) {
		const perms = this.perms();
		const where = (this.ctl.state.location && this.ctl.state.location.warehouse_name) || this.here();
		const plan = this.bm.unassign_plan(row, perms, this.here());
		if (plan.direct) {
			this.save_dialog({
				kind: "unassign",
				row,
				title: __("Unassign {0}", [this.safe(row.item_name)]),
				primary_label: __("Unassign"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "bm_text",
						options: `<p>${this.esc(
							__("Take {0} off {1}? Nothing is on hand here, so no stock moves. You can undo it.", [row.item_name, where])
						)}</p>`,
					},
				],
			});
			return;
		}
		// Stock is still here, and unassign refuses that: first empty the bin, by Move or the boneyard.
		const d = new frappe.ui.Dialog({
			title: __("Unassign {0}", [this.safe(row.item_name)]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "bm_text",
					options: `<p>${this.esc(
						__("{0} is still on hand here. Move it to another bin or send it to the boneyard first; when this bin is empty the item comes off it.", [
							this.bm.amount(row.on_hand, row.stock_uom),
						])
					)}</p>
					<div class="bm-choices">${plan.options
						.map(
							(a) =>
								`<button type="button" class="btn btn-sm btn-default" data-bm-next="${a}">${this.esc(__(this.bm.ACTION_LABELS[a]))}…</button>`
						)
						.join(" ")}</div>
					${plan.options.length ? "" : `<p class="bm-muted">${this.esc(__("You cannot move stock. Ask a Stock Manager."))}</p>`}`,
				},
			],
		});
		d.fields_dict.bm_text.$wrapper.on("click", "[data-bm-next]", (e) => {
			const next = e.currentTarget.getAttribute("data-bm-next");
			d.hide();
			if (next === "move") this.move_dialog(row);
			else if (next === "boneyard") this.boneyard_dialog(row);
		});
		d.show();
	}

	add_dialog() {
		let timer = null;
		let seq = 0;
		const d = this.save_dialog({
			kind: "add",
			row: null,
			title: __("Add an existing item to this bin"),
			primary_label: __("Add"),
			fields: [
				{ fieldtype: "Data", fieldname: "query", label: __("Find the item"), placeholder: __("Part number, name or barcode") },
				{ fieldtype: "HTML", fieldname: "results" },
				{ fieldtype: "Data", fieldname: "item_code", label: __("Item"), read_only: 1 },
			],
			values: (dialog) => ({ item_code: dialog.get_value("item_code") }),
			on_build: (dialog) => {
				const results = dialog.fields_dict.results.$wrapper;
				const search = () => {
					const query = (dialog.get_value("query") || "").trim();
					const mine = ++seq;
					if (!query) {
						results.empty();
						return;
					}
					results.html(`<p class="bm-muted">${this.esc(__("Searching…"))}</p>`);
					this.ctl.search_items(query).then(
						(rows) => {
							if (mine !== seq) return;
							rows = rows || [];
							if (!rows.length) {
								results.html(`<p class="bm-muted">${this.esc(__("No item matches. Use New item to create it."))}</p>`);
								return;
							}
							results.html(
								`<ul class="bm-pick-list">${rows
									.map((r) => {
										const usable = !!r.is_stock_item;
										const here = r.on_hand_here ? __("{0} here", [this.bm.plain(r.on_hand_here)]) : "";
										return `<li><button type="button" class="bm-pick" data-bm-item="${this.esc(r.item_code)}" ${usable ? "" : "disabled"}>
											${this.esc(r.item_name)} <span class="bm-muted">${this.esc(r.item_code)}${
											usable ? (here ? ` · ${this.esc(here)}` : "") : ` · ${this.esc(__("not kept in stock"))}`
										}</span></button></li>`;
									})
									.join("")}</ul>`
							);
						},
						(err) => {
							if (mine === seq) results.html(`<p class="bm-dialog-msg">${this.esc(err.message || "")}</p>`);
						}
					);
				};
				dialog.fields_dict.query.$input.on("input", () => {
					clearTimeout(timer);
					timer = setTimeout(search, BM_SEARCH_DELAY_MS);
				});
				results.on("click", "[data-bm-item]", (e) => {
					dialog.set_value("item_code", e.currentTarget.getAttribute("data-bm-item"));
				});
			},
		});
		return d;
	}

	create_dialog() {
		const boot = this.ctl.state.boot || {};
		let timer = null;
		let seq = 0;
		const uoms = boot.uoms || [];
		this.save_dialog({
			kind: "create",
			row: null,
			title: __("New item on this bin"),
			primary_label: __("Create and add"),
			fields: [
				{ fieldtype: "Data", fieldname: "item_code", label: __("Part or model number"), reqd: 1, description: __("Exactly as printed on the part or its box.") },
				{ fieldtype: "Data", fieldname: "item_name", label: __("Name"), reqd: 1, description: __("What it is, for example FITTING, ELBOW, PVC, 2 IN.") },
				{ fieldtype: "Select", fieldname: "item_group", label: __("Group"), options: [""].concat(boot.item_groups || []), reqd: 1 },
				{ fieldtype: "Select", fieldname: "stock_uom", label: __("Unit"), options: uoms, default: uoms[0], reqd: 1 },
				{ fieldtype: "HTML", fieldname: "preview" },
			],
			values: (d) => ({
				item_code: d.get_value("item_code"),
				item_name: d.get_value("item_name"),
				item_group: d.get_value("item_group"),
				stock_uom: d.get_value("stock_uom"),
			}),
			on_build: (d) => {
				const preview = d.fields_dict.preview.$wrapper;
				const check = () => {
					const values = {
						item_code: d.get_value("item_code"),
						item_name: d.get_value("item_name"),
						item_group: d.get_value("item_group"),
						stock_uom: d.get_value("stock_uom"),
					};
					if (!values.item_code && !values.item_name) {
						preview.empty();
						d.get_primary_btn().prop("disabled", false);
						return;
					}
					const mine = ++seq;
					this.ctl.check_new_item(values).then(
						(out) => {
							if (mine !== seq) return;
							this.draw_preview(d, out || {});
						},
						() => {
							// The preview is advice: the naming guard still judges the Item when it is created.
							if (mine === seq) preview.empty();
						}
					);
				};
				const later = () => {
					clearTimeout(timer);
					timer = setTimeout(check, BM_SEARCH_DELAY_MS);
				};
				d.fields_dict.item_code.$input.on("input", later);
				d.fields_dict.item_name.$input.on("input", later);
				preview.on("click", "[data-bm-instead]", (e) => {
					const code = e.currentTarget.getAttribute("data-bm-instead");
					d.hide();
					this.ctl.quick("add", null, { item_code: code }).catch((err) => {
						this.ctl.state.notice = { message: err.message || __("Could not save."), error: true };
						this.render();
					});
				});
				preview.on("click", "[data-bm-group]", (e) => d.set_value("item_group", e.currentTarget.getAttribute("data-bm-group")));
			},
		});
	}

	// api.stock_scan.check_new_item's answer: already here, what the naming guard will refuse, what
	// is like it, and advice. An existing or usable similar item can be added instead.
	draw_preview(d, out) {
		const parts = [];
		const instead = (row) =>
			row.usable && row.stocked
				? ` <button type="button" class="btn btn-xs btn-default" data-bm-instead="${this.esc(row.item_code)}">${this.esc(__("Add this one instead"))}</button>`
				: row.refusal
				? ` <span class="bm-muted">${this.esc(row.refusal)}</span>`
				: "";
		if (out.exists) {
			parts.push(`<p class="bm-dialog-msg">${this.esc(__("{0} is already in ERPNext.", [out.exists.item_code]))}${instead(out.exists)}</p>`);
		}
		if (out.blocking && out.blocking.length) {
			const head = out.will_refuse ? __("Creating it will be refused:") : __("From {0} this will be refused:", [out.refuse_from || ""]);
			parts.push(
				`<div class="bm-dialog-msg"><p>${this.esc(head)}</p><ul>${out.blocking.map((f) => `<li>${this.esc(f.message)}</li>`).join("")}</ul></div>`
			);
		}
		if (out.similar && out.similar.length) {
			parts.push(
				`<p class="bm-preview-head">${this.esc(__("Already in ERPNext and alike:"))}</p><ul class="bm-similar">${out.similar
					.map((s) => `<li>${this.esc(s.item_name)} <span class="bm-muted">${this.esc(s.item_code)}</span>${instead(s)}</li>`)
					.join("")}</ul>`
			);
		}
		if (out.suggested_group && !d.get_value("item_group")) {
			parts.push(
				`<p class="bm-muted">${this.esc(__("Items like it are in {0}.", [out.suggested_group]))}
				<button type="button" class="btn btn-xs btn-default" data-bm-group="${this.esc(out.suggested_group)}">${this.esc(__("Use it"))}</button></p>`
			);
		}
		if (out.advice && out.advice.length) {
			parts.push(`<ul class="bm-advice">${out.advice.map((f) => `<li>${this.esc(f.message)}</li>`).join("")}</ul>`);
		}
		d.fields_dict.preview.$wrapper.html(parts.join(""));
		d.get_primary_btn().prop("disabled", !!(out.exists || out.will_refuse));
	}
}
