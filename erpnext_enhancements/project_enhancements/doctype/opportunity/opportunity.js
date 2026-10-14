// "Set Reminder", drawn into the Opportunity's custom_reminder_action field. The dialog (a ToDo for
// the person, dated) lives in public/js/global_enhancements/record_actions.js since v1.686.0
// (erpnext_enhancements.record_actions.open_reminder), shared with the guided page's Actions menu.
frappe.ui.form.on("Opportunity", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			var field = frm.get_field("custom_reminder_action");
			var actions = window.erpnext_enhancements && erpnext_enhancements.record_actions;

			// A tab still holding the bundle from before v1.686.0: no button until it reloads.
			if (field && actions) {
				var $btn = $(
					'<button class="btn btn-default btn-sm icon-btn"><span class="icon icon-sm"><svg class="es-icon es-line  icon-sm" aria-hidden="true"><use href="#es-line-bell"></use></svg></span> Set Reminder</button>'
				);

				$btn.on("click", function () {
					actions.open_reminder({
						doctype: frm.doc.doctype,
						name: frm.doc.name,
						title: frm.doc.title,
					});
				});

				field.$wrapper.empty().append($btn);
			}
		}
	},
});
