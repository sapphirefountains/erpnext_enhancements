#!/usr/bin/env node
/**
 * Opportunity Kanban customization (kanban_customization.js), executed against the shapes of
 * frappe 16.53's classic Kanban engine rather than grepped.
 *
 * Until v1.779.0 the file patched `KanbanView.prototype.render_card` and `.get_data`, neither of
 * which frappe 16.50's engine has, so the high-value cards and the value-stream dots had been
 * silently gone since the 2026-10-06 upgrade. This harness fakes what v16.53.0 really has
 * (views/kanban/kanban_view.js and kanban_board.bundle.js, read at the tag):
 *
 *   - `frappe.views.KanbanView.prototype.set_fields` builds `this.fields` through `_add_field`;
 *   - `.render_kanban_board()` waits for `this.load_lib`, then builds `frappe.views.KanbanBoard`
 *     with `cards: this.data`;
 *   - `frappe.views.KanbanBoardCard(card, wrapper)`, defined by the bundle when it loads, appends
 *     `.kanban-card-wrapper[data-name]` to `wrapper` with a `.kanban-doc-field` per board field (an
 *     icon titled by the field's label) and a `.kanban-card-meta`, and RETURNS UNDEFINED (a bare
 *     `init();`); only the title link opens the document. The first cut of v1.779.0 read its return
 *     value and decorated nothing (review of PR #1413): the stub returns undefined for that reason.
 *
 * Checks:
 *   1. The party fields are added to the query on the Opportunity board only.
 *   2. Value streams are fetched once per tick for every card drawn without them, hung on the data
 *      rows, and drawn on cards that arrive after the first render (a realtime redraw, a scrolled
 *      page); a row that already has them draws at once without a fetch.
 *   3. A card over 4000 gets the navy class; one under does not.
 *   4. Dots: one per distinct stream colour, titled with the stream.
 *   5. The customer's name becomes a link to the Customer; a Lead to the Lead; no party, or a
 *      dash, no link. With "Show Labels" on the field is found by its first span.
 *   6. A click on the link routes to the form and does not reach the card.
 *   7. The card wrapper is installed once and only after the bundle has loaded.
 *   8. A failing value-stream fetch still draws the board.
 *   9. Nothing is patched when the engine lacks the hooks.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const FILE = path.join(ROOT, "erpnext_enhancements", "public", "js", "kanban_customization.js");
if (!fs.existsSync(FILE)) {
	console.error("MARKERS NOT FOUND: " + path.relative(process.cwd(), FILE) + " does not exist.");
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, "utf8");

let failures = 0;
function check(condition, message) {
	if (condition) console.log("  ok    " + message);
	else {
		failures += 1;
		console.error("  FAIL  " + message);
	}
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---- a tiny element model, enough for the jQuery calls the file makes --------------------------
class El {
	constructor(cls) {
		this.classes = new Set((cls || "").split(" ").filter(Boolean));
		this.children = [];
		this.attrs = {};
		this.css_ = {};
		this.textContent = "";
		this.handlers = {};
		this.parent = null;
	}
	append(child) {
		child.parent = this;
		this.children.push(child);
		return this;
	}
	all() {
		return this.children.flatMap((c) => [c, ...c.all()]);
	}
	has(cls) {
		return this.classes.has(cls);
	}
	text() {
		return this.children.length ? this.children.map((c) => c.text()).join("") : this.textContent;
	}
}

function $(arg) {
	if (arg instanceof El) return wrap([arg]);
	if (arg && Array.isArray(arg.els)) return arg;
	if (Array.isArray(arg)) return wrap(arg);
	if (typeof arg === "string" && arg.startsWith("<")) {
		const m = arg.match(/^<(\w+)([^>]*)>/);
		const el = new El((m[2].match(/class="([^"]*)"/) || ["", ""])[1]);
		el.tag = m[1];
		return wrap([el]);
	}
	throw new Error("unsupported $ arg " + arg);
}
function wrap(els) {
	const q = {
		length: els.length,
		els,
		find(sel) {
			const cls = sel.replace(/^a\./, "").replace(/^\./, "");
			const tag = sel.startsWith("a.") ? "a" : null;
			return wrap(els.flatMap((e) => e.all()).filter((e) => e.has(cls) && (!tag || e.tag === tag)));
		},
		children(sel) {
			const by_class = sel && sel.startsWith(".") ? sel.slice(1) : null;
			const tag = by_class ? null : sel;
			return wrap(
				els.flatMap((e) => e.children).filter((e) => (by_class ? e.has(by_class) : !tag || e.tag === tag))
			);
		},
		first: () => wrap(els.slice(0, 1)),
		last: () => wrap(els.slice(-1)),
		each(fn) {
			for (const e of els) if (fn.call(e) === false) break;
			return q;
		},
		addClass(c) {
			els.forEach((e) => e.classes.add(c));
			return q;
		},
		attr(name, value) {
			if (value === undefined) return els[0] ? els[0].attrs[name] : undefined;
			els.forEach((e) => (e.attrs[name] = value));
			return q;
		},
		css(name, value) {
			els.forEach((e) => (e.css_[name] = value));
			return q;
		},
		text(value) {
			if (value === undefined) return els.map((e) => e.text()).join("");
			els.forEach((e) => {
				e.children = [];
				e.textContent = value;
			});
			return q;
		},
		empty() {
			els.forEach((e) => (e.children = []));
			return q;
		},
		append(other) {
			els.forEach((e) => other.els.forEach((o) => e.append(o)));
			return q;
		},
		appendTo(other) {
			other.els.forEach((o) => els.forEach((e) => o.append(e)));
			return q;
		},
		on(events, fn) {
			els.forEach((e) => events.split(" ").forEach((ev) => (e.handlers[ev] = fn)));
			return q;
		},
	};
	return q;
}

// a card the way kanban_board.bundle.js draws it (labels off unless show_labels)
function makeCard(doc, show_labels) {
	const wrapper = new El("kanban-card-wrapper");
	wrapper.attrs["data-name"] = encodeURIComponent(doc.name);
	const body = new El("kanban-card-body");
	wrapper.append(body);
	const fields = [
		["customer_name", "Customer Name", doc.customer_name],
		["opportunity_amount", "Opportunity Amount", doc.opportunity_amount],
	];
	fields.forEach(([, label, value]) => {
		const field = new El("kanban-doc-field");
		const first = new El(show_labels ? "" : "kanban-doc-icon");
		first.tag = "span";
		if (show_labels) first.textContent = label + ": ";
		else first.attrs.title = label;
		const val = new El(show_labels ? "" : "truncate");
		val.tag = "span";
		val.textContent = value == null || value === "" ? "-" : String(value);
		field.append(first).append(val);
		body.append(field);
	});
	body.append(new El("kanban-card-meta"));
	return wrapper;
}

function makeDesk(options) {
	options = options || {};
	const calls = [];
	const routes = [];
	const metaFields = [{ fieldname: "custom_value_stream", options: "Opportunity Value Stream" }];
	const frappe = {
		views: {},
		provide(ns) {
			const parts = ns.split(".");
			let node = sandbox;
			parts.forEach((p) => {
				node[p] = node[p] || {};
				node = node[p];
			});
			return node;
		},
		get_meta: () => ({ fields: metaFields }),
		meta: { get_docfield: () => ({ label: "Customer Name" }) },
		call(args) {
			calls.push(args);
			if (options.fetch_fails) return Promise.reject(new Error("offline"));
			return Promise.resolve({ message: options.streams || [] });
		},
		set_route: (...args) => routes.push(args),
	};
	if (!options.no_engine) {
		class KanbanView {
			constructor(doctype, board_name, data) {
				this.doctype = doctype;
				this.board_name = board_name;
				this.board = { name: board_name };
				this.data = data;
				this.fields = [];
				this.load_lib = Promise.resolve().then(() => {
					// the bundle defines the card function when it loads
					if (!frappe.views.KanbanBoardCard) frappe.views.KanbanBoardCard = cardFn;
				});
				this.rendered = 0;
				// the column every card is appended to, as kanban_board.bundle.js's $kanban_cards
				this.$column = $(new El("kanban-cards"));
			}
			_add_field(f) {
				if (!this.fields.includes(f)) this.fields.push(f);
			}
			set_fields() {
				this.fields = ["name", "status", "customer_name"];
			}
			// append_kanban_card: the result of KanbanBoardCard is ignored; the card is in the column
			draw(doc) {
				const card = { doctype: this.doctype, name: doc.name, doc };
				const returned = frappe.views.KanbanBoardCard(card, this.$column);
				if (returned !== undefined) throw new Error("the wrapper must return what the original returns: undefined");
				const $card = this.$column.children(".kanban-card-wrapper").els.filter((e) => e.attrs["data-name"] === encodeURIComponent(doc.name));
				return { card, $card: $($card) };
			}
			render_kanban_board() {
				return Promise.resolve(this.load_lib).then(() => {
					this.rendered += 1;
					this.cards = this.data.map((doc) => this.draw(doc));
				});
			}
		}
		frappe.views.KanbanView = KanbanView;
	}
	// kanban_board.bundle.js: appends the card to the wrapper and returns nothing
	const cardFn = function (card, wrapper) {
		$(makeCard(card.doc, options.show_labels)).appendTo(wrapper);
	};
	const sandbox = { frappe, console, window: null, __: (t, args) => (args ? t.replace("{0}", args[0]) : t), $, Set, parseFloat, Promise, setImmediate, setTimeout, encodeURIComponent };
	sandbox.window = sandbox;
	vm.createContext(sandbox);
	vm.runInContext(SOURCE, sandbox, { filename: "kanban_customization.js" });
	return { frappe, calls, routes, sandbox };
}

const tick = () => new Promise((resolve) => setTimeout(() => setImmediate(resolve), 1));

async function render(desk, board_name, data) {
	const view = new desk.frappe.views.KanbanView("Opportunity", board_name, data);
	desk.sandbox.cur_list = view;
	view.set_fields();
	await view.render_kanban_board();
	await tick(); // the per-tick value-stream fetch
	return view;
}

(async () => {
	// 1. fields
	{
		const desk = makeDesk();
		const view = new desk.frappe.views.KanbanView("Opportunity", "Opportunity", []);
		view.set_fields();
		check(same(view.fields, ["name", "status", "customer_name", "party_name", "opportunity_from", "opportunity_amount"]), "the Opportunity board's query carries the party and the amount");
		const other = new desk.frappe.views.KanbanView("Opportunity", "Opportunity Pipeline", []);
		other.set_fields();
		check(same(other.fields, ["name", "status", "customer_name"]), "another board's query is untouched");
	}

	// 2, 3, 4, 5: a page of three cards
	{
		const streams = [
			{ parent: "OPP-1", value_stream: "Build" },
			{ parent: "OPP-1", value_stream: "Build" },
			{ parent: "OPP-1", value_stream: "Design" },
		];
		const desk = makeDesk({ streams });
		const data = [
			{ name: "OPP-1", customer_name: "Salt Development", party_name: "Salt Development", opportunity_from: "Customer", opportunity_amount: 579050 },
			{ name: "OPP-2", customer_name: "Jane Lead", party_name: "CRM-LEAD-0007", opportunity_from: "Lead", opportunity_amount: 1200 },
			{ name: "OPP-3", customer_name: "", party_name: "", opportunity_from: "Customer", opportunity_amount: 0 },
		];
		const view = await render(desk, "Opportunity", data);
		check(desk.calls.length === 1 && same(desk.calls[0].args.filters, { parent: ["in", ["OPP-1", "OPP-2", "OPP-3"]] }), "value streams are fetched once, in one call, for every card drawn in the tick");
		check(same(data[0].custom_value_stream.map((r) => r.value_stream), ["Build", "Build", "Design"]) && same(data[1].custom_value_stream, []), "the rows are hung on the data");
		check(view.rendered === 1, "the board rendered once");

		const [c1, c2, c3] = view.cards.map((c) => c.$card);
		check(c1.find(".kanban-card-body").els[0].has("opportunity-high-value") && c1.els[0].has("opportunity-high-value-parent"), "a deal over 4000 is a high-value card");
		check(!c2.find(".kanban-card-body").els[0].has("opportunity-high-value"), "a deal under 4000 is not");
		const dots = c1.find(".value-stream-dot").els;
		check(dots.length === 2 && same(dots.map((d) => d.attrs.title), ["Build", "Design"]) && dots[0].css_["background-color"] === "#00609C", "one dot per distinct stream, titled, colored");
		check(c2.find(".value-stream-dot").length === 0, "no streams, no dots");

		const link1 = c1.find("a.kanban-card-party-link");
		check(link1.length === 1 && link1.attr("href") === "/desk/customer/Salt%20Development" && link1.text() === "Salt Development", "the customer's name links to the Customer");
		const link2 = c2.find("a.kanban-card-party-link");
		check(link2.length === 1 && link2.attr("href") === "/desk/lead/CRM-LEAD-0007", "a Lead's name links to the Lead");
		check(c3.find("a.kanban-card-party-link").length === 0, "no party, no link");
		const amountField = c1.find(".kanban-doc-field").els[1];
		check(amountField.all().every((e) => e.tag !== "a"), "only the customer field is linked");

		// 6. the click
		// a card drawn later (a realtime redraw, a scrolled page) gets its dots too, from one more call
		desk.calls.length = 0;
		const late_doc = { name: "OPP-4", customer_name: "Late Co", party_name: "Late Co", opportunity_from: "Customer", opportunity_amount: 10 };
		desk.frappe.call = (args) => {
			desk.calls.push(args);
			return Promise.resolve({ message: [{ parent: "OPP-4", value_stream: "Service" }] });
		};
		const late = view.draw(late_doc);
		check(late.$card.find(".value-stream-dot").length === 0 && desk.calls.length === 0, "a card drawn after the first render has no dots yet and nothing was fetched synchronously");
		await tick();
		check(desk.calls.length === 1 && same(desk.calls[0].args.filters, { parent: ["in", ["OPP-4"]] }), "its rows are fetched on the next tick, for that card alone");
		check(late.$card.find(".value-stream-dot").els.map((d) => d.attrs.title).join() === "Service", "and its dots are drawn when they arrive");
		check(late.$card.find("a.kanban-card-party-link").length === 1, "the link and styling never waited for the fetch");
		desk.calls.length = 0;
		const redrawn = view.draw(late_doc);
		await tick();
		check(desk.calls.length === 0 && redrawn.$card.find(".value-stream-dot").length === 1, "a row that already carries its streams draws at once, with no fetch");

		const stopped = { prevented: 0, stopped: 0 };
		const event = { preventDefault: () => (stopped.prevented += 1), stopPropagation: () => (stopped.stopped += 1), button: 0 };
		link1.els[0].handlers.click(event);
		check(same(desk.routes, [["Form", "Customer", "Salt Development"]]) && stopped.prevented === 1 && stopped.stopped === 1, "a click routes to the form and never reaches the card");
		const modified = { preventDefault: () => (stopped.prevented += 1), stopPropagation: () => (stopped.stopped += 1), button: 0, ctrlKey: true };
		link1.els[0].handlers.click(modified);
		check(desk.routes.length === 1, "a modified click is left to the browser");
		check(typeof link1.els[0].handlers.mousedown === "function", "a press on the link does not start the card's drag");

		// re-render: the card function is wrapped once
		await view.render_kanban_board();
		check(desk.frappe.views.KanbanBoardCard.ee_wrapped === true && typeof desk.frappe.views.KanbanBoardCard.original === "function", "the card function is wrapped");
		const again = desk.frappe.views.KanbanBoardCard;
		await view.render_kanban_board();
		check(desk.frappe.views.KanbanBoardCard === again, "and wrapped once");
	}

	// 5b. labels on: found by the first span
	{
		const desk = makeDesk({ show_labels: true });
		const view = await render(desk, "Opportunity", [{ name: "OPP-9", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", opportunity_amount: 10 }]);
		check(view.cards[0].$card.find("a.kanban-card-party-link").attr("href") === "/desk/customer/Acme", "with Show Labels on, the field is found by its label span");
	}

	// another board on the same doctype is left alone
	{
		const desk = makeDesk();
		const view = await render(desk, "Opportunity Pipeline", [{ name: "OPP-5", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", opportunity_amount: 99999 }]);
		check(desk.calls.length === 0 && view.cards[0].$card.find("a.kanban-card-party-link").length === 0 && !view.cards[0].$card.find(".kanban-card-body").els[0].has("opportunity-high-value"), "another board: no fetch, no link, no styling");
	}

	// 8. a failing fetch still draws
	{
		const desk = makeDesk({ fetch_fails: true });
		const view = await render(desk, "Opportunity", [{ name: "OPP-7", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", opportunity_amount: 5000 }]);
		check(view.rendered === 1 && view.cards[0].$card.find(".kanban-card-body").els[0].has("opportunity-high-value") && view.cards[0].$card.find(".value-stream-dot").length === 0, "a failed stream fetch still draws the card, without dots");
	}

	// 9. no engine hooks: nothing patched, nothing thrown
	{
		const desk = makeDesk({ no_engine: true });
		check(desk.frappe.views.KanbanView === undefined && desk.sandbox.erpnext_enhancements.kanban.installed === true, "without the hooks the file does nothing");
	}

	if (failures) {
		console.error(failures + " check(s) failed");
		process.exit(1);
	}
	console.log("kanban customization: all checks passed");
})().catch((error) => {
	console.error(error);
	process.exit(1);
});
