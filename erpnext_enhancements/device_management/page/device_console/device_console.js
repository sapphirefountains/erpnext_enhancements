// Device Console — mobile-first check-in / enrollment for Device Managers.
//
// Scan a device barcode/IMEI (keyboard-wedge scanner or the device camera) to
// pull up its record, then check it out to an employee, check it in, transfer it,
// send it for repair, or flag it lost. An unknown scan offers to enroll a new
// device pre-filled with the scanned code. All data flows through
// erpnext_enhancements.api.device_management. Theme-aware (Frappe CSS vars);
// semantic status colours are literal.
//
// Camera: the scanner kit (public/js/scanner_kit, v1.666.0), shared with the
// Inventory Scanner Audit. The native BarcodeDetector where it reads QR codes, else
// the vendored jsQR decoder, loaded on first use. Every iPhone takes the jsQR path:
// Safari has no BarcodeDetector, and before v1.666.0 the camera button never
// appeared there. jsQR reads QR only, so on an iPhone a device's 1D barcode still
// needs the wedge scanner or typing.
//
// The phone's Back button: each sheet (Camera Scan, Choose Employee) is a route
// segment of its own — device-console/camera, device-console/employee — so Back
// closes the sheet and stays on the console, and Forward opens it again. A scan is
// never a history entry. The kit (scanner_kit/sheets.js) pushes the entries and
// steps back off them; what can be opened again, and what a read or a pick leads
// to, is the console's own.

frappe.pages['device-console'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Device Console'),
		single_column: true,
	});
	wrapper.device_console = new DeviceConsole(page, wrapper);
};

frappe.pages['device-console'].on_page_show = function (wrapper) {
	if (wrapper.device_console) wrapper.device_console.onShow();
};

const DC_METHOD = 'erpnext_enhancements.api.device_management.';
const DC_ROUTE = 'device-console';
const DC_SHEETS = ['camera', 'employee'];
// How long to wait for the router to land our own history.back() before going on without it.
const DC_BACK_TIMEOUT_MS = 1500;

// sf_kit (v1.645.0): the shared loading / error / no-access panel, the toast and the quiet
// server call, from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It
// never rejects: a kit that never arrived resolves to a plain stand-in, so the console carries
// on. A Desk opened before the deploy that added the loader has no sf_kit_ready yet, and asks
// frappe.require for the kit itself.
function dc_kit() {
	if (typeof window.sf_kit_ready === 'function') return window.sf_kit_ready();
	return new Promise((resolve) =>
		frappe.require(['sf_kit.bundle.js', 'sf_kit.bundle.css'], () =>
			resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : dc_bare_kit())
		)
	);
}

// Neither the loader nor the kit: a Desk opened before this deploy whose old kit file is gone too.
// A line of text and a button, so the page still says something instead of throwing.
function dc_bare_kit() {
	const failed = (x) => ({ status: (x && x.status) || 0, message: '', exc_type: '', session_expired: (x && x.status) === 401 });
	const render = (el, kind, o) => {
		const $el = $(el).empty().append($('<div class="text-muted"></div>').text((o && o.title) || ''));
		if (o && o.on_action) $('<button class="btn btn-default btn-sm"></button>').text(o.action_label || __('Try again')).on('click', o.on_action).appendTo($el);
	};
	const call = (method, args) => Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => r && r.message, (x) => Promise.reject(failed(x)));
	return { esc: (v) => frappe.utils.escape_html(v == null ? '' : String(v)), call, state: { render }, toast: (message) => frappe.show_alert({ message, indicator: 'orange' }) };
}

// The scanner kit (v1.666.0): the camera sheet, its decoder, and the sheets' history handling,
// shared with the Inventory Scanner Audit. There is no global loader for it (the global bundle
// is left alone), so this is the guard: the kit already on the page answers at once, and only
// the first visit on a Desk asks frappe.require for it. `then` gets the kit, or null when it did
// not arrive (v16's frappe.require runs its callback even when the file failed to load), and is
// never left waiting.
function dc_scanner(then) {
	const kit = () => (window.sf_scanner && window.sf_scanner.version >= 1 ? window.sf_scanner : null);
	if (kit()) return then(kit());
	try {
		frappe.require(['scanner_kit.bundle.js'], () => then(kit()));
	} catch (e) {
		then(null);
	}
}

// No scanner kit: each sheet is a plain dialog with no entry of its own, so Back leaves the
// console, and the camera button stays hidden. The scans, the picker and the actions all work.
function dc_bare_sheets() {
	const nav = {
		sheet: null,
		opening: null,
		current: () => null,
		open: (name, show) => show(),
		hide: (d) => d.hide(),
		gone(d) {
			if (nav.sheet && nav.sheet.dialog === d) nav.sheet = null;
		},
		leave: (name, then) => then && then(),
		closed(name, d, then) {
			nav.gone(d);
			if (then) then();
		},
		show: () => ({ sheet: null, came: null }),
	};
	return nav;
}

// A failed load's panel: no access on a 403; a way back in when the session has lapsed (the
// quiet path puts up no sign-in dialog of frappe's); otherwise what went wrong, with Try again.
function dc_failed(sf, el, err, retry) {
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
			title: __('You do not have access to the Device Console.'),
			body: err.message,
		});
	}
	return sf.state.render(el, 'error', {
		title: __('Could not load the Device Console.'),
		body: err.message,
		on_action: retry,
	});
}

class DeviceConsole {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.state = { camera: false, device: null };
		this.pick = null; // {action, device} of a Choose Employee closed with no pick, so Forward can reopen it
		this.booted = false; // the bootstrap has answered: whether camera scanning is on is known
		this.kit = null; // the scanner kit, once it has arrived
		this.nav = dc_bare_sheets(); // the sheets' history handling; the kit's, once it has arrived
		// While the kit loads (a Desk's first visit), a show and any sheet tapped open wait here.
		this.scannerQueue = [];
		this.showQueued = false;
		this.injectStyles();
		this.buildSkeleton();
		dc_scanner((kit) => this.scannerArrived(kit));
		this.boot();
	}

	scannerArrived(kit) {
		if (kit) {
			this.kit = kit;
			this.nav = kit.sheets({ route: DC_ROUTE, names: DC_SHEETS, mark: 'dc_sheet', back_timeout_ms: DC_BACK_TIMEOUT_MS });
			this.nav.watch(this.wrapper);
		}
		const queue = this.scannerQueue;
		this.scannerQueue = null;
		this.renderCamera();
		queue.forEach((fn) => fn());
	}

	// `fn` once the scanner kit has answered: at once, after the first load.
	afterScanner(fn) {
		if (this.scannerQueue) this.scannerQueue.push(fn);
		else fn();
	}

	call(method, args) {
		return frappe.call({ method: DC_METHOD + method, args: args || {} }).then((r) => r.message);
	}

	// Quiet (v1.645.0): a failed start-up says so in the console's own panel, with Try again,
	// instead of leaving the counts blank behind a frappe dialog. Scans still work either way.
	boot() {
		dc_kit().then((sf) => {
			sf.state.render(this.$load, 'loading', { title: __('Loading the console…') });
			sf.call(DC_METHOD + 'get_console_bootstrap', {}, { quiet: true }).then(
				(d) => {
					this.$load.empty();
					this.state.camera = !!(d && d.enable_camera_scan);
					this.booted = true;
					this.renderCounts((d && d.counts) || {});
					this.renderCamera();
					this.focusScan();
				},
				(err) => dc_failed(sf, this.$load, err, () => this.boot())
			);
		});
	}

	// The camera button shows once the bootstrap has said camera scanning is on, and the kit is
	// here to say this browser can give the page a camera at all.
	cameraOn() {
		return this.state.camera && !!this.kit && this.kit.camera.available();
	}

	renderCamera() {
		if (this.booted) this.$cam.toggle(this.cameraOn());
	}

	focusScan() {
		if (this.$scan) setTimeout(() => this.$scan.focus(), 50);
	}

	// ----- sheets and the phone's Back button -----
	//
	// The kit's sheets (scanner_kit/sheets.js) push each sheet's route from the tap that opens
	// it, show the sheet once the route has settled, step back off its entry when it is closed any
	// other way than Back, and on a show decide whether a sheet URL is the console's own. What is
	// the console's: which sheet Back or Forward may open again (reopenSheet), and that a read or
	// a pick is acted on only once the console is back on its own entry, so the enroll prompt or
	// an error is not closed by that step.

	onShow() {
		if (this.scannerQueue) {
			// The kit is still loading: the show runs once it has answered, if the console is
			// still the page showing.
			if (!this.showQueued) {
				this.showQueued = true;
				this.afterScanner(() => {
					this.showQueued = false;
					if ((frappe.get_route() || [])[0] === DC_ROUTE) this.onShow();
				});
			}
			return;
		}
		const { sheet, came } = this.nav.show({ reopen: (name) => this.reopenSheet(name) });
		// A sheet has just closed: focus stays where closing it left it. On a phone, focusing
		// the scan box would raise the keyboard over the device card.
		if (sheet || came) return;
		this.focusScan();
	}

	// Forward onto a sheet Back closed: open it again on its entry. False when it cannot be.
	// The picker opens again only for a pick nobody made, for the device still on the card,
	// and only while that device's status still allows the action: after a Check Out it is
	// Assigned, and a second check-out would only collect the server's refusal.
	reopenSheet(name) {
		if (name === 'camera' && this.cameraOn()) {
			this.openCamera(true);
			return true;
		}
		const pick = this.pick;
		const d = this.state.device;
		if (name === 'employee' && pick && d && d.name === pick.device && this.pickAllowed(pick.action, d)) {
			if (pick.action === 'transfer') this.pickEmployeeAndTransfer(true);
			else this.pickEmployeeAndCheckOut(true);
			return true;
		}
		return false;
	}

	// The statuses renderDevice offers Check Out and Transfer for, which are the ones
	// api/device_management.py accepts.
	pickAllowed(action, d) {
		if (action === 'transfer') return d.status === 'Assigned';
		return d.status === 'In Stock' || d.status === 'In Repair';
	}

	buildSkeleton() {
		const $b = $(this.page.body);
		$b.html(`
			<div class="dc">
				<div class="dc-load"></div>
				<div class="dc-counts"></div>
				<div class="dc-scanrow">
					<input type="text" class="dc-scan" autocomplete="off" autocapitalize="characters"
						spellcheck="false" placeholder="${__('Scan or type a barcode / IMEI / asset tag…')}" />
					<button class="dc-cam" title="${__('Camera scan')}" aria-label="${__('Camera scan')}">📷</button>
				</div>
				<div class="dc-device"></div>
				<div class="dc-hint">${__('Scan a device to check it in or out.')}</div>
			</div>
		`);
		this.$load = $b.find('.dc-load');
		this.$counts = $b.find('.dc-counts');
		this.$scan = $b.find('.dc-scan');
		this.$cam = $b.find('.dc-cam');
		this.$device = $b.find('.dc-device');
		this.$hint = $b.find('.dc-hint');

		this.$scan.on('keydown', (e) => {
			if (e.key === 'Enter') {
				e.preventDefault();
				this.handleScan(this.$scan.val());
			}
		});
		this.$cam.on('click', () => this.openCamera());
	}

	renderCounts(c) {
		const tile = (label, value, cls) =>
			`<div class="dc-count ${cls || ''}"><div class="dc-count-n">${frappe.utils.escape_html(String(value || 0))}</div><div class="dc-count-l">${label}</div></div>`;
		this.$counts.html(
			tile(__('Total'), c.total) +
				tile(__('In Stock'), c.in_stock) +
				tile(__('Assigned'), c.assigned) +
				tile(__('Non-compliant'), c.non_compliant, c.non_compliant ? 'bad' : '')
		);
	}

	// After an action, in the background: quiet, and a failure keeps the counts already showing.
	refreshCounts() {
		dc_kit().then((sf) =>
			sf.call(DC_METHOD + 'get_console_bootstrap', {}, { quiet: true }).then(
				(d) => this.renderCounts((d && d.counts) || {}),
				() => sf.toast(__('Could not refresh the device counts.'), { tone: 'warning' })
			)
		);
	}

	handleScan(raw) {
		const code = (raw || '').trim();
		this.$scan.val('');
		if (!code) return;
		this.call('resolve_device_scan', { code }).then((res) => this.onResolved(res, code));
	}

	// The console's own entry is showing, with no sheet on it or on its way.
	onConsole() {
		return (frappe.get_route() || [])[0] === DC_ROUTE && !this.nav.current() && !this.nav.sheet && !this.nav.opening;
	}

	onResolved(res, code) {
		if (!res) return;
		if (res.type === 'device') {
			this.state.device = res.device;
			this.renderDevice();
			return;
		}
		// unknown. A reply that lands after the user has moved on (to another page, or a sheet
		// opened since) must not put the enroll prompt up over wherever they are: say it.
		if (!this.onConsole()) {
			frappe.show_alert({ message: __('No device matches “{0}”.', [frappe.utils.escape_html(code)]), indicator: 'orange' });
			return;
		}
		this.state.device = null;
		this.$device.empty();
		frappe.confirm(
			__('No device matches “{0}”. Enroll it as a new device?', [frappe.utils.escape_html(code)]),
			() => {
				frappe.new_doc('Managed Device', { barcode: code, asset_tag: code });
			},
			() => this.focusScan()
		);
	}

	renderDevice() {
		const d = this.state.device;
		if (!d) {
			this.$device.empty();
			return;
		}
		const esc = frappe.utils.escape_html;
		const statusCls = this.statusClass(d.status);
		const holder = d.assigned_to_employee
			? `${__('Held by')}: <b>${esc(d.assigned_to_employee)}</b>`
			: __('Unassigned');
		const compCls = d.compliance_status === 'Compliant' ? 'ok' : d.compliance_status === 'Non-Compliant' ? 'bad' : 'unk';

		this.$device.html(`
			<div class="dc-card">
				<div class="dc-card-head">
					<div>
						<div class="dc-card-name">${esc(d.device_name || d.name)}</div>
						<div class="dc-card-sub">${esc(d.name)}${d.asset_tag ? ' · ' + esc(d.asset_tag) : ''} · ${esc(d.platform || '')} ${esc(d.device_type || '')}</div>
					</div>
					<span class="dc-badge ${statusCls}">${esc(d.status)}</span>
				</div>
				<div class="dc-card-row">
					<span class="dc-chip ${d.ownership === 'BYOD' ? 'byod' : ''}">${esc(d.ownership || '')}</span>
					<span class="dc-chip ${compCls}">${esc(d.compliance_status || 'Unknown')}</span>
				</div>
				<div class="dc-card-holder">${holder}</div>
				<div class="dc-actions"></div>
				<a class="dc-open" href="${frappe.utils.get_form_link('Managed Device', d.name)}">${__('Open full record →')}</a>
			</div>
		`);
		// get_form_link is a /desk path, which the desk's link handler routes in place. An /app
		// href is a full page load on v16, and Back from the record then reloaded the console
		// from scratch, with the scanned device gone.

		const $actions = this.$device.find('.dc-actions');
		const btn = (label, cls, fn) => {
			const $btn = $(`<button class="btn btn-sm ${cls}">${label}</button>`);
			$btn.on('click', fn);
			$actions.append($btn);
		};
		if (d.status === 'In Stock' || d.status === 'In Repair') {
			btn(__('Check Out'), 'btn-primary', () => this.pickEmployeeAndCheckOut());
		}
		if (d.status === 'Assigned') {
			btn(__('Check In'), 'btn-primary', () => this.act('check_in', { device: d.name }));
			btn(__('Transfer'), 'btn-default', () => this.pickEmployeeAndTransfer());
		}
		if (d.status !== 'Retired' && d.status !== 'In Repair') {
			btn(__('Send to Repair'), 'btn-default', () => this.act('mark_repair', { device: d.name }));
		}
		if (d.status !== 'Retired' && d.status !== 'Lost/Stolen') {
			btn(__('Mark Lost'), 'btn-default dc-danger', () =>
				frappe.confirm(__('Flag {0} as lost/stolen?', [d.device_name || d.name]), () =>
					this.act('mark_lost', { device: d.name })
				)
			);
		}
	}

	statusClass(status) {
		return (
			{
				'In Stock': 'instock',
				Assigned: 'assigned',
				'In Repair': 'repair',
				'Lost/Stolen': 'lost',
				Retired: 'retired',
			}[status] || ''
		);
	}

	act(method, args) {
		return this.call(method, args).then((device) => {
			this.state.device = device;
			this.renderDevice();
			this.refreshCounts();
			frappe.show_alert({ message: __('Done.'), indicator: 'green' });
			this.focusScan();
		});
	}

	// The device is the one the picker opened for, not whatever a scan still being looked up
	// puts on the card while the picker is open.
	pickEmployeeAndCheckOut(reopen) {
		const device = this.state.device.name;
		this.pickEmployee('check_out', (employee) => this.act('check_out', { device, employee }), reopen);
	}

	pickEmployeeAndTransfer(reopen) {
		const device = this.state.device.name;
		this.pickEmployee('transfer', (employee) => this.act('transfer', { device, new_employee: employee }), reopen);
	}

	// ----- employee picker -----
	pickEmployee(action, onPick, reopen) {
		// Remembered so Forward can open the same sheet again, for the same device only.
		this.pick = { action, device: this.state.device && this.state.device.name };
		this.afterScanner(() => this.nav.open('employee', () => this.showEmployeeSheet(onPick), reopen));
	}

	showEmployeeSheet(onPick) {
		const app = this;
		const d = new frappe.ui.Dialog({ title: __('Choose Employee'), size: 'small' });
		let picked = null;
		d.$body.html(`
			<input type="text" class="form-control dc-emp-search" placeholder="${__('Name or ID')}" />
			<div class="dc-emp-results" style="margin-top:10px;max-height:50vh;overflow:auto;"></div>
		`);
		const $search = d.$body.find('.dc-emp-search');
		const $results = d.$body.find('.dc-emp-results');
		const run = frappe.utils.debounce(() => {
			const q = $search.val();
			if (!q) {
				$results.empty();
				return;
			}
			app.call('lookup_employee', { query: q, limit: 12 }).then((emps) => {
				if (!emps || !emps.length) {
					$results.html(`<div class="dc-empty">${__('No matches.')}</div>`);
					return;
				}
				$results.html(
					emps
						.map(
							(e) =>
								`<button class="btn btn-default btn-sm dc-emp-pick" style="display:block;width:100%;text-align:left;margin-bottom:4px;" data-emp="${frappe.utils.escape_html(e.name)}"><b>${frappe.utils.escape_html(e.employee_name || e.name)}</b> <span class="text-muted">${frappe.utils.escape_html(e.name)}</span></button>`
						)
						.join('')
				);
			});
		}, 250);
		$search.on('input', run);
		$results.on('click', '.dc-emp-pick', (e) => {
			picked = $(e.currentTarget).data('emp');
			app.nav.hide(d);
		});
		d.onhide = () => {
			// A pick made is done with: Forward onto this entry must not offer it again (a
			// second Transfer of a device that stays Assigned). The entry is stepped back off.
			if (picked) app.pick = null;
			app.nav.closed('employee', d, () => picked && onPick(picked));
		};
		this.nav.sheet = { name: 'employee', dialog: d };
		d.show();
		setTimeout(() => $search.focus(), 100);
	}

	// ----- camera scan (the scanner kit, shared with the Inventory Scanner Audit) -----
	openCamera(reopen) {
		this.afterScanner(() => {
			if (!this.kit) {
				frappe.msgprint(__('Camera scanning could not be loaded. Reload the page, or use a hardware/Bluetooth scanner.'));
				return;
			}
			this.kit.camera
				.prepare({
					unavailable: __('The camera needs a secure (https) connection and a browser that allows camera access. Use a hardware/Bluetooth scanner.'),
				})
				.then((reader) => {
					if (reader) this.nav.open('camera', () => this.showCamera(reader), reopen);
				});
		});
	}

	showCamera(reader) {
		this.kit.camera.show({
			nav: this.nav,
			reader,
			page: this.wrapper,
			video_class: 'dc-video',
			hint: __('Point the camera at a barcode or QR code.'),
			qr_hint: __('Point the camera at a QR code. For a barcode, use a hardware/Bluetooth scanner or type it in.'),
			// The read, or the failure, is dealt with once the console is back on its own entry:
			// stepping off the sheet's would otherwise close the enroll prompt or the message.
			on_close: ({ read, failure }) =>
				this.nav.leave('camera', () => {
					if (failure) frappe.msgprint(failure);
					else if (read) this.handleScan(read);
				}),
		});
	}

	// ----- styles (theme-aware; semantic status colours literal) -----
	injectStyles() {
		if (document.getElementById('dc-styles')) return;
		const css = `
.dc{max-width:680px;margin:0 auto;padding:6px 2px 90px;}
.dc-counts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;}
.dc-count{border:1px solid var(--border-color);border-radius:10px;padding:8px 6px;text-align:center;background:var(--control-bg,var(--bg-color));}
.dc-count-n{font-size:20px;font-weight:700;color:var(--text-color);}
.dc-count-l{font-size:11px;color:var(--text-muted);}
.dc-count.bad .dc-count-n{color:#b91c1c;}
.dc-scanrow{display:flex;gap:8px;margin-bottom:12px;}
.dc-scan{flex:1 1 auto;min-width:0;font-size:18px;padding:12px 14px;border:2px solid var(--border-color);border-radius:10px;background:var(--control-bg,var(--bg-color));color:var(--text-color);}
.dc-scan:focus{border-color:var(--primary);outline:none;}
.dc-cam{flex:0 0 auto;font-size:18px;line-height:1;padding:0 14px;border-radius:10px;border:1px solid var(--border-color);background:var(--bg-color);color:var(--text-color);cursor:pointer;}
.dc-card{border:1px solid var(--border-color);border-radius:12px;padding:14px;background:var(--control-bg,var(--bg-color));}
.dc-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;}
.dc-card-name{font-size:16px;font-weight:600;color:var(--text-color);}
.dc-card-sub{font-size:12px;color:var(--text-muted);margin-top:2px;}
.dc-badge{font-size:12px;font-weight:700;padding:3px 9px;border-radius:10px;white-space:nowrap;}
.dc-badge.instock{background:rgba(34,197,94,.15);color:#15803d;}
.dc-badge.assigned{background:rgba(59,130,246,.15);color:#1d4ed8;}
.dc-badge.repair{background:rgba(245,158,11,.18);color:#b45309;}
.dc-badge.lost{background:rgba(239,68,68,.18);color:#b91c1c;}
.dc-badge.retired{background:var(--bg-color);color:var(--text-muted);}
.dc-card-row{display:flex;gap:6px;margin:10px 0;flex-wrap:wrap;}
.dc-chip{font-size:11px;padding:2px 8px;border-radius:8px;border:1px solid var(--border-color);color:var(--text-muted);}
.dc-chip.byod{border-color:#a855f7;color:#7e22ce;}
.dc-chip.ok{border-color:#22c55e;color:#15803d;}
.dc-chip.bad{border-color:#ef4444;color:#b91c1c;}
.dc-chip.unk{border-color:var(--border-color);color:var(--text-muted);}
.dc-card-holder{font-size:13px;color:var(--text-color);margin-bottom:12px;}
.dc-actions{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;}
.dc-danger{color:#b91c1c;}
.dc-open{font-size:12px;}
.dc-hint{color:var(--text-muted);font-size:13px;text-align:center;padding:18px 0;}
.dc-empty{color:var(--text-muted);font-size:13px;text-align:center;padding:16px 0;}
.dc-video{width:100%;border-radius:8px;background:#000;max-height:60vh;}
@media(max-width:520px){.dc-counts{grid-template-columns:repeat(2,1fr);}}
`;
		$(`<style id="dc-styles">${css}</style>`).appendTo(document.head);
	}
}
