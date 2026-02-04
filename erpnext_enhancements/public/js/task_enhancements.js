frappe.ui.form.on('Task', {
	custom_create_child_task_btn: function(frm) {
		const make_child_task = () => {
			frappe.ui.form.make_quick_entry('Task', null, null, {
				project: frm.doc.project,
				parent_task: frm.doc.name
			});
		};

		let need_save = false;
		if (frm.is_new()) {
			need_save = true;
		}

		if (!frm.doc.is_group) {
			frm.set_value('is_group', 1);
			need_save = true;
		}

		if (need_save) {
			frm.save('Save', make_child_task);
		} else {
			make_child_task();
		}
	}
});
