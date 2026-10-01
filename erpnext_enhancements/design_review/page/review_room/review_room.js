// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Review Room — design reviews inside ERPNext (WI-079 slice 5, ADR 0016 §2).
//
//   /desk/review-room                                   the reviews you can open
//   /desk/review-room/<review>                          (goes to the first track)
//   /desk/review-room/<review>/<track>                  options, your ranking, the tally, decisions
//   /desk/review-room/<review>/<track>/<option>/<screen> one screen: frames, pins, verdict, notes
//
// Every view is a route, so Back and Forward step through it (Nik's rule: never break
// Back/Forward). The page moves only with frappe.set_route; Back/Forward re-render through
// on_page_show -> handle_route.
//
// Concept screens are model-written HTML. They are sanitized on import (design_review/sanitize.py)
// and drawn here only inside <iframe sandbox="allow-same-origin"> — never with allow-scripts,
// which together with allow-same-origin lets a frame lift its own sandbox — carrying a
// Content-Security-Policy of default-src 'none'. The page reads each frame's layout to place
// the pins and listens for clicks on the frame's document; the WI-079 spike proved both work
// across the frame boundary with real mouse input, and that an unsanitized hostile screen ran
// no script and reached no host.
//
// Votes, verdicts and notes are written by api/design_review.py only, after the server checks
// that you are a participant and the review is Open. Nothing here is the check; the buttons
// are hidden from people who would be refused, as a courtesy.

const DR = {
	route: "review-room",
	api: "erpnext_enhancements.api.design_review",
	font: "/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2",
	verdicts: ["Yes", "Maybe", "No"],
	mode_key: "ee_review_room_mode",
};

const DR_STYLE = `
.dr-wrap{padding-bottom:64px;color:var(--text-color);}
.dr-muted{color:var(--text-muted);}
.dr-small{font-size:12px;}
.dr-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}
.dr-pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;background:var(--bg-light-gray);}
.dr-pill.is-open{background:var(--green-100,#dcfce7);color:var(--green-800,#166534);}
.dr-pill.is-draft{background:var(--gray-100);}
.dr-pill.is-closed{background:var(--yellow-100,#fef9c3);color:var(--yellow-800,#854d0e);}
.dr-pill.is-decided{background:var(--blue-100,#dbeafe);color:var(--blue-800,#1e40af);}
.dr-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;margin:12px 0 24px;}
.dr-card{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);display:flex;flex-direction:column;overflow:hidden;}
.dr-card .dr-card-body{padding:12px 14px;display:flex;flex-direction:column;gap:6px;flex:1;}
.dr-card h4{margin:0;font-size:15px;}
.dr-card .dr-thumb{height:300px;background:var(--bg-light-gray);overflow:hidden;position:relative;display:flex;justify-content:center;padding-top:10px;}
.dr-tabs{display:flex;gap:4px;border-bottom:1px solid var(--border-color);margin:8px 0 14px;flex-wrap:wrap;}
.dr-tab{border:0;background:none;padding:8px 14px;font-weight:600;color:var(--text-muted);border-bottom:3px solid transparent;cursor:pointer;}
.dr-tab.is-on{color:var(--text-color);border-bottom-color:var(--primary);}
.dr-two{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,380px);gap:20px;align-items:start;}
@media (max-width: 991px){.dr-two{grid-template-columns:1fr;}}
.dr-panel{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);padding:14px;display:flex;flex-direction:column;gap:10px;}
.dr-panel h5{margin:0;font-size:14px;}
.dr-rank-row{display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--border-color);border-radius:8px;}
.dr-rank-row .dr-n{width:26px;height:26px;border-radius:50%;background:var(--primary);color:#fff;display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;flex:none;}
.dr-rank-row .dr-name{flex:1;font-weight:600;}
.dr-bar{display:grid;grid-template-columns:150px 1fr 40px;gap:8px;align-items:center;font-size:13px;}
.dr-bar .dr-track{height:10px;background:var(--bg-light-gray);border-radius:5px;overflow:hidden;}
.dr-bar .dr-track span{display:block;height:100%;background:var(--primary);}
.dr-strip{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0 10px;}
.dr-strip .dr-stage{font-size:11px;font-weight:700;color:var(--text-muted);margin:0 2px 0 8px;text-transform:uppercase;}
.dr-chip{border:1px solid var(--border-color);background:var(--card-bg);border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer;}
.dr-chip.is-on{background:var(--primary);color:#fff;border-color:var(--primary);}
.dr-chip .dr-count{display:inline-block;min-width:16px;margin-left:4px;padding:0 4px;border-radius:8px;background:#ffb819;color:#00111c;font-weight:700;}
.dr-frames{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;}
.dr-slot{position:relative;overflow:hidden;border:1px solid var(--border-color);border-radius:6px;background:#f8f8f8;flex:none;}
.dr-slot iframe{position:absolute;left:0;top:0;border:0;transform-origin:0 0;}
.dr-pins{position:absolute;inset:0;pointer-events:none;}
.dr-pin{position:absolute;min-width:22px;height:20px;border-radius:10px;background:#ffb819;color:#00111c;font:700 11px/20px var(--font-stack);text-align:center;border:0;padding:0 5px;pointer-events:auto;cursor:pointer;transform:translate(-25%,-25%);}
.dr-pin.has-notes{background:#bd2e2b;color:#fff;}
.dr-pin.is-on{outline:3px solid var(--primary);}
.dr-hl{position:absolute;border:3px solid var(--primary);border-radius:4px;pointer-events:none;}
.dr-verdicts{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
.dr-verdicts .btn.is-on{outline:2px solid var(--primary);}
.dr-note{border:1px solid var(--border-color);border-radius:8px;padding:8px 10px;display:flex;flex-direction:column;gap:4px;font-size:13px;}
.dr-note .dr-note-meta{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--text-muted);align-items:center;}
.dr-note.is-imported{border-style:dashed;}
.dr-code{font-family:var(--font-stack-monospace,monospace);font-size:12px;font-weight:700;}
.dr-empty{padding:24px;text-align:center;color:var(--text-muted);}
`;

frappe.pages[DR.route].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Review Room"), single_column: true });
	wrapper.review_room = new ReviewRoom(page);
};

frappe.pages[DR.route].on_page_show = function (wrapper) {
	if (wrapper.review_room) wrapper.review_room.handle_route();
};

class ReviewRoom {
	constructor(page) {
		this.page = page;
		this.reviews = {};
		// Screen markup by name AND content hash: an import updates a screen in place, so a name
		// alone would keep showing the previous revision until a reload.
		this.html = {};
		this.selected = null;
		try {
			this.mode = localStorage.getItem(DR.mode_key) === "annotate" ? "annotate" : "click";
		} catch (e) {
			this.mode = "click";
		}
		if (!document.getElementById("dr-style")) {
			$("<style id='dr-style'></style>").text(DR_STYLE).appendTo(document.head);
		}
		this.$root = $('<div class="dr-wrap"></div>').appendTo(page.main);
		$(window).on("resize.review_room", frappe.utils.debounce(() => this.relayout(), 150));
	}

	// ------------------------------------------------------------------ routing

	route() {
		const r = frappe.get_route() || [];
		return r[0] === DR.route ? r.slice(1).map((x) => decodeURIComponent(x || "")) : null;
	}

	go(parts, replace) {
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route([DR.route].concat(parts));
		delete frappe.route_flags.replace_route;
	}

	async handle_route() {
		const parts = this.route();
		if (!parts) return;
		const [name, track, option, screen] = parts;
		this.page.clear_inner_toolbar();
		this.page.clear_menu();
		if (!name) return this.show_list();
		const review = await this.load(name);
		if (!review) return;
		this.page.set_title(review.title);
		this.add_menu(review);
		const t = track && review.tracks.find((x) => x.id === track);
		if (!t) return this.go([name, review.tracks[0].id], true);
		if (!option) return this.show_track(review, t);
		const opt = review.options.find((o) => o.option_code === option && o.track === t.id);
		if (!opt) return this.go([name, t.id], true);
		const order = this.screen_order(review, opt.option_code);
		if (!screen || !order.includes(screen)) return this.go([name, t.id, opt.option_code, order[0]], true);
		return this.show_screen(review, t, opt, screen);
	}

	async load(name, force) {
		if (this.reviews[name] && !force) return this.reviews[name];
		try {
			const r = await frappe.call({ method: `${DR.api}.get_review`, args: { review: name } });
			this.reviews[name] = r.message;
			return r.message;
		} catch (e) {
			this.$root.html(`<div class="dr-empty">${__("This review was not found, or you are not on it.")}</div>`);
			return null;
		}
	}

	async refresh(name) {
		await this.load(name, true);
		await this.handle_route();
	}

	add_menu(review) {
		if (!review.me.moderator) return;
		this.page.add_menu_item(__("Open the review record"), () => frappe.set_route("Form", "Design Review", review.name));
		this.page.add_menu_item(__("Import a new revision"), () => this.import_dialog(review.name));
	}

	// ------------------------------------------------------------------ list

	async show_list() {
		this.page.set_title(__("Review Room"));
		this.$root.html(`<div class="dr-empty">${__("Loading…")}</div>`);
		const r = await frappe.call({ method: `${DR.api}.list_reviews` });
		const rows = r.message || [];
		if (frappe.user.has_role("System Manager")) {
			this.page.add_inner_button(__("Import a review"), () => this.import_dialog(null));
		}
		if (!rows.length) {
			this.$root.html(`<div class="dr-empty">${__("No design reviews include you yet.")}</div>`);
			return;
		}
		const cards = rows
			.map(
				(row) => `<div class="dr-card"><div class="dr-card-body">
				<div class="dr-row"><span class="dr-pill is-${frappe.scrub(row.status || "draft")}">${__(row.status || "Draft")}</span>
				${row.participant ? `<span class="dr-small dr-muted">${__("You are a participant")}</span>` : `<span class="dr-small dr-muted">${__("Moderator view")}</span>`}</div>
				<h4>${frappe.utils.escape_html(row.title)}</h4>
				<div class="dr-small dr-muted">${frappe.utils.escape_html(row.description || "")}</div>
				<div><button class="btn btn-primary btn-sm" data-open="${frappe.utils.escape_html(row.name)}">${__("Open")}</button></div>
			</div></div>`
			)
			.join("");
		this.$root.html(`<div class="dr-cards">${cards}</div>`);
		this.$root.find("[data-open]").on("click", (e) => this.go([$(e.currentTarget).attr("data-open")]));
	}

	// ------------------------------------------------------------------ track overview

	show_track(review, track) {
		const options = review.options.filter((o) => o.track === track.id);
		const open = review.status === "Open";
		const can_vote = review.me.participant && open && track.votable;
		const $head = $(`<div>
			<div class="dr-row"><span class="dr-pill is-${frappe.scrub(review.status)}">${__(review.status)}</span>
				<span class="dr-small dr-muted">${__("Revision {0}", [review.revision])}</span>
				${review.me.participant ? "" : `<span class="dr-small dr-muted">${__("You can read this review; only participants vote and write notes.")}</span>`}
				${review.me.participant && !open ? `<span class="dr-small dr-muted">${__("Voting and notes open when the review is Open.")}</span>` : ""}
			</div>
			<div class="dr-tabs">${review.tracks
				.map((t) => `<button class="dr-tab ${t.id === track.id ? "is-on" : ""}" data-track="${t.id}">${frappe.utils.escape_html(t.label)}</button>`)
				.join("")}</div>
			<p class="dr-muted">${frappe.utils.escape_html(track.blurb || "")}</p>
		</div>`);
		const $cards = $('<div class="dr-cards"></div>');
		options.forEach((o) => {
			const notes = review.notes.filter((n) => n.option_code === o.option_code).length;
			const $card = $(`<div class="dr-card"><div class="dr-thumb"></div><div class="dr-card-body">
				<span class="dr-code">${o.option_code}</span><h4>${frappe.utils.escape_html(o.option_name)}</h4>
				<div class="dr-small">${frappe.utils.escape_html(o.what || "")}</div>
				<div class="dr-small dr-muted">${o.tradeoff ? __("Trade-off: {0}", [frappe.utils.escape_html(o.tradeoff)]) : ""}</div>
				<div class="dr-row"><button class="btn btn-default btn-sm" data-option="${o.option_code}">${__("Open")}</button>
				${notes ? `<span class="dr-small dr-muted">${__("{0} notes", [notes])}</span>` : ""}</div>
			</div></div>`);
			$cards.append($card);
			const first = this.screen_order(review, o.option_code)[0];
			const frame = this.pick_frame(review, o.option_code, first, "phone");
			if (frame) this.mount_thumb(review, $card.find(".dr-thumb")[0], frame);
		});
		const $two = $('<div class="dr-two"></div>');
		const $left = $("<div></div>").append(this.decisions_panel(review, track));
		const $right = $("<div style='display:flex;flex-direction:column;gap:16px'></div>");
		if (track.votable) {
			if (can_vote) $right.append(this.ranking_panel(review, track, options));
			$right.append(this.tally_panel(review, track, options));
		}
		$two.append($left, $right);
		this.$root.empty().append($head, $cards, $two);
		this.$root.find("[data-track]").on("click", (e) => this.go([review.name, $(e.currentTarget).attr("data-track")]));
		this.$root.find("[data-option]").on("click", (e) => this.go([review.name, track.id, $(e.currentTarget).attr("data-option")]));
	}

	ranking_panel(review, track, options) {
		const saved = (review.my_votes[track.id] || {}).ranking || [];
		const codes = options.map((o) => o.option_code);
		let order = saved.length === codes.length && saved.every((c) => codes.includes(c)) ? saved.slice() : codes.slice();
		const $p = $(`<div class="dr-panel"><h5>${__("Your ranking")}</h5>
			<div class="dr-small dr-muted">${__("1 is your favourite. Use the arrows, then save. Saving again replaces it.")}</div>
			<div class="dr-ranks" style="display:flex;flex-direction:column;gap:6px"></div>
			<div class="dr-row"><button class="btn btn-primary btn-sm dr-save">${__("Save my ranking")}</button><span class="dr-small dr-muted dr-status"></span></div></div>`);
		const name = (code) => (options.find((o) => o.option_code === code) || {}).option_name || code;
		const draw = () => {
			const $list = $p.find(".dr-ranks").empty();
			order.forEach((code, i) => {
				const $row = $(`<div class="dr-rank-row"><span class="dr-n">${i + 1}</span><span class="dr-name">${code} · ${frappe.utils.escape_html(name(code))}</span>
					<button class="btn btn-xs btn-default" aria-label="${__("Move up")}" ${i === 0 ? "disabled" : ""}>▲</button>
					<button class="btn btn-xs btn-default" aria-label="${__("Move down")}" ${i === order.length - 1 ? "disabled" : ""}>▼</button></div>`);
				$row.find("button").eq(0).on("click", () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(); });
				$row.find("button").eq(1).on("click", () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(); });
				$list.append($row);
			});
			$p.find(".dr-status").text(saved.length && saved.join() === order.join() ? __("Saved") : saved.length ? __("Changed since you saved") : __("Not saved yet"));
		};
		draw();
		$p.find(".dr-save").on("click", async () => {
			await frappe.call({ method: `${DR.api}.cast_vote`, args: { review: review.name, track: track.id, ranking: JSON.stringify(order) }, freeze: true });
			frappe.show_alert({ message: __("Ranking saved"), indicator: "green" });
			this.refresh(review.name);
		});
		return $p;
	}

	tally_panel(review, track, options) {
		const t = review.tallies[track.id] || {};
		const $p = $(`<div class="dr-panel"><h5>${__("Team tally")}</h5></div>`);
		const block = (label, data, first) => {
			if (!data || !data.voters) return `<div class="dr-small dr-muted">${label}: ${__("no rankings yet")}</div>`;
			const max = Math.max(1, ...Object.values(data.points));
			const rows = options
				.slice()
				.sort((a, b) => (data.points[b.option_code] || 0) - (data.points[a.option_code] || 0))
				.map((o) => {
					const pts = data.points[o.option_code] || 0;
					const who = (first[o.option_code] || []).map((x) => frappe.utils.escape_html(x)).join(", ");
					return `<div class="dr-bar"><span>${o.option_code} · ${frappe.utils.escape_html(o.option_name)}</span><div class="dr-track"><span style="width:${Math.round((100 * pts) / max)}%"></span></div><b>${pts}</b></div>
						${who ? `<div class="dr-small dr-muted" style="margin:-2px 0 4px">${__("First choice of {0}", [who])}</div>` : ""}`;
				})
				.join("");
			return `<div class="dr-small"><b>${label}</b> · ${__("{0} people ranked; first place scores {1}", [data.voters, options.length])}</div>${rows}`;
		};
		$p.append(block(__("This review"), t.live, t.live_first || {}));
		if (t.imported && t.imported.voters) {
			$p.append(`<hr style="margin:6px 0">${block(__("Imported from the claude.ai ballot, counted apart"), t.imported, t.imported_first || {})}`);
		}
		return $p;
	}

	decisions_panel(review, track) {
		const decisions = review.decisions.filter((d) => !d.track || d.track === track.id);
		const $p = $(`<div class="dr-panel"><div class="dr-row" style="justify-content:space-between"><h5>${__("Decisions")}</h5></div><div class="dr-list"></div></div>`);
		if (review.me.moderator) {
			$(`<button class="btn btn-default btn-sm">${__("Record a decision")}</button>`)
				.appendTo($p.find(".dr-row"))
				.on("click", () => this.decision_dialog(review, track));
		}
		const $list = $p.find(".dr-list");
		if (!decisions.length) $list.append(`<div class="dr-small dr-muted">${__("Nothing decided yet.")}</div>`);
		decisions.forEach((d) => {
			const $d = $(`<div class="dr-note"><div class="dr-note-meta"><span>${d.option_code ? `<span class="dr-code">${d.option_code}</span> · ` : ""}${frappe.utils.escape_html(d.status)}</span>
				<span>${d.promoted_request ? `<a href="/desk/enhancement-request/${encodeURIComponent(d.promoted_request)}">${d.promoted_request}</a>` : ""}</span></div>
				<b>${frappe.utils.escape_html(d.title)}</b><div>${frappe.utils.escape_html(d.decision)}</div>
				<div class="dr-small dr-muted">${d.notes.length ? __("Carries {0} notes", [d.notes.length]) : ""}</div></div>`);
			if (review.me.promoter && d.status !== "Promoted") {
				$(`<div><button class="btn btn-primary btn-xs">${__("Promote to an enhancement request")}</button></div>`)
					.appendTo($d)
					.find("button")
					.on("click", () => this.promote_dialog(review, d));
			}
			$list.append($d);
		});
		return $p;
	}

	// ------------------------------------------------------------------ screen view

	// The option's screens in story order: the track's stages (its experience map) first, in
	// their order, then any screen no stage names, in import order. Next, Previous and the strip
	// all read this, so the strip never shows a stage twice.
	screen_order(review, option_code) {
		const drawn = [];
		review.screens
			.filter((s) => s.option_code === option_code)
			.forEach((s) => { if (!drawn.includes(s.screen_code)) drawn.push(s.screen_code); });
		const option = review.options.find((o) => o.option_code === option_code);
		const track = option && review.tracks.find((t) => t.id === option.track);
		const staged = [];
		((track && track.groups) || []).forEach((g) => (g.screens || []).forEach((sc) => {
			if (drawn.includes(sc) && !staged.includes(sc)) staged.push(sc);
		}));
		return staged.concat(drawn.filter((sc) => !staged.includes(sc)));
	}

	pick_frame(review, option_code, screen_code, prefer) {
		const frames = review.screens.filter((s) => s.option_code === option_code && s.screen_code === screen_code);
		return frames.find((s) => s.frame === prefer) || frames[0] || null;
	}

	async show_screen(review, track, option, screen_code) {
		const order = this.screen_order(review, option.option_code);
		const frames = review.screens.filter((s) => s.option_code === option.option_code && s.screen_code === screen_code);
		const meta = frames[0] || {};
		const open = review.status === "Open";
		const voter = review.me.participant && open;
		this.current = { review, track, option, screen_code };
		if (!this.selected || this.selected.screen !== `${option.option_code}-${screen_code}`) this.selected = null;

		const stage_of = {};
		(track.groups || []).forEach((g) => (g.screens || []).forEach((sc) => { if (!(sc in stage_of)) stage_of[sc] = g.name; }));
		const groups = [];
		order.forEach((sc) => {
			const s = this.pick_frame(review, option.option_code, sc, "phone");
			const g = stage_of[sc] || (s && s.group_name) || "";
			if (!groups.length || groups[groups.length - 1].name !== g) groups.push({ name: g, screens: [] });
			groups[groups.length - 1].screens.push(s);
		});
		const strip = groups
			.map((g) => `${g.name ? `<span class="dr-stage">${frappe.utils.escape_html(g.name)}</span>` : ""}${g.screens
				.map((s) => {
					const n = review.notes.filter((x) => x.option_code === option.option_code && x.screen_code === s.screen_code).length;
					return `<button class="dr-chip ${s.screen_code === screen_code ? "is-on" : ""}" data-screen="${s.screen_code}">${s.screen_code} · ${frappe.utils.escape_html(s.screen_name)}${n ? `<span class="dr-count">${n}</span>` : ""}</button>`;
				})
				.join("")}`)
			.join("");
		const idx = order.indexOf(screen_code);
		const mine = review.verdicts.mine[`${option.option_code}:${screen_code}`] || "";
		const counts = review.verdicts.live[`${option.option_code}:${screen_code}`] || {};
		const $head = $(`<div>
			<div class="dr-row" style="justify-content:space-between">
				<div class="dr-row"><button class="btn btn-default btn-xs" data-back>${__("All {0} options", [frappe.utils.escape_html(track.label)])}</button>
				<span class="dr-code">${option.option_code}</span><b>${frappe.utils.escape_html(option.option_name)}</b>
				<span class="dr-muted">· ${frappe.utils.escape_html(meta.screen_name || screen_code)}</span></div>
				<div class="dr-row">
					<div class="btn-group btn-group-sm" role="group" aria-label="${__("Clicks in the screen")}">
						<button class="btn btn-default ${this.mode === "click" ? "active" : ""}" data-mode="click">${__("Click through")}</button>
						<button class="btn btn-default ${this.mode === "annotate" ? "active" : ""}" data-mode="annotate">${__("Pick parts")}</button>
					</div>
					<button class="btn btn-default btn-sm" data-step="-1" ${idx <= 0 ? "disabled" : ""}>${__("Previous")}</button>
					<button class="btn btn-default btn-sm" data-step="1" ${idx >= order.length - 1 ? "disabled" : ""}>${__("Next")}</button>
				</div>
			</div>
			<div class="dr-strip">${strip}</div>
		</div>`);
		const verdict_html = track.votable
			? `<div class="dr-verdicts"><span class="dr-small"><b>${__("Does this screen work?")}</b></span>
				${DR.verdicts
					.map((v) => `<button class="btn btn-sm btn-default ${mine === v ? "is-on" : ""}" data-verdict="${v}" ${voter ? "" : "disabled"}>${__(v)} <span class="dr-muted">${counts[v] || 0}</span></button>`)
					.join("")}
				${mine && voter ? `<button class="btn btn-xs btn-link" data-verdict="">${__("Clear mine")}</button>` : ""}</div>`
			: "";
		const $two = $('<div class="dr-two"></div>');
		const $frames = $('<div class="dr-frames"></div>');
		const $left = $("<div style='display:flex;flex-direction:column;gap:10px'></div>").append(verdict_html, $frames);
		const $panel = $('<div class="dr-panel dr-notes-panel"></div>');
		$two.append($left, $panel);
		this.$root.empty().append($head, $two);

		$head.find("[data-back]").on("click", () => this.go([review.name, track.id]));
		$head.find("[data-screen]").on("click", (e) => this.go([review.name, track.id, option.option_code, $(e.currentTarget).attr("data-screen")]));
		$head.find("[data-step]").on("click", (e) => {
			const next = order[idx + Number($(e.currentTarget).attr("data-step"))];
			if (next) this.go([review.name, track.id, option.option_code, next]);
		});
		$head.find("[data-mode]").on("click", (e) => {
			this.mode = $(e.currentTarget).attr("data-mode");
			try { localStorage.setItem(DR.mode_key, this.mode); } catch (err) { /* private window */ }
			$head.find("[data-mode]").removeClass("active");
			$(e.currentTarget).addClass("active");
			(this.slots || []).forEach((slot) => slot.place());
			this.render_notes();
		});
		$left.find("[data-verdict]").on("click", async (e) => {
			await frappe.call({
				method: `${DR.api}.cast_verdict`,
				args: { review: review.name, option_code: option.option_code, screen_code, verdict: $(e.currentTarget).attr("data-verdict") },
			});
			this.refresh(review.name);
		});

		const ordered = frames.slice().sort((a, b) => (a.frame === "phone") - (b.frame === "phone"));
		await this.fetch_html(review, ordered);
		if (!this.current || this.current.screen_code !== screen_code || this.current.option !== option) return;
		this.slots = ordered.map((f) => this.mount_frame(review, $frames[0], f, true));
		this.relayout();
		this.render_notes();
	}

	relayout() {
		if (!this.slots || !this.slots.length) return;
		const $frames = this.$root.find(".dr-frames");
		// Borders (2px a frame) and the 16px gap come out first, or the pair overshoots and wraps.
		const avail = Math.max(320, ($frames.width() || 900) - 8);
		const phone = this.slots.find((s) => s.frame.frame === "phone");
		const wide = this.slots.filter((s) => s.frame.frame !== "phone");
		const phone_w = phone ? Math.min(phone.frame.width * 0.8, wide.length ? 320 : avail) : 0;
		const left = wide.length ? avail - (phone ? phone_w + 20 : 0) : 0;
		this.slots.forEach((slot) => {
			const f = slot.frame;
			let scale = f.frame === "phone" ? phone_w / f.width : left / f.width;
			if (wide.length && phone && left < 480) scale = Math.min(1, avail / f.width);
			scale = Math.min(scale, 1.1);
			slot.scale = scale;
			slot.el.style.width = Math.round(f.width * scale) + "px";
			slot.el.style.height = Math.round(f.height * scale) + "px";
			slot.iframe.style.transform = `scale(${scale})`;
			slot.place();
		});
	}

	html_key(frame) {
		return `${frame.name}:${frame.content_hash || ""}`;
	}

	async fetch_html(review, frames) {
		const need = frames.filter((f) => !(this.html_key(f) in this.html));
		if (!need.length) return;
		const r = await frappe.call({
			method: `${DR.api}.get_screens`,
			args: { review: review.name, names: JSON.stringify(need.map((f) => f.name)) },
		});
		const got = r.message || {};
		need.forEach((f) => { if (f.name in got) this.html[this.html_key(f)] = got[f.name]; });
	}

	frame_doc(review, html) {
		// default-src 'none': the screen can fetch nothing. The brand face is served by this site,
		// which is the frame's own origin under allow-same-origin.
		const csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'self' data:";
		return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">
			<style>@font-face{font-family:"Big Noodle Titling";src:url(${DR.font}) format("woff2");font-weight:700}
			html,body{margin:0;overflow:hidden;background:#f8f8f8}${review.stylesheet || ""}</style></head><body>${html}</body></html>`;
	}

	mount_thumb(review, el, frame) {
		const load = async () => {
			await this.fetch_html(review, [frame]);
			const scale = Math.min(280 / frame.width, 290 / frame.height);
			const box = document.createElement("div");
			box.style.cssText = `position:relative;overflow:hidden;width:${Math.round(frame.width * scale)}px;height:${Math.round(frame.height * scale)}px;border-radius:6px;`;
			const f = this.make_iframe(review, frame);
			f.style.transform = `scale(${scale})`;
			f.style.pointerEvents = "none";
			f.setAttribute("tabindex", "-1");
			f.setAttribute("aria-hidden", "true");
			box.appendChild(f);
			el.appendChild(box);
		};
		if ("IntersectionObserver" in window) {
			const io = new IntersectionObserver((entries) => {
				if (entries.some((e) => e.isIntersecting)) { io.disconnect(); load(); }
			});
			io.observe(el);
		} else load();
	}

	make_iframe(review, frame) {
		const f = document.createElement("iframe");
		f.setAttribute("sandbox", "allow-same-origin"); // never allow-scripts: see the header comment
		f.setAttribute("referrerpolicy", "no-referrer");
		f.setAttribute("title", `${frame.option_code} ${frame.screen_name} (${frame.frame})`);
		f.style.cssText = `position:absolute;left:0;top:0;border:0;transform-origin:0 0;width:${frame.width}px;height:${frame.height}px;`;
		f.width = frame.width;
		f.height = frame.height;
		f.srcdoc = this.frame_doc(review, this.html[this.html_key(frame)] || "");
		return f;
	}

	mount_frame(review, parent, frame) {
		const el = document.createElement("div");
		el.className = "dr-slot";
		const iframe = this.make_iframe(review, frame);
		const pins = document.createElement("div");
		pins.className = "dr-pins";
		el.appendChild(iframe);
		el.appendChild(pins);
		parent.appendChild(el);
		const parts = review.parts[`${frame.track}:${frame.screen_code}`] || [];
		const number_of = (name) => (parts.find((p) => p[1] === name) || [])[0];
		const slot = { el, iframe, frame, scale: 1, place: () => {} };
		slot.place = () => {
			const doc = iframe.contentDocument;
			pins.innerHTML = "";
			if (!doc || !doc.body) return;
			const seen = {};
			const W = frame.width;
			const H = frame.height;
			doc.querySelectorAll("[data-c]").forEach((node) => {
				const name = node.getAttribute("data-c");
				const number = number_of(name);
				if (!number || seen[name]) return;
				const pin_code = `${frame.option_code}-${frame.screen_code}-E${String(number).padStart(2, "0")}`;
				const noted = (this.current ? this.current.review.notes : []).some((n) => n.code === pin_code);
				const chosen = this.selected && this.selected.code === pin_code;
				// Clicking through, the design is the point: a pin shows only where a note already is.
				if (this.mode === "click" && !noted && !chosen) return;
				const r = node.getBoundingClientRect();
				// Parts below the frame's drawn height are not shown, so neither is their pin (spike finding).
				if (r.width < 2 || r.height < 2 || r.top >= H - 4 || r.left >= W - 4 || r.bottom <= 0) return;
				seen[name] = 1;
				const code = `${frame.option_code}-${frame.screen_code}-E${String(number).padStart(2, "0")}`;
				const count = (this.current ? this.current.review.notes : []).filter((n) => n.code === code).length;
				const b = document.createElement("button");
				b.className = "dr-pin" + (count ? " has-notes" : "") + (this.selected && this.selected.code === code ? " is-on" : "");
				b.textContent = `E${String(number).padStart(2, "0")}${count ? " · " + count : ""}`;
				b.title = `${code} ${name}`;
				b.setAttribute("aria-label", __("Part {0}, {1}", [code, name]));
				b.style.left = `${Math.max(0, r.left) * slot.scale}px`;
				b.style.top = `${Math.max(0, r.top) * slot.scale}px`;
				b.addEventListener("click", () => this.select_part(code, name, frame));
				pins.appendChild(b);
				if (this.selected && this.selected.code === code) {
					const hl = document.createElement("div");
					hl.className = "dr-hl";
					hl.style.cssText = `left:${r.left * slot.scale}px;top:${r.top * slot.scale}px;width:${r.width * slot.scale}px;height:${r.height * slot.scale}px`;
					pins.appendChild(hl);
				}
			});
		};
		iframe.addEventListener("load", () => {
			const doc = iframe.contentDocument;
			if (!doc) return;
			this.decorate(doc, frame);
			slot.place();
			if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(() => slot.place());
			// Listeners registered by this page on the frame's document. The frame itself runs nothing.
			doc.addEventListener("mouseover", (e) => {
				doc.querySelectorAll(".dr-hot").forEach((h) => h.classList.remove("dr-hot"));
				if (this.mode !== "click") return;
				const hot = e.target.closest ? e.target.closest("[data-nav]") : null;
				if (hot) hot.classList.add("dr-hot");
			});
			doc.addEventListener(
				"click",
				(e) => {
					e.preventDefault();
					const part = e.target.closest ? e.target.closest("[data-c]") : null;
					const name = part ? part.getAttribute("data-c") : null;
					const number = name ? number_of(name) : null;
					const code = number ? `${frame.option_code}-${frame.screen_code}-E${String(number).padStart(2, "0")}` : null;
					if (this.mode === "annotate") {
						if (code) this.select_part(code, name, frame);
						return;
					}
					this.navigate_from(e.target, doc, frame, code, name);
				},
				true
			);
		});
		return slot;
	}

	// ------------------------------------------------------------------ click-through
	//
	// The review's flow rules (written by the concept generator, stored on the Design Review)
	// say where a click goes. Targets: "S04" (a screen of this track), "learner:S01" (another
	// track, the option in the same position), "next" (the story's next screen, else the next in
	// order), "up" (the screen's own back target, else browser Back), "done:<message>" (shown, no
	// move). Ported from the claude.ai Concept Viewer so a review tells the same story here:
	// rules are resolved once per screen and written onto the frame's elements as data-nav, the
	// way its markHotspots did, so hover shows what is clickable before anyone clicks.
	//
	//   flow.text[track]             [[pattern, target, screens?, options?]] on a button's or link's text
	//   flow.icon                    [[pattern, target, track?]] on an icon button's aria-label
	//   flow.aria                    [[pattern, target, track?]] on any element's aria-label
	//   flow.disabled[track][part]   target for a disabled control inside that part
	//   flow.nav[track][part]        target | {screen: target, "*": target} | [[pattern, target]]
	//   flow.next / backto / chain   the story's order, each screen's back target, cross-track hops
	//   flow.dom                     selectors: icon, step, step_label, noinherit, inert; bars: part names

	decorate(doc, frame) {
		const flow = this.current.review.flow || {};
		const dom = Object.assign(
			{ icon: ".ux-iconbtn", step: ".ux-step", step_label: "span:not(.ux-stepnum):not(.ux-badge)", noinherit: "", inert: "", bars: [] },
			flow.dom || {}
		);
		const style = doc.createElement("style");
		style.textContent = ".dr-hot{outline:3px solid #00a0df !important;outline-offset:-3px}[data-nav]{cursor:pointer}";
		doc.head.appendChild(style);
		const inert = (n) => dom.inert && n.closest(dom.inert);
		doc.querySelectorAll("a,button,[data-c]").forEach((n) => {
			if (inert(n)) return;
			const tg = this.target_of(n, frame, flow, dom);
			if (tg) n.setAttribute(this.is_here(tg, frame) ? "data-here" : "data-nav", tg);
		});
		// An unmapped main button carries its bar's target; secondary ones do not.
		doc.querySelectorAll("a,button").forEach((c) => {
			if (c.hasAttribute("data-nav") || c.hasAttribute("data-here") || inert(c)) return;
			if (dom.noinherit && c.matches(dom.noinherit)) return;
			const up = c.parentElement && c.parentElement.closest("[data-nav]");
			if (up) c.setAttribute("data-nav", up.getAttribute("data-nav"));
		});
		// A bar hands its target only to its main button: its padding and status text are not a door.
		(dom.bars || []).forEach((bar) => {
			doc.querySelectorAll(`[data-c="${bar}"]`).forEach((el) => {
				if (el.querySelector("a,button")) el.removeAttribute("data-nav");
			});
		});
	}

	target_of(n, frame, flow, dom) {
		const t = frame.track;
		const sid = frame.screen_code;
		const norm = (el) => (el.textContent || "").replace(/\s+/g, " ").trim().toUpperCase();
		const label = n.getAttribute("aria-label") || "";
		if (dom.icon && n.matches(dom.icon)) {
			for (const [pattern, tg, only] of flow.icon || []) {
				if ((!only || only === t) && this.matches(pattern, label)) return tg;
			}
			return null;
		}
		if (label) {
			for (const [pattern, tg, only] of flow.aria || []) {
				if ((!only || only === t) && this.matches(pattern, label)) return tg;
			}
		}
		for (const [part, tg] of Object.entries((flow.disabled || {})[t] || {})) {
			if (n.classList.contains("is-disabled") && n.closest(`[data-c="${part}"]`)) return tg;
		}
		if (n.tagName === "A" || n.tagName === "BUTTON") {
			const lb = dom.step && n.matches(dom.step) ? n.querySelector(dom.step_label) : null;
			const txt = norm(lb || n);
			for (const [pattern, tg, screens, options] of (flow.text || {})[t] || []) {
				if (screens && !screens.includes(sid)) continue;
				if (options && !options.includes(frame.option_code)) continue;
				if (this.matches(pattern, txt)) return tg;
			}
		}
		const name = n.getAttribute("data-c");
		let rule = name ? ((flow.nav || {})[t] || {})[name] : null;
		if (Array.isArray(rule)) {
			const txt = norm(n);
			const hit = rule.find(([pattern]) => this.matches(pattern, txt));
			return hit ? hit[1] : null;
		}
		if (rule && typeof rule === "object") rule = Object.prototype.hasOwnProperty.call(rule, sid) ? rule[sid] : rule["*"];
		return rule || null;
	}

	is_here(target, frame) {
		if (/^(end|start)$/.test(target) || target.startsWith("done:")) return false;
		const r = this.resolve(target, frame);
		return !!r && r.track === frame.track && r.screen === frame.screen_code;
	}

	matches(pattern, text) {
		try {
			return new RegExp(pattern).test(text);
		} catch (e) {
			return false;
		}
	}

	navigate_from(target, doc, frame, code, name) {
		const ctl = target.closest ? target.closest("a,button") : null;
		const n = target.closest ? target.closest("[data-nav],[data-here]") : null;
		if (ctl && !ctl.hasAttribute("data-nav") && !ctl.hasAttribute("data-here")) {
			this.flash(doc);
			frappe.show_alert({ message: __("That control is not linked in this prototype. The outlined spots are."), indicator: "orange" });
			if (code) this.select_part(code, name, frame);
			return;
		}
		if (n) {
			if (!n.hasAttribute("data-nav")) {
				frappe.show_alert({ message: __("You are already on this screen."), indicator: "blue" });
				return;
			}
			this.follow(n.getAttribute("data-nav"), frame);
			return;
		}
		this.flash(doc);
		if (code) this.select_part(code, name, frame);
	}

	flash(doc) {
		doc.querySelectorAll("[data-nav]").forEach((n) => n.classList.add("dr-hot"));
		setTimeout(() => doc.querySelectorAll(".dr-hot").forEach((n) => n.classList.remove("dr-hot")), 900);
	}

	resolve(target, frame) {
		const { review, option } = this.current;
		const flow = review.flow || {};
		const track = frame.track;
		const sid = frame.screen_code;
		if (target === "up") {
			target = ((flow.backto || {})[track] || {})[sid];
			if (!target) return { special: "back" };
		}
		if (target === "next") target = ((flow.next || {})[track] || {})[sid] || "+1";
		if (target === "end") {
			const chain = (flow.chain || {})[`${track}:${sid}`];
			if (!chain) return { special: "end" };
			target = chain;
		}
		if (target === "+1") {
			const order = this.screen_order(review, option.option_code);
			const next = order[order.indexOf(sid) + 1];
			return next ? { track, screen: next } : { special: "end" };
		}
		if (target.includes(":")) {
			const [t, sc] = target.split(":");
			return { track: t, screen: sc };
		}
		return { track, screen: target };
	}

	follow(target, frame) {
		const { review, track, option } = this.current;
		if (typeof target !== "string") return false;
		if (target.startsWith("done:")) {
			frappe.show_alert({ message: target.slice(5), indicator: "blue" }, 6);
			return true;
		}
		const r = this.resolve(target, frame);
		if (r.special === "back") {
			window.history.back();
			return true;
		}
		if (r.special === "end") {
			frappe.show_alert({ message: __("That is the end of this option's story. Pick another option, or another track."), indicator: "blue" });
			return true;
		}
		if (r.track === track.id) {
			if (!this.screen_order(review, option.option_code).includes(r.screen)) return false;
			this.go([review.name, track.id, option.option_code, r.screen]);
			return true;
		}
		// Another track: the option in the same position there (L3 -> C3), else its first.
		const here = review.options.filter((o) => o.track === track.id).map((o) => o.option_code);
		const there = review.options.filter((o) => o.track === r.track);
		if (!there.length) return false;
		const pick = there[here.indexOf(option.option_code)] || there[0];
		this.go([review.name, r.track, pick.option_code, r.screen]);
		return true;
	}

	// ------------------------------------------------------------------ notes

	select_part(code, name, frame) {
		this.selected = { code, name, screen: `${frame.option_code}-${frame.screen_code}` };
		(this.slots || []).forEach((s) => s.place());
		this.render_notes();
	}

	render_notes() {
		const $p = this.$root.find(".dr-notes-panel");
		if (!$p.length || !this.current) return;
		const { review, option, screen_code } = this.current;
		const here = review.notes.filter((n) => n.option_code === option.option_code && n.screen_code === screen_code);
		const can_write = review.me.participant && review.status === "Open";
		$p.empty();
		if (this.selected) {
			const sel = this.selected;
			const mine = here.filter((n) => n.code === sel.code);
			const number = sel.code.split("-E")[1];
			const elsewhere = review.notes.filter((n) => n.option_code !== option.option_code && n.screen_code === screen_code && String(n.part_number).padStart(2, "0") === number);
			$p.append(`<div><span class="dr-code">${sel.code}</span> <b>${frappe.utils.escape_html(sel.name)}</b></div>`);
			if (elsewhere.length) $p.append(`<div class="dr-small dr-muted">${__("{0} notes on this part in other options", [elsewhere.length])}</div>`);
			mine.forEach((n) => $p.append(this.note_el(review, n)));
			if (!mine.length) $p.append(`<div class="dr-small dr-muted">${__("No notes on this part yet.")}</div>`);
			if (can_write) this.note_form($p, review, sel.code);
			$p.append(`<button class="btn btn-xs btn-link" data-clear>${__("Show every note on this screen")}</button>`);
			$p.find("[data-clear]").on("click", () => { this.selected = null; (this.slots || []).forEach((s) => s.place()); this.render_notes(); });
			return;
		}
		$p.append(`<h5>${__("Notes on this screen")}</h5>`);
		$p.append(`<div class="dr-small dr-muted">${this.mode === "annotate" ? __("Click a part of the screen, or a numbered pin, to read and write its notes.") : __("Clicks move through the screens; outlined spots are linked. Switch to Pick parts to number every part and write a note on one.")}</div>`);
		if (!here.length) $p.append(`<div class="dr-small dr-muted">${__("None yet.")}</div>`);
		here.forEach((n) => $p.append(this.note_el(review, n, true)));
	}

	note_el(review, n, show_code) {
		const $n = $(`<div class="dr-note ${n.is_imported ? "is-imported" : ""}">
			<div class="dr-note-meta"><span>${show_code ? `<span class="dr-code">${n.code}</span> · ` : ""}${frappe.utils.escape_html(n.author_name || "")}${n.raised_by_name ? " " + __("for {0}", [frappe.utils.escape_html(n.raised_by_name)]) : ""}${n.is_imported ? " · " + __("imported") : ""}</span>
			<span>${frappe.utils.escape_html(__(n.status))}</span></div>
			<div>${frappe.utils.escape_html(n.text)}</div></div>`);
		if (review.me.moderator && !n.promoted_request) {
			const $s = $(`<select class="form-control input-xs" style="max-width:140px;height:26px;padding:0 6px;font-size:12px"></select>`);
			["Open", "Accepted", "Rejected", "Done"].forEach((s) => $s.append(`<option value="${s}" ${s === n.status ? "selected" : ""}>${__(s)}</option>`));
			$s.on("change", async () => {
				await frappe.call({ method: `${DR.api}.set_note_status`, args: { note: n.name, status: $s.val() } });
				this.refresh(review.name);
			});
			$n.find(".dr-note-meta span").last().replaceWith($s);
		}
		if (n.mine && n.status === "Open" && review.status === "Open") {
			$(`<button class="btn btn-xs btn-link" style="align-self:flex-start;padding:0">${__("Delete")}</button>`)
				.appendTo($n)
				.on("click", async () => {
					await frappe.call({ method: `${DR.api}.delete_note`, args: { note: n.name } });
					this.refresh(review.name);
				});
		}
		return $n;
	}

	note_form($p, review, code) {
		const $f = $(`<div style="display:flex;flex-direction:column;gap:6px"><label class="dr-small"><b>${__("Add a note on {0}", [code])}</b></label>
			<textarea class="form-control" rows="3" maxlength="2000" placeholder="${__("What should change about this part?")}"></textarea>
			<div class="dr-raised"></div>
			<div><button class="btn btn-primary btn-sm">${__("Add note")}</button></div></div>`);
		$p.append($f);
		const raised = frappe.ui.form.make_control({
			parent: $f.find(".dr-raised"),
			df: { fieldname: "raised_by", fieldtype: "Link", options: "Employee", label: __("Raised by (optional, someone in the meeting)") },
			render_input: true,
		});
		$f.find("button").on("click", async () => {
			const text = ($f.find("textarea").val() || "").trim();
			if (!text) return;
			await frappe.call({ method: `${DR.api}.add_note`, args: { review: review.name, code, text, raised_by: raised.get_value() || "" }, freeze: true });
			frappe.show_alert({ message: __("Note added"), indicator: "green" });
			this.refresh(review.name);
		});
	}

	// ------------------------------------------------------------------ moderator dialogs

	decision_dialog(review, track) {
		const options = review.options.filter((o) => o.track === track.id);
		const notes = review.notes.filter((n) => n.track === track.id && n.status === "Accepted" && !n.promoted_request);
		const d = new frappe.ui.Dialog({
			title: __("Record a decision"),
			fields: [
				{ fieldname: "title", fieldtype: "Data", label: __("Title"), reqd: 1 },
				{ fieldname: "option_code", fieldtype: "Select", label: __("Option chosen (optional)"), options: [""].concat(options.map((o) => o.option_code)) },
				{ fieldname: "decision", fieldtype: "Small Text", label: __("What was decided"), reqd: 1 },
				{
					fieldname: "notes",
					fieldtype: "MultiCheck",
					label: __("Accepted notes it carries forward"),
					options: notes.map((n) => ({ label: `${n.code} · ${n.text.slice(0, 80)}`, value: n.name })),
					columns: 1,
				},
			],
			primary_action_label: __("Record"),
			primary_action: async (values) => {
				await frappe.call({
					method: `${DR.api}.record_decision`,
					args: { review: review.name, title: values.title, decision: values.decision, track: track.id, option_code: values.option_code || "", notes: JSON.stringify(values.notes || []) },
					freeze: true,
				});
				d.hide();
				this.refresh(review.name);
			},
		});
		d.show();
	}

	promote_dialog(review, decision) {
		const d = new frappe.ui.Dialog({
			title: __("Promote to an enhancement request"),
			fields: [
				{ fieldtype: "HTML", options: `<p class="text-muted small">${__("Files {0} as an enhancement request that is already approved, with you as the approver, and starts its breakdown.", [frappe.utils.escape_html(decision.title)])}</p>` },
				{ fieldname: "target_erpnext", fieldtype: "Check", label: __("Plan it on the ERPNext board"), default: 1 },
				{ fieldname: "target_triton", fieldtype: "Check", label: __("Plan it on the Triton board") },
				{ fieldname: "request_type", fieldtype: "Select", label: __("Type"), options: ["Feature", "Bug"], default: "Feature" },
				{ fieldname: "impact", fieldtype: "Select", label: __("Impact"), options: ["Blocking my work", "Painful but I can work around it", "Nice to have"], default: "Nice to have" },
			],
			primary_action_label: __("Promote"),
			primary_action: async (values) => {
				const r = await frappe.call({
					method: `${DR.api}.promote_decision`,
					args: { decision: decision.name, target_erpnext: values.target_erpnext ? 1 : 0, target_triton: values.target_triton ? 1 : 0, request_type: values.request_type, impact: values.impact },
					freeze: true,
				});
				d.hide();
				frappe.show_alert({ message: __("Filed {0}", [r.message.request]), indicator: "green" }, 6);
				this.refresh(review.name);
			},
		});
		d.show();
	}

	import_dialog(review_name) {
		new frappe.ui.FileUploader({
			folder: "Home",
			make_attachments_public: false,
			restrictions: { allowed_file_types: [".json"] },
			on_success: async (file_doc) => {
				const r = await frappe.call({ method: `${DR.api}.import_review`, args: { file_name: file_doc.name, review: review_name || "" }, freeze: true, freeze_message: __("Importing…") });
				const report = r.message || {};
				frappe.msgprint({
					title: __("Imported"),
					message: __("{0}, revision {1}: {2} options, {3} screens, {4} new parts.", [report.review, report.revision, report.options, report.screens, report.parts_added]),
					indicator: "green",
				});
				this.reviews = {};
				this.go([report.review]);
			},
		});
	}
}
