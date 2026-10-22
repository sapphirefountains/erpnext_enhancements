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
// Camera: the scanner kit (public/js/scanner_kit, v1.666.0), shared with the Device
// Console. The native BarcodeDetector where it reads QR codes (Chrome on Android), else
// the vendored jsQR decoder, loaded once on first use. Every iPhone takes the jsQR path:
// Safari has no BarcodeDetector, and before v1.521.0 the camera button simply never
// appeared there. jsQR reads QR only, so on an iPhone an item BARCODE still needs the
// wedge scanner or "Find item" — the location labels are QR, which is what matters.
//
// The phone's Back button: each sheet (Camera Scan, Find Item) is a route segment of
// its own — inventory-scanner-audit/camera, inventory-scanner-audit/find — so Back
// closes the sheet and stays on the count, and Forward opens it again. A scan is never
// a history entry, and neither is the pending-item card: it is part of this one
// screen. The kit (scanner_kit/sheets.js) pushes the entries and steps back off them;
// what a camera read's entry is used for while its lookup runs is the count's own. See
// "sheets and the phone's Back button" below.

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

// sf_kit (v1.645.0): the shared loading / error / no-access panel and the quiet server call,
// from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never rejects: a
// kit that never arrived resolves to a plain stand-in, so the count carries on. A Desk opened
// before the deploy that added the loader has no sf_kit_ready yet, and asks frappe.require for
// the kit itself. The jsQR decoder stays a require of its own (the scanner kit's) on first
// camera use, and only where BarcodeDetector cannot read QR: most counts never open the camera.
function isa_kit() {
	if (typeof window.sf_kit_ready === 'function') return window.sf_kit_ready();
	return new Promise((resolve) =>
		frappe.require(['sf_kit.bundle.js', 'sf_kit.bundle.css'], () =>
			resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : isa_bare_kit())
		)
	);
}

// Neither the loader nor the kit: a Desk opened before this deploy whose old kit file is gone too.
// A line of text and a button, so the page still says something instead of throwing.
function isa_bare_kit() {
	const failed = (x) => ({ status: (x && x.status) || 0, message: '', exc_type: '', session_expired: (x && x.status) === 401 });
	const render = (el, kind, o) => {
		const $el = $(el).empty().append($('<div class="text-muted"></div>').text((o && o.title) || ''));
		if (o && o.on_action) $('<button class="btn btn-default btn-sm"></button>').text(o.action_label || __('Try again')).on('click', o.on_action).appendTo($el);
	};
	const call = (method, args) => Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => r && r.message, (x) => Promise.reject(failed(x)));
	return { esc: (v) => frappe.utils.escape_html(v == null ? '' : String(v)), call, state: { render }, toast: (message) => frappe.show_alert({ message, indicator: 'orange' }) };
}

// The scanner kit (v1.666.0): the camera sheet, its decoder, and the sheets' history handling,
// shared with the Device Console. There is no global loader for it (the global bundle is left
// alone), so this is the guard: the kit already on the page answers at once, and only the first
// visit on a Desk asks frappe.require for it. `then` gets the kit, or null when it did not arrive
// (v16's frappe.require runs its callback even when the file failed to load), and is never left
// waiting.
function isa_scanner(then) {
	const kit = () => (window.sf_scanner && window.sf_scanner.version >= 1 ? window.sf_scanner : null);
	if (kit()) return then(kit());
	try {
		frappe.require(['scanner_kit.bundle.js'], () => then(kit()));
	} catch (e) {
		then(null);
	}
}

// The reorder boxes (v1.725.0): the shared editor behind the Bin Manager's Reorder column, here on the
// card of the item just scanned. Optional like the scanner kit: `then` gets it, or null when it did not
// arrive, and the card is the card it always was.
function isa_reorder(then) {
	const ro = () => (window.sf_reorder && window.sf_reorder.version >= 1 ? window.sf_reorder : null);
	if (ro()) return then(ro());
	try {
		frappe.require(['reorder_levels.bundle.js', 'reorder_levels.bundle.css'], () => then(ro()));
	} catch (e) {
		then(null);
	}
}

// No scanner kit: each sheet is a plain dialog with no entry of its own, so Back leaves the
// count, and the camera button stays hidden. Scanning, Find Item and the count all work.
function isa_bare_sheets() {
	const nav = {
		sheet: null,
		opening: null,
		current: () => null,
		open: (name, show, reopen, took) => {
			if (took) took();
			show();
		},
		hide: (d) => d.hide(),
		gone(d) {
			if (nav.sheet && nav.sheet.dialog === d) nav.sheet = null;
		},
		leave: (name, then) => then && then(),
		show: () => ({ sheet: null, came: null }),
	};
	return nav;
}

// A failed load's panel: no access on a 403; a way back in when the session has lapsed (the
// quiet path puts up no sign-in dialog of frappe's); otherwise what went wrong, with Try again.
function isa_failed(sf, el, err, retry) {
	err = err || {};
	if (err.session_expired) {
		return sf.state.render(el, 'error', {
			title: __('You have been signed out.'),
			body: __('Reload the page to sign in again.'),
			action_label: __('Reload'),
			on_action: () => window.location.reload(),
		});
	}
	if (err.status === 403) {
		return sf.state.render(el, 'no-access', {
			title: __('You do not have access to the Inventory Scanner Audit.'),
			body: err.message,
		});
	}
	return sf.state.render(el, 'error', {
		title: __('Could not load the count.'),
		body: err.message,
		on_action: retry,
	});
}

class InventoryScanner {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.state = { settings: {}, session: null, activeLocation: null, pendingItem: null };
		this.booted = false; // the bootstrap has answered: whether camera scanning is on is known
		this.kit = null; // the scanner kit, once it has arrived
		this.nav = isa_bare_sheets(); // the sheets' history handling; the kit's, once it has arrived
		// While the kit loads (a Desk's first visit), a show and any sheet tapped open wait here.
		this.scannerQueue = [];
		this.showQueued = false;
		this.shows = 0; // every show, counted: a lookup's reply compares it to see whether the clerk has moved
		this.lastSearch = ''; // Find Item's pre-fill, so Forward can reopen it as it was
		// 'camera' while the camera's entry holds no sheet: its read is being looked up there,
		// or the lookup failed. Such an entry is taken over by the next sheet, never reopened.
		this.leftover = null;
		this.reads = 0; // camera reads, counted: `leftover` is the last one's (see handleScan)
		// Bumped whenever the clerk starts, cancels or finishes a count: a start-up reply asked for
		// before the last bump knows nothing of that count and must not set the session (see boot).
		this.sessionTouched = 0;
		this.reorder = null; // the reorder editor, once its bundle has arrived
		this.injectStyles();
		this.buildSkeleton();
		isa_scanner((kit) => this.scannerArrived(kit));
		isa_reorder((ro) => this.reorderArrived(ro));
		this.boot();
	}

	// The card's reorder boxes. The editor reads the item's rule for the active location when a card
	// is drawn, and saves through api.reorder_levels; nothing here pushes a history entry.
	reorderArrived(ro) {
		if (!ro) return;
		const root = () => this.$pending[0];
		const esc = (v) => frappe.utils.escape_html(v == null ? '' : String(v));
		try {
			this.reorder = new ro.ReorderEditor({
				call: (method, args, opts) => isa_kit().then((sf) => sf.call(method, args, opts)),
				read: (code) => ro.read_boxes(root(), code),
				paint: (code, force) => ro.paint(root(), this.reorder, esc, (t) => __(t), code, document.activeElement, !!force, this.reorderLabels()),
				paint_all: () => ro.paint(root(), this.reorder, esc, (t) => __(t), null, document.activeElement, false, this.reorderLabels()),
				later: (fn, ms) => setTimeout(fn, ms),
				tr: (text) => __(text),
				confirm: (text, yes, no) => frappe.confirm(text, yes, no),
			});
			// Enter in a reorder box leaves it, and the wedge scanner's next read would go nowhere: the focus
			// goes to the counted-quantity box, where the clerk was before they stopped to look at the rule.
			ro.bind(root(), this.reorder, { after_enter: () => this.$pending.find('.isa-qty').focus() });
		} catch (e) {
			this.reorder = null;
			return;
		}
		// A card already drawn before the bundle arrived gets its boxes now, in its own slot: the
		// card is not redrawn, so a count already typed into it stays.
		const p = this.state.pendingItem;
		if (p) {
			this.$pending.find('.isa-reorder').html(this.reorderCell(p));
			this.loadReorder();
		}
	}

	// The rule of the item on the card, for the warehouse being counted.
	loadReorder() {
		const p = this.state.pendingItem;
		if (this.reorder && p) this.reorder.load(this.activeWarehouse() || '', [p.item_code]);
	}

	reorderLabels() {
		const p = this.state.pendingItem;
		return p ? { [p.item_code]: p.item_name || p.item_code } : {};
	}

	// The reorder cell of the item on the card, as markup ('' with no editor).
	reorderCell(p) {
		if (!this.reorder) return '';
		const esc = (v) => frappe.utils.escape_html(v == null ? '' : String(v));
		return window.sf_reorder.cell_html(esc, (t) => __(t), this.reorder.view(p.item_code), p.item_code, p.item_name || p.item_code);
	}

	scannerArrived(kit) {
		// A kit that throws as it starts counts as no kit: the page carries on with plain sheets,
		// and the queue below still runs, so nothing is left waiting on it.
		const queue = this.scannerQueue;
		this.scannerQueue = null;
		if (kit) {
			try {
				const nav = kit.sheets({ route: ISA_ROUTE, names: ISA_SHEETS, mark: 'isa_sheet', back_timeout_ms: ISA_BACK_TIMEOUT_MS });
				nav.watch(this.wrapper);
				this.nav = nav;
				this.kit = kit;
			} catch (e) {
				this.kit = null;
				this.nav = isa_bare_sheets();
			}
		}
		// Whether the camera button shows is known once both the kit and the bootstrap have answered.
		if (this.booted) this.renderControls();
		queue.forEach((fn) => fn());
	}

	// `fn` once the scanner kit has answered: at once, after the first load.
	afterScanner(fn) {
		if (this.scannerQueue) this.scannerQueue.push(fn);
		else fn();
	}

	call(method, args) {
		return frappe.call({ method: ISA_METHOD + method, args: args || {} }).then((r) => r.message);
	}

	// Quiet (v1.645.0): a failed start-up says so in its own slot above the count's lines, with
	// Try again, instead of leaving an empty page behind a frappe dialog. Its own slot, not the
	// lines': the clerk can start a count (Start, or a scan) while it is still loading, and its
	// loading panel, its error or a retry must not paint over lines already counted. For the same
	// reason a late reply leaves the session alone once the clerk has started, cancelled or finished
	// a count since it was asked for: it would bring back a count just cancelled, or drop one begun.
	boot() {
		isa_kit().then((sf) => {
			sf.state.render(this.$boot, 'loading', { title: __('Loading the count…') });
			const touched = this.sessionTouched;
			sf.call(ISA_METHOD + 'get_bootstrap', {}, { quiet: true }).then(
				(d) => {
					this.$boot.empty();
					this.booted = true;
					this.state.settings = (d && d.settings) || {};
					if (this.sessionTouched === touched) this.state.session = (d && d.session) || null;
					this.renderAll();
					this.focusScan();
				},
				(err) => isa_failed(sf, this.$boot, err, () => this.boot())
			);
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
	// The kit's sheets (scanner_kit/sheets.js) push each sheet's route from the tap that opens
	// it, show the sheet once the route has settled, step back off its entry when it is closed any
	// other way than Back, and on a show decide whether a sheet URL is the count's own. What is the
	// count's: which sheet Back or Forward may open again (reopenSheet), and what a camera read's
	// entry is used for while the read is looked up.
	//
	// A camera read is not a tap. Its lookup runs on the camera's entry, and what it leads
	// to either takes that entry over (Find Item, for an unknown code) or steps back off it
	// before the result is drawn (see handleScan). Chrome skips on Back an entry a page
	// pushed with no tap since the one before, so the page never pushes on its own. While
	// the lookup runs the entry is `leftover`: a show there neither reopens the camera nor
	// steps off, and the next sheet opened takes the entry over (openSheet clears it).

	openSheet(name, show, reopen) {
		this.afterScanner(() => this.nav.open(name, show, reopen, () => (this.leftover = null)));
	}

	onShow() {
		if (this.scannerQueue) {
			// The kit is still loading: the show runs once it has answered, if the count is still
			// the page showing.
			if (!this.showQueued) {
				this.showQueued = true;
				this.afterScanner(() => {
					this.showQueued = false;
					if ((frappe.get_route() || [])[0] === ISA_ROUTE) this.onShow();
				});
			}
			return;
		}
		this.shows += 1;
		const { sheet, came } = this.nav.show({
			// The camera's entry while its read is looked up, or while the lookup's failure is on
			// screen: not a sheet to reopen, and no camera started by a Back or Forward nobody
			// tapped for. handleScan steps off it, or hands it to Find Item, once either is over.
			hold: (name) => this.leftover === name,
			reopen: (name) => this.reopenSheet(name),
		});
		// A sheet has just closed: focus stays where closing it left it (the counted-qty box,
		// after a read of an item). The wedge scanner's focus comes back with the next scan.
		if (sheet || came) return;
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
					<button class="isa-find" title="${__('Find item')}" aria-label="${__('Find item')}">🔍</button>
					<button class="isa-cam" title="${__('Camera scan')}" aria-label="${__('Camera scan')}">📷</button>
				</div>
				<div class="isa-location"></div>
				<div class="isa-pending"></div>
				<div class="isa-boot"></div>
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
		this.$boot = $b.find('.isa-boot');
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
				<div class="isa-reorder">${this.reorderCell(p)}</div>
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
		this.loadReorder();
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
					<button type="button" class="isa-rm" data-idx="${ln.idx}" title="${__('Remove')}" aria-label="${__('Remove')}">×</button>
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
			this.sessionTouched += 1;
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
			if (fromCamera) this.nav.leave('camera');
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
						(!fromCamera || (own && this.nav.current() === 'camera'));
					if (fromCamera && !(search && this.unknownOpensSearch(res))) {
						if (!own) {
							this.onResolved(res, code);
							return;
						}
						this.leftover = null;
						this.nav.leave('camera', () => this.onResolved(res, code));
						return;
					}
					this.onResolved(res, code, search);
				},
				() => {
					// frappe has already said why the lookup failed, in a dialog that stepping
					// off the camera's entry would close — unless the request never got through,
					// when it puts no dialog up. The entry stays `leftover` until any such dialog
					// has gone. A sheet opened on it since has taken it over; one Back has
					// already moved off it, and the step then steps over nothing.
					if (!fromCamera) return;
					this.afterFrappeMessage(() => {
						if (!mine()) return;
						this.leftover = null;
						this.nav.leave('camera');
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
		this.sessionTouched += 1;
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
			app.nav.hide(d);
		});
		d.onhide = () => {
			app.nav.gone(d);
			app.nav.leave('find', () => {
				if (!picked) return;
				const itemCode = picked;
				app.call('resolve_scan', { code: itemCode, warehouse: app.activeWarehouse() }).then((res) => app.onResolved(res, itemCode));
			});
		};
		this.nav.sheet = { name: 'find', dialog: d };
		d.show();
		setTimeout(() => {
			run();
			$search.focus();
		}, 100);
	}

	// ----- camera scan (the scanner kit, shared with the Device Console) -----
	// getUserMedia exists only in a secure context (https, or localhost). This, not
	// BarcodeDetector, is what decides whether the camera button shows; the kit has to be here
	// to open it at all.
	cameraAvailable() {
		return !!this.kit && this.kit.camera.available();
	}

	openCamera(reopen) {
		this.afterScanner(() => {
			if (!this.kit) {
				frappe.msgprint(__('Camera scanning could not be loaded. Reload the page, or use a hardware/Bluetooth scanner or “Find item”.'));
				return;
			}
			this.kit.camera
				.prepare({
					unavailable: __('The camera needs a secure (https) connection and a browser that allows camera access. Use a hardware/Bluetooth scanner or “Find item”.'),
					no_reader: __('Could not load the QR code reader. Check the connection, or use a hardware/Bluetooth scanner or “Find item”.'),
				})
				.then((reader) => {
					if (reader) this.openSheet('camera', () => this.showCamera(reader), reopen);
				});
		});
	}

	showCamera(reader) {
		this.kit.camera.show({
			nav: this.nav,
			reader,
			page: this.wrapper,
			video_class: 'isa-video',
			hint: __('Point the camera at a barcode or QR code.'),
			qr_hint: __('Point the camera at a QR code. For an item barcode, use a scanner or “Find item”.'),
			// A read is looked up on this sheet's entry (see handleScan). A failure is said
			// once the count is back on its own entry: stepping off this one would close it.
			on_close: ({ read, failure }) => {
				if (read) this.handleScan(read, true);
				else this.nav.leave('camera', () => failure && frappe.msgprint(failure));
			},
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
.isa-reorder{margin:0 0 12px;}
.isa-reorder:empty{display:none;}
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
