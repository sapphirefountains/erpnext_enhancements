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
 * back stack, mirrored into browser history entries that carry NO URL (nav.js), so the phone's
 * Back and Forward walk the screens and Back closes a sheet first. The URL itself never
 * changes — no URL argument, no hash: iOS Safari asks for camera permission again whenever the
 * URL changes, and the camera is used on every shelf. `BROWSER_HISTORY` switches the mirror off.
 *
 * "Report a problem" (the capture panel, WI-079) opens from the header on every view. The panel
 * owns its own history entry and its own Back; this page stands aside while it is open.
 *
 * STORE RUNS (v1.536.0, POL-0602 §4.7-4.8): "Bought on a store run" on + records a part bought
 * at a walk-in counter — one line per save, a submitted Purchase Receipt with no PO — with the
 * store, the price, the reason, the job and the receipt photo; a run id ties one trip's lines
 * together and the run bar keeps it in view while the technician scans the next bin. A part
 * not in ERPNext gets a quick Item, its name checked live against the naming rules with the
 * nearest existing items shown first. `boot.store_run` is null wherever that is not set up, and
 * then none of it appears.
 *
 * SAVES post immediately (submitted vouchers) and can be undone for a while. Each intended
 * save carries a `client_ref`; a retry after no answer reuses it (for `RETRY_WINDOW_MS`), so
 * the server returns the first save instead of posting twice (`logic.saveKey` /
 * `logic.mayHaveSaved` / `logic.keptRef`). A save's answer belongs to the view that sent it:
 * if the person has moved on by the time it arrives, a failure is raised as a toast naming the
 * item rather than written into whatever is on screen now (`post`).
 */

import { M, call, upload } from "./transport.js";
import {
	MAX_QTY,
	REASON_ONLY_THIS_JOB,
	boughtLabel,
	clampChange,
	describeChange,
	formatWhen,
	jobExpired,
	keptRef,
	logHeadline,
	mayHaveSaved,
	mintRef,
	mintRunId,
	money,
	ordersAtStore,
	parseQty,
	plain,
	reasonWarning,
	receiptCheck,
	rememberedJob,
	reopenedDraft,
	reportAvailable,
	runIsOpen,
	runOffered,
	runSummary,
	saveKey,
	saveLabel,
	scanFailure,
	shortDate,
	siteTimeMs,
	suggestedReason,
	undoQuestion,
	validPrice,
	validTotal,
} from "./logic.js";
import { append, button, el, fill, glyph, icon, img, input, select, shrinkPhoto, thumb } from "./dom.js";
import {
	ask,
	busy,
	buzz,
	closeAllSheets,
	mountToasts,
	onSheetChange,
	readTheme,
	setTheme,
	sheet,
	sheetDepth,
	syncThemeMeta,
	toast,
	whenNoSheets,
} from "./ui.js";
import { openScanner } from "./scanner.js";
import { NavHistory } from "./nav.js";

/**
 * Browser Back/Forward (nav.js). `false` turns off the page's OWN history entries — screens and
 * sheet markers — and puts it back on its in-memory stack alone (NavHistory with no history
 * object does nothing). The switch people actually use is the "Turn Off Browser Back on Stock
 * Scan" box in Inventory Scanner Settings, which reaches the page as `settings.browser_history`
 * = 0 and does the same without a deploy, if an iPhone ever re-prompts for the camera. The
 * report form pushes an entry of its own while it is open; the template hands the same setting
 * to capture/panel.js as `EE_CAPTURE.history` (`wantsHistoryEntry`), so the box turns off both.
 */
const BROWSER_HISTORY = true;

const JOB_KEY = "ee_ss_job";
const RECENT_LIMIT = 12;
const SEARCH_DEBOUNCE_MS = 250;
const START = { name: "start" };
const STILL_SAVING = "Your last save is still going through. Try again in a moment.";
const REPEATED = "That save was already recorded — nothing new was posted. Tap Save again to record another.";
/** How a store run's wrong receipt total is put right: its receipts are submitted, so they are undone and recorded again. */
const RESTART_RUN =
	"If the total is wrong, undo the run's lines (under Recent, on the start screen) and record them again: the run then asks for its receipt again, filled in to correct. Past the undo window, tell Purchasing.";

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

// ---------------------------------------------------------------------------
// Store runs (v1.536.0): which run this phone is adding to, and which it has finished. Only
// markers live here. The runs themselves come from the server (boot.store_run.open_runs and
// every store-run save's `run`), so a cleared or blocked storage loses nothing but the bar:
// the run is still offered as "Add to your … run" when + is pressed.
// ---------------------------------------------------------------------------

const RUN_KEY_PREFIX = "ee-ss-store-run:";

function readRunState(user) {
	const empty = { current: null, finished: [] };
	try {
		const raw = window.localStorage.getItem(RUN_KEY_PREFIX + (user || ""));
		if (!raw) return empty;
		const value = JSON.parse(raw) || {};
		return {
			current: typeof value.current === "string" ? value.current : null,
			finished: Array.isArray(value.finished) ? value.finished.filter((id) => typeof id === "string").slice(-20) : [],
		};
	} catch (e) {
		return empty;
	}
}

function writeRunState(user, state) {
	try {
		window.localStorage.setItem(RUN_KEY_PREFIX + (user || ""), JSON.stringify(state));
	} catch (e) {
		/* storage blocked: the bar holds until the page reloads, and the run is still offered */
	}
}

/** A form row: a caption over its control. `group` for a set of buttons, which a <label> must not wrap. */
function field(caption, control, note, group) {
	const node = el(group ? "div" : "label", "ee-ss-field");
	const title = el("span", "ee-ss-field-label", caption);
	if (group && control && control.setAttribute) control.setAttribute("aria-label", caption);
	return append(node, title, control, note || null);
}

/** One option of a set that behaves like radio buttons (a store, a day). */
function choiceButton(label, on, onClick) {
	const node = button(label, `ee-ss-choice${on ? " is-on" : ""}`, onClick);
	node.setAttribute("role", "radio");
	node.setAttribute("aria-checked", on ? "true" : "false");
	return node;
}

function firstName(full) {
	return String(full || "").trim().split(/\s+/)[0] || "someone";
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
		this.settings = Object.assign({ require_project_for_take: 0, undo_window_minutes: 0, browser_history: 1 }, this.boot.settings || {});
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
		const history = BROWSER_HISTORY && this.settings.browser_history !== 0 ? window.history : null;
		this.nav = new NavHistory(history, mintRef(), { busy: () => this.loads > 0 });
		this.report = null; // the capture panel's handle while it is open
		this.reportWanted = false; // "Report a problem" tapped, the panel still on its way
		this.reportLoading = false; // capture.open() asked and not answered yet (Back may have unwanted it)
		// "Bought on a store run" (v1.536.0): null unless a store is flagged and the fields exist.
		const storeRun = this.boot.store_run;
		this.storeRun = storeRun && Array.isArray(storeRun.suppliers) && storeRun.suppliers.length ? storeRun : null;
		this.runs = this.storeRun && Array.isArray(this.storeRun.open_runs) ? this.storeRun.open_runs.slice() : [];
		this.runState = readRunState(this.boot.user);
		// The site's clock at boot, and when that was here: `siteNow()` for how long ago another
		// person's run was last added to, without the phone's own clock or zone.
		this.bootNow = siteTimeMs(this.boot.now);
		this.bootAt = Date.now();
		// The run not saved yet: its id is minted once and kept with its photo and header until a
		// line posts, so a retry after no answer (or reopening the sheet) sends the SAME run id.
		this.newRun = null;
		// The last line's reason and job, offered again on the next line of the same run.
		this.lastLine = null;
		this.runBar = null;
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
		// "Report a problem" (the capture panel, WI-079), in the header on every view: the floating
		// launcher would sit on Scan. Hidden when the recorder is not on the page or the viewer is
		// not a System User (the server checks again).
		this.reportBtn = button([icon("report"), el("span", "ee-ss-top-btn-text", "Report")], "ee-ss-top-btn", () => this.openReport());
		this.reportBtn.setAttribute("aria-label", "Report a problem");
		this.reportBtn.setAttribute("aria-haspopup", "dialog");
		this.reportBtn.hidden = !reportAvailable(window.ee_capture, document.cookie);
		this.top = append(el("header", "ee-ss-top"), this.titleEl, this.jobChip, this.reportBtn, this.progress);

		this.main = el("main", "ee-ss-main");
		// The open store run on this phone, above every view while it lasts (render keeps it first).
		this.runBar = el("div", "ee-ss-runbar");
		this.runBar.setAttribute("role", "status");
		this.runBar.hidden = true;

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
		this.renderRunBar();
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

		onSheetChange((opened) => (opened ? this.nav.overlayOpened() : this.nav.overlayClosed()));
		window.addEventListener("popstate", (ev) => this.onPopState(ev.state));
		// Back from another page restored this one whole (bfcache): check the entry anyway.
		window.addEventListener("pageshow", (ev) => {
			if (ev.persisted) this.onPopState(window.history.state);
		});
		this.registerCaptureState();

		// The first screen is stamped onto the entry the phone opened (replaceState, nav.js), so
		// Back from it leaves the page as it always has.
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
		// A sheet that closed into a navigation keeps its marker until that navigation lands (nav.js).
		if (!this.loads) this.nav.settled();
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
	 *
	 * `entry`: the browser history entry the view gets (nav.js). "push" (the default) for every
	 * screen the person asked for, "replace" for one the page opened by itself. The document's
	 * first screen, and one reached from a sheet or the camera, replace whatever `entry` says.
	 */
	enter(view, mode, entry) {
		if (mode === "push") this.back.push(this.view);
		else if (mode === "root") this.back = view.name === "start" ? [] : [START];
		this.view = view;
		this.change = 0;
		this.nav.screen(this.snapshot(), entry || "push");
		this.render(true);
	}

	/** What a history entry remembers of a screen. Kept in memory by nav.js, never in `history.state`. */
	snapshot() {
		return { view: this.view, back: this.back.slice() };
	}

	goBack() {
		if (this.reportBusy()) return;
		const parent = this.back[this.back.length - 1] || START;
		if (this.nav.behindIs(parent, this.back.slice(0, -1))) {
			// The entry behind this one IS the parent: step back onto it, so this link and the
			// phone's Back walk the same list. popstate draws it (onPopState). A lookup still
			// loading must not land in the meantime.
			++this.seq;
			this.nav.back();
			return;
		}
		// Otherwise (a scan made Start the parent, a shelf was put in between, history is off)
		// go up as a NEW entry, so the phone's Back still retraces the steps actually taken.
		this.back.pop();
		this.show(parent, this.back, "push");
	}

	/** Put a remembered view back: the back link, Back, Forward. What it showed may be stale. */
	show(view, back, entry) {
		++this.seq;
		this.view = view;
		this.back = back.slice();
		this.change = 0;
		if (entry) this.nav.screen(this.snapshot(), entry);
		this.render(true);
		// What was on screen before may be stale: a save just changed its numbers.
		this.refreshView(view);
	}

	/** The browser moved to another entry: Back, Forward, our own nav.back(), or a bfcache restore. */
	onPopState(state) {
		// The report form pushed that entry and answers Back itself (capture/panel.js): it asks
		// before throwing a typed report away. Nothing here moves while it is open.
		if (this.reportOpen()) return;
		const step = this.nav.popped(state);
		// Back while a lookup started from the camera or a search was loading: never mind it.
		if (step.cancel) ++this.seq;
		// Back closes what covers the page, as its × would. The browser already stepped off the
		// marker, so nothing here steps back again.
		if (step.close) closeAllSheets();
		if (step.snap) this.show(step.snap.view, step.snap.back);
		// Back or Forward while the report form was still on its way: never mind it either.
		if (step.snap || step.close) this.reportWanted = false;
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
			// Opened FOR the person: the item takes over this location's history entry, so the
			// phone's Back does not stop on a list they never looked at, and no entry is added
			// without a tap (nav.js).
			this.openItem(items[0].item_code, location.warehouse, { code: o.code, auto: true });
		}
	}

	/**
	 * An item payload. At a location it sits on top of that location (so its back link reads
	 * "All items at …"); without one it is "Where is it?".
	 *
	 * `opts.auto`: the page opened it by itself (a scanned bin holding one item), so it replaces
	 * the current history entry. Otherwise it is a new entry, even where the in-page stack
	 * swaps: after a camera scan the marker's entry is on top and a new one is taken anyway, so
	 * the scanner gun and the camera build the same history — and the phone's Back is
	 * chronological, "the item I just looked at".
	 */
	showItem(item, opts) {
		const o = opts || {};
		const entry = o.auto ? "replace" : "push";
		if (!item.warehouse) {
			this.enter({ name: "free", item, scannedCode: o.code || "" }, "root", entry);
			return;
		}
		const view = { name: "item", item, scannedCode: o.code || "" };
		const cur = this.view;
		if (cur.name === "item" && cur.item.warehouse === item.warehouse) {
			// The next item on the same shelf: swap it in, keep the way back to the shelf.
			this.enter(view, "replace", entry);
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
		this.enter(view, "replace", entry);
	}

	/** Fetch an item (at `warehouse` when given) and show it. `row` shows a spinner meanwhile. */
	async openItem(itemCode, warehouse, opts) {
		if (this.reportBusy()) return;
		const o = opts || {};
		const seq = ++this.seq;
		if (o.row) o.row.classList.add("is-loading");
		this.setLoading(true);
		try {
			const item = await call(M.ITEM, { item_code: itemCode, warehouse: warehouse || undefined });
			if (seq !== this.seq) return;
			this.showItem(item, { code: o.code, auto: o.auto });
		} catch (e) {
			if (seq === this.seq) this.fail(e, { retry: () => this.openItem(itemCode, warehouse, o) });
		} finally {
			this.setLoading(false);
			if (o.row) o.row.classList.remove("is-loading");
		}
	}

	async openLocation(warehouse, opts) {
		if (this.reportBusy()) return;
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
		if (this.scanner || this.reportBusy()) return;
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
		if (this.reportBusy()) return null;
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
		if (sheetDepth() || this.scanner || this.reportBusy() || ev.ctrlKey || ev.metaKey || ev.altKey) return;
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
		fill(this.main, this.runBar, node);
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
		// A store run names the store it came from: "Home Depot → Bin A1".
		const where =
			log.action === "Move"
				? `${log.from_warehouse_name} → ${log.warehouse_name}`
				: log.action === "Store Run" && log.supplier
					? `${log.supplier} → ${log.warehouse_name}`
					: log.warehouse_name;
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
			// Not kept in stock, but a part the crew does buy at a counter (most tools and
			// consumables are): the store run is still recorded, with no stock added.
			if (item.store_run_ok && this.storeRunReady()) {
				view.appendChild(
					button([glyph("+"), el("span", null, "Bought it on a store run")], "ee-ss-btn ee-ss-btn-add is-lg ee-ss-nonstock-run", () =>
						this.startStoreRun({ v, item, qty: 1 })
					)
				);
			}
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
		if (!item || this.savingHere() || this.reportBusy()) return;
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
		if (v.name !== "item" || this.reportBusy()) return;
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
		const storeRun = this.storeRunReady() && item.store_run_ok !== false;
		if ((item.open_orders && item.open_orders.length) || this.job || storeRun) this.askWhereFrom(v, qty);
		else this.confirmWithoutOrder(v, qty);
	}

	/**
	 * "Where did these come from?" — each open order line FIRST (a planned pickup recorded as a
	 * store run would leave its order open and be received twice); then, where store runs are
	 * on, "Add to your <store> run" for each run still open today (anyone's: two people on one
	 * trip is one run) and "Bought on a store run"; then, only while a job is picked, "Returned
	 * from <job>"; then "Not on a purchase order" (found, or not from a job).
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
		const storeRun = this.storeRunReady() && item.store_run_ok !== false;
		const runs = storeRun ? this.offeredRuns().slice(0, 3) : [];
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
				if (storeRun) {
					const open = (run) => {
						handle.close("action");
						this.openStoreRun({ v, item, qty, run });
					};
					for (const row of this.runRows(runs, open)) list.appendChild(row);
					list.appendChild(this.runChoice("Bought on a store run", `${this.storeNames()} · with the receipt photo`, () => open(null)));
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
				body.appendChild(el("p", "ee-ss-note", "Anything added without a purchase order is reviewed: store runs by Purchasing, the rest by a stock manager."));
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
			// A store-run line has more to do once it is in (the run bar, the item at its bin,
			// "take them to the job now?"). Caught on its own: a failure there is not the save's,
			// and must never be reported as "did not save".
			if (ui && ui.after) {
				try {
					ui.after(result);
				} catch (err) {
					/* the save is in; what follows is a convenience */
				}
			}
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
					const again = retry ? () => this.post(v, method, args, { keepChange, after: ui && ui.after }) : null;
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
		const viewItem = (v && v.item) || {};
		// The save's own item: a store-run line may be for a new item, or for one picked in its
		// sheet, while the view that sent it shows a location or another item.
		const created = args.new_item || null;
		const item = created ? {} : !args.item_code || viewItem.item_code === args.item_code ? viewItem : { item_code: args.item_code };
		const name = created ? created.item_name || created.item_code : item.item_name || item.item_code;
		const what = saveLabel(args.action, args.qty, created ? created.stock_uom : item.stock_uom, name);
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
		if (!item || !item.elsewhere || !item.elsewhere.length || this.reportBusy()) return;
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
		if (this.reportBusy()) return;
		const ok = await ask({ title: "Undo this?", body: undoQuestion(log), ok: "Undo it", okKind: "primary" });
		if (ok) this.undo(log);
	}

	async undo(log) {
		this.setLoading(true);
		try {
			const result = await call(M.UNDO, { log: log.name });
			const fresh = result && result.log;
			if (fresh) this.recent = this.recent.map((r) => (r.name === fresh.name ? fresh : r));
			// A store-run line: its run's count and total change (the rest of the run stays).
			if (result && result.run) this.noteRun(result.run);
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

	/**
	 * Pick the job parts are taken for. `opts.then` continues a take that needed one.
	 * `opts.reason` "store_run": a store-run line's job, where "No job" is the line's own choice
	 * ("No job: safety or shop"), so the picker offers jobs only.
	 */
	pickJob(opts) {
		if (this.reportBusy()) return;
		const o = opts || {};
		const required = o.reason === "take" || o.reason === "store_run";
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
			title: o.reason === "store_run" ? "Which job were these bought for?" : required ? "Which job are these parts for?" : "Which job?",
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
	// Store runs: "Bought on a store run" (v1.536.0, POL-0602 §4.7-4.8)
	//
	// One save is one line: a submitted Purchase Receipt with no PO, posted at once with its own
	// Undo, like every other save here. A run id ties the lines of one trip together; the lines
	// may go to different bins, so the run bar stays up while the technician scans the next bin,
	// opens the item and presses + ("Add to your … run"). There is no cart to lose when the phone
	// drops the tab.
	// -----------------------------------------------------------------------

	/** Store runs are on here: a flagged store, the receipt fields, and create + submit on a receipt. */
	storeRunReady() {
		return !!(this.storeRun && this.storeRun.can_record);
	}

	/** Runs still taking lines today, anyone's, the one this phone is adding to first. */
	openRuns() {
		const current = this.runState.current;
		return this.runs
			.filter((r) => runIsOpen(r, this.boot.today) && this.runState.finished.indexOf(r.run) === -1)
			.sort((a, b) => (a.run === current ? -1 : b.run === current ? 1 : 0));
	}

	/** The site's clock now (`boot.now` plus the time since the page loaded), or null. */
	siteNow() {
		return this.bootNow === null ? null : this.bootNow + (Date.now() - this.bootAt);
	}

	/**
	 * The open runs the page offers as "Add to …": the person's own, and another person's only
	 * while its last line is under three hours old (`logic.runOffered`). Finish is stored on the
	 * starter's phone only, so this is what keeps a second trip that afternoon off the first.
	 * This phone's current run is always offered: the run bar is still telling the person to add
	 * to it, and dropping it would split one trip into two runs.
	 */
	offeredRuns() {
		const now = this.siteNow();
		const current = this.runState.current;
		return this.openRuns().filter((r) => r.run === current || runOffered(r, this.boot.user, now));
	}

	/** "Home Depot, Lowe's…": the stores, for a new run's row. */
	storeNames() {
		const names = this.storeRun.suppliers.map((s) => s.supplier_name);
		return `${names.slice(0, 2).join(", ")}${names.length > 2 ? "…" : ""}`;
	}

	/** One row of a "which run?" list. */
	runChoice(title, sub, onPick) {
		return append(
			el("li"),
			button(
				[append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", title), el("span", "ee-ss-row-sub", sub)), icon("next", "ee-ss-row-go")],
				"ee-ss-row ee-ss-order ee-ss-run-choice",
				onPick
			)
		);
	}

	/** "Add to your Home Depot run" / "Add to Sam's Home Depot run" rows, this phone's run first. */
	runRows(runs, onPick) {
		return runs.map((run) => {
			const mine = run.started_by === this.boot.user;
			const title = mine ? `Add to your ${run.supplier_name} run` : `Add to ${firstName(run.started_by_name)}'s ${run.supplier_name} run`;
			const lines = Number(run.lines || 0);
			const sub = lines
				? [`${lines} ${lines === 1 ? "item" : "items"}`, formatWhen(run.last_at, this.boot.today)].filter(Boolean).join(" · ")
				: "Every line undone · start it again, the receipt open to correct";
			return this.runChoice(title, sub, () => onPick(run));
		});
	}

	/**
	 * The doors that start from an item card or a search rather than + → Save — a non-stock item's
	 * "Bought it on a store run" and "Not in ERPNext?" — ask the same question + → Save does:
	 * the open runs first (this phone's current one at the top), then "A different store run".
	 * Without it every such part started a run of its own, and one trip split into several: the
	 * store, day, photo and total typed again, and the KPI counting a trip per split.
	 * With no run to offer it goes straight to a new one.
	 */
	startStoreRun(ctx) {
		if (this.reportBusy() || !this.storeRunReady()) return;
		const c = ctx || {};
		const runs = this.offeredRuns().slice(0, 3);
		// No bin, no line: openStoreRun says so before anything is asked.
		if (!runs.length || !(c.warehouse || (c.item && c.item.warehouse))) {
			this.openStoreRun(Object.assign({}, ctx, { run: null }));
			return;
		}
		sheet({
			title: "Which store run?",
			body: (body, handle) => {
				const open = (run) => {
					handle.close("action");
					this.openStoreRun(Object.assign({}, ctx, { run }));
				};
				const list = el("ul", "ee-ss-list");
				for (const row of this.runRows(runs, open)) list.appendChild(row);
				list.appendChild(this.runChoice("A different store run", `${this.storeNames()} · with its own receipt photo`, () => open(null)));
				body.appendChild(list);
			},
			actions: [{ label: "Cancel", kind: "ghost" }],
		});
	}

	/** The run this phone is adding to, while it is open and not finished here. */
	currentRun() {
		const id = this.runState.current;
		if (!id || this.runState.finished.indexOf(id) !== -1) return null;
		const run = this.runs.find((r) => r.run === id);
		return run && runIsOpen(run, this.boot.today) ? run : null;
	}

	/** Keep the server's latest word on a run (every store-run save and undo returns it). */
	noteRun(run) {
		if (!run || !run.run) return;
		const at = this.runs.findIndex((r) => r.run === run.run);
		if (at === -1) this.runs.unshift(run);
		else this.runs[at] = run;
		this.runs = this.runs.slice(0, 12);
		this.renderRunBar();
	}

	setCurrentRun(id) {
		this.runState = { current: id || null, finished: this.runState.finished.slice() };
		writeRunState(this.boot.user, this.runState);
		this.renderRunBar();
	}

	/** "Home Depot run · 2 items · $14.91 before tax   [Finish]", above every view while a run is open. */
	renderRunBar() {
		const bar = this.runBar;
		if (!bar) return;
		const run = this.currentRun();
		fill(bar);
		bar.hidden = !run;
		if (!run) return;
		const sub = Number(run.lines) > 0
			? `Receipt ${money(run.receipt_total)} with tax · press + on an item to add to it`
			: "Every line undone · press + on an item to start it again, the receipt open to correct";
		append(
			bar,
			append(el("div", "ee-ss-runbar-text"), el("span", "ee-ss-runbar-title", runSummary(run)), el("span", "ee-ss-runbar-sub", sub)),
			button("Finish", "ee-ss-btn ee-ss-btn-outline ee-ss-runbar-finish", () => this.finishRun())
		);
	}

	/** "Not in ERPNext?": the run sheet with a quick Item instead of an item, into this location — on an open run when there is one (`startStoreRun`). */
	openQuickItem(opts) {
		if (this.reportBusy()) return;
		const o = opts || {};
		this.startStoreRun({ warehouse: o.warehouse, warehouse_name: o.warehouse_name, qty: 1, newItem: { item_name: o.name || "" } });
	}

	/**
	 * The run sheet: one line of a store run. `ctx`: `{v, item, qty, run, warehouse,
	 * warehouse_name, newItem}` — `run` to add to an open run (its header comes from the server
	 * and is shown on one line), none to start one; `item` an Item payload, or `newItem` for the
	 * quick-item form.
	 *
	 * A new run's header is the store, the day bought, the receipt photo (required, sent as soon
	 * as it is taken), its total with tax (required) and its number (optional). The line is the
	 * item, how many, the price each before tax, why, and the job — or an explicit "No job: safety
	 * or shop", so a blank is a decision (POL-0602 §4.7 asks for the job). The reason starts from
	 * the item: a stocked item (a reorder level above 0) "was out", anything else is "not
	 * something we stock", and a contradiction is pointed out, not refused.
	 *
	 * A run's header is its first Posted line's (`api.stock_scan._run_head`). A run whose every
	 * line was undone (`lines` 0) has none left, so it opens as a new run's header under the same
	 * run id, prefilled with what it had (`logic.reopenedDraft`): that is how a mistyped receipt
	 * total is corrected. Finish says "Check the receipt total" when the total is well above what
	 * the lines plus tax could come to (`logic.receiptCheck`); a joined run's header shows only the
	 * lines so far against the receipt.
	 */
	openStoreRun(ctx) {
		if (this.reportBusy() || !this.storeRunReady()) return;
		const c = ctx || {};
		const v = c.v || this.view;
		const cfg = this.storeRun;
		const today = this.boot.today;
		const warehouse = c.warehouse || (c.item && c.item.warehouse) || null;
		if (!warehouse) {
			toast("Scan the bin these went into, then record the store run from there.", { kind: "info" });
			return;
		}
		const chosen = c.run && runIsOpen(c.run, today) ? c.run : null;
		// Every line undone: the run starts again, its old header prefilled to correct.
		const emptied = !!chosen && !(Number(chosen.lines) > 0);
		const run = emptied ? null : chosen;
		if (emptied && !(this.newRun && this.newRun.run === chosen.run)) {
			this.newRun = reopenedDraft(chosen, today);
		} else if (!run && !emptied && (!this.newRun || this.newRun.reopened)) {
			// A new run. A reopened run's draft is that run's, not a new one's.
			this.newRun = { run: mintRunId(), supplier: "", bought: "today", photo: "", receipt_number: "", receipt_total: "" };
		}
		const draft = run ? null : this.newRun;
		const runId = run ? run.run : draft.run;
		const last = this.lastLine && this.lastLine.run === runId ? this.lastLine : null;
		const uoms = cfg.uoms && cfg.uoms.length ? cfg.uoms : ["Unit"];
		const groups = cfg.item_groups || [];
		const s = {
			item: c.item || null,
			newItem: c.item
				? null
				: {
						item_code: "",
						item_name: (c.newItem && c.newItem.item_name) || "",
						item_group: groups[0] || "",
						stock_uom: uoms[0],
						groupTouched: false,
					},
			check: null,
			checkedKey: null,
			checkSeq: 0,
			checking: false,
			checkAgain: false,
			checkTimer: null,
			reason: last && last.reason === REASON_ONLY_THIS_JOB ? last.reason : suggestedReason(c.item ? c.item.reorder_level : 0, !c.item),
			reasonTouched: false,
			job: last ? last.job : this.job ? { project: this.job.project, project_name: this.job.project_name } : null,
			noJob: last ? !!last.noJob : false,
			linePhoto: "",
			uploading: false,
		};
		let handle = null;
		let closed = false;
		let quick = null;

		const headBox = el("div", "ee-ss-run-head");
		const itemTitle = el("h3", "ee-ss-section-title");
		const itemBox = el("div", "ee-ss-run-item");
		const hintBox = el("div", "ee-ss-run-hints");
		const reasonBox = el("div", "ee-ss-run-reasons");
		const jobBox = el("div", "ee-ss-run-job");
		const unitLabel = el("span", "ee-ss-qty-unit");
		const qtyField = input({
			className: "ee-ss-input ee-ss-run-qty",
			inputmode: "decimal",
			enterkeyhint: "next",
			label: "How many",
			value: plain(c.qty || 1),
		});
		const priceField = input({ inputmode: "decimal", enterkeyhint: "done", label: "Price each, before tax, as on the receipt", placeholder: "4.97" });
		const error = errorLine("ee-ss-field-error");
		const showError = (text, action) => error.show(text, action);
		const saveBtn = () => (handle ? handle.el.querySelector(".ee-ss-sheet-foot .ee-ss-btn-primary") : null);
		const blockedByCheck = () => !!(s.newItem && s.check && (s.check.exists || s.check.will_refuse));
		const refreshSave = () => {
			const btn = saveBtn();
			if (btn && !this.saving) btn.disabled = s.uploading || blockedByCheck();
		};
		const unit = () => (s.item ? s.item.stock_uom : s.newItem.stock_uom);
		const storeOf = () => {
			const id = run ? run.supplier : draft.supplier;
			if (!id) return null;
			return cfg.suppliers.find((x) => x.supplier === id) || { supplier: id, supplier_name: (run && run.supplier_name) || id };
		};
		const jobOf = () => (this.job ? { project: this.job.project, project_name: this.job.project_name } : null);

		// --- the receipt photo: shrunk, then sent at once, so Save is not a 5 MB upload ---------
		// When the photo is in, only this field is redrawn (swapped for a fresh picker in place):
		// a receipt total being typed meanwhile keeps its field, and the phone its keyboard. The
		// store and day buttons redraw only themselves, so a tap there leaves the progress bar be.
		const photoPicker = (label, current, onDone, afterDone) => {
			const wrap = el("div", "ee-ss-photo");
			// No `capture`: a phone then offers the camera AND the photo library, so a receipt
			// photographed in the store, or last night, can be used.
			const file = input({ type: "file", accept: "image/*", className: "ee-ss-file", label });
			file.hidden = true;
			const url = current();
			const status = el("p", "ee-ss-note ee-ss-photo-status");
			status.setAttribute("role", "status");
			const meter = el("div", "ee-ss-upload");
			const level = el("span", "ee-ss-upload-fill");
			meter.appendChild(level);
			meter.hidden = true;
			const pick = button(
				[icon("camera"), el("span", null, url ? "Retake" : label)],
				`ee-ss-btn ${url ? "ee-ss-btn-outline" : "ee-ss-btn-primary"} ee-ss-photo-btn`,
				() => {
					if (!s.uploading) file.click();
				}
			);
			file.addEventListener("change", async () => {
				const chosen = file.files && file.files[0];
				if (!chosen) return;
				s.uploading = true;
				pick.disabled = true;
				meter.hidden = false;
				level.style.width = "0%";
				status.textContent = "Getting the photo ready…";
				refreshSave();
				try {
					const small = await shrinkPhoto(chosen);
					status.textContent = "Sending the photo…";
					const res = await upload(small, (share) => {
						level.style.width = `${Math.round(share * 100)}%`;
					});
					onDone(res.file_url);
					status.textContent = "";
					if (!closed && wrap.parentNode) {
						const parent = wrap.parentNode;
						parent.insertBefore(photoPicker(label, current, onDone, afterDone), wrap);
						parent.removeChild(wrap);
					}
					if (!closed && afterDone) afterDone();
				} catch (e) {
					const text = (e && e.message) || "The photo did not go through. Try again.";
					status.textContent = text;
					if (needsReload(e)) showError(text, RELOAD);
				} finally {
					s.uploading = false;
					pick.disabled = false;
					meter.hidden = true;
					try {
						file.value = "";
					} catch (err) {
						/* some browsers refuse; the next choice still fires change */
					}
					refreshSave();
				}
			});
			const shot = url ? img(url, "ee-ss-photo-thumb") : null;
			append(wrap, file, shot, append(el("div", "ee-ss-photo-side"), url ? el("span", "ee-ss-photo-ok", "Receipt photo ✓") : null, pick), meter, status);
			return wrap;
		};

		// --- the header: a new run's, or the run's on one line ----------------------------------
		// Drawn once per sheet. Only the parts a tap changes redraw: the store buttons, the day
		// buttons, and the photo field once its upload is in.
		const drawHead = () => {
			fill(headBox);
			if (run) {
				const line = el("p", "ee-ss-run-line");
				const drawLine = () => {
					const photo = s.linePhoto || run.receipt_photo;
					const whose = run.started_by && run.started_by !== this.boot.user ? ` · ${firstName(run.started_by_name)}'s run` : "";
					line.textContent = `${run.supplier_name} · ${boughtLabel(run.bought, today)} · receipt ${photo ? "✓" : "missing"} · ${money(run.receipt_total)} with tax${whose}`;
				};
				drawLine();
				// Progress, not a warning: mid-run the lines are nearly always short of the receipt.
				// The "Check the receipt total" warning belongs to Finish, where it means something.
				append(
					headBox,
					line,
					el("p", "ee-ss-run-progress", `Lines so far: ${money(run.amount)} before tax, of a ${money(run.receipt_total)} receipt.`),
					photoPicker("Another photo (a long receipt)", () => s.linePhoto, (url) => (s.linePhoto = url), drawLine)
				);
				return;
			}
			const stores = el("div", "ee-ss-choices");
			stores.setAttribute("role", "radiogroup");
			const drawStores = () => {
				fill(stores);
				for (const store of cfg.suppliers) {
					stores.appendChild(
						choiceButton(store.supplier_name, draft.supplier === store.supplier, () => {
							draft.supplier = store.supplier;
							showError("");
							drawStores();
							drawHints();
						})
					);
				}
			};
			const days = el("div", "ee-ss-choices is-two");
			days.setAttribute("role", "radiogroup");
			const dayNote = el("div", "ee-ss-day-note");
			const drawDays = () => {
				fill(days);
				fill(dayNote);
				for (const [value, label] of [
					["today", "Today"],
					["yesterday", "Yesterday"],
				]) {
					days.appendChild(
						choiceButton(label, draft.bought === value, () => {
							draft.bought = value;
							drawDays();
						})
					);
				}
				if (draft.bought === "yesterday") {
					dayNote.appendChild(el("p", "ee-ss-note", "The policy asks for the same day, so the log notes it was recorded late."));
				}
			};
			drawStores();
			drawDays();
			const total = input({ inputmode: "decimal", label: "Receipt total, tax included", placeholder: "23.41", value: draft.receipt_total });
			total.addEventListener("input", () => {
				draft.receipt_total = total.value;
				showError("");
			});
			const number = input({ label: "Receipt number", placeholder: "Optional", value: draft.receipt_number, maxlength: 140 });
			number.addEventListener("input", () => {
				draft.receipt_number = number.value;
			});
			append(
				headBox,
				draft.reopened
					? el("p", "ee-ss-notice", "Every line of this run was undone. Check the receipt below, correct what was wrong, and record the lines again.")
					: null,
				field("Store", stores, null, true),
				field("Bought", days, dayNote, true),
				field("Receipt photo", photoPicker("Take the receipt photo", () => draft.photo, (url) => (draft.photo = url)), null, true),
				field("Receipt total, tax included", total),
				field("Receipt number (optional)", number)
			);
		};

		// --- the item: a summary, or the quick-item form --------------------------------------
		const drawItem = () => {
			fill(itemBox);
			itemTitle.textContent = s.item ? "What was bought" : "A part not in ERPNext";
			if (s.item) {
				const it = s.item;
				const text = append(el("div", "ee-ss-item-text"), el("h3", "ee-ss-item-name", it.item_name), el("p", "ee-ss-item-code", it.item_code));
				if (!it.is_stock_item) text.appendChild(el("span", "ee-ss-badge", "not a stock item"));
				append(itemBox, append(el("div", "ee-ss-item-head is-compact"), thumb(it), text));
				if (!it.is_stock_item) {
					itemBox.appendChild(el("p", "ee-ss-note", "Not kept in stock: the receipt records the purchase, and no stock is added."));
				}
				return;
			}
			itemBox.appendChild(quick ? quick.node : buildQuick());
		};

		const buildQuick = () => {
			const ni = s.newItem;
			const code = input({ label: "Part or model number", placeholder: "As printed on the package", value: ni.item_code, maxlength: 140 });
			const name = input({ label: "Name", placeholder: "e.g. ELBOW, 90, SOC, PVC, 2 IN", value: ni.item_name, maxlength: 140 });
			code.setAttribute("autocapitalize", "characters");
			name.setAttribute("autocapitalize", "characters");
			const group = select(groups, ni.item_group, "Group");
			const uom = select(uoms, ni.stock_uom, "Unit");
			const results = el("div", "ee-ss-check");
			results.setAttribute("aria-live", "polite");
			code.addEventListener("input", () => {
				ni.item_code = code.value;
				showError("");
				scheduleCheck();
			});
			name.addEventListener("input", () => {
				ni.item_name = name.value;
				showError("");
				scheduleCheck();
			});
			code.addEventListener("blur", () => runCheck());
			name.addEventListener("blur", () => runCheck());
			group.addEventListener("change", () => {
				ni.item_group = group.value;
				ni.groupTouched = true;
			});
			uom.addEventListener("change", () => {
				ni.stock_uom = uom.value;
				unitLabel.textContent = uom.value;
			});
			const node = append(
				el("div", "ee-ss-quick"),
				el("p", "ee-ss-sheet-text", "Not in ERPNext yet. Describe it once; Purchasing reviews every new item."),
				field(
					"Part or model number, exactly as printed",
					code,
					el("p", "ee-ss-note", "The manufacturer's number from the package. If there is none, the store's SKU from the receipt.")
				),
				field("Name", name),
				field("Group", group),
				field("Unit", uom, el("p", "ee-ss-note", "Fixed once saved: ERPNext can't change an item's unit after stock moves.")),
				results
			);
			quick = { node, code, name, group, results };
			if (ni.item_name) scheduleCheck();
			return node;
		};

		// --- the live name check: 500 ms after typing stops, and on leaving a field ------------
		const scheduleCheck = () => {
			if (s.checkTimer) clearTimeout(s.checkTimer);
			s.checkTimer = setTimeout(() => runCheck(), 500);
		};
		const runCheck = async () => {
			if (s.checkTimer) {
				clearTimeout(s.checkTimer);
				s.checkTimer = null;
			}
			const ni = s.newItem;
			if (!ni || closed) return;
			const key = JSON.stringify([ni.item_code.trim(), ni.item_name.trim()]);
			if (key === s.checkedKey) return;
			// One request at a time; a change made meanwhile is checked when it answers.
			if (s.checking) {
				s.checkAgain = true;
				return;
			}
			if (!ni.item_code.trim() && !ni.item_name.trim()) {
				s.check = null;
				s.checkedKey = key;
				drawCheck();
				refreshSave();
				return;
			}
			s.checking = true;
			const mine = ++s.checkSeq;
			try {
				const res = await call(M.CHECK_NEW_ITEM, {
					item_code: ni.item_code.trim(),
					item_name: ni.item_name.trim() || undefined,
					item_group: ni.item_group || undefined,
					stock_uom: ni.stock_uom || undefined,
				});
				if (mine === s.checkSeq && !closed && s.newItem === ni) {
					s.check = res || null;
					s.checkedKey = key;
					const suggested = res && res.suggested_group;
					if (suggested && !ni.groupTouched && groups.indexOf(suggested) !== -1 && quick) {
						ni.item_group = suggested;
						quick.group.value = suggested;
					}
					drawCheck();
				}
			} catch (e) {
				if (mine === s.checkSeq && !closed && s.newItem === ni) {
					s.check = { checked: false, error: (e && e.message) || "no answer" };
					s.checkedKey = null;
					drawCheck();
				}
			} finally {
				s.checking = false;
				refreshSave();
				if (s.checkAgain && !closed) {
					s.checkAgain = false;
					runCheck();
				}
			}
		};

		const useExisting = async (code) => {
			showError("");
			try {
				const found = await call(M.ITEM, { item_code: code, warehouse });
				if (closed) return;
				s.item = found;
				s.newItem = null;
				s.check = null;
				quick = null;
				if (!s.reasonTouched) s.reason = suggestedReason(found.reorder_level, false);
				drawItem();
				drawHints();
				drawQty();
				drawReason();
				refreshSave();
			} catch (e) {
				showError((e && e.message) || "Could not open that item.", needsReload(e) ? RELOAD : undefined);
			}
		};

		const similarRow = (row) => {
			const li = el("li", "ee-ss-similar");
			append(
				li,
				append(
					el("span", "ee-ss-row-main"),
					el("span", "ee-ss-row-title", row.item_name),
					el("span", "ee-ss-row-sub", [row.item_code, row.item_group, row.stocked ? "" : "not a stock item"].filter(Boolean).join(" · "))
				)
			);
			if (row.usable) {
				li.appendChild(
					button(row.stocked ? "Use this one" : "Record against it (not stocked)", "ee-ss-btn ee-ss-btn-outline ee-ss-similar-use", () =>
						useExisting(row.item_code)
					)
				);
			} else {
				li.appendChild(el("p", "ee-ss-note", row.refusal || "This one can't be used here."));
			}
			return li;
		};

		const drawCheck = () => {
			if (!quick) return;
			const box = quick.results;
			fill(box);
			const res = s.check;
			if (!res) return;
			if (res.error) {
				box.appendChild(el("p", "ee-ss-note", `Couldn't check the name (${res.error}). You can still save: the naming check runs on save.`));
				return;
			}
			if (!res.checked) box.appendChild(el("p", "ee-ss-note", "Couldn't check the name; you can still save."));
			if (res.similar && res.similar.length) {
				box.appendChild(el("h4", "ee-ss-group-title", "Is it one of these?"));
				box.appendChild(append(el("ul", "ee-ss-list ee-ss-similar-list"), ...res.similar.map(similarRow)));
			}
			if (res.exists) {
				const ex = res.exists;
				const line = append(el("div", "ee-ss-notice ee-ss-exists"), el("p", null, `Already in ERPNext: ${ex.item_name} (${ex.item_code}).`));
				if (ex.usable) {
					line.appendChild(button(ex.stocked ? "Use it" : "Record against it (not stocked)", "ee-ss-btn ee-ss-btn-outline", () => useExisting(ex.item_code)));
				} else if (ex.refusal) {
					line.appendChild(el("p", "ee-ss-note", ex.refusal));
				}
				box.appendChild(line);
			}
			for (const finding of res.blocking || []) {
				const now = !!res.will_refuse;
				const when = now ? "This will be refused" : `From ${shortDate(res.refuse_from) || "October 1"} this will be refused`;
				box.appendChild(el("p", now ? "ee-ss-alert" : "ee-ss-notice", `${when}: ${finding.message}`));
			}
			if (res.advice && res.advice.length) {
				const tips = el("details", "ee-ss-advice");
				tips.appendChild(el("summary", null, `${res.advice.length} naming ${res.advice.length === 1 ? "tip" : "tips"}`));
				tips.appendChild(append(el("ul", "ee-ss-advice-list"), ...res.advice.map((a) => el("li", null, a.message))));
				box.appendChild(tips);
			}
		};

		// --- what to check before buying: stock elsewhere, and an order at this store ----------
		const receiveOnOrder = (line) => {
			const q = parseQty(qtyField.value, !!s.item.whole_number, s.item.stock_uom);
			if (q.problem) return showError(q.problem);
			handle.close("action");
			this.post(v, M.ADD, {
				action: "add",
				item_code: s.item.item_code,
				warehouse,
				qty: q.qty,
				purchase_order_item: line.purchase_order_item,
				scanned_code: (v && v.scannedCode) || undefined,
			});
			return undefined;
		};
		const drawHints = () => {
			fill(hintBox);
			const it = s.item;
			if (!it) return;
			const where = (it.elsewhere || []).filter((p) => Number(p.on_hand) > 0);
			if (where.length && it.is_stock_item) {
				const more = where.length > 1 ? `, and ${where.length - 1} more ${where.length === 2 ? "place" : "places"}` : "";
				hintBox.appendChild(el("p", "ee-ss-notice", `ERPNext shows ${plain(where[0].on_hand)} at ${where[0].warehouse_name}${more}. Check there before buying more.`));
			}
			const store = storeOf();
			const lines = store ? ordersAtStore(it, store) : [];
			if (lines.length && it.warehouse === warehouse) {
				const line = lines[0];
				append(
					hintBox,
					append(
						el("div", "ee-ss-notice ee-ss-po-warning"),
						el(
							"p",
							null,
							`${line.purchase_order} has ${plain(line.pending_stock_qty)} ${it.stock_uom} of these on order from ${line.supplier_name || line.supplier}. If this is that pickup, receive it on the order, or it will be received twice.`
						),
						button(`Receive on ${line.purchase_order}`, "ee-ss-btn ee-ss-btn-outline", () => receiveOnOrder(line))
					)
				);
			}
		};

		const drawQty = () => {
			unitLabel.textContent = unit() || "";
			qtyField.setAttribute("inputmode", s.item && s.item.whole_number ? "numeric" : "decimal");
		};

		// --- why, and for which job -----------------------------------------------------------
		const drawReason = () => {
			fill(reasonBox);
			const group = el("div", "ee-ss-reasons");
			group.setAttribute("role", "radiogroup");
			group.setAttribute("aria-label", "Why were these bought?");
			for (const r of cfg.reasons || []) {
				const on = s.reason === r.value;
				const row = button(
					[append(el("span", "ee-ss-row-main"), el("span", "ee-ss-row-title", r.value), el("span", "ee-ss-row-sub", r.hint)), on ? glyph("✓", "ee-ss-row-check") : null],
					`ee-ss-row ee-ss-reason${on ? " is-current" : ""}`,
					() => {
						s.reason = r.value;
						s.reasonTouched = true;
						if (r.value === REASON_ONLY_THIS_JOB) s.noJob = false;
						showError("");
						drawReason();
						drawJob();
					}
				);
				row.setAttribute("role", "radio");
				row.setAttribute("aria-checked", on ? "true" : "false");
				group.appendChild(row);
			}
			reasonBox.appendChild(group);
			const warning = reasonWarning(s.reason, s.item ? s.item.reorder_level : 0, !s.item);
			if (warning) reasonBox.appendChild(el("p", "ee-ss-notice", warning));
		};

		const drawJob = () => {
			fill(jobBox);
			const only = s.reason === REASON_ONLY_THIS_JOB;
			if (only) s.noJob = false;
			const said = s.job ? `Job: ${s.job.project_name || s.job.project}` : s.noJob ? "No job: safety or shop" : "No job chosen yet";
			const pick = button([icon("job"), el("span", null, s.job ? "Change the job" : "Pick the job")], "ee-ss-btn ee-ss-btn-outline", () =>
				this.pickJob({
					reason: "store_run",
					then: () => {
						s.job = jobOf();
						s.noJob = false;
						showError("");
						drawJob();
					},
				})
			);
			append(jobBox, el("p", `ee-ss-run-job-state${s.job || s.noJob ? " is-set" : ""}`, said), pick);
			if (only) {
				jobBox.appendChild(el("p", "ee-ss-note", "Bought only for this job, so the job is needed."));
			} else {
				const none = button("No job: safety or shop", `ee-ss-btn ee-ss-btn-ghost ee-ss-nojob${s.noJob ? " is-on" : ""}`, () => {
					s.noJob = true;
					s.job = null;
					showError("");
					drawJob();
				});
				none.setAttribute("aria-pressed", s.noJob ? "true" : "false");
				jobBox.appendChild(none);
			}
		};

		// --- Save to the run -------------------------------------------------------------------
		const go = () => {
			showError("");
			if (s.uploading) return showError("Wait for the photo to finish sending.");
			const store = storeOf();
			if (!store) return showError("Pick the store.");
			const photo = run ? s.linePhoto || run.receipt_photo : draft.photo;
			if (!photo) return showError("Take the receipt photo first.");
			let total = null;
			if (!run) {
				const t = validTotal(draft.receipt_total);
				if (t.problem) return showError(t.problem);
				total = t.total;
			}
			let itemArgs;
			let uom;
			if (s.item) {
				itemArgs = { item_code: s.item.item_code };
				uom = s.item.stock_uom;
			} else {
				const ni = s.newItem;
				if (!ni.item_code.trim()) return showError("Enter the part or model number, exactly as printed.");
				if (!ni.item_name.trim()) return showError("Enter a name for the item.");
				if (s.check && s.check.exists) return showError(`${s.check.exists.item_code} is already in ERPNext. Use it instead.`);
				if (s.check && s.check.will_refuse) return showError("This part number or name would be refused. Change it first.");
				itemArgs = {
					new_item: { item_code: ni.item_code.trim(), item_name: ni.item_name.trim(), item_group: ni.item_group, stock_uom: ni.stock_uom },
				};
				uom = ni.stock_uom;
			}
			const q = parseQty(qtyField.value, !!(s.item && s.item.whole_number), uom);
			if (q.problem) return showError(q.problem);
			const price = validPrice(priceField.value);
			if (price.problem) return showError(price.problem);
			if (!s.reason) return showError("Pick why these were bought.");
			if (!s.job && (s.reason === REASON_ONLY_THIS_JOB || !s.noJob)) {
				// No job and no "No job" decision: ask for the job rather than refuse the tap.
				this.pickJob({
					reason: "store_run",
					then: () => {
						s.job = jobOf();
						s.noJob = false;
						drawJob();
					},
				});
				return undefined;
			}
			const job = s.job;
			const noJob = !job;
			const args = Object.assign(
				{
					action: "store_run",
					run: runId,
					supplier: store.supplier,
					warehouse,
					qty: q.qty,
					rate: price.rate,
					reason: s.reason,
					receipt_photo: photo,
					bought: run ? (boughtLabel(run.bought, today) === "yesterday" ? "yesterday" : "today") : draft.bought,
					receipt_number: run ? run.receipt_number || undefined : (draft.receipt_number || "").trim() || undefined,
					receipt_total: run ? run.receipt_total : total,
					project: job ? job.project : undefined,
					no_job: noJob ? 1 : 0,
					scanned_code: (v && v.scannedCode) || undefined,
					// The page's own day: a page left open overnight is told to reload (`_stale_page`).
					page_today: today || undefined,
				},
				itemArgs
			);
			this.post(v, M.STORE_RUN, args, {
				button: saveBtn(),
				showError,
				isOpen: () => !closed,
				onDone: () => handle.close("action"),
				after: (result) => this.afterStoreRun(args, result, { job, noJob }),
			});
			return undefined;
		};

		const reopenedStore = draft && draft.reopened ? storeOf() : null;
		handle = sheet({
			title: run ? `Add to the ${run.supplier_name} run` : reopenedStore ? `Start the ${reopenedStore.supplier_name} run again` : "Bought on a store run",
			full: true,
			className: "ee-ss-run-sheet",
			initialFocus: "sheet",
			body: (body) =>
				append(
					body,
					headBox,
					itemTitle,
					itemBox,
					hintBox,
					field("How many", append(el("div", "ee-ss-qty-row"), qtyField, unitLabel)),
					field("Price each, before tax, as on the receipt", priceField),
					el("h3", "ee-ss-section-title", "Why"),
					reasonBox,
					el("h3", "ee-ss-section-title", "Job"),
					jobBox,
					el("p", "ee-ss-note", `Location: ${c.warehouse_name || (c.item && c.item.warehouse_name) || warehouse}`),
					error.node
				),
			actions: [
				{ label: "Save to the run", kind: "primary", large: true, close: false, onClick: go },
				{ label: "Cancel", kind: "ghost" },
			],
			onClose: () => {
				closed = true;
				if (s.checkTimer) clearTimeout(s.checkTimer);
				s.checkSeq += 1;
			},
		});
		drawHead();
		drawItem();
		drawHints();
		drawQty();
		drawReason();
		drawJob();
		refreshSave();
	}

	/**
	 * A store-run line is in. The run becomes this phone's current one (the bar), the next line
	 * of it starts from this one's reason and job, and a save made from a location or a search
	 * goes on to the item at its bin with its new on-hand. "Only for this job" then offers to
	 * take the parts to the job now, which is the default: left in the bin, parts that went to
	 * the job would read as shelf stock.
	 */
	afterStoreRun(args, result, line) {
		const run = result && result.run;
		if (run) {
			if (this.newRun && this.newRun.run === run.run) this.newRun = null;
			this.noteRun(run);
			this.setCurrentRun(run.run);
		}
		this.lastLine = { run: args.run, reason: args.reason, job: (line && line.job) || null, noJob: !!(line && line.noJob) };
		const fresh = result && result.item;
		const cur = this.view;
		const onIt = !!fresh && cur.name === "item" && cur.item.item_code === fresh.item_code && cur.item.warehouse === fresh.warehouse;
		if (fresh && fresh.warehouse && !onIt) this.showItem(fresh);
		const posted = result && !result.repeated && result.log && result.log.status === "Posted";
		if (posted && args.reason === REASON_ONLY_THIS_JOB && args.project && fresh && fresh.is_stock_item) {
			this.offerTakeNow(args, fresh, line && line.job);
		}
	}

	/** "Take 3 Unit to <job> now?" after a line bought only for that job. Take is the default. */
	async offerTakeNow(args, item, job) {
		if (this.reportBusy()) return;
		const name = (job && (job.project_name || job.project)) || args.project;
		const amount = `${plain(args.qty)} ${item.stock_uom || ""}`.trim();
		const ok = await ask({
			title: `Take ${amount} to ${name} now?`,
			body: "They were bought only for this job. Taking them now charges them to it; left in the bin they would read as stock on the shelf.",
			ok: "Take them now",
			okKind: "take",
			okGlyph: "−",
			cancel: "Leave them in the bin",
		});
		if (!ok) return;
		const cur = this.view;
		const target = cur.name === "item" && cur.item.item_code === item.item_code && cur.item.warehouse === item.warehouse ? cur : { name: "item", item };
		this.post(target, M.TAKE, {
			action: "take",
			item_code: item.item_code,
			warehouse: item.warehouse,
			qty: args.qty,
			project: args.project,
		});
	}

	/** The run bar's Finish: what was recorded, against the paper total, and where the receipt goes. */
	finishRun() {
		if (this.reportBusy()) return;
		const run = this.currentRun();
		if (!run) return;
		const mine = this.recent.filter((r) => r.store_run === run.run && r.status === "Posted");
		sheet({
			title: `Finish the ${run.supplier_name} run`,
			body: (body) => {
				if (mine.length) {
					body.appendChild(
						append(
							el("ul", "ee-ss-run-lines"),
							...mine.map((r) => el("li", null, `${logHeadline(r)} · ${r.item_name}${r.rate ? ` · ${money(r.rate)} each` : ""}`))
						)
					);
				}
				const lines = Number(run.lines || 0);
				const gap = Number(run.receipt_total || 0) - Number(run.amount || 0);
				// Well above the lines plus tax: a line not recorded yet, or a mistyped total, which
				// would otherwise sit on every receipt of the run and in the KPI as said.
				const check = receiptCheck(run.receipt_total, run.amount);
				append(
					body,
					el("p", "ee-ss-sheet-text", `${lines} ${lines === 1 ? "line" : "lines"} recorded, ${money(run.amount)} before tax.`),
					el("p", "ee-ss-sheet-text", `Receipt total with tax: ${money(run.receipt_total)}.`),
					check
						? append(
								el("div", "ee-ss-notice ee-ss-receipt-check"),
								el("p", null, `Check the receipt total. ${money(run.receipt_total)} is more than these lines with tax: ${money(run.amount)} before tax is about ${money(check.low)}–${money(check.high)} with tax.`),
								el("p", null, `If something on the receipt is not recorded yet, tap Keep adding and add it. ${RESTART_RUN}`)
							)
						: gap > 0.005
							? el("p", "ee-ss-note", `The difference, ${money(gap)}, is the tax and anything on the receipt not recorded yet.`)
							: null,
					gap < -0.005 ? el("p", "ee-ss-notice", "The lines add up to more than the receipt total. Check the prices, or tell Purchasing.") : null,
					el("p", "ee-ss-notice", "Hand the paper receipt to Accounting within 2 business days (POL-0602 §4.7).")
				);
			},
			actions: [
				{ label: "Done", kind: "primary", large: true, onClick: () => this.markRunFinished(run.run) },
				{ label: "Keep adding", kind: "ghost" },
			],
		});
	}

	markRunFinished(id) {
		const finished = this.runState.finished.indexOf(id) === -1 ? this.runState.finished.concat([id]).slice(-20) : this.runState.finished;
		this.runState = { current: this.runState.current === id ? null : this.runState.current, finished };
		writeRunState(this.boot.user, this.runState);
		this.renderRunBar();
		toast("Run finished. Don't forget the paper receipt.", { kind: "ok" });
	}

	// -----------------------------------------------------------------------
	// Report a problem (the capture panel, WI-079)
	// -----------------------------------------------------------------------

	/**
	 * Open the capture panel over whatever is on screen, as the kiosk's Settings row does
	 * (`kiosk/settings.js`). Through the `window.ee_capture` global only: this bundle may not
	 * import the recorder (tests/test_feedback_capture_surface.py).
	 *
	 * The panel pushes and removes its OWN history entry and answers the phone's Back itself —
	 * "Discard this report?" before a typed report is thrown away — so this page adds no marker
	 * for it and stands aside while it is open (`onPopState`). Two things are settled first:
	 *  - a lookup still loading is dropped: its screen would be drawn under the form, and its
	 *    history entry pushed on top of the form's own;
	 *  - a sheet's marker still waiting for that lookup is stepped back over (`nav.whenQuiet`),
	 *    so the form's entry sits on a screen of ours.
	 * And from the tap until the form has closed, the page opens nothing and goes nowhere
	 * (`reportBusy`): the form downloads on first use, and on a slow connection the page is
	 * still there to tap in the meantime.
	 */
	openReport() {
		if (this.report || this.reportWanted || sheetDepth() || this.scanner) return;
		const capture = window.ee_capture;
		if (!capture || typeof capture.open !== "function") {
			// The recorder never loaded (the button is hidden then; this is its Try again).
			toast("The report form is not on this page. Reload the page, then try again.", {
				kind: "error",
				actionLabel: RELOAD.label,
				onAction: RELOAD.onClick,
			});
			return;
		}
		this.reportWanted = true;
		++this.seq;
		this.reportBtn.setAttribute("aria-busy", "true");
		// Back un-wanted an earlier tap's form while it was still loading: that one is wanted
		// again. A second open() would be answered with the same panel, and the first answer,
		// no longer wanted, would close it.
		if (this.reportLoading) return;
		this.reportLoading = true;
		const settle = () => {
			const wanted = this.reportWanted;
			this.reportWanted = false;
			this.reportLoading = false;
			this.reportBtn.removeAttribute("aria-busy");
			return wanted;
		};
		this.nav.whenQuiet(() => {
			if (!this.reportWanted) {
				settle();
				return;
			}
			let opening;
			try {
				// open() reports a failed load by rejecting; a throw is caught the same way.
				opening = Promise.resolve(capture.open({ surface: "web" }));
			} catch (e) {
				opening = Promise.reject(e);
			}
			opening.then(
				(handle) => {
					const wanted = settle();
					if (!handle) return;
					if (!wanted || sheetDepth() || this.scanner) {
						// Not to be kept: Back was pressed while it loaded, or a sheet or the camera
						// opened under it after all (every door here checks `reportBusy`, so one that
						// forgot to). A sheet under the form keeps its keys — the camera's code box
						// takes every letter typed into the form — and its marker sits under the
						// form's entry. Closing the form removes that entry again (capture/panel.js)
						// and leaves the sheet, its marker and the screen exactly as they were.
						if (typeof handle.close === "function") handle.close();
						return;
					}
					this.report = handle;
					const done = () => {
						if (this.report === handle) this.report = null;
					};
					if (handle.closed && typeof handle.closed.then === "function") handle.closed.then(done, done);
					else done();
				},
				() => {
					if (settle()) this.reportFailed();
				}
			);
		});
	}

	/** The form did not open: say what to do, with the one thing that helps. */
	reportFailed() {
		toast("The report form did not open. Check your signal, then tap Try again.", {
			kind: "error",
			actionLabel: "Try again",
			onAction: () => this.openReport(),
		});
	}

	/** The report form is open over the page, so Back is its to answer (capture/panel.js). */
	reportOpen() {
		try {
			const capture = window.ee_capture;
			if (capture && typeof capture.isOpen === "function") return !!capture.isOpen();
		} catch (e) {
			/* a broken recorder is a closed form */
		}
		return !!this.report;
	}

	/**
	 * The report form is on its way or open, so the page opens no sheet and starts no
	 * navigation: every door on it checks this (the scanner gun's `onWedgeKey` included). Under
	 * the form, a sheet would keep acting on keys — the camera moves focus into its code box on
	 * every letter, Escape and Tab reach the sheet — and a screen that landed would write its
	 * history entry where the form's own is. Back while it loads still works (`onPopState`) and
	 * un-wants it.
	 */
	reportBusy() {
		return this.reportWanted || this.reportOpen();
	}

	/**
	 * What a report from this page says about it, beside the recorder's own rings: codes and
	 * counts only. No item or supplier names, quantities or jobs — a report is read by people
	 * who never saw the shelf, and the page has no cost or price to leak.
	 */
	registerCaptureState() {
		try {
			const capture = window.ee_capture;
			if (!capture || typeof capture.registerCaptureState !== "function") return;
			capture.registerCaptureState(() => {
				const v = this.view;
				const here = this.here();
				return {
					stock_scan: {
						view: v.name,
						warehouse: here ? here.warehouse : null,
						item_code: v.item ? v.item.item_code : null,
						back_depth: this.back.length,
						history_entries: this.nav.trail.length,
						sheets_open: sheetDepth(),
						saving: this.saving,
						retries_kept: this.pending.size,
						job_picked: !!this.job,
						// A store run on this phone: open or not, and how many lines. Never the store
						// or a price: a report is read by people who never saw the receipt.
						run_open: !!this.currentRun(),
						run_lines: this.currentRun() ? Number(this.currentRun().lines || 0) : 0,
						build: this.boot.build || null,
					},
				};
			});
		} catch (e) {
			/* a report is a convenience; the page is not */
		}
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
		if (this.reportBusy()) return;
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
		// "Add an item here" with store runs on: a part that is not in ERPNext at all is recorded
		// as bought on a store run, with a quick Item (POL-0602 §4.8). Offered under the results
		// and when nothing matches, never before a search: search first, so no duplicate is made.
		const newItemDoor = (query) => {
			if (!(o.addHere && here && this.storeRunReady())) return null;
			if (!this.storeRun.can_create_items) {
				return el("p", "ee-ss-note", "Not in ERPNext? Ask Purchasing to add it, then record the purchase here.");
			}
			return button([glyph("+"), el("span", null, "Not in ERPNext? Record it as bought on a store run")], "ee-ss-linkbtn ee-ss-newitem-door", () => {
				handle.close("action");
				this.openQuickItem({ warehouse: here.warehouse, warehouse_name: here.warehouse_name, name: query });
			});
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
				const door = newItemDoor(query);
				if (door) results.appendChild(door);
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
