// Managed Device form — lifecycle actions for Device Managers and a self-service
// Attest button for whoever the device is assigned to. Buttons mirror the Device
// Console; all writes go through erpnext_enhancements.api.device_management so the
// custody history + compliance rules stay server-owned.

frappe.ui.form.on('Managed Device', {
	refresh(frm) {
		if (frm.is_new()) return;
		const api = 'erpnext_enhancements.api.device_management.';
		const status = frm.doc.status;

		const action = (method, args, confirmMsg) => {
			const run = () =>
				frappe.call({ method: api + method, args, freeze: true }).then(() => frm.reload_doc());
			if (confirmMsg) frappe.confirm(confirmMsg, run);
			else run();
		};

		const pickEmployee = (label, onPick) => {
			frappe.prompt(
				[{ fieldname: 'employee', label: __('Employee'), fieldtype: 'Link', options: 'Employee', reqd: 1 }],
				(v) => onPick(v.employee),
				label,
				__('Confirm')
			);
		};

		if (status === 'In Stock' || status === 'In Repair') {
			frm.add_custom_button(__('Check Out'), () =>
				pickEmployee(__('Check Out Device'), (employee) =>
					action('check_out', { device: frm.doc.name, employee })
				), __('Device')
			);
		}
		if (status === 'Assigned') {
			frm.add_custom_button(__('Check In'), () => action('check_in', { device: frm.doc.name }), __('Device'));
			frm.add_custom_button(__('Transfer'), () =>
				pickEmployee(__('Transfer Device'), (employee) =>
					action('transfer', { device: frm.doc.name, new_employee: employee })
				), __('Device')
			);
		}
		if (status !== 'Retired' && status !== 'In Repair') {
			frm.add_custom_button(__('Send to Repair'), () => action('mark_repair', { device: frm.doc.name }), __('Device'));
		}
		if (status !== 'Retired' && status !== 'Lost/Stolen') {
			frm.add_custom_button(__('Mark Lost / Stolen'), () =>
				action('mark_lost', { device: frm.doc.name }, __('Flag this device as lost/stolen? It will be marked non-compliant.')), __('Device')
			);
		}
		if (status !== 'Retired') {
			frm.add_custom_button(__('Retire'), () =>
				action('retire', { device: frm.doc.name }, __('Retire this device? This is terminal.')), __('Device')
			);
		}

		// Self-service: the assignee can attest their own device's posture.
		if (frm.doc.assigned_to_user && frm.doc.assigned_to_user === frappe.session.user) {
			frm.add_custom_button(__('Attest Security'), () => attest(frm)).addClass('btn-primary');
		}

		// MDM remote actions (Phase 2) — shown only when the device is linked to a
		// provider. Each routes through mdm_integration.api.remote_action; the server
		// enforces capabilities + the BYOD wipe guard.
		if (frm.doc.mdm_provider && frm.doc.mdm_provider_device_id) {
			const remote = (label, act, opts) => {
				opts = opts || {};
				frm.add_custom_button(label, () => {
					const run = (extra) =>
						frappe
							.call({
								method: 'erpnext_enhancements.mdm_integration.api.remote_action',
								args: Object.assign({ device: frm.doc.name, action: act }, extra || {}),
								freeze: true,
							})
							.then(() => {
								frappe.show_alert({ message: __('Action sent.'), indicator: 'green' });
								frm.reload_doc();
							});
					if (opts.prompt) opts.prompt(run);
					else if (opts.confirm) frappe.confirm(opts.confirm, () => run());
					else run();
				}, __('Remote'));
			};
			if (frm.doc.mdm_provider === 'Miradore') {
				remote(__('Remote Lock'), 'lock', { confirm: __('Lock this device now?') });
				remote(__('Remote Wipe'), 'wipe', {
					prompt: (run) =>
						frappe.prompt(
							[{ fieldname: 'mode', label: __('Wipe mode'), fieldtype: 'Select', options: 'selective\nfull', default: 'selective', reqd: 1 }],
							(v) =>
								frappe.confirm(
									__('Wipe {0} ({1})? BYOD devices are always selective.', [frm.doc.device_name || frm.doc.name, v.mode]),
									() => run({ mode: v.mode })
								),
							__('Remote Wipe'),
							__('Continue')
						),
				});
				remote(__('Locate'), 'locate');
			} else if (frm.doc.mdm_provider === 'Action1') {
				remote(__('Reboot'), 'reboot', { confirm: __('Reboot this computer now?') });
				remote(__('Run Script'), 'run_script', {
					prompt: (run) =>
						frappe.prompt(
							[{ fieldname: 'script', label: __('Script'), fieldtype: 'Code', reqd: 1 }],
							(v) => run({ script: v.script }),
							__('Run Script'),
							__('Run')
						),
				});
			}
		}
	},
});

function attest(frm) {
	frappe.prompt(
		[
			{ fieldname: 'screen_lock', label: __('Screen lock enabled'), fieldtype: 'Check', default: frm.doc.screen_lock_enabled ? 1 : 0 },
			{ fieldname: 'encryption', label: __('Storage encryption enabled'), fieldtype: 'Check', default: frm.doc.encryption_enabled ? 1 : 0 },
			{ fieldname: 'os_version', label: __('OS version'), fieldtype: 'Data', default: frm.doc.os_version || '' },
		],
		(v) => {
			frappe
				.call({
					method: 'erpnext_enhancements.api.device_management.attest_device',
					args: { device: frm.doc.name, screen_lock: v.screen_lock, encryption: v.encryption, os_version: v.os_version },
					freeze: true,
				})
				.then(() => frm.reload_doc());
		},
		__('Confirm Device Security'),
		__('Submit')
	);
}
