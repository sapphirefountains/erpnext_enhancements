/**
 * The Stock Scan page (`/stock-scan`): scan a location label, see what is kept there, take
 * parts out or put stock in with − / + and Save, scan the next label.
 *
 * Who holds this phone: a technician with parts in one hand, or a receiver with a box, in a
 * dim warehouse on a weak signal. So: big targets, few words, an answer on every tap, and
 * every error says what to do next. Plain DOM and no framework: this is a website route, and
 * none of Desk's client libraries are loaded on it.
 *
 * VIEWS. `start` → `location` → `item` (an item at a location, with the stepper), plus `free`
 * (an item found by search with no location open: "Where is it?"). Navigation is an in-memory
 * back stack. The URL NEVER changes — no history entries, no hash: iOS Safari asks for camera
 * permission again whenever the URL changes, and the camera is used on every shelf.
 *
 * SAVES post immediately (submitted vouchers) and can be undone for a while. Each intended
 * save carries a `client_ref`; a retry after no answer reuses it (for `RETRY_WINDOW_MS`), so
 * the server returns the first save instead of posting twice (`logic.saveKey` /
 * `logic.mayHaveSaved` / `logic.keptRef`). A save's answer belongs to the view that sent it:
 * if the person has moved on by the time it arrives, a failure is raised as a toast naming the
 * item rather than written into whatever is on screen now (`post`).
 */

import { M, call } from "./transport.js";
import {
	MAX_QTY,
	clampChange,
	describeChange,
	formatWhen,
	jobExpired,
	keptRef,
	logHeadline,
	mayHaveSaved,
	mintRef,
	parseQty,
	plain,
	rememberedJob,
	saveKey,
	saveLabel,
	scanFailure,
	shortDate,
	undoQuestion,
} from "./logic.js";
import { append, button, el, fill, glyph, icon, input, thumb } from "./dom.js";
import {
	ask,
	busy,
	buzz,
	closeAllSheets,
	mountToasts,
	readTheme,
	setTheme,
	sheet,
	sheetDepth,
	syncThemeMeta,
	toast,
	whenNoSheets,
} from "./ui.js";
import { openScanner } from "./scanner.js";

const JOB_KEY = "ee_ss_job";
const RECENT_LIMIT = 12;
const SEARCH_DEBOUNCE_MS = 250;
const START = { name: "start" };
const STILL_SAVING = "Your last save is still going through. Try again in a moment.";
const REPEATED = "That save was already recorded — nothing new was posted. Tap Save again to record another.";

/** The only cure for a lost session or a stale CSRF token (`StockScanCallError.needsReload`). */
const RELOAD = { label: "Reload", onClick: () => window.location.reload() };

function needsReload(error) {
	return !!(
		error &&
		(error.needsReload || error.signedOut || error.status === 401 || error.excType === "CSRFTokenError" || error.excType === "SessionExpired")
	);
}

/**
 * An error line with room for one way out ("Reload"): the stepper's and the Move sheet's.
 * `show(text, action)`; an empty `text` hides it.
 */
function errorLine(className) {
	const text = el("span", "ee-ss-error-text");
	let onAction = null;
	const act = button("", "ee-ss-btn ee-ss-btn-outline ee-ss-error-action", () => {
		if (onAction) onAction();
	});
	act.hidden = true;
	const node = append(el("div", className), text, act);
	node.setAttribute("role", "alert");
	node.hidden = true;
	return {
		node,
		show(message, action) {
			text.textContent = message || "";
			node.hidden = !message;
			onAction = message && action ? action.onClick : null;
			act.textContent = onAction ? action.label : "";
			act.hidden = !onAction;
		},
	};
}

// ---------------------------------------------------------------------------
// The job picked for this run, remembered on this phone for one shift — and only for the
// person who picked it. Phones get handed on; the next person's takes must not be charged to
// a job they never chose (and may not even be able to see).
// ---------------------------------------------------------------------------

function readJob(user) {
	try {
		const raw = window.localStorage.getItem(JOB_KEY);
		if (!raw) return null;
		const job = rememberedJob(JSON.parse(raw), user);
		if (job) return job;
		// Lapsed, or somebody else's: nobody's job now.
		window.localStorage.removeItem(JOB_KEY);
	} catch (e) {
		/* storage blocked or garbled: no job */
	}
	return null;
}

function writeJob(job) {
	try {
		if (job) window.localStorage.setItem(JOB_KEY, JSON.stringify(job));
		else window.localStorage.removeItem(JOB_KEY);
	} catch (e) {
		/* storage blocked: the job still holds until the page reloads */
	}
}

/** "Stores - SF · 12 on hand" pieces for a place an item is recorded. */
function placeAmount(place, uom) {
	const onHand = `${plain(place.on_hand)} ${uom || ""}`.trim();
	if (place.available !== undefined && place.available !== null && place.available < place.on_hand) {
		return `${onHand} on hand · ${plain(Math.max(place.available, 0))} free`;
	}
	return `${onHand} on hand`;
}

export class StockScanApp {
	constructor(root, boot) {
		this.root = root;
		this.boot = boot || {};
		this.settings = Object.assign({ require_project_for_take: 0, undo_window_minutes: 0 }, this.boot.settings || {});
		this.recent = Array.isArray(this.boot.recent) ? this.boot.recent.slice(0, RECENT_LIMIT) : [];
		this.recentAt = Date.now();
		this.view = START;
		this.back = [];
		this.change = 0;
		// saveKey -> {ref, at}, for every save that got no answer (or a 409) and may have posted:
		// repeating that same save within RETRY_WINDOW_MS, here or after walking elsewhere, must
		// send the same ref. After that the same numbers are a new save (`keptRef`).
		this.pending = new Map();
		// One save at a time. `savingView` is the view that sent it: only that view shows
		// "Saving…", and only that view may be told inline how it went.
		this.saving = false;
		this.savingView = null;
		this.seq = 0; // bumps on every navigation; an answer for an older one is dropped
		this.loads = 0;
		this.job = readJob(this.boot.user);
		this.scanner = null;
		this.startMessage = "";
		this.wedge = "";
		this.wedgeAt = 0;
		this.stepper = null;
	}

	// -----------------------------------------------------------------------
	// Shell
	// -----------------------------------------------------------------------

	mount() {
		const root = this.root;
		fill(root);
		root.setAttribute("aria-busy", "false");

		this.progress = el("div", "ee-ss-progress");
		this.progress.setAttribute("aria-hidden", "true");
		this.titleEl = el("div", "ee-ss-top-title", "Stock Scan");
		this.jobChip = button("", "ee-ss-job-chip", () => this.pickJob());
		this.top = append(el("header", "ee-ss-top"), this.titleEl, this.jobChip, this.progress);

		this.main = el("main", "ee-ss-main");

		this.scanLabel = el("span", null, "Scan");
		this.scanBtn = button([icon("scan"), this.scanLabel], "ee-ss-btn ee-ss-btn-primary ee-ss-bar-scan", () => this.scan());
		this.searchBtn = button([icon("search"), el("span", null, "Search")], "ee-ss-btn ee-ss-btn-outline ee-ss-bar-search", () =>
			this.openSearch()
		);
		const bar = append(el("nav", "ee-ss-bar"), this.scanBtn, this.searchBtn);
		bar.setAttribute("aria-label", "Scan or search");

		append(root, this.top, this.main, bar);
		mountToasts();
		this.renderJobChip();
		syncThemeMeta();
		try {
			const dark = window.matchMedia("(prefers-color-scheme: dark)");
			const onScheme = () => syncThemeMeta();
			if (dark.addEventListener) dark.addEventListener("change", onScheme);
			else if (dark.addListener) dark.addListener(onScheme);
		} catch (e) {
			/* no matchMedia: the meta keeps its first color */
		}
		document.addEventListener("keydown", (ev) => this.onWedgeKey(ev));

		this.openInitial(this.boot.initial);
	}

	/** The label the phone's own camera opened, resolved server-side into the boot payload. */
	openInitial(initial) {
		const code = window.location.href;
		if (initial && initial.kind === "location" && initial.location) {
			this.showLocation(initial.location, { code, scanned: true });
		} else if (initial && initial.kind === "item" && initial.item) {
			this.showItem(initial.item, { code });
		} else {
			if (initial && initial.kind === "unknown") this.startMessage = initial.message || "";
			this.enter(START, "root");
		}
	}

	setLoading(on) {
		this.loads = Math.max(0, this.loads + (on ? 1 : -1));
		this.root.classList.toggle("is-loading", this.loads > 0);
		this.root.setAttribute("aria-busy", this.loads > 0 ? "true" : "false");
	}

	renderJobChip() {
		const label = this.job ? `Job: ${this.job.project_name || this.job.project}` : "No job";
		fill(this.jobChip, icon("job"), el("span", "ee-ss-job-chip-text", label));
		this.jobChip.classList.toggle("is-set", !!this.job);
		this.jobChip.setAttribute("aria-label", this.job ? `${label}. Change job` : "No job. Pick a job");
	}

	/** Where the person is standing, as far as the page knows: the open location, if any. */
	here() {
		const v = this.view;
		if (v.name === "location") {
			const loc = v.location || v.stub;
			return loc ? { warehouse: loc.warehouse, warehouse_name: loc.warehouse_name } : null;
		}
		if (v.name === "item" && v.item.warehouse) {
			return { warehouse: v.item.warehouse, warehouse_name: v.item.warehouse_name || v.item.warehouse };
		}
		return null;
	}

	// -----------------------------------------------------------------------
	// Navigation
	// -----------------------------------------------------------------------

	/**
	 * Show `view`. `mode`: "push" keeps the current view to come back to, "replace" swaps it,
	 * "root" makes Start the only way back. Anything half-typed on the old view is dropped.
	 *
	 * `this.pending` is deliberately NOT cleared here. Its refs belong to saves that got no
	 * answer and may have posted; each is keyed by everything its save would post (`saveKey`),
	 * so a different save can never reuse one, while walking away and coming back to repeat
	 * the SAME save gets the first one back from the server instead of posting it twice. Each
	 * is kept for `RETRY_WINDOW_MS` only (`keptRef`), so the same numbers much later are a new
	 * save rather than an "already saved" that posts nothing.
	 */
	enter(view, mode) {
		if (mode === "push") this.back.push(this.view);
		else if (mode === "root") this.back = view.name === "start" ? [] : [START];
		this.view = view;
		this.change = 0;
		this.render(true);
	}

	goBack() {
		const prev = this.back.pop() || START;
		++this.seq;
		this.view = prev;
		this.change = 0;
		this.render(true);
		// What was on screen before may be stale: a save just changed its numbers.
		this.refreshView(prev);
	}

	backLabel() {
		const prev = this.back[this.back.length - 1];
		if (!prev || prev.name === "start") return "Start";
		if (prev.name === "location") {
			const loc = prev.location || prev.stub;
			return `All items at ${loc ? loc.warehouse_name : "this location"}`;
		}
		if (prev.name === "free") return "Where is it?";
		if (prev.name === "item") return prev.item.item_name;
		return "Back";
	}

	/** Re-read a view from the server and redraw it if it is still the one on screen. */
	async refreshView(view) {
		const seq = this.seq;
		try {
			if (view.name === "location") {
				const target = view.location || view.stub;
				const fresh = await call(M.LOCATION, { warehouse: target.warehouse });
				if (seq !== this.seq || this.view !== view) return;
				view.location = fresh;
				this.render(false);
			} else if (view.name === "item" || view.name === "free") {
				const fresh = await call(M.ITEM, { item_code: view.item.item_code, warehouse: view.item.warehouse || undefined });
				// The view's own save in flight will hand back fresher numbers than these.
				if (seq !== this.seq || this.view !== view || this.savingHere()) return;
				view.item = fresh;
				this.change = clampChange(this.change, fresh.available);
				this.render(false);
			} else if (view.name === "start" && Date.now() - this.recentAt > 30000) {
				this.refreshRecent();
			}
		} catch (e) {
			if (seq !== this.seq || this.view !== view) return;
			if (view.name === "location" && !view.location) {
				this.fail(e, { retry: () => this.refreshView(view) });
			}
			// Otherwise the numbers on screen are the last known ones; the next save re-checks them.
		}
	}

	/** A location payload: Start > location. A scanned bin holding one item opens that item. */
	showLocation(location, opts) {
		const o = opts || {};
		const view = { name: "location", location, scannedCode: o.code || "" };
		this.enter(view, "root");
		const items = location.items || [];
		if (o.scanned && items.length === 1) {
			this.openItem(items[0].item_code, location.warehouse, { code: o.code });
		}
	}

	/**
	 * An item payload. At a location it sits on top of that location (so its back link reads
	 * "All items at …"); without one it is "Where is it?".
	 */
	showItem(item, opts) {
		const o = opts || {};
		if (!item.warehouse) {
			this.enter({ name: "free", item, scannedCode: o.code || "" }, "root");
			return;
		}
		const view = { name: "item", item, scannedCode: o.code || "" };
		const cur = this.view;
		if (cur.name === "item" && cur.item.warehouse === item.warehouse) {
			// The next item on the same shelf: swap it in, keep the way back to the shelf.
			this.enter(view, "replace");
			return;
		}
		const curLoc = cur.name === "location" ? cur.location || cur.stub : null;
		this.back.push(cur);
		if (!curLoc || curLoc.warehouse !== item.warehouse) {
			// Reached from somewhere else (Recent, search, "Where is it?"): put the shelf itself
			// in between, loaded when someone goes back to it.
			this.back.push({
				name: "location",
				location: null,
				stub: { warehouse: item.warehouse, warehouse_name: item.warehouse_name || item.warehouse },
			});
		}
		this.enter(view, "replace");
	}

	/** Fetch an item (at `warehouse` when given) and show it. `row` shows a spinner meanwhile. */
	async openItem(itemCode, warehouse, opts) {
		const o = opts || {};
		const seq = ++this.seq;
		if (o.row) o.row.classList.add("is-loading");
		this.setLoading(true);
		try {
			const item = await call(M.ITEM, { item_code: itemCode, warehouse: warehouse || undefined });
			if (seq !== this.seq) return;
			this.showItem(item, { code: o.code });
		} catch (e) {
			if (seq === this.seq) this.fail(e, { retry: () => this.openItem(itemCode, warehouse, o) });
		} finally {
			this.setLoading(false);
			if (o.row) o.row.classList.remove("is-loading");
		}
	}

	async openLocation(warehouse, opts) {
		const seq = ++this.seq;
		this.setLoading(true);
		try {
			const location = await call(M.LOCATION, { warehouse });
			if (seq === this.seq) this.showLocation(location, opts);
		} catch (e) {
			if (seq === this.seq) this.fail(e, { retry: () => this.openLocation(warehouse, opts) });
		} finally {
			this.setLoading(false);
		}
	}

	/**
	 * A failed read: say it, and offer the one thing that helps — Reload when the session is
	 * gone (a retry would fail the same way forever), else Try again. `opts.text` overrides
	 * the error's own sentence.
	 */
	fail(error, opts) {
		const o = opts || {};
		const text = o.text || (error && error.message) || "Something went wrong. Try again.";
		if (needsReload(error)) toast(text, { kind: "error", actionLabel: RELOAD.label, onAction: RELOAD.onClick });
		else if (o.retry) toast(text, { kind: "error", actionLabel: "Try again", onAction: o.retry });
		else toast(text, { kind: "error" });
	}

	// -----------------------------------------------------------------------
	// Scanning
	// -----------------------------------------------------------------------

	scan() {
		if (this.scanner) return;
		closeAllSheets();
		this.scanner = openScanner({
			decoderUrl: this.boot.decoder_url,
			onCode: (code) => {
				this.scanner = null;
				this.resolve(code, { scanned: true });
			},
			onClose: () => {
				this.scanner = null;
			},
		});
	}

	/**
	 * Look a scanned or typed code up, in the context of where the person is standing. The
	 * server's `resolve` decides what the code is; the page never acts on its own reading.
	 *
	 * Returns the server's answer, or `null` when a newer lookup or navigation took over. With
	 * `opts.quiet` (the Search sheet, which has its own status line) a failed call comes back
	 * as `{kind: "error", message}` instead of a toast — a toast would sit hidden BEHIND the
	 * full-height sheet while the sheet said "Nothing matches".
	 */
	async resolve(code, opts) {
		const o = opts || {};
		const here = this.here();
		const seq = ++this.seq;
		this.setLoading(true);
		try {
			const res = await call(M.RESOLVE, { code, warehouse: here ? here.warehouse : undefined });
			if (seq !== this.seq) return null;
			if (res && res.kind === "location") this.showLocation(res.location, { code, scanned: !!o.scanned });
			else if (res && res.kind === "item") this.showItem(res.item, { code });
			else if (!o.quiet) {
				toast((res && res.message) || "Nothing matches that code.", {
					kind: "error",
					actionLabel: "Scan again",
					onAction: () => this.scan(),
				});
			}
			return res;
		} catch (e) {
			if (seq !== this.seq) return null;
			// No answer to act on, so at least say WHICH label failed:
			// "Couldn't open Bin B1-2-10 - SF: …", read with parseScan, the twin of the server's
			// parse_scan (`logic.scanFailure`). Naming it is all the page's own reading is for.
			const message = scanFailure(code, e && e.message);
			if (o.quiet) return { kind: "error", message, error: e };
			this.fail(e, { text: message, retry: () => this.resolve(code, o) });
			return null;
		} finally {
			this.setLoading(false);
		}
	}

	/**
	 * A Bluetooth/USB scanner gun types the code and presses Enter, far faster than a person.
	 * Outside any text box, a burst like that ending in Enter is a scan.
	 */
	onWedgeKey(ev) {
		if (sheetDepth() || this.scanner || ev.ctrlKey || ev.metaKey || ev.altKey) return;
		const t = ev.target;
		if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
		const now = Date.now();
		if (ev.key === "Enter") {
			const burst = this.wedge;
			this.wedge = "";
			if (burst.length >= 3 && now - this.wedgeAt < 120) {
				ev.preventDefault();
				this.resolve(burst, { scanned: true });
			}
			return;
		}
		if (ev.key && ev.key.length === 1) {
			if (now - this.wedgeAt > 60) this.wedge = "";
			this.wedge += ev.key;
			this.wedgeAt = now;
		}
	}

	// -----------------------------------------------------------------------
	// Drawing
	// -----------------------------------------------------------------------

	render(navigated) {
		const v = this.view;
		const here = this.here();
		this.titleEl.textContent = here ? here.warehouse_name : "Stock Scan";
		this.scanLabel.textContent = v.name === "start" ? "Scan" : "Scan next";
		this.stepper = null;
		let node;
		if (v.name === "location") node = this.renderLocation(v);
		else if (v.name === "item") node = this.renderItem(v);
		else if (v.name === "free") node = this.renderFree(v);
		else node = this.renderStart();
		fill(this.main, node);
		if (navigated) {
			window.scrollTo(0, 0);
			const heading = this.main.querySelector("h1, h2");
			if (heading) {
				heading.tabIndex = -1;
				try {
					heading.focus({ preventScroll: true });
				} catch (e) {
					/* focus is best effort */
				}
			}
		}
	}

	backLink() {
		return button([icon("back"), el("span", null, this.backLabel())], "ee-ss-back", () => this.goBack());
	}

	// --- Start ---------------------------------------------------------------------------

	renderStart() {
		const view = el("section", "ee-ss-view ee-ss-start");
		const heading = el("h1", "ee-ss-sr-only", "Stock Scan");
		const hero = button(
			[icon("scan", "ee-ss-hero-icon"), el("span", "ee-ss-hero-text", "Scan a location label")],
			"ee-ss-btn ee-ss-btn-primary ee-ss-hero",
			() => this.scan()
		);
		const find = button([icon("search"), el("span", null, "Item or location")], "ee-ss-searchbox", () => this.openSearch());
		append(view, heading, hero, find);
		if (this.startMessage) {
			const alert = el("p", "ee-ss-alert", this.startMessage);
			alert.setAttribute("role", "alert");
			view.appendChild(alert);
		}
		append(view, this.renderRecent(), this.renderThemeSwitch());
		return view;
	}

	renderRecent() {
		const box = el("section", "ee-ss-recent");
		box.appendChild(el("h2", "ee-ss-section-title", "Recent"));
		if (!this.recent.length) {
			box.appendChild(el("p", "ee-ss-note", "Your saves will show here, each with Undo for a few minutes."));
			return box;
		}
		const list = el("ul", "ee-ss-recent-list");
		for (const log of this.recent) list.appendChild(this.recentRow(log));
		box.appendChild(list);
		return box;
	}

	recentRow(log) {
		const undone = log.status !== "Posted";
		const row = el("li", `ee-ss-recent-row${undone ? " is-undone" : ""}`);
		const kind = log.action === "Take" ? "take" : log.action === "Move" ? "move" : "add";
		const mark = glyph(kind === "take" ? "−" : kind === "move" ? "→" : "+", `ee-ss-recent-mark is-${kind}`);
		const where = log.action === "Move" ? `${log.from_warehouse_name} → ${log.warehouse_name}` : log.warehouse_name;
		const when = formatWhen(log.posted_at, this.boot.today);
		const open = button(
			[
				el("span", "ee-ss-row-title", `${logHeadline(log)} · ${log.item_name}`),
				el("span", "ee-ss-row-sub", [where, when, log.project].filter(Boolean).join(" · ")),
			],
			"ee-ss-recent-main",
			() => this.openItem(log.item_code, log.warehouse, { row })
		);
		append(row, mark, open);
		if (undone) {
			append(row, append(el("span", "ee-ss-pill is-undone"), glyph("↺"), el("span", null, "Undone")));
		} else if (log.can_undo) {
			append(row, button([icon("undo"), el("span", null, "Undo")], "ee-ss-undo-btn", () => this.confirmUndo(log)));
		} else if (log.needs_review) {
			append(row, append(el("span", "ee-ss-pill is-review"), glyph("⚑"), el("span", null, "Review")));
		}
		return row;
	}

	async refreshRecent() {
		try {
			const rows = await call(M.RECENT, {});
			this.recent = Array.isArray(rows) ? rows.slice(0, RECENT_LIMIT) : [];
			this.recentAt = Date.now();
			if (this.view.name === "start") this.render(false);
		} catch (e) {
			/* Recent is a convenience; the list on screen stays as it was */
		}
	}

	renderThemeSwitch() {
		const wrap = el("div", "ee-ss-theme");
		wrap.setAttribute("role", "group");
		wrap.setAttribute("aria-label", "Screen colors");
		wrap.appendChild(el("span", "ee-ss-theme-label", "Screen"));
		const current = readTheme();
		for (const [mode, label] of [
			["system", "Auto"],
			["light", "Light"],
			["dark", "Dark"],
		]) {
			const opt = button(label, `ee-ss-theme-opt${current === mode ? " is-on" : ""}`, () => {
				setTheme(mode);
				this.render(false);
			});
			opt.setAttribute("aria-pressed", current === mode ? "true" : "false");
			wrap.appendChild(opt);
		}
		return wrap;
	}

	// --- Location -----------------------------------------------------------------------

	renderLocation(v) {
		const view = el("section", "ee-ss-view");
		const loc = v.location;
		const head = v.location || v.stub;
		append(view, this.backLink(), el("h1", "ee-ss-h1", head.warehouse_name));
		if (loc && loc.trail) view.appendChild(el("p", "ee-ss-trail", loc.trail));
		if (!loc) {
			view.appendChild(el("p", "ee-ss-note ee-ss-loading-note", "Loading what is kept here…"));
			return view;
		}
		const items = loc.items || [];
		if (!items.length) {
			const empty = append(
				el("div", "ee-ss-empty"),
				el("p", "ee-ss-empty-text", "Nothing is recorded here yet."),
				button([glyph("+"), el("span", null, "Add an item here")], "ee-ss-btn ee-ss-btn-add is-lg", () =>
					this.openSearch({ addHere: true })
				)
			);
			view.appendChild(empty);
			return view;
		}
		const list = el("ul", "ee-ss-list");
		for (const item of items) {
			const li = el("li");
			const row = button(
				[
					thumb(item),
					append(
						el("span", "ee-ss-row-main"),
						el("span", "ee-ss-row-title", item.item_name),
						el("span", "ee-ss-row-sub", item.item_code)
					),
					append(
						el("span", `ee-ss-row-qty${item.on_hand > 0 ? "" : " is-zero"}`),
						el("strong", null, plain(item.on_hand)),
						el("span", null, item.stock_uom)
					),
				],
				"ee-ss-row",
				() => this.openItem(item.item_code, loc.warehouse, { code: v.scannedCode, row })
			);
			li.appendChild(row);
			list.appendChild(li);
		}
		view.appendChild(list);
		if (loc.truncated) {
			view.appendChild(el("p", "ee-ss-note", `Showing the first ${items.length} items. Use Search to find the rest.`));
		}
		view.appendChild(
			button([glyph("+"), el("span", null, "Add another item here")], "ee-ss-linkbtn", () => this.openSearch({ addHere: true }))
		);
		return view;
	}

	// --- Item at a location -------------------------------------------------------------

	itemHeader(item) {
		const text = append(el("div", "ee-ss-item-text"), el("h2", "ee-ss-item-name", item.item_name), el("p", "ee-ss-item-code", item.item_code));
		if (item.description) text.appendChild(el("p", "ee-ss-item-desc", item.description));
		return append(el("div", "ee-ss-item-head"), thumb(item, "lg"), text);
	}

	renderItem(v) {
		const item = v.item;
		const view = el("section", "ee-ss-view ee-ss-item");
		append(view, this.backLink(), this.itemHeader(item));

		const onHand = append(
			el("div", "ee-ss-onhand"),
			el("span", "ee-ss-onhand-label", "On hand here"),
			append(el("span", "ee-ss-onhand-qty"), el("strong", null, plain(item.on_hand)), el("span", null, ` ${item.stock_uom}`))
		);
		if (item.available !== null && item.available < item.on_hand) {
			onHand.appendChild(
				el("span", "ee-ss-onhand-sub", `${plain(Math.max(item.available, 0))} free · ${plain(item.on_hand - item.available)} reserved`)
			);
		}
		view.appendChild(onHand);

		if (item.blocked) {
			const notice = el("p", "ee-ss-notice", item.blocked);
			notice.setAttribute("role", "note");
			view.appendChild(notice);
		} else {
			view.appendChild(this.buildStepper(item));
			if (item.elsewhere && item.elsewhere.length) {
				view.appendChild(
					button([icon("move"), el("span", null, "Move here from…")], "ee-ss-btn ee-ss-btn-outline ee-ss-move-btn", () => this.openMove())
				);
			}
		}
		if (item.elsewhere && item.elsewhere.length) {
			const shown = item.elsewhere.slice(0, 3).map((p) => `${p.warehouse_name} (${plain(p.on_hand)})`);
			const more = item.elsewhere.length > 3 ? `, and ${item.elsewhere.length - 3} more` : "";
			view.appendChild(el("p", "ee-ss-also", `Also at: ${shown.join(", ")}${more}`));
		}
		this.updateStepper();
		return view;
	}

	buildStepper(item) {
		const minus = button("−", "ee-ss-step ee-ss-step-minus", () => this.nudge(-1));
		minus.setAttribute("aria-label", "One less");
		const plus = button("+", "ee-ss-step ee-ss-step-plus", () => this.nudge(1));
		plus.setAttribute("aria-label", "One more");
		const value = button("0", "ee-ss-step-value", () => this.askQuantity());
		const hint = el("p", "ee-ss-step-hint");
		hint.setAttribute("aria-live", "polite");
		const note = el("p", "ee-ss-step-note");
		const error = errorLine("ee-ss-save-error");
		const save = button("Save", "ee-ss-btn ee-ss-save", () => this.save());
		this.stepper = { minus, plus, value, hint, note, error, save };
		return append(
			el("div", "ee-ss-stepper-box"),
			append(el("div", "ee-ss-stepper"), minus, value, plus),
			hint,
			note,
			error.node,
			save
		);
	}

	/** Whether the save in flight was sent from the view on screen now. */
	savingHere() {
		return this.saving && this.savingView === this.view;
	}

	/**
	 * Redraw the stepper in place: rebuilding the view on every tap would flicker and lose focus.
	 *
	 * Only the view that sent the save in flight shows "Saving…" and holds its stepper still.
	 * Any other view (the person scanned on before the answer came) can dial, but its Save waits
	 * — one save at a time — under its own label, with a note saying why.
	 */
	updateStepper() {
		const s = this.stepper;
		const v = this.view;
		if (!s || v.name !== "item") return;
		const item = v.item;
		const available = Math.max(item.available || 0, 0);
		const c = this.change;
		const d = describeChange(c, item.stock_uom, item.on_hand);
		const mine = this.savingHere();
		const waiting = this.saving && !mine;

		s.value.textContent = c < 0 ? `−${plain(-c)}` : c > 0 ? `+${plain(c)}` : "0";
		s.value.className = `ee-ss-step-value${c < 0 ? " is-take" : c > 0 ? " is-add" : ""}${s.value.textContent.length > 4 ? " is-long" : ""}`;
		s.value.setAttribute("aria-label", `${d.verb === "none" ? "No change" : d.label}. Tap to type a number`);
		s.minus.disabled = mine || c <= -available + 1e-9;
		s.plus.disabled = mine || c >= MAX_QTY;
		s.value.disabled = mine;

		const uom = item.stock_uom || "";
		if (d.verb === "take") s.hint.textContent = `Taking ${plain(-c)} → ${plain(d.after)} left`;
		else if (d.verb === "add") s.hint.textContent = `Adding ${plain(c)} → ${plain(d.after)} here`;
		else s.hint.textContent = available > 0 ? "Tap − to take, + to add" : "Tap + to add";

		let note = "";
		if (waiting) {
			note = "Your last save is still going through…";
		} else if (available <= 0 && (item.on_hand || 0) > 0) {
			note = `All ${plain(item.on_hand)} ${uom} here are reserved.`;
		} else if (available <= 0) {
			note = item.elsewhere && item.elsewhere.length ? "None recorded here. If it is on this shelf, use Move here." : "None recorded here.";
		} else if (c < 0 && c <= -available + 1e-9) {
			note = `Only ${plain(available)} ${uom} here.`;
		}
		s.note.textContent = note;
		s.note.hidden = !note;

		// Undo any earlier "Saving…" first (it keeps the label it replaced), then draw.
		busy(s.save, false);
		fill(
			s.save,
			...(d.verb === "take" ? [glyph("−"), el("span", null, d.label)] : d.verb === "add" ? [glyph("+"), el("span", null, d.label)] : [el("span", null, "Save")])
		);
		s.save.className = `ee-ss-btn ee-ss-save${d.verb === "take" ? " ee-ss-btn-take" : d.verb === "add" ? " ee-ss-btn-add" : ""}`;
		s.save.disabled = this.saving || d.verb === "none";
		if (mine) busy(s.save, true, "Saving…");
	}

	nudge(delta) {
		this.setChange(this.change + delta);
	}

	setChange(value) {
		const item = this.view.item;
		this.change = clampChange(value, item ? item.available : 0);
		this.showSaveError("");
		this.updateStepper();
	}

	/** The stepper's error line; `action` ({label, onClick}) adds its one way out. */
	showSaveError(text, action) {
		const s = this.stepper;
		if (!s) return;
		s.error.show(text, action);
	}

	/** "How many?" — for a box of 200 nobody taps + two hundred times. */
	askQuantity() {
		const item = this.view.item;
		if (!item || this.savingHere()) return;
		const available = Math.max(item.available || 0, 0);
		const field = input({
			className: "ee-ss-input ee-ss-qty-input",
			inputmode: item.whole_number ? "numeric" : "decimal",
			enterkeyhint: "done",
			label: "How many?",
			value: this.change ? plain(Math.abs(this.change)) : "",
		});
		const error = el("p", "ee-ss-field-error");
		error.setAttribute("role", "alert");
		error.hidden = true;
		const say = (text) => {
			error.textContent = text;
			error.hidden = !text;
			if (text) field.focus();
		};
		const read = () => {
			const r = parseQty(field.value, item.whole_number, item.stock_uom);
			if (r.problem) say(r.problem);
			return r.qty;
		};
		const take = () => {
			const qty = read();
			if (qty === null) return false;
			if (qty > available + 1e-9) {
				say(
					available > 0
						? `Only ${plain(available)} ${item.stock_uom} here.`
						: (item.on_hand || 0) > 0
							? `All ${plain(item.on_hand)} ${item.stock_uom} here are reserved.`
							: "None recorded here to take."
				);
				return false;
			}
			this.setChange(-qty);
			return true;
		};
		const add = () => {
			const qty = read();
			if (qty === null) return false;
			this.setChange(qty);
			return true;
		};
		const handle = sheet({
			title: "How many?",
			className: "ee-ss-qty-sheet",
			initialFocus: field,
			body: append(
				el("div", "ee-ss-qty"),
				append(el("div", "ee-ss-qty-row"), field, el("span", "ee-ss-qty-unit", item.stock_uom)),
				el("p", "ee-ss-note", `${plain(item.on_hand)} ${item.stock_uom} here now.`),
				error
			),
			actions: [
				{ label: "Take", glyph: "−", kind: "take", disabled: available <= 0, onClick: take },
				{ label: "Add", glyph: "+", kind: "add", onClick: add },
				{ label: "Cancel", kind: "ghost" },
			],
		});
		field.addEventListener("keydown", (ev) => {
			if (ev.key !== "Enter") return;
			ev.preventDefault();
			// Done on the keyboard keeps the direction already chosen; with none chosen, ask.
			let done = false;
			if (this.change < 0) done = take();
			else if (this.change > 0) done = add();
			else say("Tap Take or Add.");
			if (done) handle.close("action");
		});
	}

	// -----------------------------------------------------------------------
	// Saving
	// -----------------------------------------------------------------------

	save() {
		const v = this.view;
		if (v.name !== "item") return;
		if (this.saving) {
			// Save is disabled meanwhile; this is a take continuing after the job picker.
			toast(STILL_SAVING, { kind: "info" });
			return;
		}
		const item = v.item;
		const d = describeChange(this.change, item.stock_uom, item.on_hand);
		if (d.verb === "none") return;
		const qty = Math.abs(this.change);
		this.expireJob();
		if (d.verb === "take") {
			if (Number(this.settings.require_project_for_take) && !this.job) {
				this.pickJob({ reason: "take", then: () => this.save() });
				return;
			}
			this.post(v, M.TAKE, {
				action: "take",
				item_code: item.item_code,
				warehouse: item.warehouse,
				qty,
				project: this.job ? this.job.project : undefined,
				scanned_code: v.scannedCode || undefined,
			});
			return;
		}
		if ((item.open_orders && item.open_orders.length) || this.job) this.askWhereFrom(v, qty);
		else this.confirmWithoutOrder(v, qty);
	}

	/**
	 * "Where did these come from?" — each open order line; then, only while a job is picked,
	 * "Returned from <job>"; then "Not on a purchase order" (found, or not from a job).
	 *
	 * The job goes on an add ONLY when the person says the parts came back from it:
	 * `api/stock_scan.add` books a without-PO add that names a job as a RETURN from that job
	 * (offset to the account a take charges, so the job's cost nets out), and one without a job
	 * as found or unplanned stock. Sending the run's job with every add would credit found
	 * stock to whatever job happened to be picked.
	 */
	askWhereFrom(v, qty) {
		const item = v.item;
		const uom = item.stock_uom;
		const job = this.job;
		const orders = item.open_orders || [];
		const send = (extra) =>
			this.post(
				v,
				M.ADD,
				Object.assign(
					{ action: "add", item_code: item.item_code, warehouse: item.warehouse, qty, scanned_code: v.scannedCode || undefined },
					extra
				)
			);
		const choice = (handle, title, sub, extra) =>
			append(
				el("li"),
				button(
					[append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", title), el("span", "ee-ss-row-sub", sub)), icon("next", "ee-ss-row-go")],
					"ee-ss-row ee-ss-order",
					() => {
						handle.close("action");
						send(extra);
					}
				)
			);
		sheet({
			title: "Where did these come from?",
			body: (body, handle) => {
				body.appendChild(
					el(
						"p",
						"ee-ss-sheet-text",
						orders.length
							? `Adding ${plain(qty)} ${uom} of ${item.item_name}.`
							: `No open purchase order for ${item.item_name}. Adding ${plain(qty)} ${uom}.`
					)
				);
				const list = el("ul", "ee-ss-list");
				for (const line of orders) {
					const ordered = Number(line.ordered_qty || 0) * Number(line.conversion_factor || 1);
					const due = shortDate(line.expected_delivery_date || line.schedule_date);
					const parts = [
						el("span", "ee-ss-row-title", `${line.purchase_order} · ${line.supplier_name || line.supplier}`),
						el("span", "ee-ss-row-sub", `${plain(line.pending_stock_qty)} of ${plain(ordered)} ${uom} still to come${due ? ` · due ${due}` : ""}`),
					];
					if (qty > line.pending_stock_qty + 1e-9) {
						parts.push(el("span", "ee-ss-row-warn", `More than is still to come on this order.`));
					}
					const row = button([append(el("span", "ee-ss-row-main"), ...parts), icon("next", "ee-ss-row-go")], "ee-ss-row ee-ss-order", () => {
						handle.close("action");
						send({ purchase_order_item: line.purchase_order_item });
					});
					list.appendChild(append(el("li"), row));
				}
				if (job) {
					list.appendChild(
						choice(handle, `Returned from ${job.project_name || job.project}`, `Parts back from this job · ${job.project}`, {
							without_po: 1,
							project: job.project,
						})
					);
				}
				list.appendChild(choice(handle, "Not on a purchase order", "Found, or not from a job", { without_po: 1 }));
				body.appendChild(list);
				body.appendChild(el("p", "ee-ss-note", "A stock manager reviews anything added without a purchase order."));
			},
			actions: [{ label: "Cancel", kind: "ghost" }],
		});
	}

	/** No open order and no job picked: found or unplanned stock, so no job goes with it. */
	async confirmWithoutOrder(v, qty) {
		const item = v.item;
		const ok = await ask({
			title: "Not on a purchase order",
			body: `No open purchase order for ${item.item_name}. Add ${plain(qty)} ${item.stock_uom} without one? A stock manager will review it.`,
			ok: `Add ${plain(qty)} without one`,
			okKind: "add",
			okGlyph: "+",
		});
		if (!ok || this.view !== v) return;
		this.post(v, M.ADD, {
			action: "add",
			item_code: item.item_code,
			warehouse: item.warehouse,
			qty,
			without_po: 1,
			scanned_code: v.scannedCode || undefined,
		});
	}

	/**
	 * Post one save. `args.action` names it for the idempotency key and is not sent.
	 *
	 * `ui` is the Move sheet's `{button, showError, isOpen, onDone, keepChange}`; without one the
	 * save belongs to the item view `v` and its stepper. The answer belongs to whoever sent the
	 * save. If the person has moved on before it arrives — Scan next, Back, Search, the Move
	 * sheet closed — a failure is NOT written into whatever is on screen now, where it would
	 * vanish on the next render or land under a different item: it is raised as a toast naming
	 * the item, held until no sheet covers it (`whenNoSheets`), with Try again (same reference)
	 * after no answer, Open after a refusal, Reload when signed out.
	 */
	async post(v, method, args, ui) {
		const sheetUi = ui && ui.showError ? ui : null;
		if (this.saving) {
			if (sheetUi) sheetUi.showError(STILL_SAVING);
			else toast(STILL_SAVING, { kind: "info" });
			return;
		}
		const { action, ...body } = args;
		const key = saveKey(Object.assign({ action }, body));
		const kept = keptRef(this.pending.get(key), Date.now());
		if (!kept) this.pending.delete(key);
		const ref = kept || mintRef();
		body.client_ref = ref;
		const keepChange = !!(ui && ui.keepChange);

		// Where this save's own error line is still on screen, if anywhere.
		const onStepper = () => !sheetUi && this.view === v && !!this.stepper;
		const inSheet = () => !!sheetUi && (!sheetUi.isOpen || sheetUi.isOpen());
		if (inSheet()) sheetUi.showError("");
		else if (onStepper()) this.showSaveError("");
		this.saving = true;
		this.savingView = v;
		if (sheetUi) busy(sheetUi.button, true, "Saving…");
		this.updateStepper();
		try {
			const result = await call(method, body);
			// Saved (or handed back as already saved): the same save again is a new one.
			this.pending.delete(key);
			this.saving = false;
			this.savingView = null;
			if (sheetUi) busy(sheetUi.button, false);
			if (ui && ui.onDone) ui.onDone();
			this.afterSave(v, result, keepChange);
		} catch (e) {
			this.saving = false;
			this.savingView = null;
			if (sheetUi) busy(sheetUi.button, false);
			const retry = mayHaveSaved(e && e.status);
			// Nothing was saved after a refusal, so the next tap is a new save. After no answer
			// it may have been, so a tap within the retry window repeats this one and the server
			// dedupes it.
			if (retry) this.pending.set(key, { ref, at: Date.now() });
			else this.pending.delete(key);
			const text = (e && e.message) || "That did not save. Try again.";
			const line = needsReload(e) ? [text, RELOAD] : [retry ? `${text} Tapping again will not save it twice.` : text];
			if (inSheet()) {
				sheetUi.showError(...line);
			} else {
				// The item view underneath still gets the sentence if it is the one on screen; but
				// a sheet over it (the camera, a search) may be about to replace it, so unless the
				// view is plainly in front of the person the toast says it too.
				if (onStepper()) this.showSaveError(...line);
				if (!onStepper() || sheetDepth()) {
					const again = retry ? () => this.post(v, method, args, keepChange ? { keepChange: true } : undefined) : null;
					this.saveFailedElsewhere(v, args, e, text, again);
				}
			}
			this.updateStepper();
		}
	}

	/**
	 * A save failed after the person moved on from the view that sent it: say so wherever they
	 * are now, naming the item — "Take 3 Each of Widget did not save: …" — with the one thing
	 * that helps. Held until no sheet covers the toast.
	 */
	saveFailedElsewhere(v, args, error, text, again) {
		const item = (v && v.item) || {};
		const what = saveLabel(args.action, args.qty, item.stock_uom, item.item_name || item.item_code);
		const opts = { kind: "error", ms: 15000 };
		let message = `${what} did not save: ${text}`;
		if (needsReload(error)) {
			Object.assign(opts, { actionLabel: RELOAD.label, onAction: RELOAD.onClick });
		} else if (again) {
			message = `${what} may not have saved: ${text} Try again will not save it twice.`;
			Object.assign(opts, { actionLabel: "Try again", onAction: again });
		} else if (item.item_code && item.warehouse) {
			Object.assign(opts, { actionLabel: "Open", onAction: () => this.openItem(item.item_code, item.warehouse, { code: v.scannedCode }) });
		}
		whenNoSheets(() => toast(message, opts));
	}

	/**
	 * A save went through. `keepChange`: the save was a Move, not the stepper's own Save, so a
	 * quantity already dialled on the stepper stays (within what is now available).
	 *
	 * `result.repeated`: the server already had a save with this reference and posted nothing
	 * new. Usually that is the retry of a save whose answer was lost, and the earlier attempt
	 * did the work — but it may be a person who meant a second, identical save. So it is said
	 * as exactly that, and the dialled change stays: the reference is spent (`pending` dropped
	 * it above), so tapping Save again posts a genuinely new save.
	 */
	afterSave(v, result, keepChange) {
		const log = result && result.log;
		const repeated = !!(result && result.repeated === true);
		if (log) {
			this.recent = [log].concat(this.recent.filter((r) => r.name !== log.name)).slice(0, RECENT_LIMIT);
		}
		const fresh = result && result.item;
		const cur = this.view;
		if (fresh && cur.name === "item" && cur.item.warehouse === fresh.warehouse && cur.item.item_code === fresh.item_code) {
			// The view that saved — or the same item reached again since — gets the new numbers.
			// A change dialled on a different view than the one that saved is the person's own.
			cur.item = fresh;
			this.change = cur === v && !keepChange && !repeated ? 0 : clampChange(this.change, fresh.available);
			this.render(false);
		} else {
			this.updateStepper();
		}
		const undo = log && log.can_undo ? { actionLabel: "Undo", onAction: () => this.confirmUndo(log) } : {};
		if (repeated) toast(REPEATED, Object.assign({ kind: "info", ms: 9000 }, undo));
		else toast((result && result.message) || "Saved.", Object.assign({ kind: "ok" }, undo));
		buzz(40);
		// The next thing is almost always the next shelf: draw the eye to Scan next.
		this.scanBtn.classList.remove("is-pulse");
		void this.scanBtn.offsetWidth;
		this.scanBtn.classList.add("is-pulse");
	}

	// --- Move here ------------------------------------------------------------------------

	/** Put-away: bring stock recorded somewhere else (today, mostly Stores) onto this shelf. */
	openMove() {
		const v = this.view;
		const item = v.item;
		if (!item || !item.elsewhere || !item.elsewhere.length) return;
		const uom = item.stock_uom;
		let handle = null;
		// A Move in flight outlives its sheet if the person closes it; its answer then goes to a
		// toast (`post`), not to an error line nobody can see.
		let closed = false;

		const chooseSource = (body) => {
			handle.setTitle("Move here from…");
			fill(body, el("p", "ee-ss-sheet-text", `Where is ${item.item_name} recorded now?`));
			const list = el("ul", "ee-ss-list");
			for (const place of item.elsewhere) {
				const free = Math.max(place.available || 0, 0);
				const row = button(
					[
						icon("pin", "ee-ss-row-icon"),
						append(
							el("span", "ee-ss-row-main"),
							el("span", "ee-ss-row-title", place.warehouse_name),
							el("span", "ee-ss-row-sub", free > 0 ? placeAmount(place, uom) : `${plain(place.on_hand)} ${uom} on hand, all reserved`)
						),
						icon("next", "ee-ss-row-go"),
					],
					"ee-ss-row",
					() => chooseQty(body, place)
				);
				row.disabled = free <= 0;
				list.appendChild(append(el("li"), row));
			}
			body.appendChild(list);
			handle.setActions([{ label: "Cancel", kind: "ghost" }]);
		};

		const chooseQty = (body, place) => {
			const free = Math.min(Math.max(place.available || 0, 0), MAX_QTY);
			handle.setTitle(`From ${place.warehouse_name}`);
			const field = input({
				className: "ee-ss-input ee-ss-qty-input",
				inputmode: item.whole_number ? "numeric" : "decimal",
				enterkeyhint: "done",
				label: "How many to move",
				value: plain(free),
			});
			const error = errorLine("ee-ss-field-error");
			const showError = (text, action) => error.show(text, action);
			const current = () => parseQty(field.value, item.whole_number, uom);
			const step = (delta) => {
				const r = current();
				const next = Math.min(Math.max((r.qty || 0) + delta, 1), free);
				field.value = plain(next);
				relabel();
			};
			const less = button("−", "ee-ss-mini-step", () => step(-1));
			less.setAttribute("aria-label", "One less");
			const more = button("+", "ee-ss-mini-step", () => step(1));
			more.setAttribute("aria-label", "One more");
			fill(
				body,
				el("p", "ee-ss-sheet-text", `${placeAmount(place, uom)} there. How many are on this shelf now?`),
				append(el("div", "ee-ss-qty-row"), less, field, more, el("span", "ee-ss-qty-unit", uom)),
				error.node
			);
			const go = () => {
				const r = current();
				if (r.problem) return showError(r.problem);
				if (r.qty > free + 1e-9) return showError(`Only ${plain(free)} ${uom} can be moved from ${place.warehouse_name}.`);
				this.post(
					v,
					M.MOVE,
					{
						action: "move",
						item_code: item.item_code,
						warehouse: item.warehouse,
						from_warehouse: place.warehouse,
						qty: r.qty,
						scanned_code: v.scannedCode || undefined,
					},
					{ button: moveBtn(), showError, isOpen: () => !closed, onDone: () => handle.close("action"), keepChange: true }
				);
			};
			const moveBtn = () => handle.el.querySelector(".ee-ss-sheet-foot .ee-ss-btn-primary");
			const relabel = () => {
				const r = current();
				const btn = moveBtn();
				if (btn && !this.saving) fill(btn, icon("move"), el("span", null, r.qty ? `Move ${plain(r.qty)} here` : "Move here"));
				showError("");
			};
			handle.setActions([
				{ label: "Move here", kind: "primary", large: true, close: false, onClick: go },
				{ label: "Back", kind: "ghost", close: false, onClick: () => chooseSource(body) },
			]);
			field.addEventListener("input", relabel);
			field.addEventListener("keydown", (ev) => {
				if (ev.key === "Enter") {
					ev.preventDefault();
					go();
				}
			});
			relabel();
		};

		handle = sheet({
			title: "Move here from…",
			body: (body) => body,
			actions: [],
			onClose: () => {
				closed = true;
			},
		});
		chooseSource(handle.body);
	}

	// --- Undo ---------------------------------------------------------------------------

	async confirmUndo(log) {
		const ok = await ask({ title: "Undo this?", body: undoQuestion(log), ok: "Undo it", okKind: "primary" });
		if (ok) this.undo(log);
	}

	async undo(log) {
		this.setLoading(true);
		try {
			const result = await call(M.UNDO, { log: log.name });
			const fresh = result && result.log;
			if (fresh) this.recent = this.recent.map((r) => (r.name === fresh.name ? fresh : r));
			toast((result && result.message) || "Undone.", { kind: "ok" });
			buzz(30);
			this.afterUndo(fresh || log, result && result.item);
		} catch (e) {
			this.fail(e);
			// The server knows whether this can still be undone; the page's copy may be stale.
			if (!mayHaveSaved(e && e.status)) this.refreshRecent();
		} finally {
			this.setLoading(false);
		}
	}

	/** Redraw whatever on screen the undone save touched. */
	afterUndo(log, item) {
		const v = this.view;
		const places = [log.warehouse, log.from_warehouse].filter(Boolean);
		if (v.name === "item" && places.indexOf(v.item.warehouse) !== -1) {
			if (item && item.item_code === v.item.item_code && item.warehouse === v.item.warehouse && !this.savingHere()) {
				v.item = item;
				this.change = clampChange(this.change, item.available);
				this.render(false);
			} else if (v.item.item_code === log.item_code) {
				this.refreshView(v);
			}
		} else if (v.name === "location" && v.location && places.indexOf(v.location.warehouse) !== -1) {
			this.refreshView(v);
		} else if (v.name === "free" && v.item.item_code === log.item_code) {
			// "Where is it?" lists the item's quantities per location; the undo just changed one.
			this.refreshView(v);
		} else if (v.name === "start") {
			this.render(false);
		}
	}

	// -----------------------------------------------------------------------
	// The job for this run
	// -----------------------------------------------------------------------

	expireJob() {
		if (this.job && jobExpired(this.job.saved_at)) {
			this.job = null;
			writeJob(null);
			this.renderJobChip();
		}
	}

	setJob(job) {
		// Stamped with who picked it: `readJob` hands it back to that person only.
		this.job = job
			? {
					project: job.project,
					project_name: job.project_name,
					customer: job.customer || "",
					saved_at: Date.now(),
					user: this.boot.user || "",
				}
			: null;
		writeJob(this.job);
		this.renderJobChip();
	}

	/** Pick the job parts are taken for. `opts.then` continues a take that needed one. */
	pickJob(opts) {
		const o = opts || {};
		const required = o.reason === "take";
		const search = input({ placeholder: "Search jobs", label: "Search jobs", enterkeyhint: "search", type: "search" });
		const status = el("p", "ee-ss-search-status");
		status.setAttribute("role", "status");
		const list = el("ul", "ee-ss-list");
		let handle = null;
		let seq = 0;
		let timer = null;

		const choose = (job) => {
			this.setJob(job);
			handle.close("action");
			if (job && o.then) o.then();
			else if (!job && required) toast("Pick the job these parts are for, then tap Take again.", { kind: "info" });
		};

		const draw = (rows, query) => {
			fill(list);
			const none = button(
				[glyph("✕", "ee-ss-row-icon"), append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", "No job"), el("span", "ee-ss-row-sub", "Parts are not for a job"))],
				`ee-ss-row${this.job ? "" : " is-current"}`,
				() => choose(null)
			);
			if (!required) list.appendChild(append(el("li"), none));
			for (const row of rows) {
				const current = this.job && this.job.project === row.project;
				const b = button(
					[
						append(
							el("span", "ee-ss-row-main"),
							el("span", "ee-ss-row-title", row.project_name),
							el("span", "ee-ss-row-sub", [row.customer, row.project].filter(Boolean).join(" · "))
						),
						current ? glyph("✓", "ee-ss-row-check") : null,
					],
					`ee-ss-row${current ? " is-current" : ""}`,
					() => choose(row)
				);
				if (current) b.setAttribute("aria-current", "true");
				list.appendChild(append(el("li"), b));
			}
			if (!rows.length) {
				status.textContent = query ? `No job matches "${query}".` : "You can't see any jobs. Ask a manager to give you access.";
			} else {
				status.textContent = "";
			}
		};

		const load = async (query) => {
			const mine = ++seq;
			status.textContent = "Loading jobs…";
			try {
				const rows = await call(M.SEARCH_PROJECTS, { query });
				if (mine === seq) draw(Array.isArray(rows) ? rows : [], query);
			} catch (e) {
				if (mine === seq) status.textContent = e.message || "Could not load jobs. Try again.";
			}
		};

		search.addEventListener("input", () => {
			if (timer) clearTimeout(timer);
			timer = setTimeout(() => load(search.value.trim()), SEARCH_DEBOUNCE_MS);
		});

		handle = sheet({
			title: required ? "Which job are these parts for?" : "Which job?",
			full: true,
			// Do not raise the keyboard over the list: most runs pick a recent job with one tap.
			initialFocus: "sheet",
			body: (body) => append(body, append(el("div", "ee-ss-search-field"), search), status, list),
			onClose: () => {
				if (timer) clearTimeout(timer);
				seq += 1;
			},
		});
		load("");
	}

	// -----------------------------------------------------------------------
	// Search
	// -----------------------------------------------------------------------

	/**
	 * One search box. With a location open it finds items there (and their quantity here);
	 * with none it finds locations and items. `opts.addHere` is the "Add an item here" door.
	 * `opts.locationsOnly` + `opts.onLocation` pick a location for "Where is it?".
	 */
	openSearch(opts) {
		const o = opts || {};
		const here = o.locationsOnly ? null : this.here();
		const wantLocations = !here;
		const wantItems = !o.locationsOnly;
		const field = input({
			placeholder: o.locationsOnly ? "Location name" : here ? "Item name or code" : "Item or location",
			label: "Search",
			enterkeyhint: "search",
			type: "search",
		});
		const status = el("p", "ee-ss-search-status", "Type a few letters.");
		status.setAttribute("role", "status");
		const setStatus = (text, isError) => {
			status.textContent = text || "";
			status.classList.toggle("is-error", !!isError);
		};
		const results = el("div", "ee-ss-results");
		let handle = null;
		let seq = 0;
		let timer = null;
		let lastItems = [];

		const pickItem = (row) => {
			handle.close("action");
			if (here) this.openItem(row.item_code, here.warehouse, {});
			else this.openItem(row.item_code, null, {});
		};
		const pickLocation = (row) => {
			handle.close("action");
			if (o.onLocation) o.onLocation(row);
			else this.openLocation(row.warehouse, {});
		};

		const itemRow = (row) => {
			const qty = here ? row.on_hand_here : row.on_hand_total;
			const sub = append(el("span", "ee-ss-row-sub"), row.item_code);
			if (!row.is_stock_item) append(sub, " ", el("span", "ee-ss-badge", "not tracked"));
			return append(
				el("li"),
				button(
					[
						thumb(row),
						append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", row.item_name), sub),
						row.is_stock_item
							? append(el("span", `ee-ss-row-qty${qty > 0 ? "" : " is-zero"}`), el("strong", null, plain(qty || 0)), el("span", null, here ? "here" : "on hand"))
							: null,
					],
					"ee-ss-row",
					() => pickItem(row)
				)
			);
		};
		const locationRow = (row) =>
			append(
				el("li"),
				button(
					[
						icon("pin", "ee-ss-row-icon"),
						append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", row.warehouse_name), row.trail ? el("span", "ee-ss-row-sub", row.trail) : null),
						icon("next", "ee-ss-row-go"),
					],
					"ee-ss-row",
					() => pickLocation(row)
				)
			);

		const run = async (query) => {
			const mine = ++seq;
			if (!query) {
				fill(results);
				setStatus("Type a few letters.");
				return;
			}
			setStatus("Searching…");
			try {
				const [locations, items] = await Promise.all([
					wantLocations ? call(M.SEARCH_LOCATIONS, { query }) : Promise.resolve([]),
					wantItems ? call(M.SEARCH_ITEMS, { query, warehouse: here ? here.warehouse : undefined }) : Promise.resolve([]),
				]);
				if (mine !== seq) return;
				lastItems = items || [];
				fill(results);
				if (locations && locations.length) {
					if (wantItems) results.appendChild(el("h3", "ee-ss-group-title", "Locations"));
					results.appendChild(append(el("ul", "ee-ss-list"), ...locations.map(locationRow)));
				}
				if (lastItems.length) {
					if (wantLocations) results.appendChild(el("h3", "ee-ss-group-title", "Items"));
					results.appendChild(append(el("ul", "ee-ss-list"), ...lastItems.map(itemRow)));
				}
				setStatus((locations && locations.length) || lastItems.length ? "" : `Nothing matches "${query}".`);
			} catch (e) {
				if (mine === seq) setStatus((e && e.message) || "Search failed. Try again.", true);
			}
		};

		field.addEventListener("input", () => {
			if (timer) clearTimeout(timer);
			timer = setTimeout(() => run(field.value.trim()), SEARCH_DEBOUNCE_MS);
		});
		field.addEventListener("keydown", async (ev) => {
			if (ev.key !== "Enter") return;
			ev.preventDefault();
			// Enter answers now. A search still waiting out its debounce must not fire afterwards
			// and write over what Enter found.
			if (timer) clearTimeout(timer);
			timer = null;
			const query = field.value.trim();
			if (!query) return;
			// An exact code typed or wedged in goes straight there, like a scan.
			const exact = lastItems.find((r) => String(r.item_code).toLowerCase() === query.toLowerCase());
			if (exact && !o.locationsOnly) {
				pickItem(exact);
				return;
			}
			if (o.locationsOnly) {
				run(query);
				return;
			}
			seq += 1; // ... nor may a search already in flight
			setStatus("Looking up…");
			const res = await this.resolve(query, { quiet: true });
			if (!res) {
				// A newer lookup or a navigation took over; do not leave "Looking up…" behind.
				if (status.textContent === "Looking up…") setStatus("");
				return;
			}
			if (res.kind === "location" || res.kind === "item") handle.close("action");
			// No answer at all (no signal, signed out, a crash) is said as that, never as "no match".
			else if (res.kind === "error") setStatus(res.message, true);
			else setStatus(res.message || `Nothing matches "${query}".`);
		});

		handle = sheet({
			title: o.locationsOnly ? "Which location?" : o.addHere && here ? `Add an item to ${here.warehouse_name}` : here ? `Find an item at ${here.warehouse_name}` : "Search",
			full: true,
			initialFocus: field,
			body: (body) => append(body, append(el("div", "ee-ss-search-field"), field), status, results),
			onClose: () => {
				if (timer) clearTimeout(timer);
				seq += 1;
			},
		});
	}

	// --- Item with no location: "Where is it?" -------------------------------------------

	renderFree(v) {
		const item = v.item;
		const view = el("section", "ee-ss-view ee-ss-item");
		append(view, this.backLink(), this.itemHeader(item));
		if (item.blocked) {
			const notice = el("p", "ee-ss-notice", item.blocked);
			notice.setAttribute("role", "note");
			view.appendChild(notice);
			return view;
		}
		view.appendChild(el("h3", "ee-ss-section-title", "Where is it?"));
		const places = item.elsewhere || [];
		if (places.length) {
			const list = el("ul", "ee-ss-list");
			for (const place of places) {
				const row = button(
					[
						icon("pin", "ee-ss-row-icon"),
						append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", place.warehouse_name), el("span", "ee-ss-row-sub", placeAmount(place, item.stock_uom))),
						icon("next", "ee-ss-row-go"),
					],
					"ee-ss-row",
					() => this.openItem(item.item_code, place.warehouse, { code: v.scannedCode, row })
				);
				list.appendChild(append(el("li"), row));
			}
			view.appendChild(list);
		} else {
			view.appendChild(el("p", "ee-ss-note", "None on hand anywhere yet."));
		}
		view.appendChild(
			button([icon("pin"), el("span", null, "Another location…")], "ee-ss-btn ee-ss-btn-outline", () =>
				this.openSearch({
					locationsOnly: true,
					onLocation: (loc) => this.openItem(item.item_code, loc.warehouse, { code: v.scannedCode }),
				})
			)
		);
		return view;
	}
}
