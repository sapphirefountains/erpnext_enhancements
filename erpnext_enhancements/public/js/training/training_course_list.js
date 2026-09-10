// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The Training Course list view — and specifically, the moment somebody decides
// to build a course.
//
// Nik's ask was that authoring be simple enough that anyone can do it. Most of
// what stops people is not the editor: it is pressing "New", getting an empty
// form, and having to invent the content and the shape at the same time. So the
// list offers three ways in, in the order they are actually useful:
//
//   Start from a shape  -> a starter course, skeletal, with the structure done
//   Draft it with AI    -> Triton proposes a Course Spec, ERPNext builds it
//   New                 -> the empty form, still there, still first for anybody
//                          who knows exactly what they want
//
// Both of the first two land in the same place: an unpublished Draft with every
// AI question flagged for review. Neither is a second authoring path.

frappe.listview_settings["Training Course"] = {
	add_fields: ["status", "weight", "category"],

	// Status colours on the list, so "which of these is actually live?" is
	// answerable at a glance rather than by opening six courses.
	get_indicator(doc) {
		const map = {
			Published: ["Published", "green", "status,=,Published"],
			Draft: ["Draft", "orange", "status,=,Draft"],
			"In Review": ["In Review", "blue", "status,=,In Review"],
			Retired: ["Retired", "gray", "status,=,Retired"],
		};
		return map[doc.status] || [doc.status, "gray", "status,=," + doc.status];
	},

	onload(listview) {
		if (!frappe.user.has_role(["Training Author", "Training Manager", "System Manager"])) return;

		listview.page.add_inner_button(__("Start from a shape"), () => open_starters());

		// Only where the Triton widget actually loaded. A button that opens nothing
		// is worse than no button — see the "Open Builder" placeholder that outlived
		// its feature by three releases and told everyone the builder did not exist.
		if (window.SapphireTriton && window.SapphireTriton.ask) {
			listview.page.add_inner_button(__("Draft one with AI"), () => {
				window.SapphireTriton.ask(
					__(
						"I'd like to author a new training course. Ask me what it should cover and any specifics, then draft it with draft_course_spec; once I'm happy, create it with author_training_course. It will be created as an unpublished draft for me to review before publishing."
					)
				);
			});
		}
	},
};

function open_starters() {
	frappe.call({
		method: "erpnext_enhancements.api.training_course_authoring.list_starters",
		freeze: true,
		callback(r) {
			const starters = (r.message || {}).starters || [];
			if (!starters.length) {
				frappe.msgprint(__("No starters are available."));
				return;
			}
			render_gallery(starters);
		},
	});
}

function render_gallery(starters) {
	const dialog = new frappe.ui.Dialog({
		title: __("Start from a shape"),
		fields: [
			{
				fieldname: "intro",
				fieldtype: "HTML",
				options: `<p class="text-muted">${frappe.utils.escape_html(
					__(
						"Each one creates an unpublished draft with the structure already in place. The text is instructions to you, not content — replace it."
					)
				)}</p>`,
			},
			{ fieldname: "gallery", fieldtype: "HTML" },
			{
				fieldname: "course_title",
				fieldtype: "Data",
				label: __("Call it"),
				description: __("Leave blank to use the starter's own placeholder title."),
			},
		],
		primary_action_label: __("Create draft"),
		primary_action(values) {
			const chosen = dialog.$wrapper.find(".tc-starter.is-chosen").data("key");
			if (!chosen) {
				frappe.msgprint(__("Pick a shape first."));
				return;
			}
			frappe.call({
				method: "erpnext_enhancements.api.training_course_authoring.create_from_starter",
				args: { starter: chosen, course_title: values.course_title || null },
				freeze: true,
				freeze_message: __("Building the draft…"),
				callback(res) {
					const out = res.message || {};
					dialog.hide();
					if (!out.course) return;
					// Straight into the visual editor. The whole point of a starter is
					// that the next thing you do is replace the words, and landing on
					// the course form instead would mean finding the editor first.
					frappe.route_options = { course: out.course };
					frappe.set_route("training-canvas");
				},
			});
		},
	});

	const wrapper = dialog.fields_dict.gallery.$wrapper;
	starters.forEach((starter) => {
		const meta = [
			starter.lessons === 1 ? __("1 lesson") : __("{0} lessons", [starter.lessons]),
			starter.chapters ? __("{0} chapters", [starter.chapters]) : null,
		]
			.filter(Boolean)
			.join(" · ");
		const card = $(`
			<div class="tc-starter" style="border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:12px;margin-bottom:8px;cursor:pointer">
				<div style="font-weight:600"></div>
				<div class="text-muted small" style="margin-top:2px"></div>
				<div class="text-muted small" style="margin-top:6px"></div>
			</div>
		`);
		// .text(), never interpolation: these strings are ours today and a template
		// somebody adds later is still going through this same renderer.
		card.data("key", starter.key);
		card.children().eq(0).text(starter.label);
		card.children().eq(1).text(starter.blurb);
		card.children().eq(2).text(meta);
		card.on("click", () => {
			wrapper.find(".tc-starter").removeClass("is-chosen").css("border-color", "");
			card.addClass("is-chosen").css("border-color", "var(--primary)");
		});
		wrapper.append(card);
	});

	dialog.show();
}
