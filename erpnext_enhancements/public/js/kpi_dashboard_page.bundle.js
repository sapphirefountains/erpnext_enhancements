// Shared renderer for the per-department KPI desk pages.
//
// Each department has its own role-gated Page (kpi_dashboards/page/<dept>_kpi);
// that page's on_page_load calls erpnext_enhancements.kpi_page.render(wrapper,
// "Finance"). The page pulls the latest precomputed KPI Snapshot through the
// role-gated KPI API (erpnext_enhancements.api.kpi) — the same data the KPI
// Cockpit block shows — and renders it as a focused, shareable single page.

frappe.provide("erpnext_enhancements.kpi_page");

const KPIDP_STYLE_ID = "kpidp-styles";

function kpidpInjectStyles() {
	if (document.getElementById(KPIDP_STYLE_ID)) {
		return;
	}
	const style = document.createElement("style");
	style.id = KPIDP_STYLE_ID;
	style.textContent = `
		.kpidp-root { padding: 4px 0 12px; }
		.kpidp-meta { font-size: 11px; color: var(--text-muted); margin-bottom: 10px; }
		.kpidp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
		.kpidp-card {
			background: var(--card-bg, var(--fg-color, #fff));
			border: 1px solid var(--border-color);
			border-left: 3px solid var(--border-color);
			border-radius: var(--border-radius, 8px);
			padding: 12px 14px; min-height: 92px;
			display: flex; flex-direction: column; justify-content: space-between;
		}
		.kpidp-card.status-good { border-left-color: #28a745; }
		.kpidp-card.status-watch { border-left-color: #f0ad4e; }
		.kpidp-card.status-bad { border-left-color: #e24c4c; }
		.kpidp-label { font-size: 11px; color: var(--text-muted); line-height: 1.3; margin-bottom: 6px; }
		.kpidp-value { font-size: 24px; font-weight: 600; line-height: 1.1; }
		.kpidp-foot { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-top: 8px; font-size: 11px; color: var(--text-muted); }
		.kpidp-trend.up { color: #28a745; }
		.kpidp-trend.down { color: #e24c4c; }
		.kpidp-trend.flat { color: var(--text-muted); }
		.kpidp-stale { display: inline-block; font-size: 9px; text-transform: uppercase; letter-spacing: 0.04em; color: #f0ad4e; border: 1px solid #f0ad4e; border-radius: 4px; padding: 0 4px; margin-top: 4px; align-self: flex-start; }
		.kpidp-empty { color: var(--text-muted); font-size: 13px; padding: 24px 0; }
	`;
	document.head.appendChild(style);
}

function kpidpTrend(pct) {
	if (pct === null || pct === undefined) return { cls: "flat", text: "" };
	const rounded = Math.round(pct * 10) / 10;
	if (rounded > 0) return { cls: "up", text: `▲ ${rounded}%` };
	if (rounded < 0) return { cls: "down", text: `▼ ${Math.abs(rounded)}%` };
	return { cls: "flat", text: "" };
}

function kpidpCards(snap) {
	const esc = frappe.utils.escape_html;
	return (snap.values || [])
		.map((v) => {
			const status = (v.status || "").toLowerCase();
			const statusClass = status ? `status-${status}` : "";
			const trend = kpidpTrend(v.trend_pct);
			const target =
				v.target_value !== null && v.target_value !== undefined && v.target_value !== 0
					? `<span>${__("Target")}: ${esc(String(v.target_value))}</span>`
					: "<span></span>";
			const stale = v.is_stale ? `<span class="kpidp-stale">${__("stale source")}</span>` : "";
			return `
				<div class="kpidp-card ${statusClass}">
					<div class="kpidp-label">${esc(v.label || v.kpi_key)}</div>
					<div>
						<div class="kpidp-value">${esc(v.value_text || String(v.value))}</div>
						${stale}
					</div>
					<div class="kpidp-foot">
						${target}
						<span class="kpidp-trend ${trend.cls}">${trend.text}</span>
					</div>
				</div>`;
		})
		.join("");
}

erpnext_enhancements.kpi_page.render = function (wrapper, department) {
	kpidpInjectStyles();
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("{0} KPIs", [department]),
		single_column: true,
	});

	const $root = $('<div class="kpidp-root"></div>').appendTo(page.body);
	const $meta = $('<div class="kpidp-meta"></div>').appendTo($root);
	const $body = $('<div class="kpidp-body"></div>').appendTo($root);

	function showMessage(message) {
		$meta.text("");
		$body.html(`<div class="kpidp-empty">${frappe.utils.escape_html(message)}</div>`);
	}

	function load(force) {
		$body.html(`<div class="kpidp-empty">${force ? __("Recomputing…") : __("Loading…")}</div>`);
		const method = force
			? "erpnext_enhancements.api.kpi.refresh_kpi_dashboard"
			: "erpnext_enhancements.api.kpi.get_kpi_dashboard";
		frappe
			.call({ method, args: { department } })
			.then((r) => {
				const m = r.message || {};
				if (!m.available) {
					showMessage(m.reason || __("KPI dashboard unavailable."));
					return;
				}
				if (!m.snapshot) {
					showMessage(__("No snapshot yet — it generates overnight, or press Refresh."));
					return;
				}
				const snap = m.snapshot;
				const cards = kpidpCards(snap);
				$body.html(
					cards
						? `<div class="kpidp-grid">${cards}</div>`
						: `<div class="kpidp-empty">${__("This snapshot has no KPI values.")}</div>`,
				);
				const gen = snap.generated_at ? ` · ${snap.generated_at}` : "";
				$meta.text(`${snap.snapshot_date || ""}${gen}`);
			})
			.catch(() => showMessage(__("Could not load the KPI dashboard.")));
	}

	page.set_primary_action(__("Refresh"), () => load(true), "refresh");
	load(false);
};
