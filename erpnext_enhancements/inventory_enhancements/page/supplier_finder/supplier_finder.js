// Find a Supplier (v1.722.0): who sells this, or has sold it to us. Nik, 2026-10-22: "say I know
// we need a pump, but I don't remember all our pump suppliers." One search box takes a kind of item
// ("pump"), a phrase ("submersible pump") or a model or part number ("EQKT750"). Suppliers come
// back ranked by evidence: bought, then quoted/listed/priced, then only a matching Supplier Group or
// name. Duplicate supplier records show as one card with their aliases. The ranking and the
// optional AI expansion are on the server (api/supplier_search.py, rules in
// inventory_enhancements/supplier_search_rules.py). This file draws and wires.
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/supplier-finder                     the empty search, with examples
//   /desk/supplier-finder?q=pump&ai=1         a search (ai=1: let the AI broaden it)
//   /desk/supplier-finder?item=<item code>    every supplier of one Item (the Item form's button)
//   /desk/supplier-finder?supplier=<name>     what we have bought from one supplier
//
// Every move is frappe.set_route("supplier-finder", {...}). Since v16.50 the options land in the
// address bar, and the router copies the address's query back into frappe.route_options on every
// route change, Back included. So the page reads frappe.route_options on each show and then clears
// it. It never reads location.search. The router fires on_page_show, and its "change" event, for a
// change of query on this same page; both go through one debounced route_changed().
//
// Who may use it: the page's roles, then Supplier + Item read on the server. Prices show only for
// Purchase Order readers (the server leaves last_rate blank for everyone else). The CSS is
// supplier_finder.css, every selector under .sfn- (a Desk Page's stylesheet is global once loaded).

frappe.pages["supplier-finder"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Find a Supplier"),
		single_column: true,
	});
	wrapper.supplier_finder = new SupplierFinderPage(page);
	frappe.router.on("change", () => wrapper.supplier_finder.route_changed());
};

frappe.pages["supplier-finder"].on_page_show = function (wrapper) {
	if (wrapper.supplier_finder) wrapper.supplier_finder.route_changed();
};

const SFN_PAGE = "supplier-finder";
const SFN_AI_PREF = "sfn_use_ai";
const SFN_EXAMPLES = ["pump", "submersible pump", "underwater light", "EQKT750", "1-1/2 pvc union"];

class SupplierFinderPage {
	constructor(page) {
		this.page = page;
		this.seq = 0;
		this.state = null;
		this._timer = null;
		this.$root = $('<div class="sfn-app"></div>').appendTo(page.main);
		this.$root.html(`
			<form class="sfn-search" role="search">
				<input type="search" class="form-control sfn-input" autocomplete="off"
					aria-label="${this.esc(__("What are you looking for?"))}"
					placeholder="${this.esc(__("A kind of item, a model or a part number"))}">
				<button type="submit" class="btn btn-primary btn-sm sfn-go">${this.esc(__("Search"))}</button>
				<label class="sfn-ai">
					<input type="checkbox" class="sfn-ai-box">
					<span>${this.esc(__("Let AI broaden the search"))}</span>
				</label>
			</form>
			<div class="sfn-body" aria-live="polite"></div>
		`);
		this.$input = this.$root.find(".sfn-input");
		this.$ai = this.$root.find(".sfn-ai-box").prop("checked", this.ai_pref());
		this.$body = this.$root.find(".sfn-body");
		this.$root.find(".sfn-search").on("submit", (e) => {
			e.preventDefault();
			const q = (this.$input.val() || "").trim();
			if (q) this.go({ q, ai: this.$ai.prop("checked") ? 1 : 0 });
		});
		this.$ai.on("change", () => this.save_ai_pref(this.$ai.prop("checked")));
		// One delegated handler for every link the results draw.
		this.$body.on("click", "[data-sfn-go]", (e) => {
			e.preventDefault();
			this.go(JSON.parse(e.currentTarget.getAttribute("data-sfn-go")));
		});
		this.$body.on("click", "[data-sfn-group]", (e) => {
			e.preventDefault();
			frappe.set_route("List", "Supplier", {
				supplier_group: e.currentTarget.getAttribute("data-sfn-group"),
			});
		});
		page.set_secondary_action(__("New search"), () => this.go({}), "search");
	}

	esc(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	go_attr(opts) {
		return this.esc(JSON.stringify(opts));
	}

	ai_pref() {
		try {
			return localStorage.getItem(SFN_AI_PREF) !== "0";
		} catch (e) {
			return true;
		}
	}

	save_ai_pref(on) {
		try {
			localStorage.setItem(SFN_AI_PREF, on ? "1" : "0");
		} catch (e) {
			// Not remembered: the box still works for this visit.
		}
	}

	state_key(opts) {
		// ai is 1 from the search box and "1" from the address bar: the same screen either way.
		const ai = String(opts.ai) === "1" ? "1" : "";
		return JSON.stringify([opts.q || "", ai, opts.item || "", opts.supplier || ""]);
	}

	// The screen already showing (a search that failed, say) is run again in place: routing to the
	// address already in the bar pushes nothing and fires no route change.
	go(opts) {
		if (this.showing() && this.state_key(opts) === this.state) {
			this.show(opts);
			return;
		}
		frappe.set_route(SFN_PAGE, opts);
	}

	showing() {
		return (frappe.get_route() || [])[0] === SFN_PAGE;
	}

	// One show per route change: on_page_show and the router's "change" both arrive.
	route_changed() {
		if (this._timer) return;
		this._timer = setTimeout(() => {
			this._timer = null;
			if (!this.showing()) return;
			const opts = Object.assign({}, frappe.route_options || {});
			frappe.route_options = null;
			const key = this.state_key(opts);
			if (key === this.state) return;
			this.state = key;
			this.show(opts);
		}, 0);
	}

	show(opts) {
		if (opts.supplier) return this.show_supplier(opts.supplier);
		if (opts.item) return this.show_search({ item_code: opts.item });
		if (opts.q) {
			this.$input.val(opts.q);
			this.$ai.prop("checked", String(opts.ai) === "1");
			return this.show_search({ query: opts.q, use_ai: String(opts.ai) === "1" ? 1 : 0 });
		}
		this.$input.val("");
		this.$ai.prop("checked", this.ai_pref());
		this.show_start();
		setTimeout(() => this.$input.trigger("focus"), 0);
	}

	loading(text) {
		this.$body.html(`<div class="sfn-loading text-muted">${this.esc(text)}</div>`);
	}

	failed() {
		this.$body.html(
			`<div class="sfn-note sfn-note-warn">${this.esc(
				__("The search did not finish. Try again, or reload the page.")
			)}</div>`
		);
	}

	// Settles on frappe.call's own promise, not its error callback: v16 never calls `error` for a
	// QueryTimeout or a deadlock, and the page would say "Searching…" forever. A reply to an older
	// request (the person searched again meanwhile) is dropped.
	call(method, args) {
		const seq = ++this.seq;
		return Promise.resolve(
			frappe.call({
				method: `erpnext_enhancements.api.supplier_search.${method}`,
				args,
				type: "POST",
			})
		).then(
			(r) => (seq === this.seq ? (r && r.message) || null : null),
			() => {
				if (seq === this.seq) this.failed();
				return null;
			}
		);
	}

	show_start() {
		const chips = SFN_EXAMPLES.map(
			(q) => `<a href="#" class="sfn-chip" data-sfn-go="${this.go_attr({ q, ai: 0 })}">${this.esc(q)}</a>`
		).join("");
		this.$body.html(`
			<div class="sfn-start">
				<p>${this.esc(
					__(
						"Search for a kind of item to see every supplier we have bought it from, whatever Supplier Group they are in. Search for a model or part number to see exactly who sold it to us, and other suppliers in the same line."
					)
				)}</p>
				<div class="sfn-chips">${this.esc(__("Try:"))} ${chips}</div>
			</div>
		`);
	}

	show_search(args) {
		this.loading(args.use_ai ? __("Searching, and asking the AI to broaden it…") : __("Searching…"));
		this.call("search", args).then((r) => {
			if (!r) return;
			if (!r.ok) {
				this.$body.html(`<div class="sfn-note">${this.esc(r.message)}</div>`);
				return;
			}
			if (args.item_code) this.$input.val(r.item_code);
			this.$body.html(this.render_results(r));
		});
	}

	render_results(r) {
		const notes = [];
		if (r.ai && r.ai.asked) {
			if (r.ai.used) {
				const added = []
					.concat(r.ai.terms || [])
					.concat((r.ai.groups || []).map((g) => __("group {0}", [g])));
				notes.push(
					`<div class="sfn-note sfn-note-ai"><b>${this.esc(__("AI"))}:</b> ${this.esc(r.ai.note || "")}${
						added.length ? ` <span class="text-muted">${this.esc(__("Also searched:"))} ${this.esc(added.join(", "))}</span>` : ""
					}</div>`
				);
			} else if (r.ai.message) {
				notes.push(`<div class="sfn-note">${this.esc(r.ai.message)}</div>`);
			}
		}
		if (r.partial) {
			notes.push(
				`<div class="sfn-note sfn-note-warn">${this.esc(
					__("No item matched every word, so these match some of them.")
				)}</div>`
			);
		}
		const groups = (r.groups || []).length
			? `<div class="sfn-groups"><span class="text-muted">${this.esc(__("Matching Supplier Groups:"))}</span> ${r.groups
					.map(
						(g) =>
							`<a href="#" class="sfn-chip" data-sfn-group="${this.esc(g.name)}" title="${this.esc(
								__("Open the Supplier list for this group")
							)}">${this.esc(g.name)} <span class="sfn-count">${this.esc(g.suppliers)}</span></a>`
					)
					.join(" ")}</div>`
			: "";
		const suppliers = (r.suppliers || []).length
			? r.suppliers.map((s) => this.render_supplier(s, r.rates)).join("")
			: `<div class="sfn-empty text-muted">${this.esc(__("No supplier found for this search."))}</div>`;
		const items = (r.items || []).length
			? `<ul class="sfn-items">${r.items.map((i) => this.render_item(i)).join("")}</ul>`
			: `<div class="sfn-empty text-muted">${this.esc(__("No item matched."))}</div>`;
		return `
			${notes.join("")}
			${groups}
			<div class="sfn-cols">
				<section class="sfn-col-suppliers">
					<h5 class="sfn-heading">${this.esc(__("Suppliers"))} <span class="sfn-count">${this.esc((r.suppliers || []).length)}</span></h5>
					${suppliers}
				</section>
				<section class="sfn-col-items">
					<h5 class="sfn-heading">${this.esc(__("Matching items"))} <span class="sfn-count">${this.esc((r.items || []).length)}</span></h5>
					${items}
				</section>
			</div>
		`;
	}

	tier_label(tier) {
		return {
			history: __("Bought from"),
			carries: __("Quoted / listed"),
			group: __("Group or name only"),
		}[tier];
	}

	render_supplier(s, rates) {
		const aliases = s.aliases.length
			? `<div class="sfn-aliases text-muted">${this.esc(__("Also entered as:"))} ${this.esc(s.aliases.join(", "))}</div>`
			: "";
		const groups = s.groups.length
			? s.groups.map((g) => `<span class="sfn-tag">${this.esc(g)}</span>`).join(" ")
			: `<span class="sfn-tag sfn-tag-none">${this.esc(__("No group"))}</span>`;
		const items = s.items.length
			? `<ul class="sfn-sup-items">${s.items.map((i) => this.render_pair(i, rates)).join("")}</ul>`
			: "";
		const more =
			s.item_count > s.items.length
				? `<div class="text-muted small">${this.esc(__("and {0} more", [s.item_count - s.items.length]))}</div>`
				: "";
		return `
			<article class="sfn-card sfn-tier-${this.esc(s.tier)}${s.disabled ? " sfn-disabled" : ""}">
				<div class="sfn-card-head">
					<a href="/desk/supplier/${encodeURIComponent(s.supplier)}" class="sfn-sup-name">${this.esc(s.supplier_name)}</a>
					<span class="sfn-tier">${this.esc(this.tier_label(s.tier))}</span>
					${s.disabled ? `<span class="sfn-tag">${this.esc(__("Disabled"))}</span>` : ""}
				</div>
				${aliases}
				<div class="sfn-tags">${groups}</div>
				<div class="sfn-reason">${this.esc(s.reason)}</div>
				${items}${more}
				<a href="#" class="sfn-link" data-sfn-go="${this.go_attr({ supplier: s.supplier })}">${this.esc(
					__("Everything we bought from them")
				)} →</a>
			</article>
		`;
	}

	render_pair(i, rates) {
		const bits = [];
		if (i.po_lines) bits.push(__("{0} PO line(s)", [i.po_lines]));
		if (i.pr_lines) bits.push(__("{0} receipt line(s)", [i.pr_lines]));
		if (i.sq_lines) bits.push(__("quoted"));
		if (i.listed) bits.push(i.part_no ? __("their part {0}", [i.part_no]) : __("listed on the Item"));
		if (i.default) bits.push(__("default supplier"));
		if (i.price) bits.push(__("has a buying price"));
		if (i.last_date) bits.push(__("last {0}", [frappe.datetime.str_to_user(i.last_date)]));
		if (rates && i.last_rate) {
			bits.push(
				__("paid {0}{1}", [format_currency(i.last_rate), i.uom ? ` / ${i.uom}` : ""])
			);
		}
		return `
			<li>
				<a href="#" data-sfn-go="${this.go_attr({ item: i.item_code })}">${this.esc(i.item_name || i.item_code)}</a>
				<span class="text-muted small">${this.esc(i.item_code)}</span>
				<div class="small text-muted">${this.esc(bits.join(" · "))}</div>
			</li>
		`;
	}

	render_item(i) {
		const badge = i.model
			? `<span class="sfn-tag sfn-tag-model">${this.esc(__("exact"))}</span>`
			: i.via_ai
				? `<span class="sfn-tag sfn-tag-ai">${this.esc(__("AI"))}</span>`
				: "";
		const who = i.suppliers.length
			? this.esc(i.suppliers.join(", "))
			: `<span class="text-muted">${this.esc(__("no purchase or quote yet"))}</span>`;
		return `
			<li>
				<a href="#" data-sfn-go="${this.go_attr({ item: i.item_code })}">${this.esc(i.item_name || i.item_code)}</a>
				${badge}
				<div class="small text-muted">${this.esc(i.item_code)}${i.item_group ? ` · ${this.esc(i.item_group)}` : ""}</div>
				<div class="small">${who}</div>
			</li>
		`;
	}

	show_supplier(supplier) {
		this.$input.val("");
		this.loading(__("Loading…"));
		this.call("supplier_history", { supplier }).then((r) => {
			if (!r) return;
			if (!r.ok) {
				this.$body.html(`<div class="sfn-note">${this.esc(r.message)}</div>`);
				return;
			}
			const rows = r.items.length
				? r.items.map((i) => this.render_pair(i, r.rates)).join("")
				: `<li class="text-muted">${this.esc(__("Nothing bought, quoted or listed yet."))}</li>`;
			this.$body.html(`
				<div class="sfn-card">
					<div class="sfn-card-head">
						<a href="/desk/supplier/${encodeURIComponent(r.supplier)}" class="sfn-sup-name">${this.esc(r.supplier_name)}</a>
					</div>
					${
						r.aliases.length
							? `<div class="sfn-aliases text-muted">${this.esc(__("Also entered as:"))} ${this.esc(r.aliases.join(", "))}</div>`
							: ""
					}
					<div class="sfn-tags">${
						r.groups.length
							? r.groups.map((g) => `<span class="sfn-tag">${this.esc(g)}</span>`).join(" ")
							: `<span class="sfn-tag sfn-tag-none">${this.esc(__("No group"))}</span>`
					}</div>
					<h5 class="sfn-heading">${this.esc(__("Items"))} <span class="sfn-count">${this.esc(r.items.length)}</span></h5>
					<ul class="sfn-sup-items">${rows}</ul>
				</div>
			`);
		});
	}
}
