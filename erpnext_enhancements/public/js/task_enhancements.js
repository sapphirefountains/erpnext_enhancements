/**
 * Task form script — create child task.
 *
 * Targets: the "Task" doctype form.
 * Loaded via: hooks.py `doctype_js["Task"]` (with vue.global.js + comments.js
 *   and task_enhancements/doctype/task/task.js; the Comments App is auto-mounted
 *   for Task by comments_auto.js).
 *
 * Handles the `custom_create_child_task_btn` button: ensures the current task is
 * saved and is a group, then opens Quick Entry for a new child Task pre-linked to
 * the same project and this task as parent.
 */
frappe.ui.form.on('Task', {
	custom_create_child_task_btn: function(frm) {
		const make_child_task = () => {
			// `doctype` is NOT optional here, and its absence broke this button
			// outright. make_quick_entry's `check_quick_entry_doc` only builds a
			// doc via frappe.model.get_new_doc when the 4th argument is falsy --
			// pass an object and it is used VERBATIM. The dialog then posts it to
			// frappe.client.save, which raises `ValueError: "doctype" is a
			// required key` before it ever reaches Task's controller. So every
			// press of "Create Child Task" 500'd and created nothing, while the
			// dialog looked entirely normal right up to Save.
			frappe.ui.form.make_quick_entry('Task', null, null, {
				doctype: 'Task',
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
