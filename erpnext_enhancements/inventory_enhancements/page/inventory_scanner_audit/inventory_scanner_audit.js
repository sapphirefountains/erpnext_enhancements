// Inventory Scanner Audit — mobile-first physical-count page for inventory clerks.
//
// Scan a shelf/bin Storage Location, then scan items (keyboard-wedge scanner or
// the device camera via the BarcodeDetector API) and type the counted quantity.
// Counts accumulate in a resumable Inventory Count Session with a live system-qty
// snapshot and variance per line; Finalize builds a DRAFT Stock Reconciliation
// for a Stock Manager to review and submit. All data flows through
// erpnext_enhancements.api.inventory_scanner. Theme-aware (Frappe CSS vars);
// semantic variance colours are literal.

frappe.pages['inventory-scanner-audit'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Inventory Scanner Audit'),
		single_column: true,
	});
	wrapper.inventory_scanner = new InventoryScanner(page, wrapper);
};

frappe.pages['inventory-scanner-audit'].on_page_show = function (wrapper) {
	if (wrapper.inventory_scanner) wrapper.inventory_scanner.focusScan();
};

const ISA_METHOD = 'erpnext_enhancements.api.inventory_scanner.';

class InventoryScanner {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.state = { settings: {}, session: null, activeLocation: null, pendingItem: null };
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
		const camOn = this.state.settings.enable_camera_scan && 'BarcodeDetector' in window;
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
			this.$loc
				.addClass('show')
				.html(
					`📍 <b>${frappe.utils.escape_html(loc.location_name || loc.storage_location)}</b> — ${frappe.utils.escape_html(loc.warehouse_name || loc.warehouse)}`
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

	handleScan(raw) {
		const code = (raw || '').trim();
		this.clearScan();
		if (!code) return;
		const ensure = this.state.session ? Promise.resolve() : this.startSession();
		ensure.then(() =>
			this.call('resolve_scan', { code, warehouse: this.activeWarehouse() }).then((res) => this.onResolved(res, code))
		);
	}

	onResolved(res, code) {
		if (!res) return;
		if (res.type === 'location') {
			this.state.activeLocation = res;
			this.state.pendingItem = null;
			this.renderLocation();
			this.renderPending();
			frappe.show_alert({ message: __('Location: {0}', [res.location_name || res.storage_location]), indicator: 'blue' });
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
				frappe.show_alert({ message: __('Item {0} is disabled.', [res.item_code]), indicator: 'red' });
			}
			if (res.has_serial_no || res.has_batch_no) {
				frappe.show_alert({ message: __('Serial/batch item — count posts at warehouse qty only.'), indicator: 'orange' });
			}
			res.scanned_barcode = code;
			this.state.pendingItem = res;
			this.renderPending();
			return;
		}
		// unknown
		if (this.state.settings.allow_unknown_item) {
			this.openItemSearch(code);
		} else {
			frappe.show_alert({ message: __('Unknown barcode: {0}', [code]), indicator: 'red' });
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
				frappe.msgprint({
					title: __('Count Finalized'),
					indicator: 'green',
					message: __('Draft Stock Reconciliation {0} created with {1} line(s) for a Stock Manager to review.', [
						`<a href="${res.reconciliation_url}">${frappe.utils.escape_html(res.stock_reconciliation)}</a>`,
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
	openItemSearch(prefill) {
		const app = this;
		const d = new frappe.ui.Dialog({ title: __('Find Item'), size: 'small' });
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
			const itemCode = $(e.currentTarget).data('item');
			d.hide();
			app.call('resolve_scan', { code: itemCode, warehouse: app.activeWarehouse() }).then((res) => app.onResolved(res, itemCode));
		});
		d.show();
		setTimeout(() => {
			run();
			$search.focus();
		}, 100);
	}

	// ----- camera scan (BarcodeDetector) -----
	openCamera() {
		const app = this;
		if (!('BarcodeDetector' in window)) {
			frappe.msgprint(__('Camera scanning is not supported in this browser. Use a hardware/Bluetooth scanner or “Find item”.'));
			return;
		}
		const d = new frappe.ui.Dialog({ title: __('Camera Scan'), size: 'small' });
		d.$body.html(
			`<video class="isa-video" playsinline muted></video><div class="text-muted" style="margin-top:6px;">${__('Point the camera at a barcode or QR code.')}</div>`
		);
		const video = d.$body.find('video')[0];
		const detector = new window.BarcodeDetector();
		let stream = null;
		let stopped = false;
		const cleanup = () => {
			stopped = true;
			if (stream) stream.getTracks().forEach((t) => t.stop());
		};
		const tick = () => {
			if (stopped) return;
			detector
				.detect(video)
				.then((codes) => {
					if (stopped) return;
					if (codes && codes.length) {
						const val = codes[0].rawValue;
						cleanup();
						d.hide();
						app.handleScan(val);
					} else {
						setTimeout(tick, 200);
					}
				})
				.catch(() => {
					if (!stopped) setTimeout(tick, 300);
				});
		};
		navigator.mediaDevices
			.getUserMedia({ video: { facingMode: 'environment' } })
			.then((s) => {
				stream = s;
				video.srcObject = s;
				video.play();
				tick();
			})
			.catch(() => {
				frappe.msgprint(__('Could not access the camera.'));
				d.hide();
			});
		d.onhide = cleanup;
		d.show();
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
