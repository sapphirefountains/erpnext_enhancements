// The party-naming advisor — on the three forms where party-linked records are named.
//
// Targets: Project, Opportunity, Address.
// Loaded via: hooks.py `doctype_js` for each of the three.
//
// One file rather than three because the question is identical in all three cases: does this
// record's name identify the party it belongs to? The per-doctype differences (which field,
// which party, what counts as in scope) live in crm_enhancements/party_naming_rules.py, where
// they are unit-tested, and not here.
//
// Advisory only. There is no validate hook on any of these doctypes, nothing here calls
// `frappe.validated = false`, and no save is interrupted. Most of these records predate the
// rule — 769 of 823 Opportunity titles are a bare party name — so anything that blocked would
// fire constantly on legitimate edits.
//
// The check reads ONLY the record in hand (plus an Address's links). No corpus read, so it is
// cheap enough to run on every refresh. Duplicate detection needs the whole set and lives in
// the Party Naming Audit report instead.

const PNA_CHECK = "erpnext_enhancements.crm_enhancements.party_naming.check_record";
const PNA_DOCTYPES = ["Project", "Opportunity", "Address"];

for (const doctype of PNA_DOCTYPES) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.is_new()) return;
			pna_check(frm, doctype);
		},
	});
}

function pna_check(frm, doctype) {
	frappe
		.call({ method: PNA_CHECK, args: { doctype: doctype, name: frm.doc.name } })
		.then((r) => {
			const data = (r && r.message) || {};
			// Idempotent across refreshes — without the clear, banners stack up.
			frm.dashboard.clear_headline();

			// Out of scope is silence, not a pass. An internal Project is not badly named; it
			// is a different kind of record, and telling somebody it "passed" would assert
			// something untrue about a check that never ran.
			if (!data.in_scope) return;
			if (!data.findings || !data.findings.length) return;

			const stop = data.verdict === "STOP";
			frm.dashboard.set_headline_alert(
				__("Naming: {0} — {1}", [data.verdict, pna_summary(data.findings)]),
				stop ? "red" : "orange"
			);
			frm.add_custom_button(__("Why?"), () => pna_explain(data), __("Naming"));
		})
		.catch(() => {
			// A failed advisory check must never be louder than the thing it advises about.
		});
}

function pna_summary(findings) {
	const first = frappe.utils.escape_html(findings[0].code || "");
	const rest = findings.length - 1;
	return rest > 0 ? __("{0} and {1} more", [first, rest]) : first;
}

function pna_explain(data) {
	const esc = (v) => frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));
	const parts = [];

	if (data.party) {
		parts.push(`<p><b>${__("Belongs to")}</b> ${esc(data.party)}</p>`);
	}

	parts.push(`<ul style="padding-left:18px">`);
	for (const f of data.findings || []) {
		const colour = f.severity === "STOP" ? "red" : f.severity === "FIX" ? "orange" : "blue";
		const fix = f.suggestion
			? `<br><b>${__("Suggested")}:</b> <code>${esc(f.suggestion)}</code>`
			: "";
		parts.push(
			`<li style="margin-bottom:6px"><span class="indicator ${colour}">${esc(
				f.severity
			)}</span> <code>${esc(f.code)}</code><br>${esc(f.message)}${fix}</li>`
		);
	}
	parts.push("</ul>");

	parts.push(
		`<p style="color:var(--text-muted)">${__(
			"Advisory only — nothing here blocks a save. The full list across every record is in the Party Naming Audit report."
		)}</p>`
	);

	frappe.msgprint({
		title: __("Naming — {0}", [data.verdict || "?"]),
		message: parts.join(""),
		indicator: data.verdict === "STOP" ? "red" : "orange",
		wide: true,
	});
}
