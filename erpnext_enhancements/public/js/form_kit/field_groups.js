/*
 * form kit: several steps on screen at once, each in its own frappe.ui.FieldGroup (v1.684.0).
 *
 * The record editor (ui/editor.js) reads the fields on screen through one `this.fg`: its
 * `fields_dict` (a control by fieldname) and `refresh_dependency()`. One step at a time that is one
 * FieldGroup. The guided page's One page and Tabs layouts draw a FieldGroup per step, so `this.fg`
 * is then this set of them, which answers the same two questions for all of them at once: a field
 * is found in whichever step draws it (each field is in exactly one step, tests/test_form_kit.py),
 * and a change refreshes every step's depends_on, because a condition in one step may read a value
 * from another (editor.js condition reads the whole record).
 *
 * `multi` tells the editor which kind it holds; `steps()` lists the steps drawn, so "every field on
 * screen" (collect_step, collect_tables) covers every section, not only the one in view.
 *
 * Pure: it holds whatever FieldGroup-shaped objects it is given (fields_dict, refresh_dependency).
 */

export class FieldGroups {
	constructor() {
		this.multi = true;
		this.list = [];
		this.drawn = [];
		this.by_step = {};
		this.fields_dict = {};
	}

	// One step's FieldGroup, drawn: its controls join the set.
	add(step, fg) {
		if (!fg) return;
		this.list.push(fg);
		if (step && step.key) {
			this.by_step[step.key] = fg;
			this.drawn.push(step);
		}
		Object.assign(this.fields_dict, fg.fields_dict || {});
	}

	owns(fg) {
		return this.list.includes(fg);
	}

	steps() {
		return this.drawn.slice();
	}

	group_of(step_key) {
		return this.by_step[step_key] || null;
	}

	// The step whose FieldGroup holds `fieldname`, or null.
	step_of(fieldname, suffix) {
		const names = suffix ? [fieldname, fieldname + suffix] : [fieldname];
		return (
			this.drawn.find((step) => {
				const fg = this.by_step[step.key];
				return !!fg && !!fg.fields_dict && names.some((name) => !!fg.fields_dict[name]);
			}) || null
		);
	}

	refresh_dependency() {
		this.list.forEach((fg) => {
			if (fg && typeof fg.refresh_dependency === "function") fg.refresh_dependency();
		});
	}
}
