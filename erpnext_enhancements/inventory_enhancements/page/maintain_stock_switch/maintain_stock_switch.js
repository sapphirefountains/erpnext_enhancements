// Switch Items to Maintain Stock (v1.803.0). Nik, 2026-11-12: "Go in and check every Maintain Stock
// for every item to Yes or Checked"; decided scope, physical items only, and a list he approves
// before anything changes. This page is that list. Nothing switches until "Switch N items" is
// pressed and confirmed, and each item then goes through the same checked switch as the Item form's
// "Turn on Maintain Stock" (inventory_enhancements/maintain_stock.py), in a background job.
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/maintain-stock-switch            Will switch: physical items whose every check passes
//   /desk/maintain-stock-switch/stays      Stays non-stock: labor, fees, rent, freight, assets ...
//   /desk/maintain-stock-switch/blocked    Can't switch yet: the check that fails, in words
//   /desk/maintain-stock-switch/results    What a run switched, refused, or still holds
//
// A tab is one frappe.set_route(...). v16 fires on_page_show on every route change into this page,
// the same page included, so a change of tab (or Back, or Forward) arrives there and draws the tab
// from the list already loaded. Dialogs push nothing.
//
// The rules (what is a part, which checks, what an apply queues) are all on the server
// (maintain_stock_bulk_rules.py, maintain_stock_rules.py). This file draws and wires; the server
// computes the list again when it is applied, so nothing here is trusted. Every selector in
// maintain_stock_switch.css is under .mss-.

frappe.pages["maintain-stock-switch"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Switch Items to Maintain Stock"),
		single_column: true,
	});
	wrapper.maintain_stock_switch = new MaintainStockSwitchPage(page);
};

frappe.pages["maintain-stock-switch"].on_page_show = function (wrapper) {
	if (wrapper.maintain_stock_switch) wrapper.maintain_stock_switch.route_changed();
};

const MSS_PAGE = "maintain-stock-switch";
const MSS_API = "erpnext_enhancements.api.maintain_stock.";
const MSS_VIEWS = ["switch", "stays", "blocked", "results"];
const MSS_OUTCOME = { switch: "switch", stays: "stay", blocked: "blocked" };
const MSS_POLL_MS = 5000;

// The page's decisions, without the DOM. scripts/test_maintain_stock_switch.mjs loads this file.
const MSS = {
	view_from_route(route) {
		const view = (route || [])[1];
		return MSS_VIEWS.includes(view) && view !== "switch" ? view : "switch";
	},

	route_for(view) {
		return view && view !== "switch" ? [MSS_PAGE, view] : [MSS_PAGE];
	},

	// The rows of one tab that match the search box (code, name or group, any case).
	visible(rows, view, text) {
		const outcome = MSS_OUTCOME[view];
		const needle = String(text || "").trim().toLowerCase();
		return (rows || []).filter((row) => {
			if (outcome && row.outcome !== outcome) return false;
			if (!needle) return true;
			return [row.item_code, row.item_name, row.item_group].some((value) =>
				String(value || "").toLowerCase().includes(needle)
			);
		});
	},

	// What the server would queue from this list: the "switch" rows not already queued.
	to_apply(rows) {
		return (rows || [])
			.filter((row) => row.outcome === "switch" && row.status !== "Queued")
			.map((row) => row.item_code);
	},

	// The choice box's value for a row: what the person chose, else "follow the rule".
	choice(row) {
		return row.decision === "Switch" || row.decision === "Stay non-stock" ? row.decision : "";
	},

	run_line(status) {
		const s = status || {};
		const queued = Number(s.queued) || 0;
		const switched = Number(s.switched) || 0;
		const refused = Number(s.refused) || 0;
		if (!queued && !switched && !refused) return "";
		const parts = [];
		if (queued) parts.push(__("{0} waiting to switch", [queued]));
		parts.push(__("{0} switched", [switched]));
		if (refused) parts.push(__("{0} refused (see Results)", [refused]));
		return (queued ? __("Switching now: ") : __("Last run: ")) + parts.join(", ") + ".";
	},
};

class MaintainStockSwitchPage {
	constructor(page) {
		this.page = page;
		this.data = null;
		this.results = [];
		this.status = null;
		this.relax = false;
		this.search = "";
		this.timer = null;
		this.$root = $('<div class="mss-page"></div>').appendTo(page.main);
		this.page.add_inner_button(__("Download this list"), () => this.download("preview"));
		this.page.add_inner_button(__("Download results"), () => this.download("results"));
		this.page.add_menu_item(__("Stop the run"), () => this.stop());
		this.page.add_menu_item(__("Reload"), () => this.load());
		this.load();
	}

	call(method, args, opts) {
		return frappe
			.call(Object.assign({ method: MSS_API + method, args: args || {} }, opts || {}))
			.then((r) => (r ? r.message : null));
	}

	view() {
		return MSS.view_from_route(frappe.get_route());
	}

	route_changed() {
		if (this.data) this.draw();
		this.poll();
	}

	load() {
		return Promise.all([
			this.call("bulk_preview", { relax_sales_invoices: this.relax ? 1 : 0 }, { type: "GET" }),
			this.call("bulk_results", {}, { type: "GET" }),
			this.call("bulk_status", {}, { type: "GET" }),
		]).then(
			([data, results, status]) => {
				if (!data) return;
				this.data = data;
				this.results = results || [];
				this.status = status || null;
				this.draw();
				this.poll();
			},
			() => {
				/* frappe.call has said why */
			}
		);
	}

	poll() {
		const running = this.status && Number(this.status.queued) > 0;
		if (!running) {
			if (this.timer) clearInterval(this.timer);
			this.timer = null;
			return;
		}
		if (this.timer) return;
		this.timer = setInterval(() => {
			// Stop asking once the person has left the page; coming back starts it again.
			if (frappe.get_route()[0] !== MSS_PAGE) {
				clearInterval(this.timer);
				this.timer = null;
				return;
			}
			this.call("bulk_status", {}, { type: "GET" }).then((status) => {
				if (!status) return;
				const was = this.status ? Number(this.status.queued) : 0;
				this.status = status;
				if (was && !Number(status.queued)) this.load();
				else this.draw_banner();
			});
		}, MSS_POLL_MS);
	}

	esc(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	draw() {
		const view = this.view();
		const counts = this.data.counts || {};
		const tabs = [
			["switch", __("Will switch"), counts.switch || 0],
			["stays", __("Stays non-stock"), counts.stay || 0],
			["blocked", __("Can't switch yet"), counts.blocked || 0],
			["results", __("Results"), this.results.length],
		];
		const html = [];
		html.push(
			`<p class="mss-intro">${this.esc(
				__(
					"Every enabled item that keeps no stock is listed here. Parts and materials switch to Maintain Stock, so receiving them adds to stock. Labor, fees, rent, travel, freight, deposits, old QuickBooks lines and fixed assets stay as they are. The suggestion comes from each item's name and group and can be wrong: change any line with Your choice before you switch. Nothing changes until you press Switch."
				)
			)}</p>`
		);
		html.push('<div class="mss-banner" hidden></div>');
		html.push('<div class="mss-tabs" role="navigation">');
		tabs.forEach(([key, label, count]) => {
			html.push(
				`<button type="button" class="mss-tab" data-view="${key}"${
					key === view ? ' aria-current="page"' : ""
				}>${this.esc(label)} (${count})</button>`
			);
		});
		html.push("</div>");
		if (view !== "results") {
			html.push('<div class="mss-tools">');
			html.push(
				`<input type="search" class="form-control mss-search" placeholder="${this.esc(
					__("Find an item, name or group")
				)}" value="${this.esc(this.search)}">`
			);
			html.push(
				`<label class="mss-relax"><input type="checkbox" class="mss-relax-box"${
					this.relax ? " checked" : ""
				}> <span>${this.esc(
					__(
						"Also switch items held back only by Sales Invoices that moved no stock ({0} items). Canceling or crediting one of those invoices moves no stock. A Delivery Note made from one of them is refused afterwards.",
						[this.data.sales_invoices_only || 0]
					)
				)}</span></label>`
			);
			html.push("</div>");
		}
		html.push('<div class="mss-table-wrap"></div>');
		this.$root.html(html.join(""));

		this.$root.find(".mss-tab").on("click", (event) => {
			frappe.set_route(...MSS.route_for($(event.currentTarget).attr("data-view")));
		});
		this.$root.find(".mss-search").on("input", (event) => {
			this.search = event.currentTarget.value;
			this.draw_table();
		});
		this.$root.find(".mss-relax-box").on("change", (event) => {
			this.relax = !!event.currentTarget.checked;
			this.load();
		});
		this.draw_banner();
		this.draw_table();

		const picked = MSS.to_apply(this.data.rows);
		if (picked.length) {
			this.page.set_primary_action(__("Switch {0} items", [picked.length]), () => this.apply(picked));
		} else {
			this.page.clear_primary_action();
		}
	}

	draw_banner() {
		const line = MSS.run_line(this.status);
		const $banner = this.$root.find(".mss-banner");
		$banner.text(line).prop("hidden", !line);
	}

	draw_table() {
		const view = this.view();
		const $wrap = this.$root.find(".mss-table-wrap");
		if (view === "results") {
			$wrap.html(this.results_table());
			return;
		}
		const rows = MSS.visible(this.data.rows, view, this.search);
		if (!rows.length) {
			$wrap.html(`<p class="mss-empty">${this.esc(__("No items here."))}</p>`);
			return;
		}
		const head = [__("Item"), __("Item name"), __("Item group"), __("Why"), __("Your choice")];
		const body = rows.map((row) => {
			const link = frappe.utils.get_form_link("Item", row.item_code);
			const rule = row.overridden
				? `<div class="mss-rule">${this.esc(__("The rule said: {0}", [row.rule_reason]))}</div>`
				: "";
			const queued = row.status === "Queued" ? ` <span class="indicator-pill orange">${this.esc(__("Queued"))}</span>` : "";
			const refused =
				row.status === "Refused"
					? `<div class="mss-rule">${this.esc(__("Last run refused it: {0}", [row.result]))}</div>`
					: "";
			return (
				`<tr><td><a href="${this.esc(link)}">${this.esc(row.item_code)}</a>${queued}</td>` +
				`<td>${this.esc(row.item_name)}</td><td>${this.esc(row.item_group)}</td>` +
				`<td class="mss-why">${this.esc(row.reason)}${rule}${refused}</td>` +
				`<td>${this.choice_box(row)}</td></tr>`
			);
		});
		$wrap.html(
			`<table class="mss-table"><thead><tr>${head
				.map((h) => `<th>${this.esc(h)}</th>`)
				.join("")}</tr></thead><tbody>${body.join("")}</tbody></table>`
		);
		$wrap.find(".mss-choice").on("change", (event) => {
			const $select = $(event.currentTarget);
			this.decide($select.attr("data-item"), $select.val());
		});
	}

	choice_box(row) {
		// A fixed asset can't keep stock in ERPNext, and a queued item is the job's now.
		const locked = row.fixed_asset || row.status === "Queued";
		const options = [
			["", __("Follow the rule")],
			["Switch", __("Switch it")],
			["Stay non-stock", __("Keep it non-stock")],
		];
		const current = MSS.choice(row);
		return (
			`<select class="form-control mss-choice" data-item="${this.esc(row.item_code)}" aria-label="${this.esc(
				__("Your choice for {0}", [row.item_code])
			)}"${locked ? " disabled" : ""}>` +
			options
				.map(
					([value, label]) =>
						`<option value="${this.esc(value)}"${value === current ? " selected" : ""}>${this.esc(label)}</option>`
				)
				.join("") +
			"</select>"
		);
	}

	results_table() {
		if (!this.results.length) {
			return `<p class="mss-empty">${this.esc(__("No run yet. Items you switch show here, with any that were refused."))}</p>`;
		}
		const head = [__("Item"), __("Item name"), __("Status"), __("Result"), __("When")];
		const body = this.results.map((row) => {
			const link = frappe.utils.get_form_link("Item", row.item_code);
			const when = row.switched_on ? frappe.datetime.str_to_user(row.switched_on) : "";
			return (
				`<tr><td><a href="${this.esc(link)}">${this.esc(row.item_code)}</a></td>` +
				`<td>${this.esc(row.item_name)}</td><td>${this.esc(__(row.status))}</td>` +
				`<td class="mss-why">${this.esc(row.result)}</td><td>${this.esc(when)}</td></tr>`
			);
		});
		return `<table class="mss-table"><thead><tr>${head
			.map((h) => `<th>${this.esc(h)}</th>`)
			.join("")}</tr></thead><tbody>${body.join("")}</tbody></table>`;
	}

	decide(item_code, decision) {
		this.call("bulk_decide", { item_code, decision }, { type: "POST" }).then(
			() => this.load(),
			() => this.load()
		);
	}

	apply(picked) {
		frappe.confirm(
			__(
				"Switch {0} items to Maintain Stock? Each one is checked again as it is switched, and gets a comment saying who did it. There is no button to turn it back off.",
				[picked.length]
			),
			() => {
				this.call(
					"bulk_apply",
					{ items: JSON.stringify(picked), relax_sales_invoices: this.relax ? 1 : 0 },
					{ type: "POST", freeze: true, freeze_message: __("Queuing the items...") }
				).then((out) => {
					if (!out) return;
					frappe.show_alert({
						message: __("{0} items are queued. They switch in the background; this page follows along.", [
							out.queued,
						]),
						indicator: "green",
					});
					this.load();
				});
			}
		);
	}

	stop() {
		this.call("bulk_stop", {}, { type: "POST" }).then((out) => {
			if (!out) return;
			frappe.show_alert({ message: __("{0} items taken off the list.", [out.stopped]), indicator: "orange" });
			this.load();
		});
	}

	download(which) {
		this.call(
			"bulk_list_csv",
			{ which, relax_sales_invoices: this.relax ? 1 : 0 },
			{ type: "GET", freeze: true, freeze_message: __("Building the list...") }
		).then((out) => {
			if (!out || !out.csv) return;
			// A BOM so a spreadsheet reads the file as UTF-8.
			const blob = new Blob(["﻿" + out.csv], { type: "text/csv;charset=utf-8" });
			const url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = out.filename;
			document.body.appendChild(link);
			link.click();
			link.remove();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
		});
	}
}

if (typeof module !== "undefined" && module.exports) {
	module.exports = { MSS, MSS_VIEWS };
}
