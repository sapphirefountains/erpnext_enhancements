// Reorder Board (v1.730.0, ER-2026-1323810; TASK-2026-02924, -02925). Parker Bailey, approved by Nik
// 2026-10-22: "a page that has a list of all the items below their reorder point and how much they've
// been set to be reordered. From there I would like to be able to select all or some of the items ...
// and be able to create a material request form." ERPNext's own auto-indent is switched off in the same
// release; this page is how a request gets made now.
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/reorder-board            Needs reorder: at or below the reorder point, nothing open covering it
//   /desk/reorder-board/on-order   On order: each open request, Material Request -> PO -> receipt
//   /desk/reorder-board/snoozed    Snoozed: still below, parked by a buyer, never forgotten
//
// A tab is one frappe.set_route(...). v16 fires on_page_show on every route change into this page, the
// same page included, so a change of tab (or Back, or Forward) arrives there and draws the tab from the
// board already loaded. Dialogs (frappe.ui.Dialog) push nothing; the router closes an open one on Back.
// Creating a request opens its form (a history entry, after the person's click), so Back returns here.
//
// The rules (what is below, how much to buy, where a request is) are all on the server
// (inventory_enhancements/reorder_rules.py); this file draws and wires. Calls go through sf_kit's quiet
// path, so Frappe's dialogs never cover the page and a failure is said once, in the page. The CSS is
// reorder_board.css, every selector under .rob- (a Desk Page's stylesheet is global once loaded).

frappe.pages["reorder-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Reorder Board"),
		single_column: true,
	});
	wrapper.reorder_board = new ReorderBoardPage(page);
};

frappe.pages["reorder-board"].on_page_show = function (wrapper) {
	if (wrapper.reorder_board) wrapper.reorder_board.route_changed();
};

const ROB_PAGE = "reorder-board";
const ROB_API = "erpnext_enhancements.api.reorder_board.";
const ROB_VIEWS = ["needs", "on-order", "snoozed"];

// The page's decisions, without the DOM. Tests load this file under node (tests/test_reorder_board.py).
const ROB = {
	view_from_route(route) {
		const view = (route || [])[1];
		return ROB_VIEWS.includes(view) ? view : "needs";
	},

	route_for(view) {
		return view && view !== "needs" ? [ROB_PAGE, view] : [ROB_PAGE];
	},

	// The server's clean_qty: above 0, at most max, whole for a whole-number unit.
	clean_qty(text, max, whole) {
		const raw = String(text === null || text === undefined ? "" : text).trim().replace(/,/g, "");
		if (!raw) return { qty: null, error: __("Enter a quantity.") };
		const qty = Number(raw);
		if (!Number.isFinite(qty) || qty <= 0) return { qty: null, error: __("The quantity must be more than 0.") };
		if (qty > max) return { qty: null, error: __("The quantity cannot be more than {0}.", [max.toLocaleString()]) };
		if (whole && Math.abs(qty - Math.round(qty)) > 1e-9) return { qty: null, error: __("This unit takes whole numbers only.") };
		return { qty, error: null };
	},

	// ERPNext's create_material_request: stock qty / conversion factor, rounded up for a whole unit.
	order_qty(stock_qty, factor, whole) {
		const qty = Number(stock_qty) / (Number(factor) || 1);
		return whole ? Math.ceil(qty - 1e-9) : qty;
	},

	// The lines to ask for: every selected Needs reorder line with its typed (or suggested) qty.
	picked(lines, selected, typed, max) {
		const out = [];
		const errors = [];
		for (const line of lines || []) {
			if (!selected.has(line.key)) continue;
			const text = typed.has(line.key) ? typed.get(line.key) : line.suggested_qty;
			const { qty, error } = ROB.clean_qty(text, max, !!line.stock_uom_whole);
			if (error) errors.push({ line, error });
			else out.push({ line, qty });
		}
		return { out, errors };
	},

	// Whole days from a datetime string to now: "today", "1 day", "12 days".
	days_below(since, now) {
		if (!since) return "";
		const start = new Date(String(since).replace(" ", "T"));
		if (Number.isNaN(start.getTime())) return "";
		const days = Math.floor((now.getTime() - start.getTime()) / 86400000);
		if (days <= 0) return __("today");
		return days === 1 ? __("1 day") : __("{0} days", [days]);
	},

	stage_tone(stage) {
		return (
			{
				draft_request: "warn",
				requested: "neutral",
				po_draft: "warn",
				partially_ordered: "neutral",
				ordered: "good",
				partially_received: "good",
				received: "good",
			}[stage] || "neutral"
		);
	},

	new_ref() {
		try {
			if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
		} catch (e) {
			// Fall through to a random id.
		}
		return "rob-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
	},

	fmt_qty(value) {
		const n = Number(value) || 0;
		return Math.abs(n - Math.round(n)) < 1e-9 ? String(Math.round(n)) : n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
	},
};

frappe.pages["reorder-board"].logic = ROB;

// sf_kit for the page; a Desk opened before the deploy that added the loader asks for the kit itself, and a
// kit that did not arrive leaves a plain stand-in of the same shape (esc, call, state.render, chip, toast).
function rob_kit() {
	if (typeof window.sf_kit_ready === "function") return window.sf_kit_ready();
	return new Promise((resolve) => {
		const ready = () => resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : rob_plain_kit());
		try {
			frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], ready);
		} catch (e) {
			ready();
		}
	});
}

function rob_plain_kit() {
	const esc = (v) => frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));
	return {
		esc,
		chip: (label) => esc(label),
		call: (method, args, opts) =>
			Promise.resolve(frappe.call({ method, args: args || {}, type: (opts && opts.method) || "POST", silent: true })).then(
				(r) => r && r.message,
				() => Promise.reject({ message: __("The server did not answer. Try again.") })
			),
		state: {
			render(el, kind, opts) {
				$(el).empty().append($('<div class="text-muted"></div>').text((opts && opts.title) || ""));
			},
		},
		toast: (message) => frappe.show_alert({ message, indicator: "green" }),
	};
}

class ReorderBoardPage {
	constructor(page) {
		this.page = page;
		this.sf = null;
		this.data = null;
		this.view = null;
		this.loading = null;
		this.seq = 0;
		this.selected = new Set();
		this.typed = new Map();
		this._timer = null;
		this.$root = $('<div class="rob-app"></div>').appendTo(page.main);
		this.$root.html(`
			<div class="rob-banner"></div>
			<nav class="rob-tabs" role="tablist"></nav>
			<div class="rob-body" aria-live="polite"></div>
		`);
		this.$banner = this.$root.find(".rob-banner");
		this.$tabs = this.$root.find(".rob-tabs");
		this.$body = this.$root.find(".rob-body");
		this.ready = rob_kit().then((sf) => {
			this.sf = sf;
		});
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh");
		this.bind();
	}

	esc(value) {
		return this.sf ? this.sf.esc(value) : frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	call(method, args, opts) {
		return this.sf.call(ROB_API + method, args || {}, Object.assign({ quiet: true }, opts || {}));
	}

	showing() {
		return (frappe.get_route() || [])[0] === ROB_PAGE;
	}

	// One draw per route change, however many show events arrive for it.
	route_changed() {
		if (this._timer) return;
		this._timer = setTimeout(() => {
			this._timer = null;
			if (!this.showing()) return;
			const view = ROB.view_from_route(frappe.get_route());
			this.view = view;
			if (!this.data) this.load();
			else this.render();
		}, 0);
	}

	go(view) {
		if (view === this.view && this.showing()) return;
		frappe.set_route(...ROB.route_for(view));
	}

	load() {
		const seq = ++this.seq;
		this.loading = this.ready.then(() => {
			if (!this.data) this.sf.state.render(this.$body[0], "loading", { title: __("Reading the reorder rules…") });
			return this.call("get_board", {}, { method: "GET" }).then(
				(data) => {
					if (seq !== this.seq) return;
					this.data = data || null;
					// Keep a selection and typed quantities only for lines still needing a reorder.
					const keys = new Set(((data && data.needs) || []).map((l) => l.key));
					for (const key of Array.from(this.selected)) if (!keys.has(key)) this.selected.delete(key);
					for (const key of Array.from(this.typed.keys())) if (!keys.has(key)) this.typed.delete(key);
					this.render();
				},
				(err) => {
					if (seq !== this.seq) return;
					this.data = null;
					this.$tabs.empty();
					this.sf.state.render(this.$body[0], err && err.status === 403 ? "no-access" : "error", {
						title: err && err.status === 403 ? __("The Reorder Board is for Purchasing and Stock Managers.") : __("The board did not load."),
						body: (err && err.message) || "",
						on_action: () => this.load(),
					});
				}
			);
		});
		return this.loading;
	}

	render() {
		const d = this.data;
		if (!d) return;
		this.$banner.html(
			d.auto_indent_on
				? `<div class="rob-note rob-note-warn">${this.esc(
						__("Automatic Material Requests are switched on in Stock Settings, so ERPNext will also raise requests on its own every night.")
					)}</div>`
				: ""
		);
		const tabs = [
			["needs", __("Needs reorder"), d.needs.length],
			["on-order", __("On order"), d.on_order.length],
			["snoozed", __("Snoozed"), d.snoozed.length],
		];
		this.$tabs.html(
			tabs
				.map(
					([view, label, n]) =>
						`<button type="button" class="rob-tab${view === this.view ? " rob-tab-active" : ""}" role="tab" aria-selected="${
							view === this.view
						}" data-rob-view="${view}">${this.esc(label)} <span class="rob-count">${this.esc(n)}</span></button>`
				)
				.join("")
		);
		if (this.view === "on-order") this.render_on_order();
		else if (this.view === "snoozed") this.render_snoozed();
		else this.render_needs();
	}

	item_link(code) {
		return `<a href="${this.esc(frappe.utils.get_form_link("Item", code))}">${this.esc(code)}</a>`;
	}

	doc_link(doctype, name) {
		if (!name) return "";
		return `<a href="${this.esc(frappe.utils.get_form_link(doctype, name))}">${this.esc(name)}</a>`;
	}

	// A Draft request older than a month no longer covers its item (reorder_rules.STALE_DRAFT_DAYS): it is
	// named on the line so the buyer can finish it or delete it, rather than ask twice without knowing.
	old_drafts(line) {
		const names = line.old_drafts || [];
		if (!names.length) return "";
		return `<div class="rob-sub rob-old-draft">${this.esc(__("Old draft request:"))} ${names
			.map((n) => this.doc_link("Material Request", n))
			.join(", ")}</div>`;
	}

	where(line) {
		const group = line.warehouse_group && line.warehouse_group !== line.warehouse;
		return `${this.esc(line.warehouse)}${
			group ? `<div class="rob-sub">${this.esc(__("projected across {0}", [line.warehouse_group]))}</div>` : ""
		}`;
	}

	render_needs() {
		const d = this.data;
		const lines = d.needs;
		if (!lines.length) {
			this.sf.state.render(this.$body[0], "empty", {
				title: __("Nothing needs reordering."),
				body: __("{0} reorder rules checked. Items already on a request or an order are under On order.", [d.rules]),
			});
			return;
		}
		const can_create = d.permissions.can_create;
		const can_snooze = d.permissions.can_snooze;
		const show_rates = d.permissions.can_see_orders;
		const now = new Date();
		const all = lines.every((l) => this.selected.has(l.key));
		const rows = lines
			.map((l) => {
				const typed = this.typed.has(l.key) ? this.typed.get(l.key) : ROB.fmt_qty(l.suggested_qty);
				const buy_uom = l.purchase_uom && l.purchase_uom !== l.stock_uom ? l.purchase_uom : "";
				return `
				<tr data-key="${this.esc(l.key)}">
					<td class="rob-check">${
						can_create
							? `<input type="checkbox" class="rob-pick" aria-label="${this.esc(__("Select {0}", [l.item_code]))}"${
									this.selected.has(l.key) ? " checked" : ""
								}>`
							: ""
					}</td>
					<td>${this.item_link(l.item_code)}<div class="rob-sub">${this.esc(l.item_name || "")}</div>${this.old_drafts(l)}</td>
					<td>${this.where(l)}</td>
					<td class="rob-num">${this.esc(ROB.fmt_qty(l.on_hand))}</td>
					<td class="rob-num">${this.esc(ROB.fmt_qty(l.projected_qty))}</td>
					<td class="rob-num">${this.esc(ROB.fmt_qty(l.reorder_level))}</td>
					<td class="rob-num"><input type="text" inputmode="decimal" class="form-control input-xs rob-qty" value="${this.esc(
						typed
					)}" aria-label="${this.esc(__("Quantity to buy of {0}", [l.item_code]))}"${can_create ? "" : " disabled"}>
						<div class="rob-sub">${this.esc(__("rule: {0}", [ROB.fmt_qty(l.reorder_qty)]))}</div></td>
					<td>${this.esc(l.stock_uom || "")}${buy_uom ? `<div class="rob-sub">${this.esc(__("bought in {0}", [buy_uom]))}</div>` : ""}</td>
					<td>${this.esc(l.default_supplier || "")}</td>
					${show_rates ? `<td class="rob-num">${l.last_purchase_rate ? this.esc(format_currency(l.last_purchase_rate)) : ""}</td>` : ""}
					<td>${this.esc(ROB.days_below(l.below_since, now))}</td>
					<td class="rob-actions">${
						can_snooze ? `<button type="button" class="btn btn-default btn-xs rob-snooze">${this.esc(__("Snooze"))}</button>` : ""
					}</td>
				</tr>`;
			})
			.join("");
		const count = lines.filter((l) => this.selected.has(l.key)).length;
		this.$body.html(`
			<p class="rob-intro">${this.esc(
				__("At or below the reorder point, with no open request or order. Buy is ERPNext's suggested quantity; change it before you ask.")
			)}</p>
			<div class="rob-scroll">
				<table class="table table-sm rob-table">
					<thead><tr>
						<th class="rob-check">${
							can_create
								? `<input type="checkbox" class="rob-pick-all" aria-label="${this.esc(__("Select all"))}"${all ? " checked" : ""}>`
								: ""
						}</th>
						<th>${this.esc(__("Item"))}</th>
						<th>${this.esc(__("Warehouse"))}</th>
						<th class="rob-num">${this.esc(__("On hand"))}</th>
						<th class="rob-num">${this.esc(__("Projected"))}</th>
						<th class="rob-num">${this.esc(__("Reorder at"))}</th>
						<th class="rob-num">${this.esc(__("Buy"))}</th>
						<th>${this.esc(__("UOM"))}</th>
						<th>${this.esc(__("Default supplier"))}</th>
						${show_rates ? `<th class="rob-num">${this.esc(__("Last rate"))}</th>` : ""}
						<th>${this.esc(__("Below for"))}</th>
						<th></th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
			<div class="rob-footer">
				${
					can_create
						? `<span class="rob-selected">${this.esc(__("{0} selected", [count]))}</span>
				<button type="button" class="btn btn-primary btn-sm rob-create"${count ? "" : " disabled"}>${this.esc(
					__("Create Material Request")
				)}</button>`
						: `<span class="text-muted">${this.esc(__("You may not create Material Requests."))}</span>`
				}
			</div>
		`);
	}

	render_on_order() {
		const d = this.data;
		const lines = d.on_order;
		if (!lines.length) {
			this.sf.state.render(this.$body[0], "empty", { title: __("No open requests or orders for reorder items.") });
			return;
		}
		const rows = lines
			.map((t) => {
				const orders = (t.orders || [])
					.map((o) => {
						const how = o.arrival === "pickup" ? __("Pickup") : o.arrival === "delivery" ? __("Delivery") : "";
						const bits = [
							o.name ? this.doc_link("Purchase Order", o.name) : this.esc(__("a purchase order")),
							o.docstatus === 0 ? this.esc(__("(draft)")) : "",
							o.supplier_name ? `<span class="rob-sub">${this.esc(o.supplier_name)}</span>` : "",
							how ? this.sf.chip(how, "neutral") : o.order_stage ? `<span class="rob-sub">${this.esc(o.order_stage)}</span>` : "",
						];
						return `<div>${bits.filter(Boolean).join(" ")}</div>`;
					})
					.join("");
				const qty = `${ROB.fmt_qty(t.qty)} ${t.stock_uom || ""}`.trim();
				const progress = [];
				if (t.ordered_qty) progress.push(__("{0} ordered", [ROB.fmt_qty(t.ordered_qty)]));
				if (t.received_qty) progress.push(__("{0} received", [ROB.fmt_qty(t.received_qty)]));
				return `
				<tr>
					<td>${this.item_link(t.item_code)}<div class="rob-sub">${this.esc(t.item_name || "")}</div></td>
					<td>${this.esc(t.warehouse || "")}</td>
					<td class="rob-num">${this.esc(qty)}${progress.length ? `<div class="rob-sub">${this.esc(progress.join(", "))}</div>` : ""}</td>
					<td>${this.sf.chip(t.stage_label, ROB.stage_tone(t.stage))}</td>
					<td>${t.request ? this.doc_link("Material Request", t.request) : `<span class="text-muted">${this.esc(__("no request"))}</span>`}${
						t.requested_on ? `<div class="rob-sub">${this.esc(frappe.datetime.str_to_user(t.requested_on))}</div>` : ""
					}</td>
					<td>${orders || `<span class="text-muted">${this.esc(__("not ordered yet"))}</span>`}</td>
					<td>${t.expected ? this.esc(frappe.datetime.str_to_user(t.expected)) : ""}</td>
				</tr>`;
			})
			.join("");
		this.$body.html(`
			<p class="rob-intro">${this.esc(
				__("Every open request for a reorder item, from the Material Request through the purchase order to the receipt. A received line stays for a week.")
			)}</p>
			<div class="rob-scroll">
				<table class="table table-sm rob-table">
					<thead><tr>
						<th>${this.esc(__("Item"))}</th>
						<th>${this.esc(__("Warehouse"))}</th>
						<th class="rob-num">${this.esc(__("Qty"))}</th>
						<th>${this.esc(__("Stage"))}</th>
						<th>${this.esc(__("Material Request"))}</th>
						<th>${this.esc(__("Purchase Order"))}</th>
						<th>${this.esc(__("Expected"))}</th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
		`);
	}

	render_snoozed() {
		const d = this.data;
		const lines = d.snoozed;
		if (!lines.length) {
			this.sf.state.render(this.$body[0], "empty", { title: __("Nothing is snoozed.") });
			return;
		}
		const can_snooze = d.permissions.can_snooze;
		const rows = lines
			.map((l) => {
				const until =
					l.snooze_kind === "Until stock drops further"
						? __("until projected is below {0}", [ROB.fmt_qty(l.snooze_projected_qty)])
						: l.snoozed_until
							? __("until {0}", [frappe.datetime.str_to_user(l.snoozed_until)])
							: "";
				return `
				<tr data-key="${this.esc(l.key)}">
					<td>${this.item_link(l.item_code)}<div class="rob-sub">${this.esc(l.item_name || "")}</div>${this.old_drafts(l)}</td>
					<td>${this.where(l)}</td>
					<td class="rob-num">${this.esc(ROB.fmt_qty(l.projected_qty))}</td>
					<td class="rob-num">${this.esc(ROB.fmt_qty(l.reorder_level))}</td>
					<td>${this.esc(l.snooze_kind || "")}<div class="rob-sub">${this.esc(until)}</div></td>
					<td>${this.esc(l.snoozed_by ? frappe.user.full_name(l.snoozed_by) : "")}${
						l.snoozed_on ? `<div class="rob-sub">${this.esc(frappe.datetime.str_to_user(l.snoozed_on))}</div>` : ""
					}</td>
					<td>${this.esc(l.snooze_reason || "")}</td>
					<td class="rob-actions">${
						can_snooze ? `<button type="button" class="btn btn-default btn-xs rob-unsnooze">${this.esc(__("Un-snooze"))}</button>` : ""
					}</td>
				</tr>`;
			})
			.join("");
		this.$body.html(`
			<p class="rob-intro">${this.esc(
				__("Still at or below the reorder point, parked by a buyer. The daily digest skips these until the snooze ends.")
			)}</p>
			<div class="rob-scroll">
				<table class="table table-sm rob-table">
					<thead><tr>
						<th>${this.esc(__("Item"))}</th>
						<th>${this.esc(__("Warehouse"))}</th>
						<th class="rob-num">${this.esc(__("Projected"))}</th>
						<th class="rob-num">${this.esc(__("Reorder at"))}</th>
						<th>${this.esc(__("Snoozed"))}</th>
						<th>${this.esc(__("By"))}</th>
						<th>${this.esc(__("Reason"))}</th>
						<th></th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
		`);
	}

	line(key, list) {
		return ((this.data && this.data[list]) || []).find((l) => l.key === key);
	}

	bind() {
		this.$tabs.on("click", "[data-rob-view]", (e) => this.go(e.currentTarget.getAttribute("data-rob-view")));
		this.$body.on("change", ".rob-pick", (e) => {
			const key = $(e.currentTarget).closest("tr").attr("data-key");
			if (e.currentTarget.checked) this.selected.add(key);
			else this.selected.delete(key);
			this.update_footer();
		});
		this.$body.on("change", ".rob-pick-all", (e) => {
			const on = e.currentTarget.checked;
			for (const l of this.data.needs) {
				if (on) this.selected.add(l.key);
				else this.selected.delete(l.key);
			}
			this.$body.find(".rob-pick").prop("checked", on);
			this.update_footer();
		});
		// Typed quantities are kept per line (not redrawn while typing), so a redraw keeps them.
		this.$body.on("input", ".rob-qty", (e) => {
			const key = $(e.currentTarget).closest("tr").attr("data-key");
			this.typed.set(key, e.currentTarget.value);
		});
		this.$body.on("click", ".rob-create", () => this.confirm_create());
		this.$body.on("click", ".rob-snooze", (e) => {
			const line = this.line($(e.currentTarget).closest("tr").attr("data-key"), "needs");
			if (line) this.snooze_dialog(line);
		});
		this.$body.on("click", ".rob-unsnooze", (e) => {
			const line = this.line($(e.currentTarget).closest("tr").attr("data-key"), "snoozed");
			if (line) this.unsnooze(line);
		});
	}

	update_footer() {
		const count = this.data.needs.filter((l) => this.selected.has(l.key)).length;
		this.$body.find(".rob-selected").text(__("{0} selected", [count]));
		this.$body.find(".rob-create").prop("disabled", !count);
		this.$body.find(".rob-pick-all").prop("checked", count === this.data.needs.length);
	}

	confirm_create() {
		const { out, errors } = ROB.picked(this.data.needs, this.selected, this.typed, this.data.max_qty);
		if (errors.length) {
			frappe.msgprint({
				title: __("Check the quantities"),
				message: errors.map((e) => `${this.esc(e.line.item_code)}: ${this.esc(e.error)}`).join("<br>"),
				indicator: "orange",
			});
			return;
		}
		if (!out.length) return;
		const rows = out
			.map(({ line, qty }) => {
				const buy_uom = line.purchase_uom && line.purchase_uom !== line.stock_uom;
				const converted = buy_uom
					? ` <span class="rob-sub">(${this.esc(
							`${ROB.fmt_qty(ROB.order_qty(qty, line.conversion_factor, line.purchase_uom_whole))} ${line.purchase_uom}`
						)})</span>`
					: "";
				return `<tr><td>${this.esc(line.item_code)}<div class="rob-sub">${this.esc(line.item_name || "")}</div></td><td>${this.esc(
					line.warehouse
				)}</td><td class="rob-num">${this.esc(`${ROB.fmt_qty(qty)} ${line.stock_uom || ""}`)}${converted}</td></tr>`;
			})
			.join("");
		const companies = new Set(out.map(({ line }) => line.company));
		// One id for this dialog: a second click, or a retry after a lost reply, returns the same request.
		const ref = ROB.new_ref();
		let busy = false;
		const d = new frappe.ui.Dialog({
			title: __("Create a Draft Material Request"),
			fields: [
				{ fieldtype: "HTML", fieldname: "rob_msg" },
				{
					fieldtype: "HTML",
					fieldname: "rob_lines",
					options: `
						<p>${this.esc(
							companies.size > 1
								? __("{0} lines, one Draft Material Request for each of {1} companies:", [out.length, companies.size])
								: __("{0} lines on one Draft Material Request (type Purchase):", [out.length])
						)}</p>
						<div class="rob-scroll"><table class="table table-sm rob-table">
							<thead><tr><th>${this.esc(__("Item"))}</th><th>${this.esc(__("Warehouse"))}</th><th class="rob-num">${this.esc(
								__("Qty")
							)}</th></tr></thead>
							<tbody>${rows}</tbody>
						</table></div>
						<p class="text-muted">${this.esc(
							__("It is saved as a Draft and opened, so you can review it and submit it there.")
						)}</p>`,
				},
			],
			primary_action_label: __("Create Draft Request"),
			primary_action: () => {
				if (busy) return;
				busy = true;
				const button = d.get_primary_btn();
				button.prop("disabled", true);
				d.fields_dict.rob_msg.$wrapper.empty();
				const lines = out.map(({ line, qty }) => ({ item_code: line.item_code, warehouse: line.warehouse, qty }));
				this.call("create_material_requests", { lines, client_ref: ref }).then(
					(r) => {
						busy = false;
						button.prop("disabled", false);
						if (!r || !r.ok) {
							const stale = ((r && r.stale) || []).map((s) => `<li>${this.esc(s.reason)}</li>`).join("");
							d.fields_dict.rob_msg.$wrapper.html(
								`<div class="rob-note rob-note-warn">${this.esc((r && r.message) || __("Nothing was requested."))}${
									stale ? `<ul>${stale}</ul>` : ""
								}</div>`
							);
							if (stale) {
								button.prop("disabled", true);
								this.load();
							}
							return;
						}
						d.hide();
						for (const { line } of out) this.selected.delete(line.key);
						const names = (r.requests || []).map((m) => m.name).filter(Boolean);
						this.load();
						if (names.length === 1) {
							this.sf.toast(__("Draft Material Request {0} created.", [names[0]]), { tone: "success" });
							frappe.set_route("Form", "Material Request", names[0]);
						} else if (names.length) {
							frappe.msgprint({
								title: __("Draft Material Requests created"),
								message: names.map((n) => this.doc_link("Material Request", n)).join("<br>"),
								indicator: "green",
							});
						}
					},
					(err) => {
						busy = false;
						button.prop("disabled", false);
						// The same id is kept: if the request was made and the reply lost, trying again returns it.
						d.fields_dict.rob_msg.$wrapper.html(
							`<div class="rob-note rob-note-warn">${this.esc((err && err.message) || __("The server did not answer. Try again."))}</div>`
						);
					}
				);
			},
		});
		d.show();
	}

	snooze_dialog(line) {
		const kinds = this.data.snooze_kinds || [];
		let busy = false;
		const d = new frappe.ui.Dialog({
			title: __("Snooze {0}", [line.item_code]),
			fields: [
				{ fieldtype: "HTML", fieldname: "rob_msg" },
				{
					fieldtype: "Select",
					fieldname: "kind",
					label: __("For how long"),
					options: kinds.join("\n"),
					default: kinds[0],
					reqd: 1,
					description: __("Until stock drops further: it comes back once the projected qty falls below {0}.", [
						ROB.fmt_qty(line.projected_qty),
					]),
				},
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Why (optional)") },
			],
			primary_action_label: __("Snooze"),
			primary_action: (values) => {
				if (busy) return;
				busy = true;
				this.call("snooze_line", {
					item_code: line.item_code,
					warehouse: line.warehouse,
					kind: values.kind,
					reason: values.reason || "",
				}).then(
					(r) => {
						busy = false;
						if (!r || !r.ok) {
							d.fields_dict.rob_msg.$wrapper.html(
								`<div class="rob-note rob-note-warn">${this.esc((r && r.message) || __("It was not snoozed."))}</div>`
							);
							return;
						}
						d.hide();
						this.selected.delete(line.key);
						this.sf.toast(__("{0} snoozed.", [line.item_code]), {
							action_label: __("Undo"),
							on_action: () => this.unsnooze(line),
						});
						this.load();
					},
					(err) => {
						busy = false;
						d.fields_dict.rob_msg.$wrapper.html(
							`<div class="rob-note rob-note-warn">${this.esc((err && err.message) || __("It was not snoozed."))}</div>`
						);
					}
				);
			},
		});
		d.show();
	}

	unsnooze(line) {
		this.call("unsnooze_line", { item_code: line.item_code, warehouse: line.warehouse }).then(
			(r) => {
				if (r && r.ok) this.sf.toast(__("{0} is back on Needs reorder.", [line.item_code]), { tone: "success" });
				this.load();
			},
			(err) => this.sf.toast((err && err.message) || __("It was not un-snoozed."), { tone: "warning" })
		);
	}
}
