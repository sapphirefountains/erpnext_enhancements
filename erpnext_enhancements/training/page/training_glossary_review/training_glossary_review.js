// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The glossary review queue — /desk/training-glossary-review.
//
// WHY IT EXISTS. Every one of the 717 seeded entries is stamped ai_generated with no
// reviewer, and the Help panel says so on each: "Drafted, not yet checked by a person."
// Nothing is gated on that, deliberately — a glossary entry is not an answer key. Which
// is exactly why the screen has to exist: the AI question queue is forced into use by
// the publish gate, and nothing forces this one. A backlog with no gate and no screen
// stays at 717 for ever.
//
// TWO TABS, BECAUSE THE GLOSSARY HAS TWO DIFFERENT PROBLEMS. Reviewing is reading a
// definition and putting a name to it. Collisions are the ones nobody would go looking
// for: a term matches by its name AND every alias, so two entries claiming the same
// spelling both appear for one word. 277 of those on production — and most are NOT
// duplicates. `vaults` is claimed by Equipment vault, Reservoir, Surge tank and Vault:
// four correct entries and one bad alias repeated. `GFCI` claimed by three entries IS
// one concept written three times. The page offers both moves and guesses neither.
//
// NO BULK ACCEPT, for the reason review.py gives about questions: a button that clears
// the backlog in one click makes reviewed-vs-unreviewed meaningless, and that
// distinction is the only thing the panel has to tell a learner with.
//
// Desk tokens only, no learner stylesheet — hence `gr-` and nothing else.
//
// THE TAB IS IN THE ROUTE. /desk/training-glossary-review is the review queue,
// .../traps is the same queue narrowed to trade traps, and .../collisions is the second
// tab. They used to be instance state under one unchanging URL, so browser Back from the
// collisions tab left the page, and a return from "Open the record" ran refresh() from
// on_page_show — which emptied the body and threw away every edit typed into a card.
// Now the route drives the view: the tabs and the toggle set_route, Back and Forward
// walk them, and a show that names the view already on screen leaves the DOM alone, the
// choice training_review.js and training_insights.js made for the same reason.
//
// The PAGE within the queue is deliberately not in the route. `start` is an offset into
// the entries still waiting, and every verdict shrinks that set, so ".../review/40"
// would name different entries each time it was visited. Each view remembers its own
// offset instead, so Back to the queue lands on the page it was left on.

const GR_NAV_ASSETS = [
	"/assets/erpnext_enhancements/css/training/desk_nav.css",
	"/assets/erpnext_enhancements/js/training/desk_nav.js",
];

const GR_ROUTE = "training-glossary-review";
// Route segments after the page name. The first is the default and is written as the
// bare route, so the rail link and every bookmark land where they always did.
const GR_VIEWS = ["review", "traps", "collisions"];

frappe.pages["training-glossary-review"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Glossary Review"),
		single_column: true,
	});
	$(wrapper).addClass("gr-page");
	wrapper.glossary = new GlossaryReview(page, wrapper);
};

// Fires on every route change INTO the page — views/container.js triggers "show" outside
// its is-this-a-different-page check — so this is how Back and Forward reach the tabs.
frappe.pages["training-glossary-review"].on_page_show = function (wrapper) {
	if (wrapper.glossary) wrapper.glossary.handleRoute();
};

class GlossaryReview {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		// What is on screen, as a GR_VIEWS name. Null until the first show draws one.
		this.view = null;
		this.tab = "review";
		this.onlyTraps = false;
		this.start = 0;
		// Each view's page offset, so Back to a tab returns to the page it was left on.
		this.starts = {};
		// Bumped by every load. A reply carrying an older number is for a view the
		// reviewer has already left — a tab clicked, or Back pressed, while it was in flight.
		this.ticket = 0;
		// What has been typed into a card and not yet sent, by entry and field. Every load
		// rebuilds the cards — a tab switch, a page turn, Back and Forward — and a card is
		// seeded from here first, so a correction survives until the verdict that sends it.
		// The primary Refresh is the one rebuild that drops it; see reload().
		this.drafts = {};
		// The entries whose cards the queue last drew, so Refresh knows whose typing it is
		// putting back. Empty while the body shows anything else.
		this.onScreen = [];
		// Verdicts given on the page on screen: answered, and still in flight. See pager().
		this.judged = 0;
		this.sending = [];
		this.body = $('<div class="gr-body"></div>').appendTo(page.main);
		this.loadNav();
		this.buildChrome();
		// No load here. on_page_show follows on_page_load on every open and draws the view
		// the route names; loading here as well was a second, identical round trip.
	}

	loadNav() {
		// The shared Training rail, pulled here rather than bundled globally — the same
		// call training_insights.js and training_review.js make.
		if (window.TR && TR.loadAssets) TR.loadAssets(GR_NAV_ASSETS);
	}

	buildChrome() {
		this.page.set_primary_action(__("Refresh"), () => this.reload(), "refresh");

		this.tabs = $('<div class="gr-tabs"></div>').appendTo(this.page.main).insertBefore(this.body);
		this.tabButton("review", __("To review"));
		this.tabButton("collisions", __("Same word, two entries"));

		this.trapToggle = $(
			`<label class="gr-toggle"><input type="checkbox"> ${__("Trade traps first")}</label>`
		).appendTo(this.tabs);
		this.trapToggle.find("input").on("change", (event) => {
			this.go(event.target.checked ? "traps" : "review");
		});
	}

	tabButton(key, label) {
		const button = $(`<button type="button" class="gr-tab">${label}</button>`).appendTo(this.tabs);
		button.on("click", () => {
			// The review tab keeps whichever trap filter it had, as it always has.
			this.go(key === "collisions" ? "collisions" : this.onlyTraps ? "traps" : "review");
		});
		(this.tabButtons = this.tabButtons || {})[key] = button;
	}

	// ---------------------------------------------------------------- the route

	// A tab or the toggle. It routes rather than drawing, so the change is a step Back can
	// undo; the route change comes back through on_page_show and handleRoute draws it.
	// Calling refresh() here as well would load everything twice.
	go(view) {
		if (view === this.view) {
			// The tab already on screen: pressing it reloads it from the first page, as it
			// did before the route knew about tabs. Nothing changes, so no history step.
			this.start = this.starts[view] = 0;
			this.refresh();
			return;
		}
		frappe.set_route(view === GR_VIEWS[0] ? [GR_ROUTE] : [GR_ROUTE, view]);
	}

	routeView() {
		const segment = (frappe.get_route() || [])[1];
		return GR_VIEWS.indexOf(segment) !== -1 ? segment : GR_VIEWS[0];
	}

	handleRoute() {
		const view = this.routeView();
		// Already showing it: leave the DOM alone. This is every return to the page — Back
		// from "Open the record", or from wherever the Training rail went — and a reload
		// here is what used to empty the body under a half-written correction.
		if (view === this.view) return;
		this.view = view;
		this.tab = view === "collisions" ? "collisions" : "review";
		// The collisions tab has no trap filter, so it leaves the review tab's alone.
		if (view !== "collisions") this.onlyTraps = view === "traps";
		this.trapToggle.find("input").prop("checked", this.onlyTraps);
		this.start = this.starts[view] || 0;
		this.refresh();
	}

	// The primary Refresh, and the one way to get the server's text back into a card: it
	// drops what was typed into the cards on screen, then reloads them — what Refresh did
	// before cards kept their typing. Asked first when there is typing to lose, because
	// keeping that typing is the reason `drafts` exists.
	reload() {
		const typed = this.onScreen.filter((name) => this.drafts[name]);
		if (!typed.length) return this.refresh();
		frappe.confirm(
			__(
				"Put back the saved text? What you typed into {0} of the entries on screen has not been sent, and it will be lost.",
				[typed.length]
			),
			() => {
				typed.forEach((name) => delete this.drafts[name]);
				this.refresh();
			}
		);
	}

	refresh() {
		Object.keys(this.tabButtons || {}).forEach((key) => {
			this.tabButtons[key].toggleClass("is-active", key === this.tab);
		});
		this.trapToggle.toggle(this.tab === "review");
		return this.tab === "review" ? this.loadQueue() : this.loadCollisions();
	}

	// ---------------------------------------------------------------- the queue

	loadQueue() {
		const ticket = ++this.ticket;
		this.onScreen = [];
		this.body.empty().append($(`<p class="gr-muted">${__("Loading…")}</p>`));
		return frappe
			.call({
				method: "erpnext_enhancements.training.glossary_review.get_glossary_queue",
				args: { start: this.start, only_traps: this.onlyTraps ? 1 : 0 },
			})
			.then((reply) => {
				// A later load owns the body now. Without this, Back from the collisions tab
				// while the queue was still coming could paint the queue under it — or the
				// other way round, whichever reply happened to arrive last.
				if (ticket !== this.ticket) return;
				this.drawQueue(reply.message || {});
			})
			.catch(() => {
				if (ticket !== this.ticket) return;
				this.body.empty().append($(`<p class="gr-bad">${__("Could not load the queue.")}</p>`));
			});
	}

	drawQueue(data) {
		this.body.empty();
		this.judged = 0;
		this.sending = [];
		this.body.append(this.counters(data));

		const terms = data.terms || [];
		this.onScreen = terms.map((entry) => entry.name);
		if (!terms.length) {
			this.body.append(
				$(
					`<p class="gr-muted">${
						this.onlyTraps
							? __("Every trade trap has been checked.")
							: __("Nothing left in the queue.")
					}</p>`
				)
			);
			return;
		}

		terms.forEach((entry) => this.body.append(this.termCard(entry)));
		this.body.append(this.pager(data));
	}

	counters(data) {
		const row = $('<div class="gr-counts"></div>');
		const done = (data.total || 0) - (data.pending || 0);
		row.append(
			$(`<span class="gr-count"><b>${data.pending || 0}</b> ${__("to review")}</span>`),
			$(`<span class="gr-count"><b>${data.traps_pending || 0}</b> ${__("of them trade traps")}</span>`),
			$(`<span class="gr-count"><b>${done}</b> ${__("of")} ${data.total || 0} ${__("checked")}</span>`)
		);
		return row;
	}

	termCard(entry) {
		const card = $('<div class="gr-card"></div>');
		card.append($("<h3 class='gr-term'></h3>").text(entry.term || ""));
		if (entry.trade_trap) card.append($(`<span class="gr-trap">${__("Trade trap")}</span>`));
		if (entry.aliases) {
			card.append(
				$("<p class='gr-aliases'></p>").text(
					__("Also matches") + ": " + entry.aliases.split("\n").filter(Boolean).join(", ")
				)
			);
		}

		const fields = {};
		const kept = this.drafts[entry.name] || {};
		const add = (key, label, rows) => {
			const wrap = $('<label class="gr-field"></label>').appendTo(card);
			wrap.append($("<span class='gr-label'></span>").text(label));
			const box = $(`<textarea rows="${rows}"></textarea>`).appendTo(wrap);
			// What the reviewer typed beats what the server sent, until a verdict sends it.
			box.val(Object.prototype.hasOwnProperty.call(kept, key) ? kept[key] : entry[key] || "");
			box.on("input", () => {
				(this.drafts[entry.name] = this.drafts[entry.name] || {})[key] = box.val();
			});
			fields[key] = box;
		};

		add("short_definition", __("Definition (this is what shows during a quiz)"), 2);
		if (entry.trade_trap) add("ordinary_meaning", __("What it sounds like in everyday English"), 2);
		add("explanation", __("Explanation"), 5);
		add("example", __("Worked example"), 3);

		const actions = $('<div class="gr-actions"></div>').appendTo(card);
		// Which load drew this card, so a verdict that lands after a page turn is not counted
		// against the page that replaced it.
		const drawn = this.ticket;
		$(`<button type="button" class="btn btn-primary btn-sm">${__("Accept")}</button>`)
			.appendTo(actions)
			.on("click", () => this.accept(entry, fields, card, drawn));
		$(`<button type="button" class="btn btn-default btn-sm">${__("Disable")}</button>`)
			.appendTo(actions)
			.on("click", () => this.disable(entry, card, drawn));
		$(`<a class="gr-open" href="/desk/training-glossary-term/${encodeURIComponent(entry.name)}">${__(
			"Open the record"
		)}</a>`).appendTo(actions);
		return card;
	}

	accept(entry, fields, card, drawn) {
		const args = { term: entry.name };
		Object.keys(fields).forEach((key) => {
			args[key] = fields[key].val();
		});
		this.send(
			frappe.call({ method: "erpnext_enhancements.training.glossary_review.accept_term", args }),
			entry,
			card,
			drawn
		);
	}

	// Every verdict goes through here, so Next can wait for the ones still in flight. The
	// wait is for an ANSWER, not a success: a verdict that failed has been reported by
	// frappe.call already, its card stays for another try, and it has left nothing.
	send(request, entry, card, drawn) {
		const landed = request.then(() => this.judge(entry, card, drawn));
		const answered = Promise.resolve(landed).then(() => {}, () => {});
		if (drawn === this.ticket) this.sending.push(answered);
	}

	// A verdict landed. Removed here rather than by reloading the page: a reviewer working
	// through 717 entries should never wait for a round trip to see the one they just
	// judged go. The entry has left the queue, so whatever was typed into it has been sent
	// (Accept) or no longer matters (Disable).
	judge(entry, card, drawn) {
		delete this.drafts[entry.name];
		if (drawn === this.ticket) this.judged += 1;
		card.addClass("is-gone");
		setTimeout(() => card.remove(), 150);
	}

	disable(entry, card, drawn) {
		frappe.prompt(
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Why is this entry wrong?"),
				reqd: 1,
			},
			(values) => {
				this.send(
					frappe.call({
						method: "erpnext_enhancements.training.glossary_review.disable_term",
						args: { term: entry.name, reason: values.reason },
					}),
					entry,
					card,
					drawn
				);
			},
			__("Disable {0}", [entry.term]),
			__("Disable")
		);
	}

	// "Previous", not "Back": this page is one step of the queue, and a button called Back
	// beside the browser's own read as the same thing and did something else.
	pager(data) {
		const row = $('<div class="gr-pager"></div>');
		const shown = (data.terms || []).length;
		if (this.start > 0) {
			$(`<button type="button" class="btn btn-default btn-sm">${__("Previous")}</button>`)
				.appendTo(row)
				.on("click", () => this.turn(Math.max(0, this.start - shown)));
		}
		if (shown) {
			$(`<button type="button" class="btn btn-default btn-sm">${__("Next")}</button>`)
				.appendTo(row)
				.on("click", () => {
					// Past what is still waiting, not past what was drawn. Every card judged on
					// this page has left the pending set, so each one moved the next unseen entry
					// up by one — and advancing by the full page skipped exactly that many.
					//
					// Counted once every verdict given here has been ANSWERED. Accept then Next
					// inside one round trip used to advance by the full page, because `judged`
					// counts a verdict when its reply lands; counting it on the click instead
					// would ask for the next page before the server had taken it out of the
					// set, and show an entry twice. Waiting is exact both ways.
					const drawn = this.ticket;
					const answered = () => {
						const waiting = this.sending.slice();
						// A verdict given while this waited is waited for too.
						return Promise.all(waiting).then(() =>
							this.sending.length > waiting.length ? answered() : null
						);
					};
					answered().then(() => {
						// Something else loaded meanwhile — a tab, Back, Refresh, a second Next.
						if (drawn !== this.ticket) return;
						this.turn(this.start + shown - this.judged);
					});
				});
		}
		return row;
	}

	turn(start) {
		this.start = this.starts[this.view] = start;
		this.refresh();
	}

	// ---------------------------------------------------------------- collisions

	loadCollisions() {
		const ticket = ++this.ticket;
		this.onScreen = [];
		this.body.empty().append($(`<p class="gr-muted">${__("Loading…")}</p>`));
		return frappe
			.call({ method: "erpnext_enhancements.training.glossary_review.get_collisions" })
			.then((reply) => {
				if (ticket !== this.ticket) return;
				this.drawCollisions(reply.message || {});
			})
			.catch(() => {
				if (ticket !== this.ticket) return;
				this.body.empty().append($(`<p class="gr-bad">${__("Could not load the collisions.")}</p>`));
			});
	}

	drawCollisions(data) {
		this.body.empty();
		const clusters = data.clusters || [];
		this.body.append(
			$(
				`<p class="gr-note">${__(
					"Each of these words is claimed by more than one entry, so a lesson using it shows all of them. Most are one bad alias repeated across entries that are each correct — check before you merge."
				)}</p>`
			)
		);
		this.body.append($(`<div class="gr-counts"><span class="gr-count"><b>${clusters.length}</b> ${__(
			"words with more than one entry"
		)}</span></div>`));

		if (!clusters.length) {
			this.body.append($(`<p class="gr-muted">${__("Nothing collides.")}</p>`));
			return;
		}

		clusters.forEach((cluster) => this.body.append(this.clusterCard(cluster)));
	}

	clusterCard(cluster) {
		const card = $('<div class="gr-card gr-cluster"></div>');
		card.append($("<h3 class='gr-term'></h3>").text(cluster.spelling));
		const list = $('<ul class="gr-holders"></ul>').appendTo(card);

		cluster.holders.forEach((holder) => {
			const item = $("<li></li>").appendTo(list);
			item.append($("<span class='gr-holder'></span>").text(holder));
			$(`<button type="button" class="gr-link">${__("Drop this alias")}</button>`)
				.appendTo(item)
				.on("click", () => this.dropAlias(holder, cluster.spelling, card));
			cluster.holders
				.filter((other) => other !== holder)
				.forEach((other) => {
					$(`<button type="button" class="gr-link">${__("Merge into {0}", [other])}</button>`)
						.appendTo(item)
						.on("click", () => this.merge(holder, other, card));
				});
		});
		return card;
	}

	dropAlias(term, alias, card) {
		frappe
			.call({
				method: "erpnext_enhancements.training.glossary_review.drop_alias",
				args: { term: term, alias: alias },
			})
			.then(() => {
				frappe.show_alert({ message: __("{0} no longer matches {1}", [term, alias]), indicator: "green" });
				card.addClass("is-gone");
				setTimeout(() => this.refresh(), 200);
			});
	}

	merge(loser, winner, card) {
		frappe.confirm(
			__(
				"Fold {0} into {1}? Its spellings move across so nothing stops matching, and it is then deleted. This cannot be undone.",
				[loser, winner]
			),
			() => {
				frappe
					.call({
						method: "erpnext_enhancements.training.glossary_review.merge_terms",
						args: { loser: loser, winner: winner },
					})
					.then(() => {
						frappe.show_alert({ message: __("{0} merged into {1}", [loser, winner]), indicator: "green" });
						card.addClass("is-gone");
						setTimeout(() => this.refresh(), 200);
					});
			}
		);
	}
}
