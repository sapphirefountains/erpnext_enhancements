// Inventory Scanner Audit — mobile-first physical-count page for inventory clerks.
//
// Scan a location — a Storage Location barcode, or the warehouse QR label printed for
// the Stock Scan page (/warehouse-labels) — then scan items (keyboard-wedge scanner or
// the device camera) and type the counted quantity. Counts accumulate in a resumable
// Inventory Count Session with a live system-qty snapshot and variance per line;
// Finalize builds a DRAFT Stock Reconciliation for a Stock Manager to review and
// submit. All data flows through erpnext_enhancements.api.inventory_scanner.
// Theme-aware (Frappe CSS vars); semantic variance colours are literal.
//
// Camera: the native BarcodeDetector where it reads QR codes (Chrome on Android), else
// the vendored jsQR decoder, loaded once on first use. Every iPhone takes the jsQR path:
// Safari has no BarcodeDetector, and before v1.521.0 the camera button simply never
// appeared there. jsQR reads QR only, so on an iPhone an item BARCODE still needs the
// wedge scanner or "Find item" — the location labels are QR, which is what matters.
//
// The phone's Back button: each sheet (Camera Scan, Find Item) is a route segment of
// its own — inventory-scanner-audit/camera, inventory-scanner-audit/find — so Back
// closes the sheet and stays on the count, and Forward opens it again. A scan is never
// a history entry, and neither is the pending-item card: it is part of this one
// screen. See "sheets and the phone's Back button" below.

frappe.pages['inventory-scanner-audit'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Inventory Scanner Audit'),
		single_column: true,
	});
	wrapper.inventory_scanner = new InventoryScanner(page, wrapper);
};

frappe.pages['inventory-scanner-audit'].on_page_show = function (wrapper) {
	if (wrapper.inventory_scanner) wrapper.inventory_scanner.onShow();
};

const ISA_METHOD = 'erpnext_enhancements.api.inventory_scanner.';
const ISA_ROUTE = 'inventory-scanner-audit';
const ISA_SHEETS = ['camera', 'find'];
// How long to wait for the router to land our own history.back() before going on without it.
const ISA_BACK_TIMEOUT_MS = 1500;
// The QR decoder for browsers without a QR-capable BarcodeDetector. The version is in the
// filename and the file is never edited, so the year-immutable /assets cache cannot serve
// a stale copy; frappe.require loads it once and adds its own ?v= on top.
const ISA_QR_DECODER = '/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js';
// jsQR decodes a frame drawn at most this wide: a QR label fills a good share of the frame,
// and a full 1080p frame costs ~5x the time for no better read.
const ISA_QR_FRAME_WIDTH = 480;

class InventoryScanner {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.state = { settings: {}, session: null, activeLocation: null, pendingItem: null };
		this.sheet = null; // {name, dialog} while a sheet is open
		this.opening = null; // the sheet whose route is being pushed
		this.shownSheet = undefined; // the sheet segment at the last show; undefined before the first
		this.away = false; // another Desk page has been shown since the last show (see onShow)
		this.shows = 0; // every show, counted: a lookup's reply compares it to see whether the clerk has moved
		this.lastSearch = ''; // Find Item's pre-fill, so Forward can reopen it as it was
		// 'camera' while the camera's entry holds no sheet: its read is being looked up there,
		// or the lookup failed. Such an entry is taken over by the next sheet, never reopened.
		this.leftover = null;
		this.reads = 0; // camera reads, counted: `leftover` is the last one's (see handleScan)
		// frappe triggers "hide" on the page it leaves for another. Only the page's own counts: a
		// Bootstrap "hide.bs.*" from inside the page bubbles up to the wrapper as well.
		$(wrapper).on('hide', (e) => {
			if (e.target === wrapper) this.away = true;
		});
		this.injectStyles();
		this.buildSkeleton();
		this.boot();
	}

	call(method, args) {
		return frappe.call({ method: ISA_METHOD + method, args: args || {} }).then((r) => r.message);
	}

	boot() {
		this.call('get_bootstrap').then((d) => {
			this.state.settings = (d && d.settings) || {};
			this.state.session = (d && d.session) || null;
			this.renderAll();
			this.focusScan();
		});
	}

	// ----- helpers -----
	activeWarehouse() {
		if (this.state.activeLocation) return this.state.activeLocation.warehouse;
		if (this.state.session && this.state.session.default_warehouse) return this.state.session.default_warehouse;
		return this.state.settings.default_warehouse || null;
	}

	focusScan() {
		if (this.$scan) setTimeout(() => this.$scan.focus(), 50);
	}

	clearScan() {
		if (this.$scan) this.$scan.val('');
	}

	// ----- sheets and the phone's Back button -----
	//
	// A sheet's route is pushed from the tap that opens it, and the sheet is shown only
	// once the route has settled: every route change closes the open dialog (the router's
	// set_history and container.change_to both do), this one included. Back then pops the
	// segment and the router closes the sheet with nothing more from here. A sheet closed
	// any other way (a read, a pick, X, Escape) steps back off its own entry, so Back never
	// lands on a sheet that is already shut. The entry behind a sheet's is always the
	// count's own: one on the first show is replaced if it is a pasted link, and stepped back
	// off if it is the page's own (a reload), as is one Back or Forward lands on that cannot
	// be opened again.
	//
	// A camera read is not a tap. Its lookup runs on the camera's entry, and what it leads
	// to either takes that entry over (Find Item, for an unknown code) or steps back off it
	// before the result is drawn (see handleScan). Chrome skips on Back an entry a page
	// pushed with no tap since the one before, so the page never pushes on its own.
	//
	// Only an entry this page pushed is a sheet's. Each is marked in history.state as it is
	// pushed (markSheet), because the same URL also arrives from outside: frappe records every
	// route with a second segment in Route History, and the awesome bar offers the most used as
	// links. A sheet opened from one of those would have another page behind it, so X, or a
	// camera read's step back, would land there. An unmarked sheet URL is handed to the
	// count, as a pasted link's is — or, when it was pushed over the count's own entry while
	// that was showing, stepped back off, onto that entry. A marked one on the first show (a
	// reload, or Android restoring a discarded tab: history.state survives both) is stepped
	// back off too, since the entry behind a marked one is always the count's own.
	//
	// Each replace the page asks for clears `route_flags.replace_route` as soon as set_route
	// returns. set_route reads the flag as it writes the entry, but clears route_flags itself
	// only once its promise settles, and that waits on every request then in flight (v16's
	// after_ajax) — on a first show, the bootstrap call. Left set, the flag would turn the
	// clerk's next tap into a replace of the count's own entry, and X or Back from the sheet it
	// opened would then leave the page.

	sheetRoute() {
		const route = frappe.get_route() || [];
		return route[0] === ISA_ROUTE && ISA_SHEETS.includes(route[1]) ? route[1] : null;
	}

	// The mark goes on the current entry, with no URL, and keeps whatever else it holds.
	markSheet(name) {
		try {
			window.history.replaceState(Object.assign({}, window.history.state, { isa_sheet: name }), '');
		} catch (e) {
			// Unmarked, the entry is handed to the count on its next show rather than reopened.
		}
	}

	ownSheet(name) {
		try {
			const state = window.history.state;
			return !!state && state.isa_sheet === name;
		} catch (e) {
			return false;
		}
	}

	// `reopen`: Forward landed on the sheet's entry; show it there, and push nothing.
	openSheet(name, show, reopen) {
		const on = this.sheetRoute();
		if (reopen || (on === name && !this.sheet)) {
			if (on === name && !this.sheet) {
				this.leftover = null;
				show();
			}
			return;
		}
		this.opening = name;
		this.leftover = null;
		// An entry left behind by a sheet that has closed (the camera's, while its read is
		// looked up) is taken over, not stacked on.
		if (on) frappe.route_flags.replace_route = true;
		const settled = frappe.set_route(ISA_ROUTE, name);
		// Read already, and left set it would outlive this call (see "sheets and the phone's Back button").
		frappe.route_flags.replace_route = false;
		// set_route has written the entry by the time it returns (push_state runs before its
		// promise does), so the mark lands on the sheet's own entry.
		this.markSheet(name);
		settled.then(() => {
			if (this.opening !== name) return;
			this.opening = null;
			// Back pressed before the route settled took the entry, and the sheet with it.
			if (this.sheetRoute() === name && !this.sheet) show();
		});
	}

	// Bootstrap ignores hide() on a modal still fading in, so that waits for it to be shown.
	hideSheet(d) {
		if (d.display) d.hide();
		else d.$wrapper.one('shown.bs.modal', () => d.hide());
	}

	// Every sheet's onhide calls this.
	sheetGone(d) {
		if (this.sheet && this.sheet.dialog === d) this.sheet = null;
	}

	// Step back off a sheet's entry. `then` runs once the count is back on its own entry, so
	// a dialog it opens is not closed by that route change. When Back closed the sheet the
	// route has already moved off the segment, and there is nothing to step over.
	leaveSheet(name, then) {
		let done = false;
		const finish = () => {
			if (done) return;
			done = true;
			if (then) then();
		};
		if (this.sheetRoute() !== name) {
			finish();
			return;
		}
		frappe.router.once('change', finish);
		setTimeout(finish, ISA_BACK_TIMEOUT_MS);
		window.history.back();
	}

	onShow() {
		const sheet = this.sheetRoute();
		const first = this.shownSheet === undefined;
		const came = this.shownSheet;
		// The count's own entry was showing at the last show, and no other page has been since.
		const stayed = !first && !this.away && came === null;
		this.away = false;
		this.shownSheet = sheet;
		this.shows += 1;
		// A sheet whose entry is no longer current. The router closes the open dialog on
		// every route change, but not one still fading in: it is not cur_dialog yet.
		if (this.sheet && this.sheet.name !== sheet) this.hideSheet(this.sheet.dialog);
		if (sheet) {
			if (this.opening === sheet || (this.sheet && this.sheet.name === sheet)) return;
			if (first || !this.ownSheet(sheet)) {
				// A reload, a pasted link, or a sheet URL reached from another page (never start a
				// camera nobody asked for, over a page it would step back to): the entry becomes
				// the count, or is stepped back off onto the count's own.
				this.shownSheet = null;
				if (stayed || (first && this.ownSheet(sheet))) {
					// Pushed over the count's own entry while it was showing (the awesome bar's
					// link to a sheet, picked here), or a reload on a sheet entry this page pushed
					// (its mark survives the reload): the entry behind is the count's, so step back
					// onto it. Replacing this one would leave two count entries in a row.
					window.history.back();
				} else {
					frappe.route_flags.replace_route = true;
					frappe.set_route(ISA_ROUTE);
					// Read already, and left set it would outlive this call (see openSheet).
					frappe.route_flags.replace_route = false;
				}
			} else if (this.leftover === sheet) {
				// The camera's entry while its read is looked up, or while the lookup's failure is
				// on screen: not a sheet to reopen, and no camera started by a Back or Forward
				// nobody tapped for. handleScan steps off it, or hands it to Find Item, once either
				// is over.
			} else if (!this.reopenSheet(sheet)) {
				// Back or Forward onto a sheet that cannot be opened again (the camera, with
				// camera scanning since turned off): step back onto the count's own entry,
				// which is always the one behind a sheet's. Replacing this entry instead would
				// leave two count entries in a row, and the next Back would seem to do nothing.
				window.history.back();
			}
			return;
		}
		// A sheet has just closed: focus stays where closing it left it (the counted-qty box,
		// after a read of an item). The wedge scanner's focus comes back with the next scan.
		if (came) return;
		this.focusScan();
	}

	// Forward onto a sheet Back closed: open it again on its entry. False when it cannot be.
	reopenSheet(name) {
		if (name === 'camera' && this.state.settings.enable_camera_scan && this.cameraAvailable()) {
			this.openCamera(true);
			return true;
		}
		if (name === 'find') {
			this.openItemSearch(this.lastSearch, true);
			return true;
		}
		return false;
	}

	// ----- skeleton -----
	buildSkeleton() {
		const $b = $(this.page.body);
		$b.html(`
			<div class="isa">
				<div class="isa-bar">
					<div class="isa-status"></div>
					<div class="isa-actions">
						<button class="btn btn-sm btn-primary isa-start">${__('Start Session')}</button>
						<button class="btn btn-sm btn-default isa-cancel">${__('Cancel')}</button>
					</div>
				</div>
				<div class="isa-scanrow">
					<input type="text" class="isa-scan" autocomplete="off" autocapitalize="off"
						spellcheck="false" placeholder="${__('Scan or type a barcode…')}" />
					<button class="isa-find" title="${__('Find item')}">🔍</button>
					<button class="isa-cam" title="${__('Camera scan')}">📷</button>
				</div>
				<div class="isa-location"></div>
				<div class="isa-pending"></div>
				<div class="isa-list"></div>
			</div>
			<div class="isa-finbar">
				<div class="isa-finsum"></div>
				<button class="btn btn-sm btn-primary isa-finalize">${__('Finalize → Draft Reconciliation')}</button>
			</div>
		`);

		this.$status = $b.find('.isa-status');
		this.$scan = $b.find('.isa-scan');
		this.$cam = $b.find('.isa-cam');
		this.$find = $b.find('.isa-find');
		this.$loc = $b.find('.isa-location');
		this.$pending = $b.find('.isa-pending');
		this.$list = $b.find('.isa-list');
		this.$start = $b.find('.isa-start');
		this.$cancel = $b.find('.isa-cancel');
		this.$finbar = $b.find('.isa-finbar');
		this.$finsum = $b.find('.isa-finsum');
		this.$finalize = $b.find('.isa-finalize');

		this.$scan.on('keydown', (e) => {
			if (e.key === 'Enter') {
				e.preventDefault();
				this.handleScan(this.$scan.val());
			}
		});
		this.$cam.on('click', () => this.openCamera());
		this.$find.on('click', () => this.openItemSearch(''));
		this.$start.on('click', () => this.startSession());
		this.$cancel.on('click', () => this.cancelSession());
		this.$finalize.on('click', () => this.finalize());
		this.$list.on('click', '.isa-rm', (e) => this.removeLine($(e.currentTarget).data('idx')));
	}

	// ----- render -----
	renderAll() {
		this.renderControls();
		this.renderLocation();
		this.renderPending();
		this.renderSession();
	}

	renderControls() {
		const s = this.state.session;
		const camOn = this.state.settings.enable_camera_scan && this.cameraAvailable();
		this.$cam.toggle(!!camOn);
		if (s) {
			this.$status.html(
				`${__('Counting')} · <b>${frappe.utils.escape_html(s.name)}</b> · ${s.summary.lines} ${__('lines')}`
			);
			this.$start.hide();
			this.$cancel.show();
		} else {
			this.$status.html(__('No active count session — scan or tap Start.'));
			this.$start.show();
			this.$cancel.hide();
		}
	}

	renderLocation() {
		const loc = this.state.activeLocation;
		if (loc) {
			// A warehouse QR label resolves with no Storage Location: its name and its
			// warehouse are the same thing, so say it once.
			const name = loc.location_name || loc.storage_location || loc.warehouse_name || loc.warehouse;
			const wh = loc.warehouse_name || loc.warehouse;
			this.$loc
				.addClass('show')
				.html(
					`📍 <b>${frappe.utils.escape_html(name)}</b>${
						wh && wh !== name ? ` — ${frappe.utils.escape_html(wh)}` : ''
					}`
				);
		} else {
			this.$loc.removeClass('show').empty();
		}
	}

	renderPending() {
		const p = this.state.pendingItem;
		if (!p) {
			this.$pending.empty();
			return;
		}
		const hasSys = p.system_qty !== undefined && p.system_qty !== null;
		const uom = frappe.utils.escape_html(p.uom || '');
		this.$pending.html(`
			<div class="isa-card">
				<h4>${frappe.utils.escape_html(p.item_name || p.item_code)}</h4>
				<div class="isa-sys">${frappe.utils.escape_html(p.item_code)}${
					hasSys ? ` · ${__('System')}: <b>${format_number(p.system_qty)}</b> ${uom}` : ''
				}</div>
				<div class="isa-qtyrow">
					<input type="number" inputmode="decimal" step="any" class="isa-qty" placeholder="${__('Counted qty')}" />
					<span class="isa-uom">${uom}</span>
				</div>
				<div class="isa-var"></div>
				<textarea class="isa-reason" rows="2" placeholder="${__('Reason for variance')}" style="display:none;"></textarea>
				<button class="btn btn-primary isa-add">${__('Add to Count')}</button>
			</div>
		`);

		const $qty = this.$pending.find('.isa-qty');
		const $var = this.$pending.find('.isa-var');
		const $reason = this.$pending.find('.isa-reason');
		const requireReason = !!this.state.settings.require_variance_reason;

		const update = () => {
			if (!hasSys || $qty.val() === '') {
				$var.text('').removeClass('ok diff');
				$reason.hide();
				return;
			}
			const v = flt($qty.val()) - flt(p.system_qty);
			if (v === 0) {
				$var.text(__('Matches system')).removeClass('diff').addClass('ok');
				$reason.hide();
			} else {
				const sign = v > 0 ? '+' : '';
				$var
					.text(__('Variance: {0}{1} {2}', [sign, format_number(v), p.uom || '']))
					.removeClass('ok')
					.addClass('diff');
				$reason.toggle(requireReason);
			}
		};
		$qty.on('input', update);
		$qty.on('keydown', (e) => {
			if (e.key === 'Enter') {
				e.preventDefault();
				this.addCount();
			}
		});
		this.$pending.find('.isa-add').on('click', () => this.addCount());
		setTimeout(() => $qty.focus(), 50);
	}

	renderSession() {
		const s = this.state.session;
		const lines = (s && s.lines) || [];
		if (!s || !lines.length) {
			this.$list.html(s ? `<div class="isa-empty">${__('No items counted yet — scan an item.')}</div>` : '');
			this.$finbar.removeClass('show');
			this.renderControls();
			return;
		}
		const rows = lines
			.slice()
			.reverse()
			.map((ln) => {
				const cls = flt(ln.variance) !== 0 ? 'diff' : 'ok';
				const sign = flt(ln.variance) > 0 ? '+' : '';
				const where = ln.storage_location || ln.warehouse || '';
				return `
				<div class="isa-line ${cls}">
					<div>
						<div class="isa-line-main">${frappe.utils.escape_html(ln.item_name || ln.item_code)}</div>
						<div class="isa-line-sub">${frappe.utils.escape_html(where)} · ${__('count')} ${format_number(ln.counted_qty)} / ${__('sys')} ${format_number(ln.system_qty)}</div>
					</div>
					<div class="isa-badge ${cls}">${sign}${format_number(ln.variance)}</div>
					<button class="isa-rm" data-idx="${ln.idx}" title="${__('Remove')}">×</button>
				</div>`;
			})
			.join('');
		this.$list.html(rows);

		this.$finsum.html(`${s.summary.lines} ${__('lines')} · ${s.summary.with_variance} ${__('with variance')}`);
		this.$finbar.addClass('show');
		this.renderControls();
	}

	// ----- actions -----
	startSession() {
		return this.call('start_session', {}).then((session) => {
			this.state.session = session;
			this.renderAll();
			this.focusScan();
			return session;
		});
	}

	// `fromCamera`: the read is looked up on the camera's own history entry (see "sheets and
	// the phone's Back button"). Find Item for an unknown code takes that entry over; any
	// other result is drawn once the count has stepped back off it. If the lookup fails, the
	// count steps back off it once frappe's error message has closed (afterFrappeMessage):
	// stepping off sooner would close the message, and leaving the entry would make the
	// clerk's next Back change nothing on screen.
	//
	// Find Item opens only where the scan was made. A reply that lands after the clerk has
	// moved — Back or Forward, a sheet tapped open, another Desk page — would otherwise route
	// them back onto the count from wherever they went, or push an entry nobody tapped for
	// (which Chrome then skips on Back). There the unknown code is only reported.
	//
	// And a camera read's reply acts only on the entry the read was taken on, while that entry
	// still holds nothing but its lookup (`mine`). A camera the clerk has opened since — on that
	// entry, or on a new one after Back — has taken it over (openSheet clears `leftover`), as has
	// a later read; stepping off then would close the clerk's camera, and Find Item would replace
	// it. The result is drawn where the clerk is, and there is nothing of this read's to step off.
	handleScan(raw, fromCamera) {
		const code = (raw || '').trim();
		this.clearScan();
		if (!code) {
			if (fromCamera) this.leaveSheet('camera');
			return;
		}
		if (fromCamera) this.leftover = 'camera';
		const read = fromCamera ? ++this.reads : 0;
		const mine = () => fromCamera && this.leftover === 'camera' && this.reads === read;
		const shows = this.shows;
		const ensure = this.state.session ? Promise.resolve() : this.startSession();
		ensure
			.then(() => this.call('resolve_scan', { code, warehouse: this.activeWarehouse() }))
			.then(
				(res) => {
					const own = mine();
					const search =
						this.shows === shows &&
						(frappe.get_route() || [])[0] === ISA_ROUTE &&
						(!fromCamera || (own && this.sheetRoute() === 'camera'));
					if (fromCamera && !(search && this.unknownOpensSearch(res))) {
						if (!own) {
							this.onResolved(res, code);
							return;
						}
						this.leftover = null;
						this.leaveSheet('camera', () => this.onResolved(res, code));
						return;
					}
					this.onResolved(res, code, search);
				},
				() => {
					// frappe has already said why the lookup failed, in a dialog that stepping
					// off the camera's entry would close — unless the request never got through,
					// when it puts no dialog up. The entry stays `leftover` until any such dialog
					// has gone. A sheet opened on it since has taken it over; one Back has
					// already moved off it, and leaveSheet then steps over nothing.
					if (!fromCamera) return;
					this.afterFrappeMessage(() => {
						if (!mine()) return;
						this.leftover = null;
						this.leaveSheet('camera');
					});
				}
			);
	}

	// `then` runs once frappe's own error message is gone, or at once when none is up. frappe
	// puts a refusal up in msgprint's dialog and a crash in its Server Error dialog, and has
	// called show() on it before a call's promise rejects. Whether one is up is Bootstrap's own
	// `_isShown`: set as show() starts, so true through the fade-in when `display` is not yet,
	// and cleared by every hide. Not frappe's `is_visible`, which only Dialog.hide() clears: the
	// dialog's X is data-dismiss="modal", which Bootstrap closes with no hide() around it, and
	// msgprint's dialog is one for the whole session. After any message anywhere was closed by
	// its X, a lookup that failed with no message at all (a dropped connection: request.js has
	// no handler for status 0) would wait for a "hidden" that never comes, and the camera's
	// entry would never be stepped off.
	afterFrappeMessage(then) {
		const up = [frappe.msg_dialog, frappe.error_dialog].filter((d) => {
			const modal = d && d.$wrapper && d.$wrapper.data('bs.modal');
			return !!(modal && modal._isShown);
		});
		if (!up.length) {
			then();
			return;
		}
		let done = false;
		up.forEach((d) =>
			d.$wrapper.one('hidden.bs.modal', () => {
				if (done) return;
				done = true;
				then();
			})
		);
	}

	// An unknown code the server gives no reason for opens Find Item, where the settings allow it.
	unknownOpensSearch(res) {
		return (
			!!res &&
			res.type !== 'location' &&
			res.type !== 'item' &&
			!res.message &&
			!!this.state.settings.allow_unknown_item
		);
	}

	// `search`: the reply landed where the scan was made, so an unknown code may open Find
	// Item (see handleScan). A Find Item pick's own lookup never reopens it.
	onResolved(res, code, search) {
		if (!res) return;
		// Everything the server sends back can carry text off a scanned code, and
		// frappe.show_alert and __() both interpolate into HTML. A QR label is a string anyone
		// can print, so a crafted one scanned by a Stock Manager must not become markup.
		const esc = frappe.utils.escape_html;
		// For a Stock Scan label the server answers with the code it actually looked up (the
		// bare value inside the URL); say and search for that, never the whole URL.
		const shown = res.code || code;
		if (res.type === 'location') {
			this.state.activeLocation = res;
			this.state.pendingItem = null;
			this.renderLocation();
			this.renderPending();
			frappe.show_alert({ message: __('Location: {0}', [esc(res.location_name || res.storage_location)]), indicator: 'blue' });
			this.focusScan();
			return;
		}
		if (res.type === 'item') {
			if (!this.activeWarehouse()) {
				frappe.show_alert({ message: __('Scan a location first (or set a default warehouse).'), indicator: 'orange' });
				this.focusScan();
				return;
			}
			if (res.disabled) {
				frappe.show_alert({ message: __('Item {0} is disabled.', [esc(res.item_code)]), indicator: 'red' });
			}
			if (res.has_serial_no || res.has_batch_no) {
				frappe.show_alert({ message: __('Serial/batch item — count posts at warehouse qty only.'), indicator: 'orange' });
			}
			res.scanned_barcode = shown;
			this.state.pendingItem = res;
			this.renderPending();
			return;
		}
		// unknown. A reason from the server (a group or disabled warehouse's label) is said
		// as it is: an item search pre-filled with a label URL helps nobody.
		if (res.message) {
			frappe.show_alert({ message: esc(res.message), indicator: 'orange' });
			this.focusScan();
			return;
		}
		if (search && this.unknownOpensSearch(res)) {
			this.openItemSearch(shown);
		} else {
			frappe.show_alert({ message: __('Unknown barcode: {0}', [esc(shown)]), indicator: 'red' });
			this.focusScan();
		}
	}

	addCount() {
		const p = this.state.pendingItem;
		if (!p) return;
		const $qty = this.$pending.find('.isa-qty');
		const $reason = this.$pending.find('.isa-reason');
		if ($qty.val() === '') {
			frappe.show_alert({ message: __('Enter a counted quantity.'), indicator: 'orange' });
			$qty.focus();
			return;
		}
		this.call('add_count', {
			session: this.state.session.name,
			item_code: p.item_code,
			counted_qty: flt($qty.val()),
			storage_location: this.state.activeLocation ? this.state.activeLocation.storage_location : null,
			warehouse: this.activeWarehouse(),
			scanned_barcode: p.scanned_barcode,
			reason: $reason.is(':visible') ? $reason.val() : '',
		}).then((session) => {
			this.state.session = session;
			this.state.pendingItem = null;
			this.renderPending();
			this.renderSession();
			frappe.show_alert({ message: __('Counted {0}', [p.item_code]), indicator: 'green' });
			this.focusScan();
		});
	}

	removeLine(idx) {
		if (!this.state.session) return;
		this.call('remove_line', { session: this.state.session.name, idx }).then((session) => {
			this.state.session = session;
			this.renderSession();
			this.focusScan();
		});
	}

	cancelSession() {
		if (!this.state.session) return;
		frappe.confirm(__('Cancel this count session? Counted lines will be discarded.'), () => {
			this.call('cancel_session', { session: this.state.session.name }).then(() => {
				this.resetSession();
				frappe.show_alert({ message: __('Session cancelled.'), indicator: 'gray' });
			});
		});
	}

	finalize() {
		if (!this.state.session) return;
		frappe.confirm(__('Finalize this count and create a draft Stock Reconciliation for review?'), () => {
			this.call('finalize_session', { session: this.state.session.name }).then((res) => {
				this.resetSession();
				// get_form_link is a /desk path, which the desk's link handler routes in place.
				// The server's reconciliation_url is an /app path: a full page load on v16.
				frappe.msgprint({
					title: __('Count Finalized'),
					indicator: 'green',
					message: __('Draft Stock Reconciliation {0} created with {1} line(s) for a Stock Manager to review.', [
						`<a href="${frappe.utils.get_form_link('Stock Reconciliation', res.stock_reconciliation)}">${frappe.utils.escape_html(res.stock_reconciliation)}</a>`,
						res.rows,
					]),
				});
			});
		});
	}

	resetSession() {
		this.state.session = null;
		this.state.activeLocation = null;
		this.state.pendingItem = null;
		this.renderAll();
		this.focusScan();
	}

	// ----- manual item search -----
	openItemSearch(prefill, reopen) {
		this.lastSearch = prefill || '';
		this.openSheet('find', () => this.showItemSearch(prefill), reopen);
	}

	showItemSearch(prefill) {
		const app = this;
		const d = new frappe.ui.Dialog({ title: __('Find Item'), size: 'small' });
		let picked = null;
		d.$body.html(`
			<input type="text" class="form-control isa-search" placeholder="${__('Item code or name')}" value="${frappe.utils.escape_html(prefill || '')}" />
			<div class="isa-results" style="margin-top:10px;max-height:50vh;overflow:auto;"></div>
		`);
		const $search = d.$body.find('.isa-search');
		const $results = d.$body.find('.isa-results');
		const run = frappe.utils.debounce(() => {
			const q = $search.val();
			if (!q) {
				$results.empty();
				return;
			}
			app.call('lookup_item', { query: q, limit: 12 }).then((items) => {
				if (!items || !items.length) {
					$results.html(`<div class="isa-empty">${__('No matches.')}</div>`);
					return;
				}
				$results.html(
					items
						.map(
							(it) =>
								`<button class="btn btn-default btn-sm isa-pick" style="display:block;width:100%;text-align:left;margin-bottom:4px;" data-item="${frappe.utils.escape_html(it.item_code)}"><b>${frappe.utils.escape_html(it.item_name || it.item_code)}</b> <span class="text-muted">${frappe.utils.escape_html(it.item_code)}</span></button>`
						)
						.join('')
				);
			});
		}, 250);
		$search.on('input', run);
		$results.on('click', '.isa-pick', (e) => {
			picked = $(e.currentTarget).data('item');
			app.hideSheet(d);
		});
		d.onhide = () => {
			app.sheetGone(d);
			app.leaveSheet('find', () => {
				if (!picked) return;
				const itemCode = picked;
				app.call('resolve_scan', { code: itemCode, warehouse: app.activeWarehouse() }).then((res) => app.onResolved(res, itemCode));
			});
		};
		this.sheet = { name: 'find', dialog: d };
		d.show();
		setTimeout(() => {
			run();
			$search.focus();
		}, 100);
	}

	// ----- camera scan (BarcodeDetector where it reads QR, else jsQR) -----
	cameraAvailable() {
		// getUserMedia exists only in a secure context (https, or localhost). This, not
		// BarcodeDetector, is what decides whether the camera button shows.
		return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
	}

	// Resolves to {read, qrOnly} — read(video) reads one frame and resolves to the code or
	// null — or to null when no decoder could be had. BarcodeDetector is used only when it
	// lists qr_code: the location labels are QR, and a detector that cannot read them makes
	// a camera that never fires. It is built with no formats, so it still reads every 1D
	// barcode the device supports, as it always has.
	qrReader() {
		const formats =
			'BarcodeDetector' in window && typeof window.BarcodeDetector.getSupportedFormats === 'function'
				? window.BarcodeDetector.getSupportedFormats().catch(() => [])
				: Promise.resolve([]);
		return formats.then((supported) => {
			if ((supported || []).includes('qr_code')) {
				const detector = new window.BarcodeDetector();
				return {
					qrOnly: false,
					read: (video) =>
						detector.detect(video).then((codes) => (codes && codes.length ? codes[0].rawValue : null)),
				};
			}
			return frappe.require(ISA_QR_DECODER).then(() => {
				// frappe.require resolves even when the script failed to load.
				if (typeof window.jsQR !== 'function') return null;
				const canvas = document.createElement('canvas');
				const ctx = canvas.getContext('2d', { willReadFrequently: true });
				return {
					qrOnly: true,
					read: (video) => {
						const vw = video.videoWidth;
						const vh = video.videoHeight;
						if (!vw || !vh) return null;
						const w = Math.min(ISA_QR_FRAME_WIDTH, vw);
						const h = Math.round(vh * (w / vw));
						if (canvas.width !== w || canvas.height !== h) {
							canvas.width = w;
							canvas.height = h;
						}
						ctx.drawImage(video, 0, 0, w, h);
						// Always these same options: jsQR folds the options it is given into its
						// module defaults, so one call with others would change every later call.
						const hit = window.jsQR(ctx.getImageData(0, 0, w, h).data, w, h, {
							inversionAttempts: 'dontInvert',
						});
						return hit && hit.data ? hit.data : null;
					},
				};
			});
		});
	}

	openCamera(reopen) {
		if (!this.cameraAvailable()) {
			frappe.msgprint(
				__('The camera needs a secure (https) connection and a browser that allows camera access. Use a hardware/Bluetooth scanner or “Find item”.')
			);
			return;
		}
		this.qrReader().then((reader) => {
			if (!reader) {
				frappe.msgprint(
					__('Could not load the QR code reader. Check the connection, or use a hardware/Bluetooth scanner or “Find item”.')
				);
				return;
			}
			this.openSheet('camera', () => this.showCamera(reader), reopen);
		});
	}

	showCamera(reader) {
		const app = this;
		const hint = reader.qrOnly
			? __('Point the camera at a QR code. For an item barcode, use a scanner or “Find item”.')
			: __('Point the camera at a barcode or QR code.');
		const d = new frappe.ui.Dialog({ title: __('Camera Scan'), size: 'small' });
		d.$body.html(
			`<video class="isa-video" playsinline muted autoplay></video><div class="text-muted" style="margin-top:6px;">${hint}</div>`
		);
		const video = d.$body.find('video')[0];
		video.muted = true; // iOS plays inline, unprompted, only when muted
		let stream = null;
		let stopped = false;
		let timer = null;
		let read = null; // what the camera read, handed on once the sheet has closed
		let failed = false;
		const later = (fn, ms) => {
			if (!stopped) timer = setTimeout(fn, ms);
		};
		// A phone that locks with the dialog open must not keep the camera running.
		const onHidden = () => {
			if (document.hidden) app.hideSheet(d);
		};
		const cleanup = () => {
			stopped = true;
			clearTimeout(timer);
			document.removeEventListener('visibilitychange', onHidden);
			if (stream) stream.getTracks().forEach((t) => t.stop());
		};
		const tick = () => {
			if (stopped) return;
			if (video.readyState < 2) {
				later(tick, 200);
				return;
			}
			Promise.resolve()
				.then(() => reader.read(video))
				.then((val) => {
					if (stopped) return;
					if (val) {
						read = val;
						cleanup();
						app.hideSheet(d);
					} else {
						later(tick, 180);
					}
				})
				.catch(() => later(tick, 300));
		};
		d.onhide = () => {
			cleanup();
			app.sheetGone(d);
			// A read is looked up on this sheet's entry (see handleScan). A failure is said
			// once the count is back on its own entry: stepping off this one would close it.
			if (read) app.handleScan(read, true);
			else app.leaveSheet('camera', () => failed && frappe.msgprint(__('Could not access the camera.')));
		};
		this.sheet = { name: 'camera', dialog: d };
		d.show();
		document.addEventListener('visibilitychange', onHidden);
		// Started after the sheet is up, never before the route has settled.
		navigator.mediaDevices
			.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
			.then((s) => {
				if (stopped) {
					// Closed while the permission prompt was up.
					s.getTracks().forEach((t) => t.stop());
					return;
				}
				stream = s;
				video.srcObject = s;
				const playing = video.play();
				if (playing && playing.catch) playing.catch(() => {});
				tick();
			})
			.catch(() => {
				failed = true;
				app.hideSheet(d);
			});
	}

	// ----- styles (theme-aware; semantic variance colours literal) -----
	injectStyles() {
		if (document.getElementById('isa-styles')) return;
		const css = `
.isa{max-width:680px;margin:0 auto;padding:6px 2px 90px;}
.isa-bar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:10px;}
.isa-status{font-size:13px;color:var(--text-muted);}
.isa-actions{display:flex;gap:6px;}
.isa-scanrow{display:flex;gap:8px;margin-bottom:10px;}
.isa-scan{flex:1 1 auto;min-width:0;font-size:18px;padding:12px 14px;border:2px solid var(--border-color);border-radius:10px;background:var(--control-bg,var(--bg-color));color:var(--text-color);}
.isa-scan:focus{border-color:var(--primary);outline:none;}
.isa-cam,.isa-find{flex:0 0 auto;font-size:18px;line-height:1;padding:0 14px;border-radius:10px;border:1px solid var(--border-color);background:var(--bg-color);color:var(--text-color);cursor:pointer;}
.isa-location{display:none;border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;margin-bottom:10px;font-size:14px;background:var(--control-bg,var(--bg-color));}
.isa-location.show{display:block;}
.isa-card{border:1px solid var(--border-color);border-radius:12px;padding:14px;margin-bottom:10px;background:var(--control-bg,var(--bg-color));}
.isa-card h4{margin:0 0 2px;font-size:16px;}
.isa-sys{font-size:13px;color:var(--text-muted);margin-bottom:10px;}
.isa-qtyrow{display:flex;align-items:center;gap:10px;}
.isa-qty{flex:1 1 auto;min-width:0;font-size:22px;text-align:center;padding:12px;border:2px solid var(--primary);border-radius:10px;background:var(--control-bg,var(--bg-color));color:var(--text-color);}
.isa-uom{font-size:14px;color:var(--text-muted);}
.isa-var{font-size:13px;font-weight:600;min-height:18px;margin:8px 0;}
.isa-var.ok{color:#15803d;}
.isa-var.diff{color:#b45309;}
.isa-reason{width:100%;margin:4px 0 8px;padding:10px;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg,var(--bg-color));color:var(--text-color);}
.isa-add{width:100%;}
.isa-list{display:flex;flex-direction:column;gap:6px;}
.isa-line{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:10px;border:1px solid var(--border-color);border-left-width:4px;border-radius:8px;padding:8px 10px;background:var(--control-bg,var(--bg-color));}
.isa-line.ok{border-left-color:#22c55e;}
.isa-line.diff{border-left-color:#f59e0b;}
.isa-line-main{font-weight:600;font-size:13px;}
.isa-line-sub{font-size:12px;color:var(--text-muted);}
.isa-badge{font-size:13px;font-weight:700;padding:2px 8px;border-radius:10px;white-space:nowrap;}
.isa-badge.ok{background:rgba(34,197,94,.15);color:#15803d;}
.isa-badge.diff{background:rgba(245,158,11,.18);color:#b45309;}
.isa-rm{border:none;background:transparent;color:var(--text-muted);font-size:20px;line-height:1;cursor:pointer;padding:0 2px;}
.isa-empty{color:var(--text-muted);font-size:13px;text-align:center;padding:22px 0;}
.isa-finbar{position:fixed;left:0;right:0;bottom:0;z-index:5;display:none;align-items:center;justify-content:space-between;gap:10px;padding:10px 14px;background:var(--fg-color,var(--bg-color));border-top:1px solid var(--border-color);}
.isa-finbar.show{display:flex;}
.isa-finsum{font-size:13px;color:var(--text-muted);}
.isa-video{width:100%;border-radius:8px;background:#000;max-height:60vh;}
`;
		$(`<style id="isa-styles">${css}</style>`).appendTo(document.head);
	}
}
