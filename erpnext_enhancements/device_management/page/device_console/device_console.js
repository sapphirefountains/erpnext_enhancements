// Device Console — mobile-first check-in / enrollment for Device Managers.
//
// Scan a device barcode/IMEI (keyboard-wedge scanner or the device camera via
// the BarcodeDetector API) to pull up its record, then check it out to an
// employee, check it in, transfer it, send it for repair, or flag it lost. An
// unknown scan offers to enroll a new device pre-filled with the scanned code.
// All data flows through erpnext_enhancements.api.device_management. Theme-aware
// (Frappe CSS vars); semantic status colours are literal. The camera/scan-row
// pattern mirrors the Inventory Scanner Audit page.
//
// The phone's Back button: each sheet (Camera Scan, Choose Employee) is a route
// segment of its own — device-console/camera, device-console/employee — so Back
// closes the sheet and stays on the console, and Forward opens it again. A scan is
// never a history entry. See "sheets and the phone's Back button" below.

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

class DeviceConsole {
	constructor(page, wrapper) {
		this.page = page;
		this.state = { camera: false, device: null };
		this.sheet = null; // {name, dialog} while a sheet is open
		this.opening = null; // the sheet whose route is being pushed
		this.shownSheet = undefined; // the sheet segment at the last show; undefined before the first
		this.away = false; // another Desk page has been shown since the last show (see onShow)
		this.pick = null; // {action, device} of a Choose Employee closed with no pick, so Forward can reopen it
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
		return frappe.call({ method: DC_METHOD + method, args: args || {} }).then((r) => r.message);
	}

	boot() {
		this.call('get_console_bootstrap').then((d) => {
			this.state.camera = !!(d && d.enable_camera_scan);
			this.renderCounts((d && d.counts) || {});
			this.$cam.toggle(this.cameraOn());
			this.focusScan();
		});
	}

	cameraOn() {
		return this.state.camera && 'BarcodeDetector' in window;
	}

	focusScan() {
		if (this.$scan) setTimeout(() => this.$scan.focus(), 50);
	}

	// ----- sheets and the phone's Back button -----
	//
	// A sheet's route is pushed from the tap that opens it, and the sheet is shown only
	// once the route has settled: every route change closes the open dialog (the router's
	// set_history and container.change_to both do), this one included. Back then pops the
	// segment and the router closes the sheet with nothing more from here. A sheet closed
	// any other way (a read, a pick, X, Escape) steps back off its own entry, so Back never
	// lands on a sheet that is already shut. The entry behind a sheet's is always the
	// console's own: one on the first show is replaced if it is a pasted link, and stepped
	// back off if it is the page's own (a reload), as is one Back or Forward lands on that
	// cannot be opened again.
	//
	// Only an entry this page pushed is a sheet's. Each is marked in history.state as it is
	// pushed (markSheet), because the same URL also arrives from outside: frappe records every
	// route with a second segment in Route History, and the awesome bar offers the most used as
	// links. A sheet opened from one of those would have another page behind it, so X, or a
	// camera read's step back, would land there and the scan be looked up off-screen. An
	// unmarked sheet URL is handed to the console, as a pasted link's is — or, when it was pushed
	// over the console's own entry while that was showing, stepped back off, onto that entry. A
	// marked one on the first show (a reload, or Android restoring a discarded tab: history.state
	// survives both) is stepped back off too, since the entry behind a marked one is always the
	// console's own.
	//
	// Each replace the page asks for clears `route_flags.replace_route` as soon as set_route
	// returns. set_route reads the flag as it writes the entry, but clears route_flags itself
	// only once its promise settles, and that waits on every request then in flight (v16's
	// after_ajax) — on a first show, the bootstrap call. Left set, the flag would turn the next
	// tap into a replace of the console's own entry, and Back or X from the sheet it opened would
	// then leave the page.

	sheetRoute() {
		const route = frappe.get_route() || [];
		return route[0] === DC_ROUTE && DC_SHEETS.includes(route[1]) ? route[1] : null;
	}

	// The mark goes on the current entry, with no URL, and keeps whatever else it holds.
	markSheet(name) {
		try {
			window.history.replaceState(Object.assign({}, window.history.state, { dc_sheet: name }), '');
		} catch (e) {
			// Unmarked, the entry is handed to the console on its next show rather than reopened.
		}
	}

	ownSheet(name) {
		try {
			const state = window.history.state;
			return !!state && state.dc_sheet === name;
		} catch (e) {
			return false;
		}
	}

	// `reopen`: Forward landed on the sheet's entry; show it there, and push nothing.
	openSheet(name, show, reopen) {
		const on = this.sheetRoute();
		if (reopen || (on === name && !this.sheet)) {
			if (on === name && !this.sheet) show();
			return;
		}
		this.opening = name;
		// An entry left behind by a sheet that has closed is taken over, not stacked on.
		if (on) frappe.route_flags.replace_route = true;
		const settled = frappe.set_route(DC_ROUTE, name);
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

	// Every sheet's onhide ends here. `then` runs once the console is back on its own entry,
	// so a dialog it opens (the enroll prompt, an error) is not closed by that route change.
	sheetClosed(name, d, then) {
		if (this.sheet && this.sheet.dialog === d) this.sheet = null;
		let done = false;
		const finish = () => {
			if (done) return;
			done = true;
			if (then) then();
		};
		// Back closed it: the route has already moved off the segment. Nothing to step over.
		if (this.sheetRoute() !== name) {
			finish();
			return;
		}
		frappe.router.once('change', finish);
		setTimeout(finish, DC_BACK_TIMEOUT_MS);
		window.history.back();
	}

	onShow() {
		const sheet = this.sheetRoute();
		const first = this.shownSheet === undefined;
		const came = this.shownSheet;
		// The console's own entry was showing at the last show, and no other page has been since.
		const stayed = !first && !this.away && came === null;
		this.away = false;
		this.shownSheet = sheet;
		// A sheet whose entry is no longer current. The router closes the open dialog on
		// every route change, but not one still fading in: it is not cur_dialog yet.
		if (this.sheet && this.sheet.name !== sheet) this.hideSheet(this.sheet.dialog);
		if (sheet) {
			if (this.opening === sheet || (this.sheet && this.sheet.name === sheet)) return;
			if (first || !this.ownSheet(sheet)) {
				// A reload, a pasted link, or a sheet URL reached from another page (never start a
				// camera nobody asked for, over a page it would step back to): the entry becomes
				// the console, or is stepped back off onto the console's own.
				this.shownSheet = null;
				if (stayed || (first && this.ownSheet(sheet))) {
					// Pushed over the console's own entry while it was showing (the awesome bar's
					// link to a sheet, picked here), or a reload on a sheet entry this page pushed
					// (its mark survives the reload): the entry behind is the console's, so step
					// back onto it. Replacing this one would leave two console entries in a row.
					window.history.back();
				} else {
					frappe.route_flags.replace_route = true;
					frappe.set_route(DC_ROUTE);
					// Read already, and left set it would outlive this call (see openSheet).
					frappe.route_flags.replace_route = false;
				}
			} else if (!this.reopenSheet(sheet)) {
				// Back or Forward onto a sheet that cannot be opened again (the picker, for a
				// device scanned since): step back onto the console's own entry, which is always
				// the one behind a sheet's. Replacing this entry instead would leave two console
				// entries in a row, and the next Back would seem to do nothing.
				window.history.back();
			}
			return;
		}
		// A sheet has just closed: focus stays where closing it left it. On a phone, focusing
		// the scan box would raise the keyboard over the device card.
		if (came) return;
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
				<div class="dc-counts"></div>
				<div class="dc-scanrow">
					<input type="text" class="dc-scan" autocomplete="off" autocapitalize="characters"
						spellcheck="false" placeholder="${__('Scan or type a barcode / IMEI / asset tag…')}" />
					<button class="dc-cam" title="${__('Camera scan')}">📷</button>
				</div>
				<div class="dc-device"></div>
				<div class="dc-hint">${__('Scan a device to check it in or out.')}</div>
			</div>
		`);
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

	refreshCounts() {
		this.call('get_console_bootstrap').then((d) => this.renderCounts((d && d.counts) || {}));
	}

	handleScan(raw) {
		const code = (raw || '').trim();
		this.$scan.val('');
		if (!code) return;
		this.call('resolve_device_scan', { code }).then((res) => this.onResolved(res, code));
	}

	// The console's own entry is showing, with no sheet on it or on its way.
	onConsole() {
		return (frappe.get_route() || [])[0] === DC_ROUTE && !this.sheetRoute() && !this.sheet && !this.opening;
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
		this.openSheet('employee', () => this.showEmployeeSheet(onPick), reopen);
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
			app.hideSheet(d);
		});
		d.onhide = () => {
			// A pick made is done with: Forward onto this entry must not offer it again (a
			// second Transfer of a device that stays Assigned). The entry is stepped back off.
			if (picked) app.pick = null;
			app.sheetClosed('employee', d, () => picked && onPick(picked));
		};
		this.sheet = { name: 'employee', dialog: d };
		d.show();
		setTimeout(() => $search.focus(), 100);
	}

	// ----- camera scan (BarcodeDetector) — same approach as Inventory Scanner -----
	openCamera(reopen) {
		if (!('BarcodeDetector' in window)) {
			frappe.msgprint(__('Camera scanning is not supported in this browser. Use a hardware/Bluetooth scanner.'));
			return;
		}
		this.openSheet('camera', () => this.showCamera(), reopen);
	}

	showCamera() {
		const app = this;
		const d = new frappe.ui.Dialog({ title: __('Camera Scan'), size: 'small' });
		d.$body.html(
			`<video class="dc-video" playsinline muted></video><div class="text-muted" style="margin-top:6px;">${__('Point the camera at a barcode or QR code.')}</div>`
		);
		const video = d.$body.find('video')[0];
		const detector = new window.BarcodeDetector();
		let stream = null;
		let stopped = false;
		let read = null; // what the camera read, handed on once the sheet has closed
		let failed = false;
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
						read = codes[0].rawValue;
						cleanup();
						app.hideSheet(d);
					} else {
						setTimeout(tick, 200);
					}
				})
				.catch(() => {
					if (!stopped) setTimeout(tick, 300);
				});
		};
		// The read, or the failure, is dealt with once the console is back on its own entry:
		// stepping off the sheet's would otherwise close the enroll prompt or the message.
		d.onhide = () => {
			cleanup();
			app.sheetClosed('camera', d, () => {
				if (failed) frappe.msgprint(__('Could not access the camera.'));
				else if (read) app.handleScan(read);
			});
		};
		this.sheet = { name: 'camera', dialog: d };
		d.show();
		// Started after the sheet is up, never before the route has settled.
		navigator.mediaDevices
			.getUserMedia({ video: { facingMode: 'environment' } })
			.then((s) => {
				if (stopped) {
					// Closed while the permission prompt was up.
					s.getTracks().forEach((t) => t.stop());
					return;
				}
				stream = s;
				video.srcObject = s;
				video.play();
				tick();
			})
			.catch(() => {
				failed = true;
				app.hideSheet(d);
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
