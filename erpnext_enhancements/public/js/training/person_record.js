// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// TR.personRecord(user) — one learner's training standing, for a manager.
//
// WHAT IT DELIBERATELY DOES NOT SHOW: scores, attempt counts, quiz history. Not
// filtered out here — `training.dashboard.get_person_dashboard` never builds them,
// so there is nothing for this file to leak even by accident. The reasoning is on
// that endpoint: showing a person their own quiz history is feedback, showing it to
// their manager is assessment, and this module took that position once already when
// the team feed was built to carry no number anyone could be judged by.
//
// It answers the compliance question instead: what do they owe, what is late, what
// is about to lapse, and how far through are they.
//
// LOADED ON DEMAND rather than shipped with the rail. A manager opens this rarely
// and a learner never, so putting it in the assets every training page pulls would
// charge fifteen people for a screen three of them can see.

(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});

	function t(text, args) {
		return typeof window.__ === "function" ? window.__(text, args) : text;
	}

	function el(tag, cls, text) {
		var node = document.createElement(tag);
		if (cls) node.className = cls;
		if (text !== undefined && text !== null) node.textContent = text;
		return node;
	}

	function pct(value) {
		return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
	}

	function tile(label, value, bad) {
		var box = el("div", "tpr-tile" + (bad ? " is-bad" : ""));
		box.appendChild(el("div", "tpr-tile-value", String(value)));
		box.appendChild(el("div", "tpr-tile-label", label));
		return box;
	}

	function courseRow(row) {
		var item = el("div", "tpr-row");
		item.appendChild(el("div", "tpr-row-title", row.title || row.course));

		var bits = [];
		if (row.overdue) bits.push(t("Overdue"));
		else if (row.due_date) bits.push(t("Due {0}", [frappe.datetime.str_to_user(row.due_date)]));
		else if (row.status) bits.push(row.status);
		if (row.expiring_soon) bits.push(t("Certificate lapsing"));
		if (row.completed_on) bits.push(t("Completed {0}", [frappe.datetime.str_to_user(row.completed_on)]));

		var meta = el("div", "tpr-row-meta", bits.join(" · "));
		if (row.overdue || row.expiring_soon) meta.classList.add("is-bad");
		item.appendChild(meta);

		var done = pct(row.percent_complete);
		if (done > 0) {
			var bar = el("div", "tpr-bar");
			bar.setAttribute("role", "progressbar");
			bar.setAttribute("aria-valuenow", String(done));
			bar.setAttribute("aria-valuemin", "0");
			bar.setAttribute("aria-valuemax", "100");
			var fill = el("div", "tpr-bar-fill");
			fill.style.width = done + "%";
			bar.appendChild(fill);
			item.appendChild(bar);
		}
		return item;
	}

	function section(title, rows) {
		if (!rows || !rows.length) return null;
		var box = el("div", "tpr-section");
		box.appendChild(el("div", "tpr-section-title", title));
		rows.forEach(function (row) {
			box.appendChild(courseRow(row));
		});
		return box;
	}

	function render(host, data) {
		host.innerHTML = "";
		if (data.enabled === false) {
			host.appendChild(el("p", "tpr-muted", data.message || t("Training is switched off.")));
			return;
		}

		var c = data.compliance || {};
		var tiles = el("div", "tpr-tiles");
		// Drawn only when there is something to say. A row of zeros is a worse
		// answer than three numbers, and a red 0 is a problem that is not there.
		if (c.overdue) tiles.appendChild(tile(t("Overdue"), c.overdue, true));
		if (c.due_soon) tiles.appendChild(tile(t("Due in 30 days"), c.due_soon));
		if (c.required_percent !== null && c.required_percent !== undefined) {
			tiles.appendChild(tile(t("Required complete"), c.required_percent + "%"));
		}
		if (c.certificates_expiring) tiles.appendChild(tile(t("Certificates lapsing"), c.certificates_expiring, true));
		if (c.certificates_expired) tiles.appendChild(tile(t("Certificates lapsed"), c.certificates_expired, true));
		if (tiles.childNodes.length) host.appendChild(tiles);

		var required = section(t("Outstanding"), data.required);
		if (required) host.appendChild(required);
		var completed = section(t("Completed"), (data.completed || []).slice(0, 8));
		if (completed) host.appendChild(completed);

		if (!(data.required || []).length && !(data.completed || []).length) {
			host.appendChild(el("p", "tpr-muted", t("No training is recorded for this person.")));
		}
	}

	TR.personRecord = function (user) {
		if (!user) return;
		var dialog = new frappe.ui.Dialog({
			title: t("Training record"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
		});
		var host = dialog.get_field("body").$wrapper.get(0);
		host.className = "tpr-record";
		host.appendChild(el("p", "tpr-muted", t("Loading…")));
		dialog.show();

		frappe
			.xcall("erpnext_enhancements.training.dashboard.get_person_dashboard", { user: user })
			.then(function (data) {
				data = data || {};
				if (data.full_name) dialog.set_title(t("Training record: {0}", [data.full_name]));
				render(host, data);
			})
			.catch(function () {
				// xcall has already shown the server's message -- including the
				// permission refusal, which is the one a non-manager will meet.
				host.innerHTML = "";
				host.appendChild(el("p", "tpr-muted", t("Could not load that training record.")));
			});
	};
})();
