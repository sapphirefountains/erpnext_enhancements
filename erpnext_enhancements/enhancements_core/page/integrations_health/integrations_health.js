/**
 * Desk page: Integrations Health (route /app/integrations-health).
 *
 * One ops screen for every external service this app depends on — QuickBooks,
 * Google Drive, Telephony (Triton/Twilio), AI drafting (Gemini), Analytics
 * (GA4/GSC) — plus scheduler liveness and a 24 h error digest. Each integration
 * renders as a green/amber/red tile; the data comes from
 * api.integrations_health.get_health (System-Manager-only, DB-only — no
 * outbound calls on load). The Drive tile carries a "Test connection" button
 * that runs the one live check on demand (api.integrations_health.run_drive_test).
 *
 * Theming follows the app convention: Frappe CSS vars for surfaces/text so it
 * works in Frappe Light + Timeless Night; the green/amber/red status colors are
 * semantic and stay literal (Frappe's --green/--yellow/--red palette vars).
 * Every server-supplied string is escaped with frappe.utils.escape_html.
 */
frappe.pages['integrations-health'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Integrations Health'),
		single_column: true,
	});

	const $body = $(`
		<div class="integrations-health">
			<style>
				.integrations-health { padding: 12px 4px 32px; }
				.ih-meta { color: var(--text-muted); font-size: 12px; margin: 0 6px 14px; }
				.ih-grid {
					display: grid;
					grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
					gap: 16px;
					margin-bottom: 22px;
				}
				.ih-card {
					background: var(--card-bg);
					border: 1px solid var(--border-color);
					border-radius: var(--border-radius);
					padding: 14px 16px;
					box-shadow: var(--card-shadow, none);
				}
				.ih-card-head { display: flex; align-items: center; gap: 9px; margin-bottom: 4px; }
				.ih-dot { width: 11px; height: 11px; border-radius: 50%; flex: 0 0 auto; }
				.ih-title { font-weight: 600; font-size: 14px; color: var(--text-color); }
				.ih-headline { font-size: 12px; color: var(--text-muted); margin: 2px 0 10px 20px; }
				.ih-metrics { display: flex; flex-direction: column; gap: 6px; }
				.ih-metric { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; }
				.ih-metric-label { color: var(--text-muted); }
				.ih-metric-value { display: flex; align-items: center; gap: 6px; color: var(--text-color); font-weight: 500; }
				.ih-mini-dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto; }
				.ih-note { font-size: 12px; color: var(--text-muted); margin-top: 10px; line-height: 1.45; }
				.ih-links { margin-top: 11px; display: flex; flex-wrap: wrap; gap: 8px; }
				.ih-links a { font-size: 12px; }
				.ih-actions { margin-top: 10px; }
				.ih-test-result { font-size: 12px; margin-top: 8px; line-height: 1.5; }
				.ih-test-result .ok { color: var(--green-600, var(--green-500)); }
				.ih-test-result .bad { color: var(--red-600, var(--red-500)); }
				.ih-lower { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
				@media (max-width: 720px) { .ih-lower { grid-template-columns: 1fr; } }
				.ih-panel-title { font-weight: 600; font-size: 13px; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; color: var(--text-color); }
				.ih-row { display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; padding: 3px 0; border-bottom: 1px solid var(--border-color); }
				.ih-row:last-child { border-bottom: none; }
				.ih-row .muted { color: var(--text-muted); }
				.ih-empty { color: var(--text-muted); font-size: 12.5px; }
				.ih-loading { color: var(--text-muted); padding: 30px; text-align: center; }
			</style>
			<div class="ih-meta"></div>
			<div class="ih-content"><div class="ih-loading">${__('Loading integration status…')}</div></div>
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

	function load() {
		const $content = $body.find('.ih-content');
		$content.html(`<div class="ih-loading">${__('Loading integration status…')}</div>`);
		frappe.call({
			method: 'erpnext_enhancements.api.integrations_health.get_health',
			callback: (r) => {
				if (!r || !r.message) {
					$content.html(`<div class="ih-empty">${__('No data returned.')}</div>`);
					return;
				}
				render(r.message);
			},
			error: () => {
				$content.html(`<div class="ih-empty">${__('Failed to load — you may not have permission.')}</div>`);
			},
		});
	}

	function render(data) {
		$body.find('.ih-meta').text(__('As of') + ' ' + (data.generated_at || ''));

		const tiles = (data.integrations || []).map(tile).join('');
		const lower = `
			<div class="ih-lower">
				<div class="ih-card">${scheduler(data.scheduler || {})}</div>
				<div class="ih-card">${errors(data.errors || {})}</div>
			</div>`;
		$body.find('.ih-content').html(`<div class="ih-grid">${tiles}</div>${lower}`);
		bindActions();
	}

	function tile(it) {
		const metrics = (it.metrics || []).map((m) => `
			<div class="ih-metric">
				<span class="ih-metric-label">${esc(m.label)}</span>
				<span class="ih-metric-value">
					${m.tone && m.tone !== 'neutral' ? `<span class="ih-mini-dot" style="background:${tone(m.tone)}"></span>` : ''}
					${esc(String(m.value))}
				</span>
			</div>`).join('');

		const links = (it.links || []).map((l) =>
			`<a href="${esc(l.route)}">${esc(l.label)}</a>`).join('');

		const notes = (it.notes || []).map((n) => `<div class="ih-note">${esc(n)}</div>`).join('');

		const actions = (it.actions || []).includes('drive_test')
			? `<div class="ih-actions">
					<button class="btn btn-xs btn-default ih-drive-test">${__('Test connection')}</button>
					<div class="ih-test-result"></div>
			   </div>`
			: '';

		return `
			<div class="ih-card" data-key="${esc(it.key)}">
				<div class="ih-card-head">
					<span class="ih-dot" style="background:${tone(it.status)}"></span>
					<span class="ih-title">${esc(it.label)}</span>
				</div>
				<div class="ih-headline">${esc(it.headline || '')}</div>
				<div class="ih-metrics">${metrics}</div>
				${notes}
				${actions}
				<div class="ih-links">${links}</div>
			</div>`;
	}

	function scheduler(s) {
		const head = `<div class="ih-panel-title"><span class="ih-dot" style="background:${tone(s.status)}"></span>${__('Background jobs')}</div>`;
		if (s.enabled === false) {
			return head + `<div class="ih-note" style="color:${TONE.red}">${__('The Frappe scheduler is DISABLED — no daily/hourly jobs are running.')}</div>`;
		}
		const summary = `
			<div class="ih-row"><span class="muted">${__('Scheduler')}</span><span>${s.enabled === null ? '—' : __('enabled')}</span></div>
			<div class="ih-row"><span class="muted">${__('App jobs registered')}</span><span>${esc(String(s.app_job_count || 0))}</span></div>
			<div class="ih-row"><span class="muted">${__('Failed (24h)')}</span><span style="color:${s.failed_24h ? TONE.red : 'inherit'}">${esc(String(s.failed_24h || 0))}</span></div>`;
		const fails = (s.recent_failures || []).map((f) =>
			`<div class="ih-row"><span>${esc(f.job)}</span><span class="muted">${esc(f.when)}</span></div>`).join('');
		return head + summary + (fails ? `<div style="margin-top:8px">${fails}</div>` : '');
	}

	function errors(e) {
		const head = `<div class="ih-panel-title"><span class="ih-dot" style="background:${tone(e.status)}"></span>${__('Errors (24h)')}</div>`;
		const total = `<div class="ih-row"><span class="muted">${__('Total error logs')}</span><span>${esc(String(e.total_24h || 0))}</span></div>`;
		const top = (e.top || []).map((t) =>
			`<div class="ih-row"><span>${esc(t.title)}</span><span class="muted">${esc(String(t.count))}</span></div>`).join('');
		return head + total + (top ? `<div style="margin-top:8px">${top}</div>` : `<div class="ih-empty" style="margin-top:8px">${__('No errors logged in the last 24 hours.')}</div>`);
	}

	function bindActions() {
		$body.find('.ih-drive-test').off('click').on('click', function () {
			const $btn = $(this);
			const $out = $btn.siblings('.ih-test-result');
			$btn.prop('disabled', true).text(__('Testing…'));
			$out.empty();
			const done = () => $btn.prop('disabled', false).text(__('Test connection'));
			frappe.call({
				method: 'erpnext_enhancements.api.integrations_health.run_drive_test',
				callback: (r) => {
					done();
					const checks = (r && r.message && r.message.checks) || [];
					if (!checks.length && r && r.message) {
						$out.html(`<div>${esc(JSON.stringify(r.message))}</div>`);
						return;
					}
					// ok is true (pass), false (fail), or null (not configured).
					$out.html(checks.map((c) => {
						const mark = c.ok === true ? '✓' : c.ok === false ? '✗' : '–';
						const cls = c.ok === true ? 'ok' : c.ok === false ? 'bad' : '';
						return `<div class="${cls}">${mark} ${esc(c.check || '')}${c.detail ? ' — ' + esc(String(c.detail)) : ''}</div>`;
					}).join(''));
				},
				error: () => {
					done();
					$out.html(`<div class="bad">${__('Test failed — see the Error Log.')}</div>`);
				},
			});
		});
	}

	load();
};
