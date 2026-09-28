// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// What only a write past the knowledge base's own code can break (WI-080 PR 4). No filters: every
// run checks everything, and a healthy site returns no rows. The checks are in
// knowledge_base/reporting.py; no row ever quotes an article's or a draft's text.
frappe.query_reports["Knowledge Base Integrity"] = {
	filters: [],
};
