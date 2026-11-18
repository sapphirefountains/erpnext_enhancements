// Work Order: the Build Checklist door (v1.826.0, product_fulfillment, TASK-2026-03229).
//
// A Work Order for a configured product carries its configuration's build steps
// (custom_build_steps, copied when it was made) and who is building it (custom_built_by). This
// adds the Build Checklist button, which opens the phone-sized page (/desk/build-checklist/<WO>,
// a history entry, so Back returns here), and a one-line headline: steps done and the builder.
// Nothing here writes; the page and its endpoints do.

frappe.ui.form.on("Work Order", {
	refresh(frm) {
		const steps = frm.doc.custom_build_steps || [];
		if (frm.is_new() || (!steps.length && !frm.doc.custom_build_configuration)) return;
		frm.add_custom_button(__("Build Checklist"), () => frappe.set_route("build-checklist", frm.doc.name));
		const build = steps.filter((s) => (s.step_type || "Build") !== "QC");
		const done = build.filter((s) => s.done).length;
		const builder = frm.doc.custom_built_by
			? __("Built by {0}.", [frappe.utils.escape_html(frappe.user.full_name(frm.doc.custom_built_by))])
			: __("Nobody is recorded as building it yet.");
		frm.dashboard.set_headline_alert(
			`${__("Build checklist: {0} of {1} steps done.", [done, build.length])} ${builder}`,
			build.length && done === build.length ? "green" : "blue"
		);
	},
});
