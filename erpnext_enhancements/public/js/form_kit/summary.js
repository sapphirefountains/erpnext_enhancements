/*
 * form kit P4-B: what the guided page's live summary and head band say about the record.
 *
 * SUMMARY names, per DocType, which fields the summary reads. Only Opportunity has a flow today
 * (form_kit/config CONFIGS); a DocType without an entry gets its name and the checklist alone.
 *
 * STREAM_PILLAR is a copy of print_style.STREAM_PILLAR, the one record of which pillar a value
 * stream takes (docs/app-design-system.md, "Pillars and value streams"); test_guided_entry holds
 * this copy to it, as test_print_style holds project_brief.js's.
 *
 * Pure: no frappe, no DOM.
 */

export const STREAM_PILLAR = {
	Design: "design",
	Build: "build",
	Products: "build",
	Delivery: "build",
	Service: "service",
	Events: "rent",
};

export const SUMMARY = {
	Opportunity: {
		title: "custom_opportunity_name",
		account: "party_name",
		streams: { field: "custom_value_stream", row_field: "value_stream" },
		amount: "opportunity_amount",
		status: "status",
	},
};

export function pillar_for(stream) {
	return STREAM_PILLAR[stream] || "neutral";
}

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

// The value streams chosen, in order, each once: [{ stream, pillar }].
export function streams_of(doctype, values) {
	const config = SUMMARY[doctype];
	if (!config || !config.streams) return [];
	const rows = (values || {})[config.streams.field];
	const seen = new Set();
	const out = [];
	(Array.isArray(rows) ? rows : []).forEach((row) => {
		const stream = text((row || {})[config.streams.row_field]);
		if (!stream || seen.has(stream)) return;
		seen.add(stream);
		out.push({ stream, pillar: pillar_for(stream) });
	});
	return out;
}

// The pillar of the head band's stripe: the first chosen stream's, else neutral.
export function lead_pillar(doctype, values) {
	const streams = streams_of(doctype, values);
	return streams.length ? streams[0].pillar : "neutral";
}

// { title, account, streams, amount, status, done, total, missing, percent } for the panel.
// `amount` is the raw number (the page formats it as currency); null when not set.
export function summary_model(doctype, name, values, completeness) {
	const config = SUMMARY[doctype] || {};
	const v = values || {};
	const c = completeness || {};
	const total = Number(c.total) || 0;
	const done = Number(c.done) || 0;
	const raw_amount = config.amount ? v[config.amount] : null;
	const amount = raw_amount === null || raw_amount === undefined || raw_amount === "" ? null : Number(raw_amount);
	return {
		title: text(config.title ? v[config.title] : "") || text(name),
		name: text(name),
		account: text(config.account ? v[config.account] : ""),
		streams: streams_of(doctype, v),
		amount: Number.isFinite(amount) ? amount : null,
		status: text(config.status ? v[config.status] : ""),
		stage: text(c.stage),
		done,
		total,
		missing: Array.isArray(c.missing) ? c.missing : [],
		percent: total > 0 ? Math.round((done / total) * 100) : 0,
	};
}
