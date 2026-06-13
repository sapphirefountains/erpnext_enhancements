/**
 * Desk page: Device Fleet Dashboard (route /app/device-fleet-dashboard).
 *
 * A green/amber/red snapshot of the managed-device fleet — status mix,
 * compliance split, stale self-attestations, warranty expiries, platform /
 * ownership breakdown — from api.device_dashboard.get_fleet_health
 * (Device-Manager / System-Manager only, DB-only). The tile/dot/metric markup
 * and theming mirror the Integrations Health page; semantic green/amber/red
 * stays literal (Frappe palette vars). Every server string is escaped.
 */
frappe.pages['device-fleet-dashboard'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Device Fleet Dashboard'),
		single_column: true,
	});

	const $body = $(`
		<div class="dfd">
			<style>
				.dfd { padding: 12px 4px 32px; }
				.dfd-meta { color: var(--text-muted); font-size: 12px; margin: 0 6px 14px; }
				.dfd-grid {
					display: grid;
					grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
					gap: 16px;
				}
				.dfd-card {
					background: var(--card-bg);
					border: 1px solid var(--border-color);
					border-radius: var(--border-radius);
					padding: 14px 16px;
					box-shadow: var(--card-shadow, none);
				}
				.dfd-head { display: flex; align-items: center; gap: 9px; margin-bottom: 2px; }
				.dfd-dot { width: 11px; height: 11px; border-radius: 50%; flex: 0 0 auto; }
				.dfd-title { font-weight: 600; font-size: 14px; color: var(--text-color); }
				.dfd-headline { font-size: 12px; color: var(--text-muted); margin: 2px 0 10px 20px; }
				.dfd-metrics { display: flex; flex-direction: column; gap: 6px; }
				.dfd-metric { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; }
				.dfd-metric-label { color: var(--text-muted); }
				.dfd-metric-value { display: flex; align-items: center; gap: 6px; color: var(--text-color); font-weight: 500; }
				.dfd-mini-dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto; }
				.dfd-links { margin-top: 11px; display: flex; flex-wrap: wrap; gap: 8px; }
				.dfd-links a { font-size: 12px; }
				.dfd-loading, .dfd-empty { color: var(--text-muted); padding: 30px; text-align: center; }
			</style>
			<div class="dfd-meta"></div>
			<div class="dfd-content"><div class="dfd-loading">${__('Loading fleet status…')}</div></div>
		</div>
	`).appendTo(page.body);

	const TONE = {
		green: 'var(--green-500)',
		amber: 'var(--yellow-500)',
		red: 'var(--red-500)',
		neutral: 'var(--gray-400, #9ca3af)',
	};
	const esc = frappe.utils.escape_html;
	const tone = (t) => TONE[t] || TONE.neutral;

	page.set_primary_action(__('Refresh'), () => load(), 'refresh');
	page.set_secondary_action(__('Device Console'), () => frappe.set_route('device-console'));

	function load() {
		const $content = $body.find('.dfd-content');
		$content.html(`<div class="dfd-loading">${__('Loading fleet status…')}</div>`);
		frappe.call({
			method: 'erpnext_enhancements.api.device_dashboard.get_fleet_health',
			callback: (r) => {
				if (!r || !r.message) {
					$content.html(`<div class="dfd-empty">${__('No data returned.')}</div>`);
					return;
				}
				render(r.message);
			},
			error: () => {
				$content.html(`<div class="dfd-empty">${__('Failed to load — you may not have permission.')}</div>`);
			},
		});
	}

	function render(data) {
		$body.find('.dfd-meta').text(
			__('{0} devices · as of {1}', [data.total || 0, data.generated_at || ''])
		);
		const tiles = (data.tiles || []).map(tile).join('');
		$body.find('.dfd-content').html(`<div class="dfd-grid">${tiles}</div>`);
	}

	function tile(it) {
		const metrics = (it.metrics || [])
			.map(
				(m) => `
				<div class="dfd-metric">
					<span class="dfd-metric-label">${esc(m.label)}</span>
					<span class="dfd-metric-value">
						${m.tone && m.tone !== 'neutral' ? `<span class="dfd-mini-dot" style="background:${tone(m.tone)}"></span>` : ''}
						${esc(String(m.value))}
					</span>
				</div>`
			)
			.join('');
		const links = (it.links || []).map((l) => `<a href="${esc(l.route)}">${esc(l.label)}</a>`).join('');
		return `
			<div class="dfd-card" data-key="${esc(it.key)}">
				<div class="dfd-head">
					<span class="dfd-dot" style="background:${tone(it.status)}"></span>
					<span class="dfd-title">${esc(it.label)}</span>
				</div>
				<div class="dfd-headline">${esc(it.headline || '')}</div>
				<div class="dfd-metrics">${metrics}</div>
				<div class="dfd-links">${links}</div>
			</div>`;
	}

	load();
};
