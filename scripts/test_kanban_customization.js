#!/usr/bin/env node
/**
 * Opportunity Kanban customization (kanban_customization.js), executed against the shapes of
 * frappe 16.53's classic Kanban engine rather than grepped.
 *
 * Until v1.779.0 the file patched `KanbanView.prototype.render_card` and `.get_data`, neither of
 * which frappe 16.50's engine has, so the high-value cards and the value-stream dots had been
 * silently gone since the 2026-10-06 upgrade. This harness fakes what v16.53.0 really has
 * (views/kanban/kanban_view.js, list/list_view.js and kanban_board.bundle.js, read at the tag):
 *
 *   - `frappe.views.KanbanView` extends the ListView; `set_fields` builds `this.fields` through
 *     `_add_field` as `[fieldname, doctype]` pairs, `_user_tags` and `_liked_by` among them;
 *   - the ListView's `get_args()` carries `with_comment_count: 1`, and the Kanban endpoints send
 *     those args, so a row has `_comment_count` only when the args ask for it;
 *   - `.render_kanban_board()` waits for `this.load_lib`, then builds `frappe.views.KanbanBoard`
 *     with `cards: this.data`;
 *   - `frappe.views.KanbanBoardCard(card, wrapper)`, defined by the bundle when it loads, appends
 *     `.kanban-card-wrapper[data-name]` to `wrapper` with a `.kanban-doc-field` per board field (an
 *     icon titled by the field's label; a Link field's value is an <a> showing the link title) and
 *     a `.kanban-card-meta` drawn like `render_card_meta`: `.kanban-tags` when `card.tags` (the
 *     row's `_user_tags`) is set, `.list-comment-count` when `card.comment_count` is, the
 *     assignees, then `cur_list.get_like_html(card)`. It RETURNS UNDEFINED (a bare `init();`);
 *     only the title link opens the document. The first cut of v1.779.0 read its return value and
 *     decorated nothing (review of PR #1413): the stub returns undefined for that reason.
 *
 * Checks:
 *   1. The query: the party and contact fields are added, `_user_tags` and `_liked_by` are taken
 *      out, on the Opportunity board only; `with_comment_count` is 0 there and 1 elsewhere, and
 *      the ListView's own `get_args` / `get_like_html` are untouched.
 *   2. Value streams are fetched once per tick for every card drawn without them, hung on the data
 *      rows, and drawn on cards that arrive after the first render (a realtime redraw, a scrolled
 *      page); a row that already has them draws at once without a fetch.
 *   3. No navy: a deal over 4000 gets no class, and the file has no trace of the old one.
 *   4. Dots: one per distinct stream color, each titled with the full list of streams (and so is the container).
 *   5. No pills, no comment count, no heart on a tagged, commented card; the assignees stay.
 *   6. The contact row: the name with the phone after it on one row, the phone row gone; a name
 *      without a phone alone; a phone without a contact dropped; with "Show Labels" on too.
 *   7. The customer's name becomes a link to the Customer; a Lead to the Lead; no party, or a
 *      dash, no link. A click on the link routes to the form and does not reach the card.
 *   8. The card wrapper is installed once and only after the bundle has loaded.
 *   9. Another board on the same doctype keeps its pills, count, heart and separate rows.
 *  10. A failing value-stream fetch still draws the board, and is not retried on a redraw.
 *  11. Nothing is patched when the engine lacks the hooks.
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
		remove() {
			els.forEach((e) => {
				if (e.parent) e.parent.children = e.parent.children.filter((c) => c !== e);
				e.parent = null;
			});
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
		insertAfter(other) {
			other.els.forEach((o) => {
				const siblings = o.parent.children;
				const at = siblings.indexOf(o) + 1;
				els.forEach((e, i) => {
					e.parent = o.parent;
					siblings.splice(at + i, 0, e);
				});
			});
			return q;
		},
		on(events, fn) {
			els.forEach((e) => events.split(" ").forEach((ev) => (e.handlers[ev] = fn)));
			return q;
		},
	};
	return q;
}

// the prod "Opportunity" board's fields (2026-11-03) and their docfields
const BOARD_FIELDS = [
	["customer_name", "Customer Name", "Data"],
	["primary_contact", "Full Name", "Link"],
	["custom_contact_mobile_number", "Contact Mobile Number", "Data"],
	["opportunity_amount", "Opportunity Amount", "Currency"],
];
const LABELS = Object.fromEntries(BOARD_FIELDS.map(([f, label]) => [f, label]));

// a card the way kanban_board.bundle.js draws it (labels off unless show_labels)
function makeCard(card, options, cur_list) {
	const doc = card.doc;
	const wrapper = new El("kanban-card-wrapper");
	wrapper.attrs["data-name"] = encodeURIComponent(doc.name);
	const body = new El("kanban-card-body");
	wrapper.append(body);
	const doc_area = new El("kanban-card-doc text-muted");
	body.append(doc_area);
	(options.board_fields || BOARD_FIELDS).forEach(([fieldname, label, fieldtype]) => {
		const field = new El("kanban-doc-field");
		const first = new El(options.show_labels ? "" : "kanban-doc-icon");
		first.tag = "span";
		if (options.show_labels) first.textContent = label + ": ";
		else first.attrs.title = label;
		const val = new El(options.show_labels ? "" : "truncate");
		val.tag = "span";
		const raw = doc[fieldname];
		if (raw == null || raw === "") val.textContent = "-";
		else if (fieldtype === "Link") {
			// the Link formatter: an <a> to the document, showing the link title when known
			const a = new El("");
			a.tag = "a";
			a.attrs.href = "/desk/contact/" + encodeURIComponent(raw);
			a.textContent = (options.link_titles || {})[raw] || raw;
			val.append(a);
		} else val.textContent = String(raw);
		field.append(first).append(val);
		doc_area.append(field);
	});
	// render_card_meta
	const meta = new El("kanban-card-meta");
	if (card.tags) {
		const tags = new El("kanban-tags");
		card.tags
			.split(",")
			.filter(Boolean)
			.forEach((t) => {
				const pill = new El("tag-pill");
				pill.textContent = t;
				tags.append(pill);
			});
		meta.append(tags);
	}
	if (card.comment_count > 0) {
		const count = new El("list-comment-count");
		count.textContent = String(card.comment_count);
		meta.append(count);
	}
	meta.append(new El("kanban-assignments"));
	const like = cur_list.get_like_html(card);
	if (like) $(like).appendTo($(meta));
	body.append(meta);
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
		meta: { get_docfield: (doctype, fieldname) => ({ label: LABELS[fieldname] }) },
		call(args) {
			calls.push(args);
			if (options.fetch_fails) return Promise.reject(new Error("offline"));
			return Promise.resolve({ message: options.streams || [] });
		},
		set_route: (...args) => routes.push(args),
	};
	let list_get_args = null;
	let list_get_like_html = null;
	if (!options.no_engine) {
		// list_view.js: the args every fetch sends, and the heart
		class ListView {
			get_args() {
				return { doctype: this.doctype, fields: this.fields.slice(), with_comment_count: 1 };
			}
			get_like_html(doc) {
				return '<span class="like-action" data-name="' + doc.name + '"></span>';
			}
		}
		list_get_args = ListView.prototype.get_args;
		list_get_like_html = ListView.prototype.get_like_html;
		class KanbanView extends ListView {
			constructor(doctype, board_name, data) {
				super();
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
			// base_list.js: [fieldname, doctype] pairs, de-duplicated
			_add_field(f) {
				if (!this.fields.some((x) => x[0] === f)) this.fields.push([f, this.doctype]);
			}
			set_fields() {
				this.fields = [];
				["name", "status", "_assign", "_user_tags", "_liked_by", "owner"].forEach((f) => this._add_field(f));
				BOARD_FIELDS.forEach(([f]) => this._add_field(f));
			}
			fetched(f) {
				return this.fields.some((x) => x[0] === f);
			}
			// prepare_card + append_kanban_card: the row carries only what the query asked for;
			// the result of KanbanBoardCard is ignored; the card is in the column
			draw(doc) {
				const card = {
					doctype: this.doctype,
					name: doc.name,
					doc,
					tags: this.fetched("_user_tags") ? doc._user_tags : undefined,
					comment_count: this.get_args().with_comment_count ? doc._comment_count || 0 : 0,
					assigned_list: [],
				};
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
		frappe.views.ListView = ListView;
		frappe.views.KanbanView = KanbanView;
	}
	// kanban_board.bundle.js: appends the card to the wrapper and returns nothing
	const cardFn = function (card, wrapper) {
		$(makeCard(card, options, sandbox.cur_list)).appendTo(wrapper);
	};
	const sandbox = {
		frappe,
		console,
		window: null,
		__: (t, args) => (args ? t.replace("{0}", args[0]) : t),
		$,
		Set,
		String,
		parseFloat,
		Promise,
		setImmediate,
		setTimeout,
		encodeURIComponent,
	};
	sandbox.window = sandbox;
	vm.createContext(sandbox);
	vm.runInContext(SOURCE, sandbox, { filename: "kanban_customization.js" });
	return { frappe, calls, routes, sandbox, list_get_args, list_get_like_html };
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

const icon_titles = ($card) => $card.find(".kanban-doc-icon").els.map((e) => e.attrs.title);
const field_rows = ($card) => $card.find(".kanban-doc-field").els;
// the row's value: every span after the first (the icon, or the "Label: " span)
const row_value = (row) => $(row).children("span").els.slice(1).map((e) => e.text()).join("");

(async () => {
	// 1. the query
	{
		const desk = makeDesk();
		const view = new desk.frappe.views.KanbanView("Opportunity", "Opportunity", []);
		view.set_fields();
		check(
			same(view.fields.map((f) => f[0]), ["name", "status", "_assign", "owner", "customer_name", "primary_contact", "custom_contact_mobile_number", "opportunity_amount", "party_name", "opportunity_from"]),
			"the Opportunity board's query carries the party and the contact fields, and neither _user_tags nor _liked_by"
		);
		check(view.fields.every((f) => f[1] === "Opportunity"), "the pairs the engine stores are kept whole");
		check(view.get_args().with_comment_count === 0, "the board's fetches ask for no comment count");
		check(view.get_like_html({ name: "OPP-1" }) === "", "and draw no heart");
		const other = new desk.frappe.views.KanbanView("Opportunity", "Opportunity Pipeline", []);
		other.set_fields();
		check(same(other.fields.map((f) => f[0]), ["name", "status", "_assign", "_user_tags", "_liked_by", "owner", "customer_name", "primary_contact", "custom_contact_mobile_number", "opportunity_amount"]), "another board's query is untouched");
		check(other.get_args().with_comment_count === 1 && other.get_like_html({ name: "OPP-1" }).includes("like-action"), "another board keeps its count and heart");
		check(desk.frappe.views.ListView.prototype.get_args === desk.list_get_args && desk.frappe.views.ListView.prototype.get_like_html === desk.list_get_like_html, "the ListView's own get_args and get_like_html are untouched (the list view keeps its count and heart)");
		check(Object.prototype.hasOwnProperty.call(desk.frappe.views.KanbanView.prototype, "get_args") && Object.prototype.hasOwnProperty.call(desk.frappe.views.KanbanView.prototype, "get_like_html"), "both are shadowed on the Kanban view's prototype");
	}

	// 2-7: a page of three cards
	{
		const streams = [
			{ parent: "OPP-1", value_stream: "Build" },
			{ parent: "OPP-1", value_stream: "Build" },
			{ parent: "OPP-1", value_stream: "Design" },
		];
		const desk = makeDesk({ streams, link_titles: { "Jane Contact-Acme Co": "Jane Contact" } });
		const data = [
			{ name: "OPP-1", customer_name: "Acme Co", party_name: "Acme Co", opportunity_from: "Customer", primary_contact: "Jane Contact-Acme Co", custom_contact_mobile_number: "(801) 555-0100", opportunity_amount: 579050, _user_tags: ",Build,Design,", _comment_count: 3 },
			{ name: "OPP-2", customer_name: "Jane Lead", party_name: "CRM-LEAD-0007", opportunity_from: "Lead", primary_contact: "Jane Lead", custom_contact_mobile_number: "", opportunity_amount: 1200, _user_tags: ",Service,", _comment_count: 1 },
			{ name: "OPP-3", customer_name: "", party_name: "", opportunity_from: "Customer", primary_contact: "", custom_contact_mobile_number: "+18016807457", opportunity_amount: 0 },
		];
		const view = await render(desk, "Opportunity", data);
		check(desk.calls.length === 1 && same(desk.calls[0].args.filters, { parent: ["in", ["OPP-1", "OPP-2", "OPP-3"]] }), "value streams are fetched once, in one call, for every card drawn in the tick");
		check(desk.calls[0].args.parent === "Opportunity" && desk.calls[0].method === "frappe.client.get_list", "the fetch names the parent doctype, so the permission checked is Opportunity's, not the child table's");
		check(same(data[0].custom_value_stream.map((r) => r.value_stream), ["Build", "Build", "Design"]) && same(data[1].custom_value_stream, []), "the rows are hung on the data");
		check(view.rendered === 1, "the board rendered once");

		const [c1, c2, c3] = view.cards.map((c) => c.$card);

		// 3. no navy
		check(!c1.els[0].all().some((e) => [...e.classes].some((c) => c.includes("high-value"))) && c1.els[0].classes.size === 1, "a deal over 4000 gets no class: it reads on the normal card background");
		check(!SOURCE.includes("opportunity-high-value") && !SOURCE.includes("HIGH_VALUE"), "the file has no trace of the navy fill");

		// 4. dots
		const dots = c1.find(".value-stream-dot").els;
		check(dots.length === 2 && dots[0].css_["background-color"] === "#00609C" && dots[1].css_["background-color"] !== "#00609C", "one dot per distinct stream, colored");
		check(same(dots.map((d) => d.attrs.title), ["Value streams: Build, Design", "Value streams: Build, Design"]), "every dot's own title holds the full list (the dots cover the container, whose title would be unreachable)");
		check(c1.find(".kanban-card-value-stream").attr("title") === "Value streams: Build, Design", "and the container says the same");
		check(c1.find(".kanban-card-value-stream").els[0].parent.has("kanban-card-meta"), "the dots sit in the meta row");
		check(c2.find(".value-stream-dot").length === 0, "no streams, no dots");

		// 5. no pills, no count, no heart
		check(c1.find(".kanban-tags").length === 0 && c1.find(".tag-pill").length === 0, "a tagged deal shows no pills");
		check(c1.find(".list-comment-count").length === 0 && c2.find(".list-comment-count").length === 0, "a commented deal shows no count");
		check(c1.find(".like-action").length === 0 && c2.find(".like-action").length === 0, "no heart");
		check(c1.find(".kanban-assignments").length === 1, "the assignees stay");

		// 6. the contact row
		check(same(icon_titles(c1), ["Customer Name", "Contact", "Opportunity Amount"]), "three rows: customer, contact, amount (the phone row is gone, the contact row retitled)");
		const contact1 = field_rows(c1)[1];
		check(row_value(contact1) === "Jane Contact · (801) 555-0100", "the contact's name and phone on one row");
		check($(contact1).find(".kanban-card-contact-phone").text() === " · (801) 555-0100", "the phone in its own span, from the data row");
		const name_span = $(contact1).children("span").els[1];
		check(name_span.has("truncate") && !$(name_span).find(".kanban-card-contact-phone").length && $(contact1).children(".kanban-card-contact-phone").length === 1 && $(contact1).children("span").els[2].has("kanban-card-contact-phone"), "the phone span is the truncate span's next sibling, not its child, so a long name shortens and the number stays whole");
		const contact_link = $(name_span).children("a");
		check(contact_link.length === 1 && contact_link.attr("href") === "/desk/contact/Jane%20Contact-Acme%20Co" && contact_link.text() === "Jane Contact", "the engine's link to the Contact, showing its title, is kept");
		check(same(icon_titles(c2), ["Customer Name", "Contact", "Opportunity Amount"]) && row_value(field_rows(c2)[1]) === "Jane Lead" && c2.find(".kanban-card-contact-phone").length === 0, "no phone: the name alone");
		check(same(icon_titles(c3), ["Customer Name", "Opportunity Amount"]) && !c3.text().includes("+18016807457"), "no contact: no row, and the phone goes with it");

		// 7. the customer link
		const link1 = c1.find("a.kanban-card-party-link");
		check(link1.length === 1 && link1.attr("href") === "/desk/customer/Acme%20Co" && link1.text() === "Acme Co", "the customer's name links to the Customer");
		const link2 = c2.find("a.kanban-card-party-link");
		check(link2.length === 1 && link2.attr("href") === "/desk/lead/CRM-LEAD-0007", "a Lead's name links to the Lead");
		check(c3.find("a.kanban-card-party-link").length === 0, "no party, no link");
		const amountField = field_rows(c1)[2];
		check(amountField.all().every((e) => e.tag !== "a"), "the amount is not linked");

		// a card drawn later (a realtime redraw, a scrolled page) gets its dots too, from one more call
		desk.calls.length = 0;
		const late_doc = { name: "OPP-4", customer_name: "Late Co", party_name: "Late Co", opportunity_from: "Customer", primary_contact: "Late Person", custom_contact_mobile_number: "555", opportunity_amount: 10 };
		desk.frappe.call = (args) => {
			desk.calls.push(args);
			return Promise.resolve({ message: [{ parent: "OPP-4", value_stream: "Service" }] });
		};
		const late = view.draw(late_doc);
		check(late.$card.find(".value-stream-dot").length === 0 && desk.calls.length === 0, "a card drawn after the first render has no dots yet and nothing was fetched synchronously");
		await tick();
		check(desk.calls.length === 1 && same(desk.calls[0].args.filters, { parent: ["in", ["OPP-4"]] }), "its rows are fetched on the next tick, for that card alone");
		check(late.$card.find(".value-stream-dot").els.map((d) => d.attrs.title).join() === "Value streams: Service" && late.$card.find(".kanban-card-value-stream").attr("title") === "Value streams: Service", "and its dots are drawn when they arrive, each titled with the list");
		check(late.$card.find("a.kanban-card-party-link").length === 1 && row_value(field_rows(late.$card)[1]) === "Late Person · 555", "the link and the contact row never waited for the fetch");
		desk.calls.length = 0;
		const redrawn = view.draw(late_doc);
		await tick();
		check(desk.calls.length === 0 && redrawn.$card.find(".value-stream-dot").length === 1 && redrawn.$card.find(".kanban-card-contact-phone").length === 1, "a row that already carries its streams draws at once, with no fetch, and the contact row merges again");

		const stopped = { prevented: 0, stopped: 0 };
		const event = { preventDefault: () => (stopped.prevented += 1), stopPropagation: () => (stopped.stopped += 1), button: 0 };
		link1.els[0].handlers.click(event);
		check(same(desk.routes, [["Form", "Customer", "Acme Co"]]) && stopped.prevented === 1 && stopped.stopped === 1, "a click routes to the form and never reaches the card");
		const modified = { preventDefault: () => (stopped.prevented += 1), stopPropagation: () => (stopped.stopped += 1), button: 0, ctrlKey: true };
		link1.els[0].handlers.click(modified);
		check(desk.routes.length === 1, "a modified click is left to the browser");
		check(typeof link1.els[0].handlers.mousedown === "function", "a press on the link does not start the card's drag");

		// 8. re-render: the card function is wrapped once
		await view.render_kanban_board();
		check(desk.frappe.views.KanbanBoardCard.ee_wrapped === true && typeof desk.frappe.views.KanbanBoardCard.original === "function", "the card function is wrapped");
		const again = desk.frappe.views.KanbanBoardCard;
		await view.render_kanban_board();
		check(desk.frappe.views.KanbanBoardCard === again, "and wrapped once");
	}

	// 6b. labels on: the rows are found by their first span
	{
		const desk = makeDesk({ show_labels: true });
		const view = await render(desk, "Opportunity", [{ name: "OPP-9", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", primary_contact: "Acme Contact", custom_contact_mobile_number: "(801) 555-0100", opportunity_amount: 10 }]);
		const $card = view.cards[0].$card;
		check($card.find("a.kanban-card-party-link").attr("href") === "/desk/customer/Acme", "with Show Labels on, the customer field is found by its label span");
		const rows = field_rows($card);
		check(rows.length === 3 && $(rows[1]).children("span").first().text() === "Full Name: " && row_value(rows[1]) === "Acme Contact · (801) 555-0100", "and so is the contact row, merged under its label");
	}

	// 6c. the board without the contact fields: nothing merged, nothing invented
	{
		const desk = makeDesk({ board_fields: [BOARD_FIELDS[0], BOARD_FIELDS[3]] });
		const view = await render(desk, "Opportunity", [{ name: "OPP-10", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", primary_contact: "Acme Contact", custom_contact_mobile_number: "555", opportunity_amount: 10 }]);
		check(same(icon_titles(view.cards[0].$card), ["Customer Name", "Opportunity Amount"]) && !view.cards[0].$card.text().includes("555"), "a board whose fields leave the contact out gets no contact row");
	}

	// 9. another board on the same doctype is left alone
	{
		const desk = makeDesk();
		const view = await render(desk, "Opportunity Pipeline", [{ name: "OPP-5", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", primary_contact: "Acme Contact", custom_contact_mobile_number: "555", opportunity_amount: 99999, _user_tags: ",Build,", _comment_count: 2 }]);
		const $card = view.cards[0].$card;
		check(desk.calls.length === 0 && $card.find("a.kanban-card-party-link").length === 0, "another board: no fetch, no link");
		check($card.find(".tag-pill").length === 1 && $card.find(".list-comment-count").length === 1 && $card.find(".like-action").length === 1, "another board keeps its pills, count and heart");
		check(same(icon_titles($card), ["Customer Name", "Full Name", "Contact Mobile Number", "Opportunity Amount"]), "another board keeps its separate rows");
	}

	// 10. a failing fetch still draws
	{
		const desk = makeDesk({ fetch_fails: true });
		const view = await render(desk, "Opportunity", [{ name: "OPP-7", customer_name: "Acme", party_name: "Acme", opportunity_from: "Customer", primary_contact: "Acme Contact", custom_contact_mobile_number: "555", opportunity_amount: 5000 }]);
		check(view.rendered === 1 && view.cards[0].$card.find("a.kanban-card-party-link").length === 1 && view.cards[0].$card.find(".value-stream-dot").length === 0, "a failed stream fetch still draws the card, without dots");
		desk.calls.length = 0;
		view.draw(view.data[0]);
		await tick();
		check(desk.calls.length === 0 && same(view.data[0].custom_value_stream, []), "a failed fetch is marked tried, so a redraw does not ask again");
	}

	// 11. no engine hooks: nothing patched, nothing thrown
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
