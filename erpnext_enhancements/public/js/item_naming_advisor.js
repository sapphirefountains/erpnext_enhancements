// The Item naming advisor — the deterministic check, on the form where items are created.
//
// Targets: the "Item" doctype form.
// Loaded via: hooks.py `doctype_js["Item"]` (alongside item.js).
//
// Advisory only, and that is a design decision rather than an omission: there is no Item
// doc_event anywhere in this app, nothing here calls `frappe.validated = false`, and a save is
// never interrupted. The SOP says compliance is procedural, and a third of the live catalogue
// would fail the comma rule today — anything that blocked would fire constantly on legitimate
// edits to records that were already there.
//
// Two round-trip costs worth knowing before editing:
//   * `refresh` calls mode="record", which reads NO corpus. A full check on every form open
//     would read the whole catalogue every time anybody looked at an Item.
//   * "Check naming" calls mode="full" — duplicates, block occupancy, neighbours — only when
//     somebody has actually asked for it.

const INA_CHECK = "erpnext_enhancements.inventory_enhancements.item_naming.check_item";
const INA_REVIEW = "erpnext_enhancements.inventory_enhancements.item_naming_triton.review_item";
const INA_AVAILABLE =
	"erpnext_enhancements.inventory_enhancements.item_naming_triton.naming_review_available";

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new()) return;
		ina_headline(frm);
		frm.add_custom_button(__("Check naming"), () => ina_full_check(frm), __("Naming"));
		ina_add_triton_button(frm);
	},
});

function ina_args(frm) {
	return {
		item_code: frm.doc.item_code || frm.doc.name,
		item_name: frm.doc.item_name,
		item_group: frm.doc.item_group,
		stock_uom: frm.doc.stock_uom,
	};
}

// Cheap pass on refresh: the record only, no corpus read.
function ina_headline(frm) {
	frappe
		.call({ method: INA_CHECK, args: { ...ina_args(frm), mode: "record" } })
		.then((r) => {
			const data = (r && r.message) || {};
			// Idempotent across refreshes — without the clear, banners stack up.
			frm.dashboard.clear_headline();
			if (!data.findings || !data.findings.length) return;
			const stop = data.verdict === "STOP";
			frm.dashboard.set_headline_alert(
				__("Item naming: {0} — {1}. Use <b>Naming → Check naming</b> for the full check.", [
					data.verdict,
					ina_summary(data.findings),
				]),
				stop ? "red" : "orange"
			);
		})
		.catch(() => {
			// A failed advisory check must never be louder than the thing it advises about.
		});
}

function ina_summary(findings) {
	const first = findings[0];
	const rest = findings.length - 1;
	const label = frappe.utils.escape_html(first.code || "");
	return rest > 0 ? __("{0} and {1} more", [label, rest]) : label;
}

// Full pass on demand: duplicates, block occupancy, neighbours.
function ina_full_check(frm) {
	frappe.dom.freeze(__("Checking the naming schema…"));
	frappe
		.call({ method: INA_CHECK, args: { ...ina_args(frm), mode: "full" } })
		.then((r) => {
			frappe.dom.unfreeze();
			const data = (r && r.message) || {};
			frappe.msgprint({
				title: __("Item naming — {0}", [data.verdict || "?"]),
				message: ina_render(data),
				indicator: data.verdict === "STOP" ? "red" : data.verdict === "FIX" ? "orange" : "green",
				wide: true,
			});
		})
		.catch(() => frappe.dom.unfreeze());
}

function ina_add_triton_button(frm) {
	frappe
		.call({ method: INA_AVAILABLE })
		.then((r) => {
			// Hidden rather than disabled when Triton is unconfigured. A button that cannot
			// work reads as a bug in the feature rather than as a missing setting — which is
			// how the "Expand with AI" one was reported.
			if (!r || !r.message || !r.message.available) return;
			frm.add_custom_button(__("Check with Triton"), () => ina_triton(frm), __("Naming"));
		})
		.catch(() => {});
}

function ina_triton(frm) {
	frappe.dom.freeze(__("Asking Triton…"));
	frappe
		.call({ method: INA_REVIEW, args: ina_args(frm) })
		.then((r) => {
			frappe.dom.unfreeze();
			const data = (r && r.message) || {};
			frappe.msgprint({
				title: __("Item naming — Triton"),
				message: ina_render(data, data.text),
				indicator: data.verdict === "STOP" ? "red" : data.verdict === "FIX" ? "orange" : "blue",
				wide: true,
			});
		})
		.catch(() => frappe.dom.unfreeze());
}

// Shared renderer. `suggestions` is Triton's prose when there is any; the deterministic
// findings are rendered above it either way, because they are the authoritative half and a
// model outage must degrade this to the local check rather than to nothing.
function ina_render(data, suggestions) {
	const esc = (v) => frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));
	const findings = data.findings || [];
	const parts = [];

	if (!findings.length) {
		parts.push(`<p>${__("No findings — this record satisfies every check.")}</p>`);
	} else {
		parts.push(
			`<p><b>${__("Findings")}</b> ${__("(from executable rules, not judgement)")}</p><ul style="padding-left:18px">`
		);
		for (const f of findings) {
			const colour = f.severity === "STOP" ? "red" : f.severity === "FIX" ? "orange" : "blue";
			const fix = f.suggestion ? ` <code>${esc(f.suggestion)}</code>` : "";
			parts.push(
				`<li style="margin-bottom:5px"><span class="indicator ${colour}">${esc(
					f.severity
				)}</span> <code>${esc(f.code)}</code><br>${esc(f.message)}${fix}</li>`
			);
		}
		parts.push("</ul>");
	}

	const dupes = (data.duplicates && data.duplicates.exact) || [];
	if (dupes.length) {
		parts.push(
			`<p><b>${__("Already exists as")}</b> ${dupes.map((d) => `<code>${esc(d.item_code)}</code>`).join(", ")}</p>`
		);
	}

	const similar = data.similar || [];
	if (similar.length) {
		parts.push(`<p><b>${__("Closest existing records")}</b></p><ul style="padding-left:18px">`);
		for (const s of similar.slice(0, 5)) {
			parts.push(`<li><code>${esc(s.item_code)}</code> — ${esc(s.item_name)}</li>`);
		}
		parts.push("</ul>");
		parts.push(
			`<p style="color:var(--text-muted)">${__(
				"The same physical part filed under two codes and two wordings collides on nothing — that judgement is yours."
			)}</p>`
		);
	}

	const block = data.block;
	if (block && block.free && block.free.length) {
		parts.push(
			`<p><b>${__("Free numbers in {0}-{1}…{2}", [
				esc(block.prefix),
				esc(block.block_start),
				esc(block.block_end),
			])}</b><br>${block.free.slice(0, 15).map((n) => `<code>${esc(n)}</code>`).join(" ")}</p>`
		);
	}

	if (suggestions) {
		parts.push(`<hr><p><b>${__("Triton suggests")}</b></p>`);
		parts.push(`<div style="white-space:pre-wrap">${esc(suggestions)}</div>`);
	}
	return parts.join("");
}
