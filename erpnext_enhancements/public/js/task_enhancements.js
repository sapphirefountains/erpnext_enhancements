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
