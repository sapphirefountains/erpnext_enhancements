// Device Console — mobile-first check-in / enrollment for Device Managers.
//
// Scan a device barcode/IMEI (keyboard-wedge scanner or the device camera via
// the BarcodeDetector API) to pull up its record, then check it out to an
// employee, check it in, transfer it, send it for repair, or flag it lost. An
// unknown scan offers to enroll a new device pre-filled with the scanned code.
// All data flows through erpnext_enhancements.api.device_management. Theme-aware
// (Frappe CSS vars); semantic status colours are literal. The camera/scan-row
// pattern mirrors the Inventory Scanner Audit page.

frappe.pages['device-console'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Device Console'),
		single_column: true,
	});
	wrapper.device_console = new DeviceConsole(page);
};

frappe.pages['device-console'].on_page_show = function (wrapper) {
	if (wrapper.device_console) wrapper.device_console.focusScan();
};

const DC_METHOD = 'erpnext_enhancements.api.device_management.';

class DeviceConsole {
	constructor(page) {
		this.page = page;
		this.state = { camera: false, device: null };
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
			this.$cam.toggle(this.state.camera && 'BarcodeDetector' in window);
			this.focusScan();
		});
	}

	focusScan() {
		if (this.$scan) setTimeout(() => this.$scan.focus(), 50);
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

	onResolved(res, code) {
		if (!res) return;
		if (res.type === 'device') {
			this.state.device = res.device;
			this.renderDevice();
			return;
		}
		// unknown
		this.state.device = null;
		this.$device.empty();
		frappe.confirm(
			__('No device matches “{0}”. Enroll it as a new device?', [code]),
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
				<a class="dc-open" href="/app/managed-device/${encodeURIComponent(d.name)}">${__('Open full record →')}</a>
			</div>
		`);

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

	pickEmployeeAndCheckOut() {
		this.pickEmployee((employee) => this.act('check_out', { device: this.state.device.name, employee }));
	}

	pickEmployeeAndTransfer() {
		this.pickEmployee((employee) => this.act('transfer', { device: this.state.device.name, new_employee: employee }));
	}

	// ----- employee picker -----
	pickEmployee(onPick) {
		const app = this;
		const d = new frappe.ui.Dialog({ title: __('Choose Employee'), size: 'small' });
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
			const emp = $(e.currentTarget).data('emp');
			d.hide();
			onPick(emp);
		});
		d.show();
		setTimeout(() => $search.focus(), 100);
	}

	// ----- camera scan (BarcodeDetector) — same approach as Inventory Scanner -----
	openCamera() {
		const app = this;
		if (!('BarcodeDetector' in window)) {
			frappe.msgprint(__('Camera scanning is not supported in this browser. Use a hardware/Bluetooth scanner.'));
			return;
		}
		const d = new frappe.ui.Dialog({ title: __('Camera Scan'), size: 'small' });
		d.$body.html(
			`<video class="dc-video" playsinline muted></video><div class="text-muted" style="margin-top:6px;">${__('Point the camera at a barcode or QR code.')}</div>`
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
