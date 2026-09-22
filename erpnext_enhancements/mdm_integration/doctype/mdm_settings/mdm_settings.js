// MDM Settings — per-provider Test Connection and Sync Now buttons.
//
// Both call erpnext_enhancements.mdm_integration.api (Device Manager gated). A passing
// test lifts the provider's auth pause, so the hourly sync resumes; Sync Now runs one
// provider's sync inside the request and reports its counters. Both read the SAVED
// settings, not the form, so unsaved credentials are refused with a message rather
// than tested against the old ones.

frappe.ui.form.on('MDM Settings', {
	refresh(frm) {
		const api = 'erpnext_enhancements.mdm_integration.api.';

		const unsaved = () => {
			if (!frm.is_dirty()) return false;
			frappe.msgprint(__('Save the settings first. The test uses the saved credentials.'));
			return true;
		};

		['Miradore', 'Action1'].forEach((provider) => {
			frm.add_custom_button(__('Test {0}', [provider]), () => {
				if (unsaved()) return;
				frappe
					.call({
						method: api + 'test_connection',
						args: { provider },
						freeze: true,
						freeze_message: __('Contacting {0}…', [provider]),
					})
					.then((r) => {
						const res = r.message || {};
						if (res.ok) {
							frappe.msgprint({
								title: __('{0} connected', [provider]),
								indicator: 'green',
								message: __('{0} returned {1} device(s) in {2} mode.', [provider, res.device_count, res.mode]),
							});
						} else {
							frappe.msgprint({
								title: __('{0} test failed', [provider]),
								indicator: 'red',
								message: frappe.utils.escape_html(res.error || __('Unknown error')),
							});
						}
						frm.reload_doc();
					});
			}, __('Test Connection'));

			frm.add_custom_button(__('Sync {0} Now', [provider]), () => {
				if (unsaved()) return;
				frappe
					.call({
						method: api + 'trigger_sync',
						args: { provider },
						freeze: true,
						freeze_message: __('Syncing {0}…', [provider]),
					})
					.then((r) => {
						const res = r.message || {};
						frappe.msgprint({
							title: __('{0} sync: {1}', [provider, res.status]),
							indicator: res.status === 'Completed' ? 'green' : 'orange',
							message: __('Created {0}, updated {1}, newly discovered {2}, now unmanaged {3}, failed {4}.', [
								res.created,
								res.updated,
								res.discovered,
								res.unmanaged,
								res.failed,
							]),
						});
						frm.reload_doc();
					});
			}, __('Sync Now'));
		});
	},
});
