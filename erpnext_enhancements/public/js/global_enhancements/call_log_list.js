/**
 * Call Log list-view enhancements (the "call archive").
 *
 * Targets: the Call Log DocType list view.
 * Loaded via: hooks.py `doctype_list_js["Call Log"]`.
 *
 * Renders the AI call-intelligence fields as native indicator pills so the
 * archive is scannable: sentiment (green/gray/red), escalation risk
 * (green/orange/red) and a small flag on compliance-flagged calls. Uses
 * Frappe's `.indicator-pill` classes, which are theme-aware in both Frappe
 * Light and Timeless Night — no hard-coded colors.
 */
frappe.listview_settings["Call Log"] = frappe.listview_settings["Call Log"] || {};

(function () {
	const SENTIMENT_COLORS = {
		Positive: "green",
		Neutral: "gray",
		Negative: "red",
	};
	const RISK_COLORS = {
		Low: "green",
		Medium: "orange",
		High: "red",
	};

	function pill(value, color) {
		return `<span class="indicator-pill ${color} ellipsis">${frappe.utils.escape_html(
			__(value)
		)}</span>`;
	}

	$.extend(frappe.listview_settings["Call Log"], {
		add_fields: [
			"custom_caller_name",
			"custom_sentiment",
			"custom_escalation_risk",
			"custom_has_compliance_flags",
			"type",
			"status",
		],

		formatters: {
			custom_sentiment(value) {
				if (!value) return "";
				return pill(value, SENTIMENT_COLORS[value] || "gray");
			},
			custom_escalation_risk(value, df, doc) {
				if (!value) return "";
				let html = pill(value, RISK_COLORS[value] || "gray");
				if (cint(doc.custom_has_compliance_flags)) {
					html += ` <span title="${__("Compliance flag")}">🚩</span>`;
				}
				return html;
			},
			custom_caller_name(value, df, doc) {
				if (!value) return "";
				const icon = doc.type === "Outgoing" ? "↗" : "↘";
				return `<span>${icon} ${frappe.utils.escape_html(value)}</span>`;
			},
		},
	});
})();
