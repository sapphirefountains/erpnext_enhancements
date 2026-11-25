// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Knowledge Source form: the reference library (2026-11-25).
//
// A source is a saved link or an uploaded file. Saving it queues the server to read it
// (knowledge_base/sources.py): fetch the link or open the file, keep a private copy of a link, take its
// text, and ask Gemini for a summary when nobody typed one. This script:
//
// * says at the top that a source is reference material, not approved company knowledge, which is
//   how the AI tools label it too;
// * shows how the reading went (Pending, Ready, Failed, Not supported, Refused) and its note;
// * offers Fetch again (api/knowledge_base_sources.refetch, POST) to whoever may change the source,
//   and Open link for a saved link;
// * reloads itself once while a source is Pending, so a person who just saved sees the result
//   without pressing anything.
//
// Every name here starts kbs_ / KBS_: Desk form scripts share one page, and the knowledge
// article scripts already use KB_ and kb_.

const KBS_REFETCH = "erpnext_enhancements.api.knowledge_base_sources.refetch";

const KBS_STATE_COLORS = {
	Pending: "orange",
	Ready: "green",
	Failed: "red",
	"Not supported": "gray",
	Refused: "red",
};

frappe.ui.form.on("Knowledge Source", {
	refresh(frm) {
		kbs_intro(frm);
		if (frm.is_new()) return;
		const state = frm.doc.extraction_state || "Pending";
		frm.page.set_indicator(__(state), KBS_STATE_COLORS[state] || "gray");

		if (frm.doc.source_type === "Link" && frm.doc.url) {
			frm.add_custom_button(__("Open link"), () => {
				window.open(frm.doc.url, "_blank", "noopener,noreferrer");
			});
		}
		if (frm.perm && frm.perm[0] && frm.perm[0].write) {
			frm.add_custom_button(__("Fetch again"), () => kbs_refetch(frm));
		}
		kbs_wait_for_reading(frm);
	},
});

function kbs_intro(frm) {
	frm.set_intro("");
	if (frm.is_new()) {
		frm.set_intro(
			__(
				"Save a link or upload a file. ERPNext reads it, keeps a private copy, and Gemini writes a summary if you leave it blank. Everyone at Sapphire can read the library, and the AI answers from it, labeled as reference. Nothing restricted (passwords, break-glass access) goes here."
			),
			"blue"
		);
		return;
	}
	// set_intro replaces what is there, so the one message carries both lines.
	const base = __(
		"Reference material, not approved company knowledge. Nobody has reviewed it; the AI says so when it answers from it."
	);
	const state = frm.doc.extraction_state;
	const note = frm.doc.extraction_message;
	let extra = "";
	let color = "blue";
	if (state === "Pending") {
		extra = __("Reading it now. This page updates when it's done.");
		color = "orange";
	} else if (note) {
		extra = note;
		color = state === "Ready" || state === "Not supported" ? "yellow" : "red";
	}
	frm.set_intro(extra ? `${frappe.utils.escape_html(base)}<br>${frappe.utils.escape_html(extra)}` : base, color);
}

function kbs_refetch(frm) {
	frappe
		.call({
			method: KBS_REFETCH,
			type: "POST",
			args: { source: frm.doc.name },
			freeze: true,
			freeze_message: __("Queuing..."),
		})
		.then(() => {
			frappe.show_alert({ message: __("Reading it again"), indicator: "blue" });
			frm.reload_doc();
		});
}

// One reload a few seconds after opening a Pending source, then one more, so a person who just saved
// sees Ready without pressing anything. Bounded: never a polling loop that outlives the page.
function kbs_wait_for_reading(frm) {
	if (frm.doc.extraction_state !== "Pending") {
		frm.__kbs_waits = 0;
		return;
	}
	frm.__kbs_waits = (frm.__kbs_waits || 0) + 1;
	if (frm.__kbs_waits > 2) return;
	const name = frm.doc.name;
	setTimeout(() => {
		if (cur_frm === frm && frm.doc.name === name && !frm.is_dirty()) frm.reload_doc();
	}, 8000 * frm.__kbs_waits);
}
