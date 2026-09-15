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

const GR_NAV_ASSETS = [
	"/assets/erpnext_enhancements/css/training/desk_nav.css",
	"/assets/erpnext_enhancements/js/training/desk_nav.js",
];

frappe.pages["training-glossary-review"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Glossary Review"),
		single_column: true,
	});
	$(wrapper).addClass("gr-page");
	wrapper.glossary = new GlossaryReview(page, wrapper);
};

frappe.pages["training-glossary-review"].on_page_show = function (wrapper) {
	if (wrapper.glossary) wrapper.glossary.refresh();
};

class GlossaryReview {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.tab = "review";
		this.onlyTraps = false;
		this.start = 0;
		this.body = $('<div class="gr-body"></div>').appendTo(page.main);
		this.loadNav();
		this.buildChrome();
		this.refresh();
	}

	loadNav() {
		// The shared Training rail, pulled here rather than bundled globally — the same
		// call training_insights.js and training_review.js make.
		if (window.TR && TR.loadAssets) TR.loadAssets(GR_NAV_ASSETS);
	}

	buildChrome() {
		this.page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");

		this.tabs = $('<div class="gr-tabs"></div>').appendTo(this.page.main).insertBefore(this.body);
		this.tabButton("review", __("To review"));
		this.tabButton("collisions", __("Same word, two entries"));

		this.trapToggle = $(
			`<label class="gr-toggle"><input type="checkbox"> ${__("Trade traps first")}</label>`
		).appendTo(this.tabs);
		this.trapToggle.find("input").on("change", (event) => {
			this.onlyTraps = !!event.target.checked;
			this.start = 0;
			this.refresh();
		});
	}

	tabButton(key, label) {
		const button = $(`<button type="button" class="gr-tab">${label}</button>`).appendTo(this.tabs);
		button.on("click", () => {
			this.tab = key;
			this.start = 0;
			this.refresh();
		});
		(this.tabButtons = this.tabButtons || {})[key] = button;
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
		this.body.empty().append($(`<p class="gr-muted">${__("Loading…")}</p>`));
		return frappe
			.call({
				method: "erpnext_enhancements.training.glossary_review.get_glossary_queue",
				args: { start: this.start, only_traps: this.onlyTraps ? 1 : 0 },
			})
			.then((reply) => this.drawQueue(reply.message || {}))
			.catch(() => {
				this.body.empty().append($(`<p class="gr-bad">${__("Could not load the queue.")}</p>`));
			});
	}

	drawQueue(data) {
		this.body.empty();
		this.body.append(this.counters(data));

		const terms = data.terms || [];
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
		const add = (key, label, rows) => {
			const wrap = $('<label class="gr-field"></label>').appendTo(card);
			wrap.append($("<span class='gr-label'></span>").text(label));
			const box = $(`<textarea rows="${rows}"></textarea>`).appendTo(wrap);
			box.val(entry[key] || "");
			fields[key] = box;
		};

		add("short_definition", __("Definition (this is what shows during a quiz)"), 2);
		if (entry.trade_trap) add("ordinary_meaning", __("What it sounds like in everyday English"), 2);
		add("explanation", __("Explanation"), 5);
		add("example", __("Worked example"), 3);

		const actions = $('<div class="gr-actions"></div>').appendTo(card);
		$(`<button type="button" class="btn btn-primary btn-sm">${__("Accept")}</button>`)
			.appendTo(actions)
			.on("click", () => this.accept(entry, fields, card));
		$(`<button type="button" class="btn btn-default btn-sm">${__("Disable")}</button>`)
			.appendTo(actions)
			.on("click", () => this.disable(entry, card));
		$(`<a class="gr-open" href="/desk/training-glossary-term/${encodeURIComponent(entry.name)}">${__(
			"Open the record"
		)}</a>`).appendTo(actions);
		return card;
	}

	accept(entry, fields, card) {
		const args = { term: entry.name };
		Object.keys(fields).forEach((key) => {
			args[key] = fields[key].val();
		});
		frappe
			.call({ method: "erpnext_enhancements.training.glossary_review.accept_term", args })
			.then(() => {
				// Removed here rather than by reloading the page: a reviewer working through 717
				// entries should never wait for a round trip to see the one they just judged go.
				card.addClass("is-gone");
				setTimeout(() => card.remove(), 150);
			});
	}

	disable(entry, card) {
		frappe.prompt(
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Why is this entry wrong?"),
				reqd: 1,
			},
			(values) => {
				frappe
					.call({
						method: "erpnext_enhancements.training.glossary_review.disable_term",
						args: { term: entry.name, reason: values.reason },
					})
					.then(() => {
						card.addClass("is-gone");
						setTimeout(() => card.remove(), 150);
					});
			},
			__("Disable {0}", [entry.term]),
			__("Disable")
		);
	}

	pager(data) {
		const row = $('<div class="gr-pager"></div>');
		if (this.start > 0) {
			$(`<button type="button" class="btn btn-default btn-sm">${__("Back")}</button>`)
				.appendTo(row)
				.on("click", () => {
					this.start = Math.max(0, this.start - (data.terms || []).length);
					this.refresh();
				});
		}
		if ((data.terms || []).length) {
			$(`<button type="button" class="btn btn-default btn-sm">${__("Next")}</button>`)
				.appendTo(row)
				.on("click", () => {
					this.start += (data.terms || []).length;
					this.refresh();
				});
		}
		return row;
	}

	// ---------------------------------------------------------------- collisions

	loadCollisions() {
		this.body.empty().append($(`<p class="gr-muted">${__("Loading…")}</p>`));
		return frappe
			.call({ method: "erpnext_enhancements.training.glossary_review.get_collisions" })
			.then((reply) => this.drawCollisions(reply.message || {}))
			.catch(() => {
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
