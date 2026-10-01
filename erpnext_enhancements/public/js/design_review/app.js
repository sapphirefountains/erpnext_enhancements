/**
 * The Review Room: design reviews at /review (WI-079 slice 5, ADR 0016 §2).
 *
 *   /review                                   the reviews you can open
 *   /review/<review>/<track>                  overview: options, your ranking, the tally, decisions
 *   /review/<review>/<track>/<option>/<screen> the viewer: one option's screen, notes, verdicts
 *   /review/<review>/<track>/all/<screen>     every option on that screen side by side
 *
 * Every view is a URL, so Back and Forward step through it (Nik's rule: never break
 * Back/Forward). The page moves with history.pushState and re-renders on popstate.
 *
 * The layout is the claude.ai Concept Viewer's, which the team reviewed the training concepts in:
 * a screen rail grouped by stage, the stage with desktop and phone frames, a notes panel, and the
 * MARKUP, LINKS and NEXT controls. What changed is underneath: screens render in sandboxed,
 * policy-locked frames (frames.js) and everything is stored in ERPNext through
 * api/design_review.py, which checks participation and status on every write.
 */

import { CallError, M, call, upload } from "./transport.js";
import { append, btn, clear, el, lsGet, lsSet } from "./dom.js";
import { decorate, makeFrame, resolve } from "./frames.js";

const VERDICTS = ["Yes", "Maybe", "No"];
const STATUSES = ["Draft", "Open", "Closed", "Decided"];
const NOTE_STATUSES = ["Open", "Accepted", "Rejected", "Done"];
const narrow = () => (window.matchMedia ? window.matchMedia("(max-width:900px)").matches : window.innerWidth <= 900);
const pad2 = (n) => String(n).padStart(2, "0");

export class ReviewApp {
	constructor(root, boot) {
		this.root = root;
		this.boot = boot || {};
		this.reviews = {}; // name -> {state, content}
		this.mem = {}; // `${review}:${track}` -> {option, screen}
		this.sel = null; // {code, name, key}
		this.say = "";
		this.ui = {
			markup: lsGet("rr.markup") === "1",
			links: false,
			// A phone shows the phone drawing by default; both at once would be too small to read.
			frame: lsGet("rr.frame") || (narrow() ? "phone" : "both"),
			rail: !narrow(),
			panel: !narrow(),
			ptab: "note",
		};
		this.applyTheme(lsGet("rr.theme") || "system");
	}

	mount() {
		window.addEventListener("popstate", () => this.route());
		window.addEventListener("resize", () => {
			clearTimeout(this._rz);
			this._rz = setTimeout(() => this.fitFrames(), 120);
		});
		document.addEventListener("keydown", (e) => this.onKey(e));
		this.route();
	}

	// ---------------------------------------------------------------- routing

	parts() {
		return location.pathname.split("/").filter(Boolean).slice(1).map((p) => decodeURIComponent(p));
	}

	go(parts, replace) {
		const url = "/review" + (parts.length ? "/" + parts.map((p) => encodeURIComponent(p)).join("/") : "");
		if (url === location.pathname) return this.route();
		history[replace ? "replaceState" : "pushState"](null, "", url);
		this.route();
	}

	async route() {
		const [name, track, option, screen] = this.parts();
		if (!name) return this.showList();
		const r = await this.load(name);
		if (!r) return;
		const { state, content } = r;
		document.title = `${state.title} · Design Review`;
		if (!content.tracks) return this.showEmpty(state);
		const t = content.tracks.find((x) => x.id === track);
		if (!t) return this.go([name, (content.tracks.find((x) => x.votable) || content.tracks[0]).id], true);
		if (!option) return this.showOverview(state, content, t);
		if (option === "all") {
			const order = this.trackOrder(content, t);
			return this.showViewer(state, content, t, null, order.includes(screen) ? screen : order[0]);
		}
		const opt = content.options.find((o) => o.code === option && o.track === t.id);
		if (!opt) return this.go([name, t.id], true);
		const order = this.order(content, opt.code);
		if (!screen || !order.includes(screen)) return this.go([name, t.id, opt.code, order[0]], true);
		return this.showViewer(state, content, t, opt, screen);
	}

	async load(name, refresh) {
		const have = this.reviews[name];
		try {
			const state = await call(M.REVIEW, { review: name });
			let content = have && have.content && have.state.content_hash === state.content_hash ? have.content : null;
			if (!content && !refresh) this.loading();
			if (!content) content = state.has_content ? await call(M.CONTENT, { review: name }) : {};
			this.reviews[name] = { state, content };
			return this.reviews[name];
		} catch (e) {
			this.fail(e instanceof CallError ? e.message : "This review could not be loaded.");
			return null;
		}
	}

	async refresh() {
		const [name] = this.parts();
		if (!name) return;
		await this.load(name, true);
		const r = this.reviews[name];
		if (this.viewer && r) {
			this.viewer.state = r.state;
			this.renderRail();
			this.renderCap();
			this.renderPanel();
			this.markFrames();
		} else {
			this.route();
		}
	}

	// ---------------------------------------------------------------- order and lookups

	trackOrder(content, track) {
		const drawn = [];
		content.screens.filter((s) => s.track === track.id).forEach((s) => drawn.includes(s.screen) || drawn.push(s.screen));
		const staged = [];
		(track.groups || []).forEach((g) => (g.screens || []).forEach((sc) => drawn.includes(sc) && !staged.includes(sc) && staged.push(sc)));
		return staged.concat(drawn.filter((sc) => !staged.includes(sc)));
	}

	order(content, optionCode) {
		const opt = content.options.find((o) => o.code === optionCode);
		const track = content.tracks.find((t) => t.id === opt.track);
		const mine = new Set(content.screens.filter((s) => s.option === optionCode).map((s) => s.screen));
		return this.trackOrder(content, track).filter((sc) => mine.has(sc));
	}

	framesOf(content, optionCode, screenCode) {
		return content.screens.filter((s) => s.option === optionCode && s.screen === screenCode);
	}

	screenName(content, track, screenCode) {
		const s = content.screens.find((x) => x.track === track && x.screen === screenCode);
		return s ? s.name : screenCode;
	}

	stageOf(track, screenCode) {
		const g = (track.groups || []).find((x) => (x.screens || []).includes(screenCode));
		return g ? g.name : "";
	}

	partsOf(content, track, screenCode) {
		return (content.parts || {})[`${track}:${screenCode}`] || [];
	}

	// ---------------------------------------------------------------- chrome

	bar(left, right) {
		const b = el("header", "rr-bar");
		append(b, el("span", "rr-mark dsp", "SAPPHIRE"), el("span", "rr-bar-sub", "DESIGN REVIEW"));
		left.forEach((n) => n && b.appendChild(n));
		b.appendChild(el("span", "rr-spacer"));
		right.forEach((n) => n && b.appendChild(n));
		this.status = el("span", "rr-status", "");
		this.status.setAttribute("role", "status");
		b.appendChild(this.status);
		return b;
	}

	setStatus(text, bad) {
		if (!this.status) return;
		this.status.textContent = text || "";
		this.status.classList.toggle("bad", !!bad);
	}

	toast(text, bad) {
		let t = document.getElementById("rr-toast");
		if (!t) {
			t = el("div", "rr-toast");
			t.id = "rr-toast";
			t.setAttribute("role", "status");
			document.body.appendChild(t);
		}
		t.textContent = text;
		t.classList.toggle("bad", !!bad);
		t.classList.add("on");
		clearTimeout(this._toast);
		this._toast = setTimeout(() => t.classList.remove("on"), Math.max(2200, text.length * 55));
	}

	loading() {
		clear(this.root);
		document.body.classList.remove("rr-viewing");
		append(this.root, el("div", "rr-boot", "Loading the review…"));
	}

	fail(message) {
		this.viewer = null;
		clear(this.root);
		document.body.classList.remove("rr-viewing");
		const box = el("div", "rr-empty");
		append(box, el("h1", "dsp", "Not available"), el("p", null, message));
		const back = el("a", "rr-btn", "ALL REVIEWS");
		back.href = "/review";
		box.appendChild(back);
		this.root.appendChild(box);
	}

	themeButton() {
		const mode = lsGet("rr.theme") || "system";
		const label = { system: "THEME: AUTO", light: "THEME: LIGHT", dark: "THEME: DARK" }[mode];
		return btn("rr-toggle", label, () => {
			const next = { system: "light", light: "dark", dark: "system" }[mode];
			lsSet("rr.theme", next);
			this.applyTheme(next);
			this.route();
		});
	}

	applyTheme(mode) {
		if (mode === "system") document.documentElement.removeAttribute("data-rr-theme");
		else document.documentElement.setAttribute("data-rr-theme", mode);
	}

	async call(method, args, okText) {
		this.setStatus("SAVING");
		try {
			const out = await call(method, args);
			this.setStatus(okText || "SAVED");
			return out;
		} catch (e) {
			this.setStatus("NOT SAVED", true);
			this.toast(e.message || "That was refused.", true);
			throw e;
		}
	}

	// ---------------------------------------------------------------- list

	async showList() {
		this.viewer = null;
		document.title = "Design Review";
		this.loading();
		let data;
		try {
			data = await call(M.LIST, {});
		} catch (e) {
			return this.fail(e.message);
		}
		clear(this.root);
		document.body.classList.remove("rr-viewing");
		const right = [this.themeButton()];
		if (data.me.moderator) right.unshift(this.importButton(null, "IMPORT A REVIEW"));
		this.root.appendChild(this.bar([], right));
		const page = el("main", "rr-page");
		append(page, el("h1", "dsp rr-title", "Design reviews"));
		if (!data.reviews.length) {
			page.appendChild(el("p", "rr-muted", data.me.moderator ? "No reviews yet. Import a bundle to start one." : "No design reviews include you yet."));
		}
		const grid = el("div", "rr-cards");
		data.reviews.forEach((r) => {
			const card = el("a", "rr-card rr-review-card");
			card.href = `/review/${encodeURIComponent(r.name)}`;
			card.addEventListener("click", (e) => {
				e.preventDefault();
				this.go([r.name]);
			});
			append(
				card,
				this.pill(r.status),
				el("h2", null, r.title),
				el("p", "rr-muted", r.description || ""),
				el("span", "rr-small rr-muted", r.participant ? "You are a participant" : "Moderator view")
			);
			grid.appendChild(card);
		});
		page.appendChild(grid);
		this.root.appendChild(page);
	}

	pill(status) {
		return el("span", `rr-pill is-${String(status || "Draft").toLowerCase()}`, status || "Draft");
	}

	showEmpty(state) {
		this.viewer = null;
		clear(this.root);
		document.body.classList.remove("rr-viewing");
		const right = [this.themeButton()];
		if (state.me.moderator) right.unshift(this.importButton(state.name, "IMPORT THE BUNDLE"));
		this.root.appendChild(this.bar([el("span", "rr-bar-title", state.title)], right));
		const page = el("main", "rr-page");
		append(page, el("h1", "dsp rr-title", state.title), el("p", "rr-muted", "This review has no screens yet. A System Manager imports its bundle."));
		this.root.appendChild(page);
	}

	importButton(review, label) {
		const input = el("input");
		input.type = "file";
		input.accept = ".json,application/json";
		input.hidden = true;
		input.addEventListener("change", async () => {
			const file = input.files && input.files[0];
			input.value = "";
			if (!file) return;
			try {
				this.setStatus("UPLOADING");
				const doc = await upload(file);
				this.setStatus("CHECKING");
				const check = await call(M.CHECK, { file_name: doc.name, review: review || "" });
				if (!check.ok) {
					this.setStatus("NOT IMPORTED", true);
					return this.toast(check.error, true);
				}
				const dropped = Object.keys(check.sanitizer_dropped || {}).length;
				const msg =
					`${check.options} options, ${check.screens} screens, ${check.parts_added} new parts` +
					(dropped ? `. The sanitizer will remove: ${Object.keys(check.sanitizer_dropped).join(", ")}` : "") +
					". Import it?";
				if (!window.confirm(msg)) return this.setStatus("");
				const report = await this.call(M.IMPORT, { file_name: doc.name, review: review || "" }, "IMPORTED");
				delete this.reviews[report.review];
				this.toast(`Imported revision ${report.revision}: ${report.screens} screens.`);
				this.go([report.review]);
			} catch (e) {
				this.setStatus("NOT IMPORTED", true);
				this.toast(e.message || "The import failed.", true);
			}
		});
		const b = btn("rr-toggle", label, () => input.click());
		const wrap = el("span");
		append(wrap, b, input);
		return wrap;
	}

	// ---------------------------------------------------------------- overview

	showOverview(state, content, track) {
		this.viewer = null;
		clear(this.root);
		document.body.classList.remove("rr-viewing");
		const tabs = el("span", "rr-seg");
		content.tracks.forEach((t) => tabs.appendChild(btn("", t.label, () => this.go([state.name, t.id]), { "aria-pressed": t.id === track.id ? "true" : "false" })));
		const right = [this.themeButton()];
		if (state.me.moderator) {
			right.unshift(this.importButton(state.name, "IMPORT A REVISION"));
			const desk = el("a", "rr-toggle", "PARTICIPANTS");
			desk.href = `/desk/design-review/${encodeURIComponent(state.name)}`;
			right.unshift(desk);
		}
		const all = el("a", "rr-toggle", "ALL REVIEWS");
		all.href = "/review";
		right.unshift(all);
		this.root.appendChild(this.bar([tabs], right));

		const page = el("main", "rr-page");
		const head = el("div", "rr-head");
		const titleBox = el("div");
		append(titleBox, el("h1", "dsp rr-title", state.title), el("p", "rr-muted", track.blurb || state.description || ""));
		head.appendChild(titleBox);
		head.appendChild(this.statusControl(state));
		page.appendChild(head);
		page.appendChild(this.notice(state));

		const options = content.options.filter((o) => o.track === track.id);
		const grid = el("div", "rr-cards");
		options.forEach((o) => {
			const order = this.order(content, o.code);
			const card = el("article", "rr-card");
			const thumb = el("button", "rr-thumb");
			thumb.type = "button";
			thumb.setAttribute("aria-label", `Open ${o.code} ${o.name}`);
			thumb.addEventListener("click", () => this.go([state.name, track.id, o.code, order[0]]));
			const first = this.framesOf(content, o.code, order[0]);
			const shot = first.find((f) => f.frame === "phone") || first[0];
			if (shot) this.thumbnail(thumb, content, shot, 300, 330);
			const body = el("div", "rr-card-body");
			const notes = state.notes.filter((n) => n.option_code === o.code).length;
			append(
				body,
				el("span", "rr-code", o.code),
				el("h2", null, o.name),
				el("p", null, o.what || ""),
				o.tradeoff ? el("p", "rr-muted", `Trade-off: ${o.tradeoff}`) : null,
				el("span", "rr-small rr-muted", `${order.length} screens${notes ? ` · ${notes} notes` : ""}`)
			);
			append(card, thumb, body);
			grid.appendChild(card);
		});
		page.appendChild(grid);

		const two = el("div", "rr-two");
		const left = el("div", "rr-col");
		const rightCol = el("div", "rr-col");
		left.appendChild(this.decisionsPanel(state, content, track));
		if (track.votable) {
			if (state.me.participant && state.status === "Open") rightCol.appendChild(this.rankPanel(state, content, track, options));
			rightCol.appendChild(this.tallyPanel(state, track, options));
		}
		append(two, left, rightCol);
		page.appendChild(two);
		this.root.appendChild(page);
	}

	notice(state) {
		const n = el("p", "rr-notice");
		if (!state.me.participant) n.textContent = state.me.moderator ? "You are moderating. Only participants rank and write notes; add yourself on the participant list to take part." : "";
		else if (state.status !== "Open") n.textContent = `This review is ${state.status}. Rankings, verdicts and notes open while it is Open.`;
		else n.textContent = "Rank the options below, then open any option to walk its screens, give verdicts and pin notes.";
		return n;
	}

	statusControl(state) {
		const box = el("div", "rr-statusbox");
		box.appendChild(this.pill(state.status));
		if (!state.me.moderator) return box;
		const select = el("select", "rr-select");
		select.setAttribute("aria-label", "Review status");
		STATUSES.forEach((s) => {
			const o = el("option", null, s);
			o.value = s;
			o.selected = s === state.status;
			select.appendChild(o);
		});
		select.addEventListener("change", async () => {
			try {
				await this.call(M.STATUS, { review: state.name, status: select.value });
				await this.refresh();
			} catch (e) {
				select.value = state.status;
			}
		});
		box.appendChild(select);
		return box;
	}

	thumbnail(host, content, screen, maxW, maxH) {
		const scale = Math.min(maxW / screen.w, maxH / screen.h);
		const box = el("div", "rr-thumbbox");
		box.style.width = Math.round(screen.w * scale) + "px";
		box.style.height = Math.round(screen.h * scale) + "px";
		const draw = () => {
			const f = makeFrame(content, screen, { inert: true });
			f.style.transform = `scale(${scale})`;
			box.appendChild(f);
		};
		host.appendChild(box);
		if ("IntersectionObserver" in window) {
			const io = new IntersectionObserver((entries) => {
				if (entries.some((e) => e.isIntersecting)) {
					io.disconnect();
					draw();
				}
			});
			io.observe(box);
		} else draw();
	}

	rankPanel(state, content, track, options) {
		const saved = (state.my_votes[track.id] || {}).ranking || [];
		const codes = options.map((o) => o.code);
		const order = saved.length === codes.length && saved.every((c) => codes.includes(c)) ? saved.slice() : codes.slice();
		const p = el("section", "rr-panel");
		append(p, el("h3", null, "Your ranking"), el("p", "rr-small rr-muted", "1 is your favourite. Saving again replaces it."));
		const list = el("ol", "rr-ranks");
		const status = el("span", "rr-small rr-muted");
		const draw = () => {
			clear(list);
			order.forEach((code, i) => {
				const o = options.find((x) => x.code === code);
				const li = el("li", "rr-rank");
				append(li, el("span", "rr-n", i + 1), el("span", "rr-rank-name", `${code} · ${o ? o.name : ""}`));
				li.appendChild(btn("rr-icon", "▲", () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(); }, { "aria-label": `Move ${code} up`, disabled: i === 0 }));
				li.appendChild(btn("rr-icon", "▼", () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(); }, { "aria-label": `Move ${code} down`, disabled: i === order.length - 1 }));
				list.appendChild(li);
			});
			status.textContent = saved.length && saved.join() === order.join() ? "Saved" : saved.length ? "Changed since you saved" : "Not saved yet";
		};
		draw();
		const save = btn("rr-btn", "SAVE MY RANKING", async () => {
			await this.call(M.VOTE, { review: state.name, track: track.id, ranking: JSON.stringify(order) });
			this.toast("Ranking saved");
			this.refresh();
		});
		const row = el("div", "rr-row");
		append(row, save, status);
		append(p, list, row);
		return p;
	}

	tallyPanel(state, track, options) {
		const t = state.tallies[track.id] || {};
		const p = el("section", "rr-panel");
		p.appendChild(el("h3", null, "Team tally"));
		const block = (label, data, first) => {
			const wrap = el("div", "rr-tally");
			if (!data || !data.voters) {
				wrap.appendChild(el("p", "rr-small rr-muted", `${label}: no rankings yet.`));
				return wrap;
			}
			wrap.appendChild(el("p", "rr-small", `${label}: ${data.voters} ranked. First place scores ${options.length}, last 1.`));
			const max = Math.max(1, ...Object.values(data.points));
			options
				.slice()
				.sort((a, b) => (data.points[b.code] || 0) - (data.points[a.code] || 0))
				.forEach((o) => {
					const row = el("div", "rr-bar-row");
					const track_ = el("div", "rr-bar-track");
					const fill = el("span");
					fill.style.width = Math.round((100 * (data.points[o.code] || 0)) / max) + "%";
					track_.appendChild(fill);
					append(row, el("span", null, `${o.code} · ${o.name}`), track_, el("b", null, data.points[o.code] || 0));
					wrap.appendChild(row);
					const who = first[o.code] || [];
					if (who.length) wrap.appendChild(el("p", "rr-small rr-muted rr-first", `First choice of ${who.join(", ")}`));
				});
			return wrap;
		};
		p.appendChild(block("This review", t.live, t.live_first || {}));
		if (t.imported && t.imported.voters) p.appendChild(block("Imported from the claude.ai ballot, counted apart", t.imported, t.imported_first || {}));
		return p;
	}

	decisionsPanel(state, content, track) {
		const p = el("section", "rr-panel");
		const head = el("div", "rr-row rr-spread");
		head.appendChild(el("h3", null, "Decisions"));
		if (state.me.moderator) head.appendChild(btn("rr-btn ghost small", "RECORD A DECISION", () => {
			form.hidden = !form.hidden;
		}));
		p.appendChild(head);
		const form = this.decisionForm(state, content, track);
		form.hidden = true;
		p.appendChild(form);
		const list = state.decisions.filter((d) => !d.track || d.track === track.id);
		if (!list.length) p.appendChild(el("p", "rr-small rr-muted", "Nothing decided yet."));
		list.forEach((d) => {
			const card = el("div", "rr-note");
			const meta = el("div", "rr-note-meta");
			append(meta, el("span", null, `${d.option_code ? d.option_code + " · " : ""}${d.status}`));
			if (d.promoted_request) {
				const a = el("a", null, d.promoted_request);
				a.href = `/desk/enhancement-request/${encodeURIComponent(d.promoted_request)}`;
				meta.appendChild(a);
			}
			append(card, meta, el("b", null, d.title), el("p", null, d.decision), d.notes.length ? el("span", "rr-small rr-muted", `Carries ${d.notes.length} notes`) : null);
			if (state.me.promoter && d.status !== "Promoted") card.appendChild(this.promoteForm(state, d));
			p.appendChild(card);
		});
		return p;
	}

	decisionForm(state, content, track) {
		const f = el("form", "rr-form");
		const title = this.field(f, "Title", el("input", "rr-fld"));
		const opt = el("select", "rr-fld");
		opt.appendChild(el("option", null, "No single option"));
		opt.firstChild.value = "";
		content.options.filter((o) => o.track === track.id).forEach((o) => {
			const x = el("option", null, `${o.code} · ${o.name}`);
			x.value = o.code;
			opt.appendChild(x);
		});
		this.field(f, "Option chosen", opt);
		const text = this.field(f, "What was decided", el("textarea", "rr-fld"));
		const accepted = state.notes.filter((n) => n.track === track.id && n.status === "Accepted" && !n.promoted_request);
		const boxes = [];
		if (accepted.length) {
			f.appendChild(el("span", "rr-lab", "Accepted notes it carries forward"));
			accepted.forEach((n) => {
				const l = el("label", "rr-check");
				const c = el("input");
				c.type = "checkbox";
				c.value = n.name;
				boxes.push(c);
				append(l, c, ` ${n.code} · ${n.text.slice(0, 90)}`);
				f.appendChild(l);
			});
		}
		const submit = btn("rr-btn", "RECORD", null);
		submit.type = "submit";
		f.appendChild(submit);
		f.addEventListener("submit", async (e) => {
			e.preventDefault();
			await this.call(M.DECIDE, {
				review: state.name, title: title.value, decision: text.value, track: track.id, option_code: opt.value,
				notes: JSON.stringify(boxes.filter((b) => b.checked).map((b) => b.value)),
			});
			this.refresh();
		});
		return f;
	}

	promoteForm(state, decision) {
		const wrap = el("details", "rr-promote");
		wrap.appendChild(el("summary", null, "Promote to an enhancement request"));
		const f = el("form", "rr-form");
		const mk = (label, checked) => {
			const l = el("label", "rr-check");
			const c = el("input");
			c.type = "checkbox";
			c.checked = checked;
			append(l, c, ` ${label}`);
			f.appendChild(l);
			return c;
		};
		f.appendChild(el("p", "rr-small rr-muted", "Files it already approved, with you as the approver, and starts its breakdown."));
		const erp = mk("Plan it on the ERPNext board", true);
		const tri = mk("Plan it on the Triton board", false);
		const type = el("select", "rr-fld");
		["Feature", "Bug"].forEach((v) => type.appendChild(Object.assign(el("option", null, v), { value: v })));
		this.field(f, "Type", type);
		const impact = el("select", "rr-fld");
		["Nice to have", "Painful but I can work around it", "Blocking my work"].forEach((v) => impact.appendChild(Object.assign(el("option", null, v), { value: v })));
		this.field(f, "Impact", impact);
		const go = btn("rr-btn", "PROMOTE", null);
		go.type = "submit";
		f.appendChild(go);
		f.addEventListener("submit", async (e) => {
			e.preventDefault();
			const out = await this.call(M.PROMOTE, {
				decision: decision.name, target_erpnext: erp.checked ? 1 : 0, target_triton: tri.checked ? 1 : 0,
				request_type: type.value, impact: impact.value,
			}, "FILED");
			this.toast(`Filed ${out.request}`);
			this.refresh();
		});
		wrap.appendChild(f);
		return wrap;
	}

	field(form, label, control) {
		const l = el("label", "rr-lab", label);
		const id = "rr-f-" + Math.random().toString(36).slice(2, 8);
		control.id = id;
		l.setAttribute("for", id);
		append(form, l, control);
		return control;
	}

	// ---------------------------------------------------------------- viewer

	showViewer(state, content, track, option, screen) {
		const compare = !option;
		const key = `${state.name}:${track.id}`;
		if (!compare) this.mem[key] = { option: option.code, screen };
		const prev = this.viewer;
		const here = compare ? `all:${screen}` : `${option.code}:${screen}`;
		if (!prev || prev.key !== here) {
			if (this.sel && (!prev || this.sel.screen !== here)) this.sel = null;
		}
		const say = this.say;
		this.say = "";
		this.viewer = { state, content, track, option, screen, compare, say, key: here, order: compare ? this.trackOrder(content, track) : this.order(content, option.code) };
		clear(this.root);
		document.body.classList.add("rr-viewing");
		document.body.classList.toggle("rr-is-markup", this.ui.markup);
		this.root.appendChild(this.viewerBar());
		const body = el("div", "rr-body");
		this.railEl = el("nav", "rr-rail");
		this.railEl.setAttribute("aria-label", "Screens");
		const stagewrap = el("div", "rr-stagewrap");
		this.capEl = el("div", "rr-cap");
		this.stageEl = el("div", "rr-stage");
		append(stagewrap, this.capEl, this.stageEl);
		this.panelEl = el("aside", "rr-panel-side");
		this.panelEl.setAttribute("aria-label", "Notes");
		append(body, this.railEl, stagewrap, this.panelEl);
		this.root.appendChild(body);
		this.renderRail();
		this.renderCap();
		this.renderPanel();
		this.renderStage();
	}

	viewerBar() {
		const v = this.viewer;
		const left = [];
		left.push(btn("rr-toggle", "SCREENS", () => { this.ui.rail = !this.ui.rail; this.applyPanes(); }, { "aria-pressed": this.ui.rail ? "true" : "false", "data-k": "rail" }));
		const seg = el("span", "rr-seg");
		v.content.tracks.forEach((t) => {
			seg.appendChild(btn("", t.short || t.label, () => this.switchTrack(t), { "aria-pressed": t.id === v.track.id ? "true" : "false", title: t.label }));
		});
		left.push(seg);
		const tabs = el("span", "rr-tabs");
		v.content.options.filter((o) => o.track === v.track.id).forEach((o) => {
			const b = btn("rr-tab", o.code, () => this.switchOption(o), { "aria-pressed": v.option && v.option.code === o.code ? "true" : "false", title: o.name });
			b.appendChild(el("small", null, ` ${o.name}`));
			tabs.appendChild(b);
		});
		if (v.content.options.filter((o) => o.track === v.track.id).length > 1) {
			tabs.appendChild(btn("rr-tab", "ALL", () => this.go([v.state.name, v.track.id, "all", v.screen]), { "aria-pressed": v.compare ? "true" : "false", title: "Every option on this screen" }));
		}
		left.push(tabs);
		const right = [];
		const overview = el("a", "rr-toggle", "OVERVIEW");
		overview.href = `/review/${encodeURIComponent(v.state.name)}/${encodeURIComponent(v.track.id)}`;
		overview.addEventListener("click", (e) => { e.preventDefault(); this.go([v.state.name, v.track.id]); });
		right.push(overview);
		right.push(btn("rr-toggle", "LINKS", () => { this.ui.links = !this.ui.links; this.route(); }, { "aria-pressed": this.ui.links ? "true" : "false", title: "Outline every clickable spot (key L)" }));
		const frames = new Set(v.compare ? [] : this.framesOf(v.content, v.option.code, v.screen).map((f) => f.frame));
		if (frames.has("desk") && frames.has("phone")) {
			const label = { both: "BOTH", desk: "DESKTOP", phone: "PHONE" }[this.ui.frame] || "BOTH";
			right.push(btn("rr-toggle", label, () => {
				this.ui.frame = { both: "desk", desk: "phone", phone: "both" }[this.ui.frame] || "both";
				lsSet("rr.frame", this.ui.frame);
				this.route();
			}, { title: "Desktop, phone or both" }));
		}
		right.push(btn("rr-toggle", "MARKUP", () => this.toggleMarkup(), { "aria-pressed": this.ui.markup ? "true" : "false", title: "Number every part to note it (key M)" }));
		right.push(btn("rr-toggle", "NOTES", () => { this.ui.panel = !this.ui.panel; this.applyPanes(); }, { "aria-pressed": this.ui.panel ? "true" : "false", "data-k": "panel" }));
		right.push(this.themeButton());
		return this.bar(left, right);
	}

	applyPanes() {
		this.railEl.classList.toggle("hide", !this.ui.rail);
		this.panelEl.classList.toggle("hide", !this.ui.panel);
		document.body.classList.toggle("rr-sheet", this.ui.panel && narrow());
		this.root.querySelectorAll("[data-k=rail]").forEach((b) => b.setAttribute("aria-pressed", this.ui.rail ? "true" : "false"));
		this.root.querySelectorAll("[data-k=panel]").forEach((b) => b.setAttribute("aria-pressed", this.ui.panel ? "true" : "false"));
		this.fitFrames();
	}

	switchTrack(t) {
		const v = this.viewer;
		const m = this.mem[`${v.state.name}:${t.id}`];
		if (m) return this.go([v.state.name, t.id, m.option, m.screen]);
		const here = v.content.options.filter((o) => o.track === v.track.id).map((o) => o.code);
		const there = v.content.options.filter((o) => o.track === t.id);
		const pick = (v.option && there[here.indexOf(v.option.code)]) || there[0];
		if (!pick) return this.go([v.state.name, t.id]);
		this.go([v.state.name, t.id, pick.code, this.order(v.content, pick.code)[0]]);
	}

	switchOption(o) {
		const v = this.viewer;
		const order = this.order(v.content, o.code);
		this.go([v.state.name, v.track.id, o.code, order.includes(v.screen) ? v.screen : order[0]]);
	}

	toggleMarkup() {
		this.ui.markup = !this.ui.markup;
		lsSet("rr.markup", this.ui.markup ? "1" : "0");
		document.body.classList.toggle("rr-is-markup", this.ui.markup);
		this.root.querySelectorAll(".rr-toggle").forEach((b) => b.textContent === "MARKUP" && b.setAttribute("aria-pressed", this.ui.markup ? "true" : "false"));
		if (this.ui.markup && !this.ui.panel) {
			this.ui.panel = true;
			this.applyPanes();
		}
		this.markFrames();
		this.renderPanel();
	}

	renderRail() {
		const v = this.viewer;
		const r = clear(this.railEl);
		r.classList.toggle("hide", !this.ui.rail);
		if (!v.compare) {
			r.appendChild(btn("rr-walk", "FROM THE START", () => this.go([v.state.name, v.track.id, v.option.code, v.order[0]])));
		}
		let group = null;
		v.order.forEach((sc) => {
			const g = this.stageOf(v.track, sc);
			if (g !== group) {
				group = g;
				if (g) r.appendChild(el("div", "rr-railgrp", g));
			}
			const row = btn("rr-srow", null, () => this.go([v.state.name, v.track.id, v.compare ? "all" : v.option.code, sc]), { "aria-pressed": sc === v.screen ? "true" : "false" });
			append(row, el("span", "rr-sc", sc), el("span", "rr-sn", this.screenName(v.content, v.track.id, sc)));
			if (!v.compare) {
				const mine = v.state.verdicts.mine[`${v.option.code}:${sc}`];
				if (mine) row.appendChild(el("span", `rr-vd is-${mine.toLowerCase()}`));
				const n = v.state.notes.filter((x) => x.option_code === v.option.code && x.screen_code === sc).length;
				if (n) row.appendChild(el("span", "rr-pip", n));
			}
			r.appendChild(row);
		});
		const foot = el("div", "rr-railfoot");
		foot.textContent = v.state.me.participant && v.state.status === "Open"
			? "Click through the screens. Turn on MARKUP to pin a note to any part; codes like L3-S04-E05 never change between revisions."
			: `This review is ${v.state.status}${v.state.me.participant ? "" : ", and you are not a participant"}: you can look and read the notes.`;
		r.appendChild(foot);
	}

	renderCap() {
		const v = this.viewer;
		const c = clear(this.capEl);
		const text = el("div", "rr-capt");
		const name = this.screenName(v.content, v.track.id, v.screen);
		if (v.compare) {
			append(text, el("h1", "dsp", `All options · ${v.screen} ${name}`), el("span", "rr-bet", `Every ${v.track.label} option on the same screen. Click one to open it.`));
		} else {
			append(text, el("h1", "dsp", `${v.option.code} ${v.option.name} · ${v.screen} ${name}`), el("span", "rr-bet", v.option.what || ""));
			if (v.option.tradeoff) text.appendChild(el("span", "rr-bet2", `Trade-off: ${v.option.tradeoff}`));
		}
		if (v.say) text.appendChild(el("span", "rr-say", v.say));
		c.appendChild(text);
		if (!v.compare && v.track.votable) {
			const can = v.state.me.participant && v.state.status === "Open";
			const mine = v.state.verdicts.mine[`${v.option.code}:${v.screen}`] || "";
			const counts = v.state.verdicts.live[`${v.option.code}:${v.screen}`] || {};
			const box = el("div", "rr-verdicts");
			box.appendChild(el("span", "rr-small", "Does this screen work?"));
			VERDICTS.forEach((x) => {
				const b = btn(`rr-vb is-${x.toLowerCase()}`, `${x.toUpperCase()} ${counts[x] || 0}`, async () => {
					await this.call(M.VERDICT, { review: v.state.name, option_code: v.option.code, screen_code: v.screen, verdict: mine === x ? "" : x });
					this.refresh();
				}, { "aria-pressed": mine === x ? "true" : "false", disabled: !can, title: can ? (mine === x ? "Click again to clear yours" : "") : "Participants give verdicts while the review is Open" });
				box.appendChild(b);
			});
			c.appendChild(box);
		}
		if (!v.compare) {
			const nx = this.storyNext(false);
			c.appendChild(btn("rr-vb nextb", "NEXT ▶", () => this.storyNext(true), { disabled: !nx, title: nx ? `Next: ${nx}` : "End of this option's story" }));
		}
	}

	storyNext(doIt) {
		const v = this.viewer;
		const r = resolve("next", { track: v.track.id, screen: v.screen, option: v.option.code }, v.content.flow || {}, v.order);
		if (!r || r.special) {
			if (doIt) this.toast("That is the end of this option's story. Pick another option or track.");
			return "";
		}
		if (doIt) this.move(r, "");
		return `${r.screen} ${this.screenName(v.content, r.track, r.screen)}`;
	}

	move(r, via) {
		const v = this.viewer;
		const flow = v.content.flow || {};
		const from = `${v.track.id}:${v.screen}`;
		const to = `${r.track}:${r.screen}`;
		this.say = (flow.say || {})[`${from}>${to}`] || (flow.say || {})[to] || "";
		if (r.track === v.track.id) {
			if (!v.order.includes(r.screen)) return this.toast(`This option has no ${r.screen}.`);
			return this.go([v.state.name, v.track.id, v.option.code, r.screen]);
		}
		const here = v.content.options.filter((o) => o.track === v.track.id).map((o) => o.code);
		const there = v.content.options.filter((o) => o.track === r.track);
		const pick = there[here.indexOf(v.option.code)] || there[0];
		if (!pick) return this.toast("That leads to a track this review does not have.");
		this.go([v.state.name, r.track, pick.code, r.screen]);
	}

	renderStage() {
		const v = this.viewer;
		const s = clear(this.stageEl);
		this.slots = [];
		if (v.compare) {
			const grid = el("div", "rr-grid");
			v.content.options.filter((o) => o.track === v.track.id).forEach((o) => {
				const frames = this.framesOf(v.content, o.code, v.screen);
				const shot = frames.find((f) => f.frame === "phone") || frames[0];
				const cell = btn("rr-cell", null, () => this.go([v.state.name, v.track.id, o.code, v.screen]), { "aria-label": `Open ${o.code} ${o.name}` });
				const head = el("span", "rr-cell-h");
				append(head, el("b", null, `${o.code} · ${o.name}`));
				const mine = v.state.verdicts.mine[`${o.code}:${v.screen}`];
				if (mine) head.appendChild(el("span", `rr-vd is-${mine.toLowerCase()}`));
				cell.appendChild(head);
				if (shot) this.thumbnail(cell, v.content, shot, shot.frame === "phone" ? 240 : 420, 420);
				else cell.appendChild(el("span", "rr-small rr-muted", "This option has no such screen."));
				grid.appendChild(cell);
			});
			s.appendChild(grid);
			return;
		}
		const all = this.framesOf(v.content, v.option.code, v.screen);
		const shown = all.filter((f) => this.ui.frame === "both" || all.length < 2 || f.frame === this.ui.frame)
			.sort((a, b) => (a.frame === "phone") - (b.frame === "phone"));
		const row = el("div", "rr-frames");
		shown.forEach((frame) => {
			const wrap = el("div", "rr-fr");
			const label = el("div", "rr-frl");
			const fbox = el("div", "rr-fbox");
			const ov = el("div", "rr-ov");
			const iframe = makeFrame(v.content, { ...frame, option: v.option.code });
			append(fbox, iframe, ov);
			append(wrap, label, fbox);
			row.appendChild(wrap);
			const slot = { frame, iframe, fbox, ov, label, scale: 1 };
			iframe.addEventListener("load", () => this.frameLoaded(slot));
			this.slots.push(slot);
		});
		s.appendChild(row);
		this.fitFrames();
	}

	fitFrames() {
		if (!this.viewer || this.viewer.compare || !this.slots || !this.slots.length) return;
		const avail = Math.max(280, this.stageEl.clientWidth - 40);
		const gap = 24;
		const total = this.slots.reduce((sum, s) => sum + s.frame.w, 0) + gap * (this.slots.length - 1);
		let scale = Math.min(1, avail / total);
		const stack = scale < 0.45 && this.slots.length > 1;
		this.stageEl.querySelector(".rr-frames").classList.toggle("stack", stack);
		this.slots.forEach((slot) => {
			const sc = stack ? Math.min(1, avail / slot.frame.w) : scale;
			slot.scale = sc;
			slot.fbox.style.width = Math.round(slot.frame.w * sc) + "px";
			slot.fbox.style.height = Math.round(slot.frame.h * sc) + "px";
			slot.iframe.style.transform = `scale(${sc})`;
			slot.label.textContent = `${slot.frame.frame === "phone" ? "PHONE" : slot.frame.frame === "desk" ? "DESKTOP" : "BOARD"} ${slot.frame.w} × ${slot.frame.h} · ${Math.round(sc * 100)}%`;
			this.placeCodes(slot);
		});
	}

	frameLoaded(slot) {
		const v = this.viewer;
		const doc = slot.iframe.contentDocument;
		if (!doc || !doc.body) return;
		const screen = { track: v.track.id, screen: v.screen, option: v.option.code };
		decorate(doc, screen, v.content.flow || {}, v.order);
		slot.doc = doc;
		this.markFrame(slot);
		if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(() => this.placeCodes(slot));
		doc.addEventListener("scroll", () => this.placeCodes(slot), true);
		doc.addEventListener("mouseover", (e) => {
			doc.querySelectorAll(".rr-hot").forEach((h) => h.classList.remove("rr-hot"));
			if (this.ui.markup) return;
			const hot = e.target.closest ? e.target.closest("[data-nav]") : null;
			if (hot) hot.classList.add("rr-hot");
		});
		doc.addEventListener("click", (e) => this.frameClick(e, slot), true);
	}

	partCode(slot, name) {
		const v = this.viewer;
		const row = this.partsOf(v.content, v.track.id, v.screen).find((p) => p[1] === name);
		return row ? `${v.option.code}-${v.screen}-E${pad2(row[0])}` : null;
	}

	frameClick(e, slot) {
		e.preventDefault();
		const v = this.viewer;
		const part = e.target.closest ? e.target.closest("[data-c]") : null;
		if (this.ui.markup) {
			if (part) {
				const name = part.getAttribute("data-c");
				const code = this.partCode(slot, name);
				if (code) this.select(code, name);
			}
			return;
		}
		const ctl = e.target.closest ? e.target.closest("a,button") : null;
		const n = e.target.closest ? e.target.closest("[data-nav],[data-here]") : null;
		if (ctl && !ctl.hasAttribute("data-nav") && !ctl.hasAttribute("data-here")) {
			this.flash(slot);
			return this.toast("That control is not linked in this prototype. The highlighted spots are.");
		}
		if (n) {
			if (!n.hasAttribute("data-nav")) return this.toast(`You are already on ${v.screen} ${this.screenName(v.content, v.track.id, v.screen)}.`);
			const r = resolve(n.getAttribute("data-nav"), { track: v.track.id, screen: v.screen, option: v.option.code }, v.content.flow || {}, v.order);
			if (!r) return;
			if (r.special === "done") return this.toast(r.message);
			if (r.special === "back") return history.back();
			if (r.special === "end") return this.toast("That is the end of this option's story.");
			if (r.special === "start") return this.toast("This is the first screen.");
			return this.move(r, ctl ? ctl.textContent : "");
		}
		this.flash(slot);
		this.toast("Click a highlighted spot to move on. Turn on MARKUP to leave a note.");
	}

	flash(slot) {
		if (!slot.doc) return;
		slot.doc.body.classList.add("rr-flash");
		setTimeout(() => slot.doc && slot.doc.body.classList.remove("rr-flash"), 900);
	}

	markFrames() {
		(this.slots || []).forEach((s) => this.markFrame(s));
	}

	markFrame(slot) {
		const doc = slot.doc;
		const v = this.viewer;
		if (!doc || !v || v.compare) return;
		doc.body.classList.toggle("rr-markup", this.ui.markup);
		doc.body.classList.toggle("rr-links", this.ui.links);
		const noted = new Set(v.state.notes.filter((n) => n.option_code === v.option.code && n.screen_code === v.screen).map((n) => n.part_name));
		doc.querySelectorAll("[data-c]").forEach((node) => {
			const name = node.getAttribute("data-c");
			node.toggleAttribute("data-rr-has", noted.has(name));
			node.toggleAttribute("data-rr-sel", !!this.sel && this.sel.name === name && this.sel.screen === v.key);
		});
		this.placeCodes(slot);
	}

	placeCodes(slot) {
		const ov = slot.ov;
		clear(ov);
		const doc = slot.doc;
		const v = this.viewer;
		if (!doc || !v || !this.ui.markup) return;
		const seen = {};
		const W = slot.frame.w;
		const H = slot.frame.h;
		doc.querySelectorAll("[data-c]").forEach((node) => {
			const name = node.getAttribute("data-c");
			if (seen[name]) return;
			const code = this.partCode(slot, name);
			if (!code) return;
			const r = node.getBoundingClientRect();
			// Parts outside the drawn frame have no pin (WI-079 spike finding).
			if (r.width < 2 || r.height < 2 || r.top >= H - 4 || r.left >= W - 4 || r.bottom <= 0 || r.right <= 0) return;
			seen[name] = 1;
			const tag = btn("rr-pcode", code.split("-").pop(), () => this.select(code, name), { title: `${code} ${name}`, "aria-label": `Part ${code}, ${name}` });
			if (node.hasAttribute("data-rr-has")) tag.dataset.has = "1";
			if (node.hasAttribute("data-rr-sel")) tag.dataset.sel = "1";
			tag.style.left = `${Math.max(0, r.left) * slot.scale}px`;
			tag.style.top = `${Math.max(0, r.top) * slot.scale}px`;
			ov.appendChild(tag);
		});
	}

	select(code, name) {
		this.sel = { code, name, screen: this.viewer.key };
		this.ui.ptab = "note";
		if (!this.ui.panel) {
			this.ui.panel = true;
			this.applyPanes();
		}
		this.markFrames();
		this.renderPanel();
	}

	// ---------------------------------------------------------------- notes panel

	renderPanel() {
		const v = this.viewer;
		const p = clear(this.panelEl);
		p.classList.toggle("hide", !this.ui.panel);
		document.body.classList.toggle("rr-sheet", this.ui.panel && narrow());
		const tabs = el("div", "rr-ptabs");
		tabs.appendChild(btn("rr-ptab", "THIS SCREEN", () => { this.ui.ptab = "note"; this.renderPanel(); }, { "aria-pressed": this.ui.ptab === "note" ? "true" : "false" }));
		tabs.appendChild(btn("rr-ptab", "ALL NOTES", () => { this.ui.ptab = "all"; this.renderPanel(); }, { "aria-pressed": this.ui.ptab === "all" ? "true" : "false" }));
		if (narrow()) tabs.appendChild(btn("rr-ptab", "CLOSE", () => { this.ui.panel = false; this.applyPanes(); }));
		const body = el("div", "rr-pbody");
		append(p, tabs, body);
		if (this.ui.ptab === "all") return this.renderAllNotes(body);
		if (v.compare) return body.appendChild(el("p", "rr-hint", "Compare shows every option on one screen. Open one to pin notes to it."));
		const can = v.state.me.participant && v.state.status === "Open";
		if (this.sel && this.sel.screen === v.key) {
			const sel = el("div", "rr-sel");
			append(sel, el("div", "rr-sel-c", this.sel.code), el("div", "rr-sel-d", this.sel.name));
			body.appendChild(sel);
			const notes = v.state.notes.filter((n) => n.code === this.sel.code);
			notes.forEach((n) => body.appendChild(this.noteCard(n, false)));
			if (!notes.length) body.appendChild(el("p", "rr-hint", "No notes on this part yet."));
			const number = Number(this.sel.code.split("-E").pop());
			const elsewhere = v.state.notes.filter((n) => n.option_code !== v.option.code && n.screen_code === v.screen && Number(n.part_number) === number);
			if (elsewhere.length) body.appendChild(el("p", "rr-hint", `${elsewhere.length} notes on this part in other options. See ALL NOTES.`));
			if (can) body.appendChild(this.noteForm(v, this.sel.code));
			else body.appendChild(el("p", "rr-hint", v.state.me.participant ? "Notes open while the review is Open." : "Only participants write notes."));
		} else if (!this.ui.markup) {
			body.appendChild(el("p", "rr-hint", "Turn on MARKUP (key M), then click any outlined part of the screen to read or write its notes. With MARKUP off, the screens are clickable: buttons, cards and links take you to the next screen. LINKS outlines every clickable spot."));
		} else {
			body.appendChild(el("p", "rr-hint", "Click any outlined part, or a numbered tag, to note it."));
		}
		const parts = this.partsOf(v.content, v.track.id, v.screen);
		if (parts.length) {
			body.appendChild(el("div", "rr-grp", "Parts on this screen"));
			const list = el("div", "rr-elist");
			const noted = new Set(v.state.notes.filter((n) => n.option_code === v.option.code && n.screen_code === v.screen).map((n) => Number(n.part_number)));
			parts.forEach(([num, name]) => {
				const code = `${v.option.code}-${v.screen}-E${pad2(num)}`;
				const row = btn("rr-erow", null, () => {
					if (!this.ui.markup) this.toggleMarkup();
					this.select(code, name);
				}, { "aria-pressed": this.sel && this.sel.code === code ? "true" : "false" });
				append(row, el("b", null, `E${pad2(num)}`), el("span", null, name));
				if (noted.has(Number(num))) row.appendChild(el("i", "rr-dot2"));
				list.appendChild(row);
			});
			body.appendChild(list);
		}
	}

	noteForm(v, code) {
		const f = el("form", "rr-form");
		const raised = el("input", "rr-fld");
		raised.placeholder = "Someone in the meeting, if not you";
		raised.autocomplete = "off";
		const listId = "rr-people";
		raised.setAttribute("list", listId);
		const dl = el("datalist");
		dl.id = listId;
		const chosen = {};
		raised.addEventListener("input", async () => {
			const text = raised.value.trim();
			if (text.length < 2 || chosen[text]) return;
			try {
				const people = await call(M.PEOPLE, { review: v.state.name, text });
				clear(dl);
				people.forEach((p) => {
					const label = `${p.employee_name} (${p.name})`;
					chosen[label] = p.name;
					const o = el("option");
					o.value = label;
					dl.appendChild(o);
				});
			} catch (e) {
				/* the field stays free text; nothing is lost */
			}
		});
		this.field(f, "Raised by (optional)", raised);
		f.appendChild(dl);
		const text = this.field(f, "Note", el("textarea", "rr-fld"));
		text.placeholder = "What should change about this part, or what works?";
		text.maxLength = 2000;
		const add = btn("rr-btn", "ADD NOTE", null);
		add.type = "submit";
		f.appendChild(add);
		f.addEventListener("submit", async (e) => {
			e.preventDefault();
			const raisedBy = chosen[raised.value.trim()] || "";
			if (raised.value.trim() && !raisedBy) return this.toast("Pick the person from the list, or leave Raised by empty.", true);
			if (text.value.trim().length < 3) return this.toast("Write the note first.", true);
			await this.call(M.NOTE, { review: v.state.name, code, text: text.value, raised_by: raisedBy });
			this.toast("Note added");
			this.refresh();
		});
		return f;
	}

	noteCard(n, showCode) {
		const v = this.viewer;
		const card = el(showCode ? "button" : "div", `rr-note${n.is_imported ? " is-imported" : ""}`);
		if (showCode) {
			card.type = "button";
			card.addEventListener("click", () => {
				const opt = v.content.options.find((o) => o.code === n.option_code);
				this.sel = { code: n.code, name: n.part_name, screen: `${n.option_code}:${n.screen_code}` };
				if (!this.ui.markup) this.toggleMarkup();
				this.ui.ptab = "note";
				if (opt) this.go([v.state.name, opt.track, n.option_code, n.screen_code]);
			});
		}
		const meta = el("div", "rr-note-meta");
		const who = `${n.author_name || ""}${n.raised_by_name ? ` for ${n.raised_by_name}` : ""}${n.is_imported ? " · imported" : ""}`;
		append(meta, el("span", null, showCode ? `${n.code} · ${who}` : who));
		if (v.state.me.moderator && !n.promoted_request && !showCode) {
			const s = el("select", "rr-select small");
			s.setAttribute("aria-label", "Note status");
			NOTE_STATUSES.forEach((x) => {
				const o = el("option", null, x);
				o.value = x;
				o.selected = x === n.status;
				s.appendChild(o);
			});
			s.addEventListener("change", async () => {
				await this.call(M.NOTE_STATUS, { note: n.name, status: s.value });
				this.refresh();
			});
			meta.appendChild(s);
		} else {
			meta.appendChild(el("span", `rr-nst is-${String(n.status).toLowerCase()}`, n.status));
		}
		append(card, meta, el("div", "rr-note-t", n.text));
		if (!showCode && n.mine && n.status === "Open" && v.state.status === "Open") {
			card.appendChild(btn("rr-link", "Delete", async () => {
				if (!window.confirm("Delete this note?")) return;
				await this.call(M.DELETE_NOTE, { note: n.name });
				this.refresh();
			}));
		}
		return card;
	}

	renderAllNotes(body) {
		const v = this.viewer;
		const notes = v.state.notes.slice().sort((a, b) => (a.option_code + a.screen_code + a.code).localeCompare(b.option_code + b.screen_code + b.code));
		if (!notes.length) return body.appendChild(el("p", "rr-hint", "No notes in this review yet."));
		let head = null;
		notes.forEach((n) => {
			const opt = v.content.options.find((o) => o.code === n.option_code);
			const h = `${n.option_code} ${opt ? opt.name : ""} · ${n.screen_code} ${this.screenName(v.content, n.track, n.screen_code)}`;
			if (h !== head) {
				head = h;
				body.appendChild(el("div", "rr-grp", h));
			}
			body.appendChild(this.noteCard(n, true));
		});
	}

	// ---------------------------------------------------------------- keyboard

	onKey(e) {
		const a = document.activeElement;
		if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT")) return;
		if (e.ctrlKey || e.metaKey || e.altKey || !this.viewer) return;
		const k = e.key.toLowerCase();
		if (k === "m" && !this.viewer.compare) this.toggleMarkup();
		else if (k === "l") { this.ui.links = !this.ui.links; this.markFrames(); }
		else if (k === "n" && !this.viewer.compare) this.storyNext(true);
		else if (k === "escape" && this.sel) { this.sel = null; this.markFrames(); this.renderPanel(); }
		else return;
		e.preventDefault();
	}
}
