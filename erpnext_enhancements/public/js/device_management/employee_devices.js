// Employee form — render the "Assigned Devices" HTML panel (custom field
// custom_managed_devices_html, provisioned by device_management/setup.py). A
// read-only list of the Managed Devices linked to this employee, so HR/managers
// see a person's devices in context. Permission-respecting (frappe.db.get_list).

frappe.ui.form.on('Employee', {
	refresh(frm) {
		if (frm.is_new() || !frm.fields_dict.custom_managed_devices_html) return;
		const $wrap = $(frm.fields_dict.custom_managed_devices_html.wrapper).empty();

		frappe.db
			.get_list('Managed Device', {
				filters: { assigned_to_employee: frm.doc.name },
				fields: ['name', 'device_name', 'device_type', 'platform', 'status', 'compliance_status'],
				order_by: 'device_name asc',
				limit: 0,
			})
			.then((rows) => {
				if (!rows || !rows.length) {
					$wrap.html(`<div class="text-muted" style="padding:6px 0;">${__('No devices assigned.')}</div>`);
					return;
				}
				const esc = frappe.utils.escape_html;
				const compColor = (c) =>
					c === 'Compliant' ? '#15803d' : c === 'Non-Compliant' ? '#b91c1c' : 'var(--text-muted)';
				const list = rows
					.map(
						(r) => `
						<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid var(--border-color);">
							<div>
								<a href="/app/managed-device/${encodeURIComponent(r.name)}"><b>${esc(r.device_name || r.name)}</b></a>
								<div style="font-size:12px;color:var(--text-muted);">${esc(r.platform || '')} ${esc(r.device_type || '')} · ${esc(r.status)}</div>
							</div>
							<span style="font-size:12px;font-weight:600;color:${compColor(r.compliance_status)};">${esc(r.compliance_status || 'Unknown')}</span>
						</div>`
					)
					.join('');
				$wrap.html(`<div style="max-width:520px;">${list}</div>`);
			});
	},
});
