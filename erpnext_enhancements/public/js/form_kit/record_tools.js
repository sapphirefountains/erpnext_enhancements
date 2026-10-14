/*
 * The record tools' decisions (v1.686.0): what the Desk form's sidebar, timeline, dashboard and
 * toolbar show for a saved record, worked out for the guided page and the side panel. Pure (no DOM,
 * no frappe at import time), run under node by tests/test_guided_record_tools.py. The tools that
 * draw them are ./ui/record_tools.js.
 *
 * Nik, 2026-10-14: "We shouldn't have lost any fidelity or information on any of the UI/UX changes."
 * Everything here is decided by the record's meta, its permissions and its values, never by its
 * DocType's name, except RECORD_TOOLS below: the Create menu and the email field are genuinely per
 * DocType (they are what that DocType's own form script adds), so a DocType lists them there.
 * A guided DocType with no entry still gets every other tool.
 */

const OPPORTUNITY = "erpnext.crm.doctype.opportunity.opportunity.";

/**
 * Per DocType, what its own form script adds:
 *   email_field   the field the form's New Email starts with (frm.email_field; ERPNext's
 *                 opportunity.js sets "contact_email"). Without one, email_id or email, as the
 *                 Desk timeline's get_recipient falls back.
 *   create        the form's Create menu: frappe.model.open_mapped_doc({ method, source_name }) with
 *                 the methods the DocType's form script uses, in its order. `hide_when` hides the
 *                 whole menu; an item's `unless` hides that item.
 */
export const RECORD_TOOLS = {
	// ERPNext v16 erpnext/crm/doctype/opportunity/opportunity.js refresh(): on a saved deal that is not
	// Lost, Supplier Quotation and Request For Quotation (under `if (doc.items)`, an array and so
	// always true), Customer unless the party is already one, and Quotation.
	Opportunity: {
		email_field: "contact_email",
		create: {
			hide_when: { field: "status", in: ["Lost"] },
			items: [
				{ label: "Supplier Quotation", doctype: "Supplier Quotation", method: OPPORTUNITY + "make_supplier_quotation" },
				{
					label: "Request For Quotation",
					doctype: "Request for Quotation",
					method: OPPORTUNITY + "make_request_for_quotation",
				},
				{
					label: "Customer",
					doctype: "Customer",
					method: OPPORTUNITY + "make_customer",
					unless: { field: "opportunity_from", in: ["Customer"] },
				},
				{ label: "Quotation", doctype: "Quotation", method: OPPORTUNITY + "make_quotation" },
			],
		},
	},
};

export function tools_config(doctype) {
	return RECORD_TOOLS[doctype] || {};
}

function matches(rule, values) {
	if (!rule || !rule.field) return false;
	const value = (values || {})[rule.field];
	return (rule.in || []).indexOf(value === undefined ? null : value) !== -1;
}

/** The Create menu's items for this record now: config order, each one the person may create. */
export function create_items(config, values, can_create) {
	const create = (config && config.create) || null;
	if (!create || matches(create.hide_when, values)) return [];
	return (create.items || []).filter(
		(item) => !matches(item.unless, values) && (typeof can_create !== "function" || can_create(item.doctype))
	);
}

/** Who a New Email goes to: frappe's form_timeline.js get_recipient, against the record's values. */
export function recipient_for(config, values) {
	const v = values || {};
	if (config && config.email_field) return v[config.email_field] || "";
	return v.email_id || v.email || "";
}

/**
 * A New Email's subject: frappe's CommunicationComposer setup_subject_and_recipients. The record's
 * title (else its name), with "(#<name>)" added so a reply threads back to the record.
 */
export function email_subject(title, name) {
	let subject = String(title || name || "");
	const identifier = `#${name}`;
	if (name && subject.indexOf(identifier) === -1) subject = `${subject} (${identifier})`;
	return subject;
}

// ------------------------------------------------------------------ attachments

/** A file's address: frappe's sidebar attachments.js get_file_url. */
export function file_url(file) {
	const f = file || {};
	let url = f.file_url;
	if (!url) {
		const name = String(f.file_name || "");
		url = name.indexOf("files/") === 0 ? "/" + name : "/files/" + name;
	}
	const is_web_url = /^(https?:)?\/\//i.test(url);
	url = encodeURI(url);
	// encodeURI leaves "#" alone; a local file name may contain one.
	if (!is_web_url) url = url.replace(/#/g, "%23");
	return url;
}

/** One row per file name, as the sidebar draws them (its remove takes every row of that name). */
export function unique_files(files) {
	const seen = new Set();
	return (files || []).filter((file) => {
		const key = (file && file.file_name) || (file && file.file_url) || (file && file.name);
		if (!key || seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

/** "12 KB", "3.4 MB": a file's size for a person. */
export function size_text(bytes) {
	const n = Number(bytes) || 0;
	if (n <= 0) return "";
	if (n < 1024) return `${n} B`;
	const units = ["KB", "MB", "GB"];
	let value = n / 1024;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value >= 10 ? Math.round(value) : Math.round(value * 10) / 10} ${units[unit]}`;
}

/** "PDF", "PNG", "Link": a file's kind, from its type or its extension. */
export function kind_text(file) {
	const f = file || {};
	if (f.file_url && /^(https?:)?\/\//i.test(f.file_url) && !f.file_type) return "Link";
	const type = String(f.file_type || "").trim();
	if (type) return type.toUpperCase();
	const name = String(f.file_name || f.file_url || "").split("?")[0];
	const dot = name.lastIndexOf(".");
	return dot > -1 && dot < name.length - 1 ? name.slice(dot + 1).toUpperCase() : "";
}

/**
 * Whether the person may remove an attachment: frappe's attachments.js can_delete_attachment. With
 * the DocType's protect_attached_files, only a draft (write) or a cancelled record (write and delete).
 */
export function can_remove_files(opts) {
	const o = opts || {};
	const write = !!o.write;
	if (o.protect_attached_files) {
		if (Number(o.docstatus) === 0) return write;
		if (Number(o.docstatus) === 2) return write && !!o.delete;
		return false;
	}
	return write;
}

/** Whether another file may be attached: write, and the DocType's max_attachments not reached. */
export function can_add_file(opts) {
	const o = opts || {};
	if (!o.write) return false;
	const max = Number(o.max_attachments) || 0;
	return !max || Number(o.count || 0) < max;
}

// ------------------------------------------------------------------ the activity feed

/** A version value as the timeline shows it: text, at most 40 characters, '""' for nothing. */
export function version_value(value) {
	let text = value === null || value === undefined ? "" : String(value);
	text = text.replace(/<[^>]*>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
	text = text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
	if (text.length > 40) text = text.slice(0, 40) + "…";
	return text || '""';
}

/**
 * One Version row as sentences' parts: frappe's version_timeline_content_builder.js, field by field.
 * `label(fieldname)` and `child_label(table_fieldname, fieldname)` answer a field's label, or null
 * when the person may not see it (hidden without show_on_timeline, or a permlevel they cannot read):
 * the Desk's own rule, applied by the caller with frappe.perm. Like the Desk, at most three changes
 * are named per sentence.
 *
 * Returns [{kind: "comment", text} | {kind: "submitted"} | {kind: "cancelled"}
 *          | {kind: "changed", parts: [{label, from, to}]}
 *          | {kind: "rows_changed", parts: [{label, from, to, row}]}
 *          | {kind: "added" | "removed", tables: [{label, count}]}]
 */
export function describe_version(data, label, child_label) {
	let d = data;
	if (typeof d === "string") {
		try {
			d = JSON.parse(d);
		} catch (e) {
			return [];
		}
	}
	if (!d || typeof d !== "object") return [];
	const name_of = typeof label === "function" ? label : () => null;
	const child_of = typeof child_label === "function" ? child_label : () => null;
	if (d.comment) return [{ kind: "comment", text: version_value(d.comment).replace(/^""$/, "") }];
	const out = [];

	if (Array.isArray(d.changed) && d.changed.length) {
		const parts = [];
		for (const p of d.changed) {
			if (!Array.isArray(p)) continue;
			if (p[0] === "docstatus") {
				if (p[2] === 1) out.push({ kind: "submitted" });
				else if (p[2] === 2) out.push({ kind: "cancelled" });
			} else {
				const field = name_of(p[0]);
				if (field) parts.push({ label: field, from: version_value(p[1]), to: version_value(p[2]) });
			}
			if (parts.length >= 3) break;
		}
		if (parts.length) out.push({ kind: "changed", parts });
	}

	if (Array.isArray(d.row_changed) && d.row_changed.length) {
		const parts = [];
		for (const row of d.row_changed) {
			if (!Array.isArray(row) || !Array.isArray(row[3])) continue;
			for (const p of row[3]) {
				const field = Array.isArray(p) ? child_of(row[0], p[0]) : null;
				if (field) {
					parts.push({ label: field, from: version_value(p[1]), to: version_value(p[2]), row: Number(row[1]) + 1 });
				}
				if (parts.length >= 3) break;
			}
			if (parts.length >= 3) break;
		}
		if (parts.length) out.push({ kind: "rows_changed", parts });
	}

	["added", "removed"].forEach((key) => {
		if (!Array.isArray(d[key]) || !d[key].length) return;
		const counts = [];
		const index = {};
		d[key].forEach((p) => {
			const table = Array.isArray(p) ? name_of(p[0]) : null;
			if (!table) return;
			if (!(table in index)) {
				index[table] = counts.length;
				counts.push({ label: table, count: 0 });
			}
			counts[index[table]].count += 1;
		});
		if (counts.length) out.push({ kind: key, tables: counts });
	});
	return out;
}

function stamp(value) {
	const s = String(value || "");
	return s;
}

/**
 * The record's activity, newest first: created and last edited, every Version, email and
 * communication, the assignment / attachment / info / workflow logs, and the milestones, as the Desk
 * timeline lists them with "Show all activity" on. `describe` turns a Version into its parts
 * (describe_version with the caller's labels); a Version that names nothing the person may see is
 * left out, as on the Desk. Each item: {at, kind, by, by_name, ...}.
 */
export function activity_feed(info, describe) {
	const i = info || {};
	const items = [];
	if (i.created && i.created.at) {
		items.push({ at: stamp(i.created.at), kind: "created", by: i.created.by, by_name: i.created.by_name });
	}
	if (i.modified && i.modified.at && i.modified.at !== (i.created || {}).at) {
		items.push({ at: stamp(i.modified.at), kind: "edited", by: i.modified.by, by_name: i.modified.by_name });
	}
	(i.versions || []).forEach((version) => {
		const parts = typeof describe === "function" ? describe(version.data) : describe_version(version.data);
		(parts || []).forEach((part) => {
			items.push(Object.assign({ at: stamp(version.at), kind: "version", by: version.owner, by_name: version.owner_name, version: version.name }, { change: part }));
		});
	});
	(i.communications || []).forEach((c) => {
		items.push({
			at: stamp(c.at),
			kind: "communication",
			by: c.sender,
			by_name: c.sender_name,
			communication: c,
		});
	});
	(i.logs || []).forEach((log) => {
		items.push({ at: stamp(log.at), kind: "log", by: log.owner, by_name: log.owner_name, log });
	});
	(i.milestones || []).forEach((m) => {
		items.push({ at: stamp(m.at), kind: "milestone", by: m.owner, by_name: m.owner_name, milestone: m });
	});
	// Newest first; a stable sort keeps a version's own sentences in order.
	return items
		.map((item, index) => ({ item, index }))
		.sort((a, b) => (a.item.at < b.item.at ? 1 : a.item.at > b.item.at ? -1 : a.index - b.index))
		.map((entry) => entry.item);
}

// ------------------------------------------------------------------ tags

/** A tag as typed: trimmed, inner spaces collapsed, no commas (the tags column is comma separated). */
export function clean_tag(text) {
	return String(text || "")
		.replace(/,/g, " ")
		.replace(/\s+/g, " ")
		.trim();
}

// ------------------------------------------------------------------ connections

/**
 * The Desk dashboard's connections: the DocType's dashboard data (meta.__dashboard, with this app's
 * override_doctype_dashboards applied server-side), each group's items limited to the DocTypes the
 * person may read, and a group left out when none remain (frappe's dashboard.js filter_permissions).
 */
export function connection_groups(dashboard, can_read) {
	const d = dashboard || {};
	const groups = [];
	(d.transactions || []).forEach((group) => {
		const items = (group.items || []).filter((doctype) => typeof can_read !== "function" || can_read(doctype));
		if (items.length) groups.push({ label: group.label || "", items });
	});
	return groups;
}

/** Every DocType the groups list, for get_open_count's `items`. */
export function connection_doctypes(groups) {
	const out = [];
	(groups || []).forEach((group) => group.items.forEach((doctype) => out.indexOf(doctype) === -1 && out.push(doctype)));
	return out;
}

/** get_open_count's answer by DocType: {count, open_count, names, internal}. */
export function connection_counts(count) {
	const out = {};
	const c = count || {};
	(c.external_links_found || []).forEach((row) => {
		if (row && row.doctype) out[row.doctype] = { count: Number(row.count) || 0, open_count: Number(row.open_count) || 0, names: row.names || [], internal: false };
	});
	(c.internal_links_found || []).forEach((row) => {
		if (row && row.doctype) out[row.doctype] = { count: Number(row.count) || 0, open_count: Number(row.open_count) || 0, names: row.names || [], internal: true };
	});
	return out;
}

/**
 * The list a connection opens, filtered to this record: frappe's dashboard.js open_document_list.
 * Linked by name (an internal link, or names the count returned): those names. An internal link
 * with no names: nothing to open (null). Otherwise the dashboard's fieldname (or the DocType's
 * non-standard one) equal to this record, with the dynamic link's type beside it.
 */
export function connection_filters(dashboard, doctype, name, found) {
	const d = dashboard || {};
	const f = found || {};
	if (f.names && f.names.length) return { name: ["in", f.names] };
	if (f.internal) return null;
	const fieldname = (d.non_standard_fieldnames && d.non_standard_fieldnames[doctype]) || d.fieldname;
	if (!fieldname) return null;
	const filters = {};
	if (d.dynamic_links && d.dynamic_links[fieldname]) {
		const link = d.dynamic_links[fieldname];
		filters[link[1]] = link[0];
	}
	filters[fieldname] = name;
	return filters;
}

// ------------------------------------------------------------------ print, and what is on the form

/** Whether the record may be printed: frappe's model.js can_print_doc, decided per record. */
export function can_print(opts) {
	const o = opts || {};
	if (o.issingle || !o.print) return false;
	if (!o.is_submittable || Number(o.docstatus) === 1) return true;
	if (Number(o.docstatus) === 2) return !!o.allow_print_for_cancelled;
	if (Number(o.docstatus) === 0) return !!o.allow_print_for_draft;
	return false;
}

/**
 * Which directory widgets the DocType's form has (the Desk's contact_list_html / address_list_html,
 * drawn by unified_tab_controller.js): the guided page shows the same lists where the form does.
 */
export function directory_widgets(fieldnames) {
	const names = new Set(fieldnames || []);
	return { contacts: names.has("contact_list_html"), addresses: names.has("address_list_html") };
}
