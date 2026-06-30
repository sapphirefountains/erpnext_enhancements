// Vehicle Maintenance Log form — load the standard checklist for the chosen
// Maintenance Type, without clobbering work already entered.
frappe.ui.form.on("Vehicle Maintenance Log", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.performed_by) {
			const me = (frappe.user && frappe.user.full_name && frappe.user.full_name()) || frappe.session.user;
			frm.set_value("performed_by", me);
		}
	},

	refresh(frm) {
		// A fresh log opened with a type pre-set (e.g. from the vehicle's "Log
		// Maintenance" button) and an empty checklist self-fills.
		if (frm.is_new() && frm.doc.maintenance_type && !(frm.doc.checklist || []).length) {
			load_checklist(frm, { silent: true });
		}
	},

	maintenance_type(frm) {
		load_checklist(frm, { silent: false });
	},

	// Mark the checklist as user-edited the moment a row is added or removed, so a
	// later type switch confirms before replacing it (an added row may carry only a
	// custom task with no status yet).
	checklist_add(frm) {
		frm.__fleet_checklist_dirty = true;
	},
	checklist_remove(frm) {
		frm.__fleet_checklist_dirty = true;
	},
});

// Editing any checklist cell also counts as user work.
frappe.ui.form.on("Vehicle Maintenance Task", {
	task(frm) {
		frm.__fleet_checklist_dirty = true;
	},
	status(frm) {
		frm.__fleet_checklist_dirty = true;
	},
	notes(frm) {
		frm.__fleet_checklist_dirty = true;
	},
});

function checklist_pristine(frm) {
	// Safe to silently replace only when nothing the user did would be lost:
	// no in-session edit/add/remove, and no status/notes on any row (covers a
	// reopened draft whose dirty flag was reset).
	if (frm.__fleet_checklist_dirty) {
		return false;
	}
	return (frm.doc.checklist || []).every((row) => !row.status && !row.notes);
}

function load_checklist(frm, { silent }) {
	const maintenance_type = frm.doc.maintenance_type;
	if (!maintenance_type) {
		return;
	}

	const fill = () => {
		frappe.call({
			method: "erpnext_enhancements.fleet_maintenance.checklists.get_default_checklist",
			args: { maintenance_type },
			callback: (r) => {
				frm.clear_table("checklist");
				(r.message || []).forEach((row) => Object.assign(frm.add_child("checklist"), row));
				frm.refresh_field("checklist");
				// The grid now holds exactly the standard, untouched rows.
				frm.__fleet_checklist_dirty = false;
			},
		});
	};

	if (silent || !(frm.doc.checklist || []).length || checklist_pristine(frm)) {
		fill();
	} else {
		frappe.confirm(
			__("Replace the checklist with the standard {0} items? Anything entered below will be cleared.", [
				__(maintenance_type),
			]),
			fill
		);
	}
}
